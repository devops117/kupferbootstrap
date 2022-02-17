import os

from config import config
from constants import Arch

BIND_BUILD_DIRS = 'BINDBUILDDIRS'
BASE_CHROOT_PREFIX = 'base_'
BUILD_CHROOT_PREFIX = 'build_'

# inspired by arch-chroot
# order of these matters!
BASIC_MOUNTS = {
    '/proc': {
        'src': 'proc',
        'type': 'proc',
        'options': ['nosuid,noexec,nodev']
    },
    '/sys': {
        'src': 'sys',
        'type': 'sysfs',
        'options': ['nosuid,noexec,nodev,ro'],
    },
    '/dev': {
        'src': 'udev',
        'type': 'devtmpfs',
        'options': ['mode=0755,nosuid'],
    },
    '/dev/pts': {
        'src': 'devpts',
        'type': 'devpts',
        'options': ['mode=0620,gid=5,nosuid,noexec'],
    },
    '/dev/shm': {
        'src': 'shm',
        'type': 'tmpfs',
        'options': ['mode=1777,nosuid,nodev'],
    },
    '/run': {
        'src': '/run',
        'type': 'tmpfs',
        'options': ['bind'],
    },
    '/etc/resolv.conf': {
        'src': os.path.realpath('/etc/resolv.conf'),
        'type': None,
        'options': ['bind'],
    },
}


def make_abs_path(path: str) -> str:
    """Simply ensures the path string starts with a '/'. Does no disk modifications!"""
    return '/' + path.lstrip('/')


def get_chroot_path(chroot_name, override_basepath: str = None) -> str:
    base_path = config.get_path('chroots') if not override_basepath else override_basepath
    return os.path.join(base_path, chroot_name)


def base_chroot_name(arch: Arch):
    return BASE_CHROOT_PREFIX + arch


def build_chroot_name(arch: Arch):
    return BUILD_CHROOT_PREFIX + arch
