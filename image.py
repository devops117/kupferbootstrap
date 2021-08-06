import atexit
from logging import root
import os
import shutil
import signal
import subprocess
import time
import click
from logger import *
from chroot import create_chroot

devices = {
    'oneplus-enchilada': ['sdm845-oneplus-enchilada'],
    'xiaomi-beryllium-ebbg': ['sdm845-xiaomi-beryllium-ebbg'],
    'xiaomi-beryllium-tianma': ['sdm845-xiaomi-beryllium-tianma'],
}

flavours = {
    'barebone': [],
    'phosh': [],
    'plasma-mobile': [],
}


def get_device_and_flavour() -> tuple[str, str]:
    if not os.path.exists('.device'):
        logging.fatal(
            f'Please set the device using \'kupferbootstrap image device ...\'')
        exit(1)
    if not os.path.exists('.flavour'):
        logging.fatal(
            f'Please set the flavour using \'kupferbootstrap image flavour ...\'')
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
        subprocess.run(['umount', '-lc', rootfs_mount],
                       stderr=subprocess.DEVNULL)
    atexit.register(umount)

    result = subprocess.run(['mount',
                             '-o', 'loop',
                             path,
                             rootfs_mount])
    if result.returncode != 0:
        logging.fatal(f'Failed to loop mount {path} to {rootfs_mount}')
        exit(1)

    return rootfs_mount


@click.group(name='image')
def cmd_image():
    pass


@click.command(name='device')
@verbose_option
@click.argument('device')
def cmd_device(verbose, device):
    setup_logging(verbose)

    for key in devices.keys():
        if '-'.join(key.split('-')[1:]) == device:
            device = key
            break

    if device not in devices:
        logging.fatal(
            f'Unknown device {device}. Pick one from:\n{", ".join(devices.keys())}')
        exit(1)

    logging.info(f'Setting device to {device}')

    with open('.device', 'w') as file:
        file.write(device)


@click.command(name='flavour')
@verbose_option
@click.argument('flavour')
def cmd_flavour(verbose, flavour):
    setup_logging(verbose)

    if flavour not in flavours:
        logging.fatal(
            f'Unknown flavour {flavour}. Pick one from:\n{", ".join(flavours.keys())}')
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

    shutil.copyfile('/app/src/pacman.conf', '/app/src/pacman_copy.conf')
    with open('/app/src/pacman_copy.conf', 'a') as file:
        file.write(
            '\n\n[main]\nServer = https://gitlab.com/kupfer/packages/prebuilts/-/raw/main/$repo')
        file.write(
            '\n\n[device]\nServer = https://gitlab.com/kupfer/packages/prebuilts/-/raw/main/$repo')

    if not os.path.exists(image_name):
        result = subprocess.run(['fallocate',
                                 '-l', '4G',
                                 image_name])
        if result.returncode != 0:
            logging.fatal(f'Failed to allocate {image_name}')
            exit(1)

        result = subprocess.run(['mkfs.ext4',
                                 '-L', 'kupfer',
                                 image_name])
        if result.returncode != 0:
            logging.fatal(f'Failed to create ext4 filesystem on {image_name}')
            exit(1)

    rootfs_mount = mount_rootfs_image(image_name)

    create_chroot(rootfs_mount, packages=(['base','base-kupfer'] + devices[device] + flavours[flavour]), pacman_conf='/app/src/pacman_copy.conf')


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
