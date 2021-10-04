import atexit
import os
import subprocess
import click
from logger import logging
from chroot import create_chroot, create_chroot_user, get_chroot_path, run_chroot_cmd
from constants import DEVICES, FLAVOURS
from config import config
from distro import get_kupfer_https, get_kupfer_local
from wrapper import enforce_wrap
from signal import pause


def resize_fs(image_path: str, shrink: bool = False):
    result = subprocess.run([
        'e2fsck',
        '-fy',
        image_path,
    ])
    # https://man7.org/linux/man-pages/man8/e2fsck.8.html#EXIT_CODE
    if result.returncode > 2:
        print(result.returncode)
        msg = f'Failed to e2fsck {image_path}'
        if shrink:
            raise Exception(msg)
        else:
            logging.warning(msg)

    result = subprocess.run(['resize2fs'] + (['-MP'] if shrink else []) + [image_path])
    if result.returncode != 0:
        raise Exception(f'Failed to resize2fs {image_path}')


def get_device_and_flavour(profile: str = None) -> tuple[str, str]:
    #config.enforce_config_loaded()
    profile = config.get_profile(profile)
    if not profile['device']:
        raise Exception("Please set the device using 'kupferbootstrap config init ...'")

    if not profile['flavour']:
        raise Exception("Please set the flavour using 'kupferbootstrap config init ...'")

    return (profile['device'], profile['flavour'])


def get_image_name(device, flavour) -> str:
    return f'{device}-{flavour}-rootfs.img'


def mount_rootfs_image(image_path, mount_path):
    if not os.path.exists(mount_path):
        os.makedirs(mount_path)

    def umount():
        subprocess.run(
            [
                'umount',
                '-lc',
                mount_path,
            ],
            stderr=subprocess.DEVNULL,
        )

    atexit.register(umount)

    result = subprocess.run([
        'mount',
        '-o',
        'loop',
        image_path,
        mount_path,
    ])
    if result.returncode != 0:
        logging.fatal(f'Failed to loop mount {image_path} to {mount_path}')
        exit(1)


def dump_bootimg(image_name: str) -> str:
    path = '/tmp/boot.img'
    result = subprocess.run([
        'debugfs',
        image_name,
        '-R',
        f'dump /boot/boot.img {path}',
    ])
    if result.returncode != 0:
        logging.fatal('Failed to dump boot.img')
        exit(1)
    return path


def dump_lk2nd(image_name: str) -> str:
    """
    This doesn't append the image with the appended DTB which is needed for some devices, so it should get added in the future.
    """
    path = '/tmp/lk2nd.img'
    result = subprocess.run([
        'debugfs',
        image_name,
        '-R',
        f'dump /boot/lk2nd.img {path}',
    ])
    if result.returncode != 0:
        logging.fatal('Failed to dump lk2nd.img')
        exit(1)
    return path


def dump_qhypstub(image_name: str) -> str:
    path = '/tmp/qhypstub.bin'
    result = subprocess.run([
        'debugfs',
        image_name,
        '-R',
        f'dump /boot/qhypstub.bin {path}',
    ])
    if result.returncode != 0:
        logging.fatal('Failed to dump qhypstub.bin')
        exit(1)
    return path


@click.group(name='image')
def cmd_image():
    pass


@cmd_image.command(name='build')
def cmd_build():
    enforce_wrap()
    profile = config.get_profile()
    device, flavour = get_device_and_flavour()
    post_cmds = FLAVOURS[flavour].get('post_cmds', [])
    image_name = get_image_name(device, flavour)

    # TODO: PARSE DEVICE ARCH
    arch = 'aarch64'

    if not os.path.exists(image_name):
        result = subprocess.run([
            'fallocate',
            '-l',
            f"{FLAVOURS[flavour].get('size',4)}G",
            image_name,
        ])
        if result.returncode != 0:
            raise Exception(f'Failed to allocate {image_name}')

        result = subprocess.run([
            'mkfs.ext4',
            '-L',
            'kupfer',
            image_name,
        ])
        if result.returncode != 0:
            raise Exception(f'Failed to create ext4 filesystem on {image_name}')
    else:
        resize_fs(image_path=image_name)

    chroot_name = f'rootfs_{device}-{flavour}'
    rootfs_mount = get_chroot_path(chroot_name)
    mount_rootfs_image(image_name, rootfs_mount)

    packages_dir = config.get_packages(arch)
    if os.path.exists(os.path.join(packages_dir, 'main')):
        extra_repos = get_kupfer_local(arch).repos
    else:
        extra_repos = get_kupfer_https(arch).repos
    packages = ['base', 'base-kupfer'] + DEVICES[device] + FLAVOURS[flavour]['packages'] + profile['pkgs_include']
    create_chroot(
        chroot_name,
        packages=packages,
        pacman_conf=os.path.join(config.runtime['script_source_dir'], 'local/etc/pacman.conf'),
        extra_repos=extra_repos,
        bind_mounts={},
    )
    create_chroot_user(chroot_name, user=profile['username'], password=profile['password'])
    if post_cmds:
        result = run_chroot_cmd(' && '.join(post_cmds), chroot_name)
        if result.returncode != 0:
            raise Exception('Error running post_cmds')


@cmd_image.command(name='inspect')
def cmd_inspect():
    device, flavour = get_device_and_flavour()
    image_name = get_image_name(device, flavour)

    rootfs_mount = get_chroot_path(f'rootfs_{device}-{flavour}')
    mount_rootfs_image(image_name, rootfs_mount)

    logging.info(f'Inspect the rootfs image at {rootfs_mount}')

    pause()
