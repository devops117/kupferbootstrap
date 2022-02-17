import atexit
import os

from constants import Arch, BASE_PACKAGES
from utils import check_findmnt

from .base import BaseChroot
from .build import BuildChroot
from .abstract import get_chroot, Chroot


class DeviceChroot(BuildChroot):

    copy_base: bool = False

    def create_rootfs(self, reset, pacman_conf_target, active_previously):
        clss = BuildChroot if self.copy_base else BaseChroot

        clss.create_rootfs(self, reset, pacman_conf_target, active_previously)

    def mount_rootfs(self, source_path: str, fs_type: str = None, options: list[str] = [], allow_overlay: bool = False):
        if self.active:
            raise Exception(f'{self.name}: Chroot is marked as active, not mounting a rootfs over it.')
        if not os.path.exists(source_path):
            raise Exception('Source does not exist')
        if not allow_overlay:
            really_active = []
            for mnt in self.active_mounts:
                if check_findmnt(self.get_path(mnt)):
                    really_active.append(mnt)
            if really_active:
                raise Exception(f'{self.name}: Chroot has submounts active: {really_active}')
            if os.path.ismount(self.path):
                raise Exception(f'{self.name}: There is already something mounted at {self.path}, not mounting over it.')
            if os.path.exists(os.path.join(self.path, 'usr/bin')):
                raise Exception(f'{self.name}: {self.path}/usr/bin exists, not mounting over existing rootfs.')
        os.makedirs(self.path, exist_ok=True)
        atexit.register(self.deactivate)
        self.mount(source_path, '/', fs_type=fs_type, options=options)


def get_device_chroot(device: str, flavour: str, arch: Arch, packages: list[str] = BASE_PACKAGES, extra_repos={}, **kwargs) -> Chroot:
    name = f'rootfs_{device}-{flavour}'
    default = DeviceChroot(name, arch, initialize=False, copy_base=False, base_packages=packages, extra_repos=extra_repos)
    return get_chroot(name, **kwargs, default=default)
