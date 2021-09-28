import atexit
import os
import subprocess
import click
from logger import logging, setup_logging, verbose_option
from chroot import create_chroot, create_chroot_user
from constants import DEVICES, FLAVOURS


def get_device_and_flavour() -> tuple[str, str]:
    if not os.path.exists('.device'):
        logging.fatal(f'Please set the device using \'kupferbootstrap image device ...\'')
        exit(1)
    if not os.path.exists('.flavour'):
        logging.fatal(f'Please set the flavour using \'kupferbootstrap image flavour ...\'')
        exit(1)

    with open('.device', 'r') as file:
        device = file.read()
    with open('.flavour', 'r') as file:
        flavour = file.read()

    return (device, flavour)


def get_image_name(device, flavour):
    return f'{device}-{flavour}-rootfs.img'


def mount_rootfs_image(path):
    rootfs_mount = '/mnt/kupfer/rootfs'
    if not os.path.exists(rootfs_mount):
        os.makedirs(rootfs_mount)

    def umount():
        subprocess.run(
            [
                'umount',
                '-lc',
                rootfs_mount,
            ],
            stderr=subprocess.DEVNULL,
        )

    atexit.register(umount)

    result = subprocess.run([
        'mount',
        '-o',
        'loop',
        path,
        rootfs_mount,
    ])
    if result.returncode != 0:
        logging.fatal(f'Failed to loop mount {path} to {rootfs_mount}')
        exit(1)

    return rootfs_mount


def dump_bootimg(image_name: str) -> str:
    path = '/tmp/boot.img'
    result = subprocess.run([
        'debugfs',
        image_name,
        '-R',
        f'dump /boot/boot.img {path}',
    ])
    if result.returncode != 0:
        logging.fatal(f'Faild to dump boot.img')
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
        logging.fatal(f'Faild to dump lk2nd.img')
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
        logging.fatal(f'Faild to dump qhypstub.bin')
        exit(1)
    return path


@click.group(name='image')
def cmd_image():
    pass


@click.command(name='device')
@verbose_option
@click.argument('device')
def cmd_device(verbose, device):
    setup_logging(verbose)

    for key in DEVICES.keys():
        if '-'.join(key.split('-')[1:]) == device:
            device = key
            break

    if device not in DEVICES:
        logging.fatal(f'Unknown device {device}. Pick one from:\n{", ".join(DEVICES.keys())}')
        exit(1)

    logging.info(f'Setting device to {device}')

    with open('.device', 'w') as file:
        file.write(device)


@click.command(name='flavour')
@verbose_option
@click.argument('flavour')
def cmd_flavour(verbose, flavour):
    setup_logging(verbose)

    if flavour not in FLAVOURS:
        logging.fatal(f'Unknown flavour {flavour}. Pick one from:\n{", ".join(FLAVOURS.keys())}')
        exit(1)

    logging.info(f'Setting flavour to {flavour}')

    with open('.flavour', 'w') as file:
        file.write(flavour)


@click.command(name='build')
@verbose_option
def cmd_build(verbose):
    setup_logging(verbose)

    device, flavour = get_device_and_flavour()
    image_name = get_image_name(device, flavour)

    if not os.path.exists(image_name):
        result = subprocess.run([
            'fallocate',
            '-l',
            '4G',
            image_name,
        ])
        if result.returncode != 0:
            logging.fatal(f'Failed to allocate {image_name}')
            exit(1)

        result = subprocess.run([
            'mkfs.ext4',
            '-L',
            'kupfer',
            image_name,
        ])
        if result.returncode != 0:
            logging.fatal(f'Failed to create ext4 filesystem on {image_name}')
            exit(1)

    rootfs_mount = mount_rootfs_image(image_name)

    extra_repos = {
        'main': {
            'Server': 'https://gitlab.com/kupfer/packages/prebuilts/-/raw/main/$repo',
        },
        'device': {
            'Server': 'https://gitlab.com/kupfer/packages/prebuilts/-/raw/main/$repo',
        },
    }

    if os.path.exists('/prebuilts'):
        extra_repos = {
            'main': {
                'Server': 'file:///prebuilts/$repo',
            },
            'device': {
                'Server': 'file:///prebuilts/$repo',
            },
            'linux': {
                'Server': 'file:///prebuilts/$repo',
            },
            'boot': {
                'Server': 'file:///prebuilts/$repo',
            },
            'firmware': {
                'Server': 'file:///prebuilts/$repo',
            },
        }

    create_chroot(
        rootfs_mount,
        packages=['base', 'base-kupfer'] + DEVICES[device] + FLAVOURS[flavour],
        pacman_conf='/app/local/etc/pacman.conf',
        extra_repos=extra_repos,
    )
    create_chroot_user(rootfs_mount)


"""
This doesn't work, because the mount isn't passed through to the real host
"""
"""
@click.command(name='inspect')
@verbose_option
def cmd_inspect(verbose):
    setup_logging(verbose)

    device, flavour = get_device_and_flavour()
    image_name = get_image_name(device, flavour)

    rootfs_mount = mount_rootfs_image(image_name)

    logging.info(f'Inspect the rootfs image at {rootfs_mount}')

    signal.pause()
"""

cmd_image.add_command(cmd_device)
cmd_image.add_command(cmd_flavour)
cmd_image.add_command(cmd_build)
# cmd_image.add_command(cmd_inspect)
