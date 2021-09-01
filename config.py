import appdirs
import os
import toml
import logging
from copy import deepcopy

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


class ConfigParserException(Exception):
    pass


def load_config(config_file=None, merge_defaults=True):
    _conf_file = config_file if config_file != None else CONFIG_DEFAULT_PATH
    loaded_conf = toml.load(_conf_file)

    if merge_defaults:
        # Selectively merge known keys in loaded_conf with CONFIG_DEFAULTS
        parsed = deepcopy(CONFIG_DEFAULTS)
    else:
        parsed = {}

    for outer_name, outer_conf in loaded_conf.items():
        # only handle known config sections
        if outer_name not in CONFIG_DEFAULTS.keys():
            logging.warning('Removed unknown config section', outer_name)
            continue
        logging.debug(f'Working on outer section "{outer_name}"')
        # check if outer_conf is a dict
        if not isinstance(outer_conf, dict):
            parsed[outer_name] = outer_conf
        else:
            if not merge_defaults:
                # init section
                parsed[outer_name] = {}

            # profiles need special handling:
            # 1. profile names are unknown keys by definition, but we want 'default' to exist
            # 2. A profile's subkeys must be compared against PROFILE_DEFAULTS.keys()
            if outer_name == 'profiles':
                if 'default' not in outer_conf.keys():
                    logging.warning('Default profile is not in profiles')

                for profile_name, profile_conf in outer_conf.items():
                    #  init profile; don't accidentally overwrite the default profile when merging
                    if not (merge_defaults and profile_name == 'default'):
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


# temporary demo
if __name__ == '__main__':
    try:
        conf = load_config()
    except FileNotFoundError as ex:
        logging.warning(f'Error reading toml file "{ex.filename}": {ex.strerror}')
        conf = deepcopy(CONFIG_DEFAULTS)
    conf['profiles']['pinephone'] = {'hostname': 'slowphone', 'pkgs_include': ['zsh', 'tmux', 'mpv', 'firefox']}
    print(toml.dumps(conf))
