import appdirs
import os
import toml

CONFIG_DEFAULT_PATH = os.path.join(appdirs.user_config_dir('kupfer'), 'kupferbootstrap.toml')

PROFILE_DEFAULTS = {
    'device': '',
    'flavour': '',
    'pkgs_include': [],
    'pkgs_exclude': [],
    'hostname': 'kupfer',
    'username': 'kupfer'
}

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
        'default': PROFILE_DEFAULTS.copy()
    }
}


def load_config(config_file=None):
    _conf_file = config_file if config_file != None else DEFAULT_CONFIG_PATH
    loaded_conf = toml.load(_conf_file)
    # TODO: validate keys in loaded_conf, selectively merge known ones with CONFIG_DEFAULTS.
    # Recurse into dict vales for one level except for profiles.* (do check for profiles.default tho!)
    pass


# temporary demo
if __name__ == '__main__':
    conf = CONFIG_DEFAULTS.copy()
    conf['profiles']['pinephone'] = {'hostname': 'slowphone', 'pkgs_include': ['zsh','tmux','mpv','firefox']}
    print(toml.dumps(conf))
