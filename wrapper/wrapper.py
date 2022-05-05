import atexit
import os
import uuid
import pathlib

from typing import Protocol

from config import config, dump_file as dump_config_file
from constants import CHROOT_PATHS


class Wrapper(Protocol):
    """Wrappers wrap kupferbootstrap in some form of isolation from the host OS, i.e. docker or chroots"""

    def wrap(self):
        """Instructs the wrapper to reexecute kupferbootstrap in a wrapped environment"""

    def stop(self):
        """Instructs the wrapper to stop the wrapped instance and clean up"""

    def is_wrapped(self) -> bool:
        """
        Queries the wrapper whether it believes we're executing wrapped by it currently.
        Checks `env[KUPFERBOOTSTRAP_WRAPPED] == self.type.capitalize()` by default.
        """


class BaseWrapper(Wrapper):
    id: str
    identifier: str
    type: str
    wrapped_config_path: str

    def __init__(self, random_id: str = None, name: str = None):
        self.uuid = str(random_id or uuid.uuid4())
        self.identifier = name or f'kupferbootstrap-{self.uuid}'

    def filter_args_wrapper(self, args):
        """filter out -c/--config since it doesn't apply in wrapper"""
        results = []
        done = False
        for i, arg in enumerate(args):
            if done:
                break
            if arg[0] != '-':
                results += args[i:]
                done = True
                break
            for argname in ['--config', '-C']:
                if arg.startswith(argname):
                    done = True
                    if arg.strip() != argname:  # arg is longer, assume --arg=value
                        offset = 1
                    else:
                        offset = 2
                    results += args[i + offset:]
                    break
            if not done:
                results.append(arg)
        return results

    def generate_wrapper_config(
        self,
        target_path: str = '/tmp/kupferbootstrap',
        paths: dict[str, str] = CHROOT_PATHS,
        config_overrides: dict[str, dict] = {},
    ) -> str:
        wrapped_config = f'{target_path.rstrip("/")}/{self.identifier}_wrapped.toml'

        # FIXME: these at_exit hooks should go and be called from somewhere better suited
        def at_exit():
            self.stop()
            os.remove(wrapped_config)

        atexit.register(at_exit)

        dump_config_file(
            file_path=wrapped_config,
            config=(config.file | {
                'paths': paths,
                'wrapper': {
                    'type': 'none'
                }
            } | config_overrides),
        )
        self.wrapped_config_path = wrapped_config
        return wrapped_config

    def wrap(self):
        raise NotImplementedError()

    def stop(self):
        raise NotImplementedError()

    def is_wrapped(self):
        return os.getenv('KUPFERBOOTSTRAP_WRAPPED') == self.type.upper()

    def get_bind_mounts_default(self, wrapped_config_path: str = None, ssh_dir: str = None, target_home: str = '/root'):
        wrapped_config_path = wrapped_config_path or self.wrapped_config_path
        ssh_dir = ssh_dir or os.path.join(pathlib.Path.home(), '.ssh')
        assert (wrapped_config_path)
        mounts = {
            '/dev': '/dev',
            wrapped_config_path: f'{target_home}/.config/kupfer/kupferbootstrap.toml',
        }
        if ssh_dir:
            mounts |= {
                ssh_dir: f'{target_home}/.ssh',
            }
        return mounts
