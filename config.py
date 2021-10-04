import appdirs
import os
import toml
import logging
from copy import deepcopy
import click

CONFIG_DIR = appdirs.user_config_dir('kupfer')
CACHE_DIR = appdirs.user_cache_dir('kupfer')

CONFIG_DEFAULT_PATH = os.path.join(CONFIG_DIR, 'kupferbootstrap.toml')

Profile = dict[str, str]

PROFILE_DEFAULTS: Profile = {
    'parent': '',
    'device': '',
    'flavour': '',
    'pkgs_include': [],
    'pkgs_exclude': [],
    'hostname': 'kupfer',
    'username': 'kupfer',
    'password': None,
}

CONFIG_DEFAULTS = {
    'build': {
        'crosscompile': True,
        'crossdirect': True,
        'threads': 0,
    },
    'paths': {
        'cache_dir': CACHE_DIR,
        'chroots': os.path.join('%cache_dir%', 'chroots'),
        'pacman': os.path.join('%cache_dir%', 'pacman'),
        'jumpdrive': os.path.join('%cache_dir%', 'jumpdrive'),
        'packages': os.path.join('%cache_dir%', 'packages'),
        'pkgbuilds': os.path.abspath(os.getcwd()),
    },
    'profiles': {
        'current': 'default',
        'default': deepcopy(PROFILE_DEFAULTS),
    },
}

CONFIG_RUNTIME_DEFAULTS = {
    'verbose': False,
    'config_file': None,
    'arch': None,
    'no_wrap': False,
    'script_source_dir': os.path.dirname(os.path.realpath(__file__))
}


def resolve_path_template(path_template: str, paths: dict[str, str]) -> str:
    terminator = '%'  # i'll be back
    result = path_template
    for path_name, path in paths.items():
        result = result.replace(terminator + path_name + terminator, path)
    return result


def resolve_profile(
    name: str,
    sparse_profiles: dict[str, Profile],
    resolved: dict[str, Profile] = None,
    _visited=None,
) -> dict[str, Profile]:
    """
    Recursively resolves the specified profile by `name` and its parents to merge the config semantically,
    applying include and exclude overrides along the hierarchy.
    If `resolved` is passed `None`, a fresh dictionary will be created.
    `resolved` will be modified in-place during parsing and also returned.
    A sanitized `sparse_profiles` dict is assumed, no checking for unknown keys or incorrect data types is performed.
    `_visited` should not be passed by users.
    """
    if _visited is None:
        _visited = list[str]()
    if resolved is None:
        resolved = dict[str, Profile]()
    if name in _visited:
        loop = list(_visited)
        raise Exception(f'Dependency loop detected in profiles: {" -> ".join(loop+[loop[0]])}')
    if name in resolved:
        return resolved

    logging.debug(f'Resolving profile {name}')
    _visited.append(name)
    sparse = sparse_profiles[name]
    full = deepcopy(sparse)
    if 'parent' in sparse and (parent_name := sparse['parent']):
        parent = resolve_profile(name=parent_name, sparse_profiles=sparse_profiles, resolved=resolved, _visited=_visited)[parent_name]
        full = parent | sparse

        # join our includes with parent's
        includes = set(parent.get('pkgs_include', []) + sparse.get('pkgs_include', []))
        if 'pkgs_exclude' in sparse:
            includes -= set(sparse['pkgs_exclude'])
        full['pkgs_include'] = list(includes)

        # join our includes with parent's
        excludes = set(parent.get('pkgs_exclude', []) + sparse.get('pkgs_exclude', []))
        # our includes override parent excludes
        if 'pkgs_include' in sparse:
            excludes -= set(sparse['pkgs_include'])
        full['pkgs_exclude'] = list(excludes)

    # now init missing keys
    for key, value in PROFILE_DEFAULTS.items():
        if key not in full.keys():
            full[key] = None
            if type(value) == list:
                full[key] = []

    resolved[name] = full
    return resolved


