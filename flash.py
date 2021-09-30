import atexit
from constants import FLASH_PARTS, LOCATIONS
from fastboot import fastboot_flash
import shutil
from image import dump_bootimg, dump_lk2nd, dump_qhypstub, get_device_and_flavour, get_image_name
import os
import subprocess
import click
import tempfile
from wrapper import enforce_wrap
from image import resize_fs

BOOTIMG = FLASH_PARTS['BOOTIMG']
LK2ND = FLASH_PARTS['LK2ND']
QHYPSTUB = FLASH_PARTS['QHYPSTUB']
ROOTFS = FLASH_PARTS['ROOTFS']


@click.command(name='flash')
@click.argument('what')
@click.argument('location', required=False)
def cmd_flash(what, location):
    enforce_wrap()
    device, flavour = get_device_and_flavour()
    image_name = get_image_name(device, flavour)

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

        image_dir = tempfile.gettempdir()
        image_path = os.path.join(image_dir, f'minimal-{image_name}')

        def clean_dir():
            shutil.rmtree(image_dir)

        atexit.register(clean_dir)

        shutil.copyfile(image_name, image_path)

        resize_fs(image_path, shrink=True)

        if location.endswith('-file'):
            part_mount = '/mnt/kupfer/fs'
            if not os.path.exists(part_mount):
                os.makedirs(part_mount)

            def umount():
                subprocess.run(
                    [
                        'umount',
                        '-lc',
                        part_mount,
                    ],
                    stderr=subprocess.DEVNULL,
                )

            atexit.register(umount)

            result = subprocess.run([
                'mount',
                path,
                part_mount,
            ])
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
                image_path,
                os.path.join(dir, 'kupfer.img'),
            ])
            if result.returncode != 0:
                raise Exception(f'Failed to mount {path} to {part_mount}')
        else:
            result = subprocess.run([
                'dd',
                f'if={image_path}',
                f'of={path}',
                'bs=20M',
                'iflag=direct',
                'oflag=direct',
                'status=progress',
                'conv=sync,noerror',
            ])
            if result.returncode != 0:
                raise Exception(f'Failed to flash {image_path} to {path}')

    elif what == BOOTIMG:
        path = dump_bootimg(image_name)
        fastboot_flash('boot', path)
    elif what == LK2ND:
        path = dump_lk2nd(image_name)
        fastboot_flash('lk2nd', path)
    elif what == QHYPSTUB:
        path = dump_qhypstub(image_name)
        fastboot_flash('qhypstub', path)
    else:
        raise Exception(f'Unknown what "{what}", this must be a bug in kupferbootstrap!')
