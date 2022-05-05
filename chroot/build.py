import logging
import os
import subprocess
from glob import glob
from typing import Optional

from config import config
from constants import Arch, GCC_HOSTSPECS, CROSSDIRECT_PKGS, CHROOT_PATHS
from distro.distro import get_kupfer_local

from .abstract import Chroot, get_chroot
from .helpers import build_chroot_name
from .base import get_base_chroot


class BuildChroot(Chroot):

    copy_base: bool = True

    def create_rootfs(self, reset: bool, pacman_conf_target: str, active_previously: bool):
        if reset or not os.path.exists(self.get_path('usr/bin')):
            base_chroot = get_base_chroot(self.arch)
            if base_chroot == self:
                raise Exception('base_chroot == self, bailing out. this is a bug')
            base_chroot.initialize()
            logging.info(f'Copying {base_chroot.name} chroot to {self.name}')
            cmd = ['rsync', '-a', '--delete', '-q', '-W', '-x']
            for mountpoint in CHROOT_PATHS:
                cmd += ['--exclude', mountpoint.rstrip('/')]
            result = subprocess.run(cmd + [f'{base_chroot.path}/', f'{self.path}/'])
            if result.returncode != 0:
                raise Exception(f'Failed to copy {base_chroot.name} to {self.name}')

        else:
            logging.debug(f'{self.name}: Reusing existing installation')

        if set(get_kupfer_local(self.arch).repos).intersection(set(self.extra_repos)):
            self.mount_packages()

        self.mount_pacman_cache()
        self.write_pacman_conf()
        self.initialized = True
        self.activate()
        self.try_install_packages(self.base_packages, refresh=True, allow_fail=False)
        self.deactivate_core()

        # patch makepkg
        with open(self.get_path('/usr/bin/makepkg'), 'r') as file:
            data = file.read()
        data = data.replace('EUID == 0', 'EUID == -1')
        with open(self.get_path('/usr/bin/makepkg'), 'w') as file:
            file.write(data)

        # configure makepkg
        self.write_makepkg_conf(self.arch, cross_chroot_relative=None, cross=False)

        if active_previously:
            self.activate()

    def mount_crossdirect(self, native_chroot: Optional[Chroot] = None, fail_if_mounted: bool = False):
        """
        mount `native_chroot` at `target_chroot`/native
        returns the absolute path that `native_chroot` has been mounted at.
        """
        target_arch = self.arch
        if not native_chroot:
            native_chroot = get_build_chroot(config.runtime['arch'])
        host_arch = native_chroot.arch
        hostspec = GCC_HOSTSPECS[host_arch][target_arch]
        cc = f'{hostspec}-cc'
        gcc = f'{hostspec}-gcc'

        native_mount = os.path.join(self.path, 'native')
        logging.debug(f'Activating crossdirect in {native_mount}')
        native_chroot.initialize()
        native_chroot.mount_pacman_cache()
        native_chroot.mount_packages()
        native_chroot.activate()
        results = dict(native_chroot.try_install_packages(
            CROSSDIRECT_PKGS + [gcc],
            refresh=True,
            allow_fail=False,
        ),)
        res_gcc = results[gcc]
        res_crossdirect = results['crossdirect']
        assert isinstance(res_gcc, subprocess.CompletedProcess)
        assert isinstance(res_crossdirect, subprocess.CompletedProcess)

        if res_gcc.returncode != 0:
            logging.debug('Failed to install cross-compiler package {gcc}')
        if res_crossdirect.returncode != 0:
            raise Exception('Failed to install crossdirect')

        cc_path = os.path.join(native_chroot.path, 'usr', 'bin', cc)
        target_lib_dir = os.path.join(self.path, 'lib64')
        # TODO: crosscompiler weirdness, find proper fix for /include instead of /usr/include
        target_include_dir = os.path.join(self.path, 'include')

        for target, source in {cc_path: gcc, target_lib_dir: 'lib', target_include_dir: 'usr/include'}.items():
            if not os.path.exists(target):
                logging.debug(f'Symlinking {source} at {target}')
                os.symlink(source, target)
        ld_so = os.path.basename(glob(f"{os.path.join(native_chroot.path, 'usr', 'lib', 'ld-linux-')}*")[0])
        ld_so_target = os.path.join(target_lib_dir, ld_so)
        if not os.path.islink(ld_so_target):
            os.symlink(os.path.join('/native', 'usr', 'lib', ld_so), ld_so_target)
        else:
            logging.debug(f'ld-linux.so symlink already exists, skipping for {self.name}')

        # TODO: find proper fix
        rustc = os.path.join(native_chroot.path, 'usr/lib/crossdirect', target_arch, 'rustc')
        if os.path.exists(rustc):
            logging.debug('Disabling crossdirect rustc')
            os.unlink(rustc)

        os.makedirs(native_mount, exist_ok=True)
        logging.debug(f'Mounting {native_chroot.name} to {native_mount}')
        self.mount(native_chroot.path, 'native', fail_if_mounted=fail_if_mounted)
        return native_mount

    def mount_crosscompile(self, foreign_chroot: Chroot, fail_if_mounted: bool = False):
        mount_dest = os.path.join(CHROOT_PATHS['chroots'].lstrip('/'), os.path.basename(foreign_chroot.path))
        return self.mount(
            absolute_source=foreign_chroot.path,
            relative_destination=mount_dest,
            fail_if_mounted=fail_if_mounted,
        )


def get_build_chroot(arch: Arch, add_kupfer_repos: bool = True, **kwargs) -> BuildChroot:
    name = build_chroot_name(arch)
    if 'extra_repos' in kwargs:
        raise Exception('extra_repos!')
    repos = get_kupfer_local(arch).repos if add_kupfer_repos else {}
    default = BuildChroot(name, arch, initialize=False, copy_base=True, extra_repos=repos)
    chroot = get_chroot(name, **kwargs, extra_repos=repos, default=default)
    assert isinstance(chroot, BuildChroot)
    return chroot