def sanitize_config(conf: dict, warn_missing_defaultprofile=True) -> dict:
    """checks the input config dict for unknown keys and returns only the known parts"""
    return merge_configs(conf_new=conf, conf_base={}, warn_missing_defaultprofile=warn_missing_defaultprofile)


def merge_configs(conf_new: dict, conf_base={}, warn_missing_defaultprofile=True) -> dict:
    """
    Returns `conf_new` semantically merged into `conf_base`, after validating
    `conf_new` keys against `CONFIG_DEFAULTS` and `PROFILE_DEFAULTS`.
    Pass `conf_base={}` to get a sanitized version of `conf_new`.
    NOTE: `conf_base` is NOT checked for invalid keys. Sanitize beforehand.
    """
    parsed = deepcopy(conf_base)

    for outer_name, outer_conf in deepcopy(conf_new).items():
        # only handle known config sections
        if outer_name not in CONFIG_DEFAULTS.keys():
            logging.warning(f'Skipped unknown config section "{outer_name}"')
            continue
        logging.debug(f'Working on outer section "{outer_name}"')
        # check if outer_conf is a dict
        if not isinstance(outer_conf, dict):
            parsed[outer_name] = outer_conf
        else:
            # init section
            if outer_name not in parsed:
                parsed[outer_name] = {}

            # profiles need special handling:
            # 1. profile names are unknown keys by definition, but we want 'default' to exist
            # 2. A profile's subkeys must be compared against PROFILE_DEFAULTS.keys()
            if outer_name == 'profiles':
                if warn_missing_defaultprofile and 'default' not in outer_conf.keys():
                    logging.warning('Default profile is not defined in config file')

                for profile_name, profile_conf in outer_conf.items():
                    if not isinstance(profile_conf, dict):
                        if profile_name == 'current':
                            parsed[outer_name][profile_name] = profile_conf
                        else:
                            logging.warning('Skipped key "{profile_name}" in profile section: only subsections and "current" allowed')
                        continue

                    #  init profile
                    if profile_name not in parsed[outer_name]:
                        parsed[outer_name][profile_name] = {}

                    for key, val in profile_conf.items():
                        if key not in PROFILE_DEFAULTS:
                            logging.warning(f'Skipped unknown config item "{key}" in profile "{profile_name}"')
                            continue
                        parsed[outer_name][profile_name][key] = val

            else:
                # handle generic inner config dict
                for inner_name, inner_conf in outer_conf.items():
                    if inner_name not in CONFIG_DEFAULTS[outer_name].keys():
                        logging.warning(f'Skipped unknown config item "{key}" in "{inner_name}"')
                        continue
                    parsed[outer_name][inner_name] = inner_conf

    return parsed


def dump_toml(conf) -> str:
    return toml.dumps(conf)


def dump_file(file_path: str, config: dict, file_mode: int = 0o600):

    def _opener(path, flags):
        return os.open(path, flags, file_mode)

    conf_dir = os.path.dirname(file_path)
    if not os.path.exists(conf_dir):
        os.makedirs(conf_dir)
    old_umask = os.umask(0)
    with open(file_path, 'w', opener=_opener) as f:
        f.write(dump_toml(conf=config))
    os.umask(old_umask)


def parse_file(config_file: str, base: dict = CONFIG_DEFAULTS) -> dict:
    """
    Parse the toml contents of `config_file`, validating keys against `CONFIG_DEFAULTS`.
    The parsed results are semantically merged into `base` before returning.
    `base` itself is NOT checked for invalid keys.
    """
    _conf_file = config_file if config_file is not None else CONFIG_DEFAULT_PATH
    logging.debug(f'Trying to load config file: {_conf_file}')
    loaded_conf = toml.load(_conf_file)
    return merge_configs(conf_new=loaded_conf, conf_base=base)


