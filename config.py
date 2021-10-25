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

PROFILE_EMPTY: Profile = {key: None for key in PROFILE_DEFAULTS.keys()}

CONFIG_DEFAULTS = {
    'build': {
        'ccache': True,
        'clean_mode': True,
        'crosscompile': True,
        'crossdirect': True,
        'threads': 0,
    },
    'pkgbuilds': {
        'git_repo': 'https://gitlab.com/kupfer/packages/pkgbuilds.git',
        'git_branch': 'dev',
    },
    'paths': {
        'cache_dir': CACHE_DIR,
        'chroots': os.path.join('%cache_dir%', 'chroots'),
        'pacman': os.path.join('%cache_dir%', 'pacman'),
        'packages': os.path.join('%cache_dir%', 'packages'),
        'pkgbuilds': os.path.join('%cache_dir%', 'pkgbuilds'),
        'jumpdrive': os.path.join('%cache_dir%', 'jumpdrive'),
        'images': os.path.join('%cache_dir%', 'images'),
    },
    'profiles': {
        'current': 'default',
        'default': deepcopy(PROFILE_DEFAULTS),
    },
}
CONFIG_SECTIONS = list(CONFIG_DEFAULTS.keys())

CONFIG_RUNTIME_DEFAULTS = {
    'verbose': False,
    'config_file': None,
    'arch': None,
    'no_wrap': False,
    'script_source_dir': os.path.dirname(os.path.realpath(__file__)),
    'error_shell': False,
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


def sanitize_config(conf: dict[str, dict], warn_missing_defaultprofile=True) -> dict[str, dict]:
    """checks the input config dict for unknown keys and returns only the known parts"""
    return merge_configs(conf_new=conf, conf_base={}, warn_missing_defaultprofile=warn_missing_defaultprofile)


def merge_configs(conf_new: dict[str, dict], conf_base={}, warn_missing_defaultprofile=True) -> dict[str, dict]:
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
        logging.debug(f'Parsing config section "{outer_name}"')
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
                        logging.warning(f'Skipped unknown config item "{inner_name}" in "{outer_name}"')
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
            if type(ex) == FileNotFoundError:
                ex = Exception("File doesn't exist. Try running `kupferbootstrap config init` first?")
            raise ex

    def get_profile(self, name: str = None) -> Profile:
        if not name:
            name = self.file['profiles']['current']
        self._profile_cache = resolve_profile(name=name, sparse_profiles=self.file['profiles'], resolved=self._profile_cache)
        return self._profile_cache[name]

    def get_path(self, path_name: str) -> str:
        paths = self.file['paths']
        return resolve_path_template(paths[path_name], paths)

    def get_package_dir(self, arch: str):
        return os.path.join(self.get_path('packages'), arch)

    def dump(self) -> str:
        """dump toml representation of `self.file`"""
        dump_toml(self.file)

    def write(self, path=None):
        """write toml representation of `self.file` to `path`"""
        if path is None:
            path = self.runtime['config_file']
        os.makedirs(os.path.dirname(path), exist_ok=True)
        dump_file(path, self.file)
        logging.info(f'Created config file at {path}')

    def invalidate_profile_cache(self):
        """Clear the profile cache (usually after modification)"""
        self._profile_cache = None

    def update(self, config_fragment: dict[str, dict], warn_missing_defaultprofile: bool = True) -> bool:
        """Update `self.file` with `config_fragment`. Returns `True` if the config was changed"""
        merged = merge_configs(config_fragment, conf_base=self.file, warn_missing_defaultprofile=warn_missing_defaultprofile)
        changed = self.file != merged
        self.file = merged
        if changed and 'profiles' in config_fragment and self.file['profiles'] != config_fragment['profiles']:
            self.invalidate_profile_cache()
        return changed

    def update_profile(self, name: str, profile: dict, merge: bool = False, create: bool = True, prune: bool = True):
        new = {}
        if name not in self.file['profiles']:
            if not create:
                raise Exception(f'Unknown profile: {name}')
        else:
            if merge:
                new = deepcopy(self.file['profiles'][name])

        new |= profile

        if prune:
            new = {key: val for key, val in new.items() if val is not None}
        self.file['profiles'][name] = new
        self.invalidate_profile_cache()


def list_to_comma_str(str_list: list[str], default='') -> str:
    if str_list is None:
        return default
    return ','.join(str_list)


def comma_str_to_list(s: str, default=None) -> list[str]:
    if not s:
        return default
    return [a for a in s.split(',') if a]


def prompt_config(
    text: str,
    default: any,
    field_type: type = str,
    bold: bool = True,
    echo_changes: bool = True,
) -> (any, bool):
    """
    prompts for a new value for a config key. returns the result and a boolean that indicates
    whether the result is different, considering empty strings and None equal to each other.
    """

    def true_or_zero(to_check) -> bool:
        """returns true if the value is truthy or int(0)"""
        zero = 0  # compiler complains about 'is with literal' otherwise
        return to_check or to_check is zero  # can't do == due to boolean<->int casting

    if type(None) == field_type:
        field_type = str

    if field_type == dict:
        raise Exception('Dictionaries not supported by config_prompt, this is likely a bug in kupferbootstrap')
    elif field_type == list:
        default = list_to_comma_str(default)
        value_conv = comma_str_to_list
    else:
        value_conv = None
        default = '' if default is None else default

    if bold:
        text = click.style(text, bold=True)

    result = click.prompt(text, type=field_type, default=default, value_proc=value_conv, show_default=True)
    changed = (result != default) and (true_or_zero(default) or true_or_zero(result))
    if changed and echo_changes:
        print(f'value changed: "{text}" = "{result}"')

    return result, changed


def prompt_profile(name: str, create: bool = True, defaults: Profile = {}) -> (Profile, bool):
    """Prompts the user for every field in `defaults`. Set values to None for an empty profile."""

    profile = PROFILE_EMPTY | defaults
    # don't use get_profile() here because we need the sparse profile
    if name in config.file['profiles']:
        profile |= config.file['profiles'][name]
    elif create:
        logging.info(f"Profile {name} doesn't exist yet, creating new profile.")
    else:
        raise Exception(f'Unknown profile "{name}"')
    logging.info(f'Configuring profile "{name}"')
    changed = False
    for key, current in profile.items():
        current = profile[key]
        text = f'{name}.{key}'
        result, _changed = prompt_config(text=text, default=current, field_type=type(PROFILE_DEFAULTS[key]))
        if _changed:
            profile[key] = result
            changed = True
    return profile, changed


def config_dot_name_get(name: str, config: dict[str, any], prefix: str = ''):
    if not isinstance(config, dict):
        raise Exception(f"Couldn't resolve config name: passed config is not a dict: {repr(config)}")
    split_name = name.split('.')
    name = split_name[0]
    if name not in config:
        raise Exception(f"Couldn't resolve config name: key {prefix + name} not found")
    value = config[name]
    if len(split_name) == 1:
        return value
    else:
        rest_name = '.'.join(split_name[1:])
        return config_dot_name_get(name=rest_name, config=value, prefix=prefix + name + '.')


def config_dot_name_set(name: str, value: any, config: dict[str, any]):
    split_name = name.split('.')
    if len(split_name) > 1:
        config = config_dot_name_get('.'.join(split_name[:-1]), config)
    config[split_name[-1]] = value


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


noninteractive_flag = click.option('-N', '--non-interactive', is_flag=True)
noop_flag = click.option('--noop', '-n', help="Don't write changes to file", is_flag=True)


@cmd_config.command(name='init')
@noninteractive_flag
@noop_flag
@click.option(
    '--sections',
    '-s',
    multiple=True,
    type=click.Choice(CONFIG_SECTIONS),
    default=CONFIG_SECTIONS,
    show_choices=True,
)
def cmd_config_init(sections: list[str] = CONFIG_SECTIONS, non_interactive: bool = False, noop: bool = False):
    """Initialize the config file"""
    if not non_interactive:
        results = {}
        for section in sections:
            if section not in CONFIG_SECTIONS:
                raise Exception(f'Unknown section: {section}')
            if section == 'profiles':
                continue

            results[section] = {}
            for key, current in config.file[section].items():
                text = f'{section}.{key}'
                result, changed = prompt_config(text=text, default=current, field_type=type(CONFIG_DEFAULTS[section][key]))
                if changed:
                    results[section][key] = result

        config.update(results)
        if 'profiles' in sections:
            current_profile = 'default' if 'current' not in config.file['profiles'] else config.file['profiles']['current']
            new_current, _ = prompt_config('profile.current', default=current_profile, field_type=str)
            profile, changed = prompt_profile(new_current, create=True)
            config.update_profile(new_current, profile)
        if not noop:
            if not click.confirm(f'Do you want to save your changes to {config.runtime["config_file"]}?'):
                return

    if not noop:
        config.write()
    else:
        logging.info(f'--noop passed, not writing to {config.runtime["config_file"]}!')


@cmd_config.command(name='set')
@noninteractive_flag
@noop_flag
@click.argument('key_vals', nargs=-1)
def cmd_config_set(key_vals: list[str], non_interactive: bool = False, noop: bool = False):
    """
    Set config entries. Pass entries as `key=value` pairs, with keys as dot-separated identifiers,
    like `build.clean_mode=false` or alternatively just keys to get prompted if run interactively.
    """
    config.enforce_config_loaded()
    config_copy = deepcopy(config.file)
    for pair in key_vals:
        split_pair = pair.split('=')
        if len(split_pair) == 2:
            key, value = split_pair
            value_type = type(config_dot_name_get(key, CONFIG_DEFAULTS))
            if value_type != list:
                value = click.types.convert_type(value_type)(value)
            else:
                value = comma_str_to_list(value, default=[])
        elif len(split_pair) == 1 and not non_interactive:
            key = split_pair[0]
            value_type = type(config_dot_name_get(key, CONFIG_DEFAULTS))
            current = config_dot_name_get(key, config.file)
            value, _ = prompt_config(text=key, default=current, field_type=value_type, echo_changes=False)
        else:
            raise Exception(f'Invalid key=value pair "{pair}"')
        print('%s = %s' % (key, value))
        config_dot_name_set(key, value, config_copy)
        if merge_configs(config_copy, warn_missing_defaultprofile=False) != config_copy:
            raise Exception('Config "{key}" = "{value}" failed to evaluate')
    if not noop:
        if not non_interactive and not click.confirm(f'Do you want to save your changes to {config.runtime["config_file"]}?'):
            return
        config.update(config_copy)
        config.write()


@cmd_config.command(name='get')
@click.argument('keys', nargs=-1)
def cmd_config_get(keys: list[str]):
    """Get config entries.
    Get entries for keys passed as dot-separated identifiers, like `build.clean_mode`"""
    if len(keys) == 1:
        print(config_dot_name_get(keys[0], config.file))
        return
    for key in keys:
        print('%s = %s' % (key, config_dot_name_get(key, config.file)))


@cmd_config.group(name='profile')
def cmd_profile():
    """Manage config profiles"""


@cmd_profile.command(name='init')
@noninteractive_flag
@noop_flag
@click.argument('name', required=True)
def cmd_profile_init(name: str, non_interactive: bool = False, noop: bool = False):
    """Create or edit a profile"""
    profile = deepcopy(PROFILE_EMPTY)
    if name in config.file['profiles']:
        profile |= config.file['profiles'][name]

    if not non_interactive:
        profile = prompt_profile(name, create=True)

    config.update_profile(name, profile)
    if not noop:
        if not click.confirm(f'Do you want to save your changes to {config.runtime["config_file"]}?'):
            return
        config.write()
    else:
        logging.info(f'--noop passed, not writing to {config.runtime["config_file"]}!')


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
