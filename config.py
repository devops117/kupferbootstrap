import appdirs
import os
import toml
import logging
from copy import deepcopy
import click

CONFIG_DEFAULT_PATH = os.path.join(appdirs.user_config_dir('kupfer'), 'kupferbootstrap.toml')

PROFILE_DEFAULTS = {'device': '', 'flavour': '', 'pkgs_include': [], 'pkgs_exclude': [], 'hostname': 'kupfer', 'username': 'kupfer'}

CONFIG_DEFAULTS = {
    'build': {
        'crosscompile': True,
        'threads': 0,
    },
    'paths': {
        'chroots': os.path.join(appdirs.user_cache_dir('kupfer'), 'chroots'),
        'pacman_cache': os.path.join(appdirs.user_cache_dir('kupfer'), 'pacman'),
        'jumpdrive_cache': os.path.join(appdirs.user_cache_dir('kupfer'), 'jumpdrive')
    },
    'profiles': {
        'default': deepcopy(PROFILE_DEFAULTS)
    }
}

def parse_file(config_file: str, base: dict=CONFIG_DEFAULTS) -> dict:
    """
    Parse the toml contents of `config_file`, validating keys against `CONFIG_DEFAULTS`.
    The parsed results are semantically merged into `base` before returning.
    `base` itself is NOT checked for invalid keys.
    """
    _conf_file = config_file if config_file != None else CONFIG_DEFAULT_PATH
    loaded_conf = toml.load(_conf_file)
    parsed = deepcopy(base)

    for outer_name, outer_conf in loaded_conf.items():
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
                if 'default' not in outer_conf.keys():
                    logging.warning('Default profile is not defined in config file')

                for profile_name, profile_conf in outer_conf.items():
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

    # config options that are persisted to file
    file: dict = {}
    # runtime config not persisted anywhere
    runtime: dict = {'verbose': False}

    def __init__(self, runtime_conf = {}, file_conf_path: str = None, file_conf_base: dict = {}):
        """init a stateholder, optionally loading `file_conf_path`"""
        self.runtime.update(runtime_conf)
        self.file.update(file_conf_base)
        if file_conf_path:
            self.try_load_file(file_conf_path)

    def try_load_file(self, config_file=None, base=CONFIG_DEFAULTS):
        _conf_file = config_file if config_file != None else CONFIG_DEFAULT_PATH
        try:
            self.file = parse_file(config_file=_conf_file, base=base)
        except Exception as ex:
            self.file_state.exception = ex
        self.file_state.load_finished = True

    def is_loaded(self):
        return self.file_state.load_finished and self.file_state.exception == None

    def enforce_config_loaded(self):
        if not self.file_state.load_finished:
            raise ConfigLoadException(Exception("Config file wasn't even parsed yet. This is probably a bug in kupferbootstrap :O"))
        ex = self.file_state.exception
        if ex:
            msg = ''
            if type(ex) == FileNotFoundError:
                msg = "File doesn't exist. Try running `kupferbootstrap config init` first?"
            raise ConfigLoadException(extra_msg=msg, inner_exception=ex)

config = ConfigStateHolder(file_conf_base=CONFIG_DEFAULTS)


config_option = click.option(
    '-C',
    '--config',
    'config_file',
    help='Override path to config file',
)

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
    conf['profiles']['pinephone'] = {'hostname': 'slowphone', 'pkgs_include': ['zsh', 'tmux', 'mpv', 'firefox']}
    print(toml.dumps(conf))
