import atexit
import shutil
import os
import subprocess
import click
import tempfile

from constants import FLASH_PARTS, LOCATIONS
from fastboot import fastboot_flash
from chroot import get_device_chroot
from image import dump_bootimg, dump_lk2nd, dump_qhypstub, get_device_and_flavour, get_image_name, get_image_path
from wrapper import enforce_wrap
from image import resize_fs
from utils import mount

BOOTIMG = FLASH_PARTS['BOOTIMG']
LK2ND = FLASH_PARTS['LK2ND']
QHYPSTUB = FLASH_PARTS['QHYPSTUB']
ROOTFS = FLASH_PARTS['ROOTFS']


@click.command(name='flash')
@click.argument('what', type=click.Choice(list(FLASH_PARTS.values())))
@click.argument('location', required=False, type=click.Choice(LOCATIONS))
def cmd_flash(what, location):
    enforce_wrap()
    device, flavour = get_device_and_flavour()
    chroot = get_device_chroot(device, flavour, 'aarch64')
    device_image_name = get_image_name(chroot)
    device_image_path = get_image_path(chroot)

    if what not in FLASH_PARTS.values():
        raise Exception(f'Unknown what "{what}", must be one of {", ".join(FLASH_PARTS.values())}')

    if what == ROOTFS:
        if location is None:
            raise Exception(f'You need to specify a location to flash {what} to')
        if location not in LOCATIONS:
            raise Exception(f'Invalid location {location}. Choose one of {", ".join(LOCATIONS)}')

        path = ''
        dir = '/dev/disk/by-id'
        for file in os.listdir(dir):
            sanitized_file = file.replace('-', '').replace('_', '').lower()
            if f'jumpdrive{location.split("-")[0]}' in sanitized_file:
                path = os.path.realpath(os.path.join(dir, file))
                result = subprocess.run(['lsblk', path, '-o', 'SIZE'], capture_output=True)
                if result.returncode != 0:
                    raise Exception(f'Failed to lsblk {path}')
                if result.stdout == b'SIZE\n  0B\n':
                    raise Exception(
                        f'Disk {path} has a size of 0B. That probably means it is not available (e.g. no microSD inserted or no microSD card slot installed in the device) or corrupt or defect'
                    )
        if path == '':
            raise Exception('Unable to discover Jumpdrive')

        minimal_image_dir = tempfile.gettempdir()
        minimal_image_path = os.path.join(minimal_image_dir, f'minimal-{device_image_name}')

        def clean_dir():
            shutil.rmtree(minimal_image_dir)

        atexit.register(clean_dir)

        shutil.copyfile(device_image_path, minimal_image_path)

        resize_fs(minimal_image_path, shrink=True)

        if location.endswith('-file'):
            part_mount = '/mnt/kupfer/fs'
            if not os.path.exists(part_mount):
                os.makedirs(part_mount)

            result = mount(path, part_mount, options=[])
            if result.returncode != 0:
                raise Exception(f'Failed to mount {path} to {part_mount}')

            dir = os.path.join(part_mount, '.stowaways')
            if not os.path.exists(dir):
                os.makedirs(dir)

            result = subprocess.run([
                'rsync',
                '--archive',
                '--inplace',
                '--partial',
                '--progress',
                '--human-readable',
                minimal_image_path,
                os.path.join(dir, 'kupfer.img'),
            ])
            if result.returncode != 0:
                raise Exception(f'Failed to mount {path} to {part_mount}')
        else:
            result = subprocess.run([
                'dd',
                f'if={minimal_image_path}',
                f'of={path}',
                'bs=20M',
                'iflag=direct',
                'oflag=direct',
                'status=progress',
                'conv=sync,noerror',
            ])
            if result.returncode != 0:
                raise Exception(f'Failed to flash {minimal_image_path} to {path}')

    elif what == BOOTIMG:
        path = dump_bootimg(device_image_path)
        fastboot_flash('boot', path)
    elif what == LK2ND:
        path = dump_lk2nd(device_image_path)
        fastboot_flash('lk2nd', path)
    elif what == QHYPSTUB:
        path = dump_qhypstub(device_image_path)
        fastboot_flash('qhypstub', path)
    else:
        raise Exception(f'Unknown what "{what}", this must be a bug in kupferbootstrap!')
