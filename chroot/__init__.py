import click
import logging
import os

from config import config
from wrapper import enforce_wrap

from .abstract import Chroot
from .base import get_base_chroot
from .build import get_build_chroot, BuildChroot
from .helpers import get_chroot_path

# export Chroot class
Chroot = Chroot


@click.command('chroot')
@click.argument('type', required=False, default='build')
@click.argument('arch', required=False, default=None)
def cmd_chroot(type: str = 'build', arch: str = None, enable_crossdirect=True):
    """Open a shell in a chroot"""
    chroot_path = ''
    if type not in ['base', 'build', 'rootfs']:
        raise Exception('Unknown chroot type: ' + type)

    enforce_wrap()
    chroot: Chroot
    if type == 'rootfs':
        if arch:
            name = 'rootfs_' + arch
        else:
            raise Exception('"rootfs" without args not yet implemented, sorry!')
            # TODO: name = config.get_profile()[...]
        chroot_path = get_chroot_path(name)
        if not os.path.exists(chroot_path):
            raise Exception(f"rootfs {name} doesn't exist")
    else:
        if not arch:
            # TODO: arch = config.get_profile()[...]
            arch = 'aarch64'
        if type == 'base':
            chroot = get_base_chroot(arch)
            if not os.path.exists(chroot.get_path('/bin')):
                chroot.initialize()
            chroot.initialized = True
        elif type == 'build':
            build_chroot: BuildChroot = get_build_chroot(arch, activate=True)
            chroot = build_chroot  # type safety
            if not os.path.exists(build_chroot.get_path('/bin')):
                build_chroot.initialize()
            build_chroot.initialized = True
            build_chroot.mount_pkgbuilds()
            if config.file['build']['crossdirect'] and enable_crossdirect:
                build_chroot.mount_crossdirect()
        else:
            raise Exception('Really weird bug')

    chroot.activate()
    logging.debug(f'Starting shell in {chroot.name}:')
    chroot.run_cmd('bash', attach_tty=True)
