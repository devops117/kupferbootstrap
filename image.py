import os
import subprocess
import click
from logger import logging
from chroot import Chroot, get_device_chroot
from constants import BASE_PACKAGES, DEVICES, FLAVOURS, Arch
from config import config
from distro import get_base_distro, get_kupfer_https, get_kupfer_local
from ssh import copy_ssh_keys
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

    result = subprocess.run(['resize2fs'] + (['-M'] if shrink else []) + [image_path])
    if result.returncode != 0:
        raise Exception(f'Failed to resize2fs {image_path}')


def get_device_and_flavour(profile: str = None) -> tuple[str, str]:
    config.enforce_config_loaded()
    profile = config.get_profile(profile)
    if not profile['device']:
        raise Exception("Please set the device using 'kupferbootstrap config init ...'")

    if not profile['flavour']:
        raise Exception("Please set the flavour using 'kupferbootstrap config init ...'")

    return (profile['device'], profile['flavour'])


def get_image_name(device_chroot: Chroot) -> str:
    return f'{device_chroot.name}.img'


def get_image_path(device_chroot: Chroot) -> str:
    return os.path.join(config.get_path('images'), get_image_name(device_chroot))


def dump_bootimg(image_path: str) -> str:
    path = '/tmp/boot.img'
    result = subprocess.run([
        'debugfs',
        image_path,
        '-R',
        f'dump /boot/boot.img {path}',
    ])
    if result.returncode != 0:
        logging.fatal('Failed to dump boot.img')
        exit(1)
    return path


def dump_lk2nd(image_path: str) -> str:
    """
    This doesn't append the image with the appended DTB which is needed for some devices, so it should get added in the future.
    """
    path = '/tmp/lk2nd.img'
    result = subprocess.run([
        'debugfs',
        image_path,
        '-R',
        f'dump /boot/lk2nd.img {path}',
    ])
    if result.returncode != 0:
        logging.fatal('Failed to dump lk2nd.img')
        exit(1)
    return path


def dump_qhypstub(image_path: str) -> str:
    path = '/tmp/qhypstub.bin'
    result = subprocess.run([
        'debugfs',
        image_path,
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

    # TODO: PARSE DEVICE ARCH
    arch: Arch = 'aarch64'

    packages_dir = config.get_package_dir(arch)
    if os.path.exists(os.path.join(packages_dir, 'main')):
        extra_repos = get_kupfer_local(arch).repos
    else:
        extra_repos = get_kupfer_https(arch).repos
    packages = BASE_PACKAGES + DEVICES[device] + FLAVOURS[flavour]['packages'] + profile['pkgs_include']

    chroot = get_device_chroot(device=device, flavour=flavour, arch=arch, packages=packages, extra_repos=extra_repos)
    image_path = get_image_path(chroot)

    if not os.path.exists(image_path):
        result = subprocess.run([
            'fallocate',
            '-l',
            f"{FLAVOURS[flavour].get('size',2)}G",
            image_path,
        ])
        if result.returncode != 0:
            raise Exception(f'Failed to allocate {image_path}')

        result = subprocess.run([
            'mkfs.ext4',
            '-L',
            'kupfer',
            image_path,
        ])
        if result.returncode != 0:
            raise Exception(f'Failed to create ext4 filesystem on {image_path}')
    else:
        resize_fs(image_path=image_path)

    chroot.mount_rootfs(image_path)
    chroot.initialize()
    chroot.activate()
    chroot.create_user(
        user=profile['username'],
        password=profile['password'],
    )

    copy_ssh_keys(
        chroot.path,
        user=profile['username'],
    )
    with open(os.path.join(chroot.path, 'etc', 'pacman.conf'), 'w') as file:
        file.write(get_base_distro(arch).get_pacman_conf(check_space=True, extra_repos=get_kupfer_https(arch).repos))
    if post_cmds:
        result = chroot.run_cmd(' && '.join(post_cmds))
        if result.returncode != 0:
            raise Exception('Error running post_cmds')


@cmd_image.command(name='inspect')
@click.option('--shell', '-s', is_flag=True)
def cmd_inspect(shell: bool = False):
    device, flavour = get_device_and_flavour()
    # TODO: get arch from profile
    arch = 'aarch64'
    chroot = get_device_chroot(device, flavour, arch)
    image_path = get_image_path(chroot)

    chroot.mount_rootfs(image_path)

    logging.info(f'Inspect the rootfs image at {chroot.path}')

    if shell:
        chroot.initialized = True
        chroot.activate()
        chroot.run_cmd('/bin/bash')
    else:
        pause()
