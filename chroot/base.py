import logging
import os
import subprocess

from glob import glob
from shutil import rmtree

from constants import Arch

from .abstract import Chroot, get_chroot
from .helpers import base_chroot_name


class BaseChroot(Chroot):

    copy_base: bool = False

    def create_rootfs(self, reset, pacman_conf_target, active_previously):
        if reset:
            logging.info(f'Resetting {self.name}')
            for dir in glob(os.path.join(self.path, '*')):
                rmtree(dir)

        self.write_pacman_conf(check_space=True)
        self.mount_pacman_cache()

        logging.info(f'Pacstrapping chroot {self.name}: {", ".join(self.base_packages)}')

        result = subprocess.run([
            'pacstrap',
            '-C',
            pacman_conf_target,
            '-c',
            '-G',
            self.path,
        ] + self.base_packages + [
            '--needed',
            '--overwrite=*',
            '-yyuu',
        ])
        if result.returncode != 0:
            raise Exception(f'Failed to initialize chroot "{self.name}"')
        self.initialized = True


def get_base_chroot(arch: Arch, **kwargs) -> BaseChroot:
    name = base_chroot_name(arch)
    default = BaseChroot(name, arch, initialize=False, copy_base=False)
    if kwargs.pop('initialize', False):
        logging.debug('get_base_chroot: Had to remove "initialize" from args. This indicates a bug.')
    chroot = get_chroot(name, **kwargs, initialize=False, default=default)
    assert (isinstance(chroot, BaseChroot))
    return chroot