class ConfigLoadException(Exception):
    inner = None

    def __init__(self, extra_msg='', inner_exception: Exception = None):
        msg = ['Config load failed!']
        if extra_msg:
            msg[0].append(':')
            msg.append(extra_msg)
        if inner_exception:
            self.inner = inner_exception
            msg.append(str(inner_exception))
        super().__init__(self, ' '.join(msg))


class ConfigStateHolder:

    class ConfigLoadState:
        load_finished = False
        exception = None

    file_state = ConfigLoadState()

    defaults = CONFIG_DEFAULTS
    # config options that are persisted to file
    file: dict = {}
    # runtime config not persisted anywhere
    runtime: dict = CONFIG_RUNTIME_DEFAULTS
    _profile_cache: dict[str, Profile] = None

    def __init__(self, runtime_conf={}, file_conf_path: str = None, file_conf_base: dict = {}):
        """init a stateholder, optionally loading `file_conf_path`"""
        self.runtime.update(runtime_conf)
        self.runtime['arch'] = os.uname().machine
        self.file.update(file_conf_base)
        if file_conf_path:
            self.try_load_file(file_conf_path)

    def try_load_file(self, config_file=None, base=CONFIG_DEFAULTS):
        _conf_file = config_file if config_file is not None else CONFIG_DEFAULT_PATH
        self.runtime['config_file'] = _conf_file
        self._profile_cache = None
        try:
            self.file = parse_file(config_file=_conf_file, base=base)
        except Exception as ex:
            self.file_state.exception = ex
        self.file_state.load_finished = True

    def is_loaded(self) -> bool:
        return self.file_state.load_finished and self.file_state.exception is None

    def enforce_config_loaded(self):
        if not self.file_state.load_finished:
            raise ConfigLoadException(Exception("Config file wasn't even parsed yet. This is probably a bug in kupferbootstrap :O"))
        ex = self.file_state.exception
        if ex:
            msg = ''
            if type(ex) == FileNotFoundError:
                msg = "File doesn't exist. Try running `kupferbootstrap config init` first?"
            raise ConfigLoadException(extra_msg=msg, inner_exception=ex)

    def get_profile(self, name: str = None) -> Profile:
        if not name:
            name = self.file['profiles']['current']
        self._profile_cache = resolve_profile(name=name, sparse_profiles=self.file['profiles'], resolved=self._profile_cache)
        return self._profile_cache[name]

    def get_path(self, path_name: str) -> str:
        paths = self.file['paths']
        return resolve_path_template(paths[path_name], paths)

    def dump(self) -> str:
        dump_toml(self.file)

    def write(self, path=None):
        if path is None:
            path = self.runtime['config_file']
        os.makedirs(os.path.dirname(path), exist_ok=True)
        dump_file(path, self.file)
        logging.info(f'Created config file at {path}')


config = ConfigStateHolder(file_conf_base=CONFIG_DEFAULTS)

config_option = click.option(
    '-C',
    '--config',
    'config_file',
    help='Override path to config file',
)


@click.group(name='config')
def cmd_config():
    pass


@cmd_config.command(name='init')
def cmd_init():
    """Initialize the config file"""
    # TODO
    config.write()


# temporary demo
if __name__ == '__main__':
    print('vanilla:')
    print(toml.dumps(config.file))
    print('\n\n-----------------------------\n\n')

    try:
        config.try_load_file()
        config.enforce_config_loaded()
        conf = config.file
    except ConfigLoadException as ex:
        logging.fatal(str(ex))
        conf = deepcopy(CONFIG_DEFAULTS)
    conf['profiles']['pinephone'] = {
        'hostname': 'slowphone',
        'parent': '',
        'pkgs_include': ['zsh', 'tmux', 'mpv', 'firefox'],
        'pkgs_exclude': ['pixman-git'],
    }
    conf['profiles']['yeetphone'] = {
        'parent': 'pinephone',
        'hostname': 'yeetphone',
        'pkgs_include': ['pixman-git'],
        'pkgs_exclude': ['tmux'],
    }
    print(toml.dumps(conf))
