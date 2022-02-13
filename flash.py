import atexit
import shutil
import os
import subprocess
import click
import tempfile

from constants import FLASH_PARTS, LOCATIONS
from fastboot import fastboot_flash
from image import dd_image, partprobe, shrink_fs, losetup_rootfs_image, dump_aboot, dump_lk2nd, dump_qhypstub, get_device_and_flavour, get_image_name, get_image_path
from wrapper import enforce_wrap

ABOOT = FLASH_PARTS['ABOOT']
LK2ND = FLASH_PARTS['LK2ND']
QHYPSTUB = FLASH_PARTS['QHYPSTUB']
ROOTFS = FLASH_PARTS['ROOTFS']


@click.command(name='flash')
@click.argument('what', type=click.Choice(list(FLASH_PARTS.values())))
@click.argument('location', required=False, type=click.Choice(LOCATIONS))
def cmd_flash(what, location):
    enforce_wrap()
    device, flavour = get_device_and_flavour()
    device_image_name = get_image_name(device, flavour)
    device_image_path = get_image_path(device, flavour)

    # TODO: PARSE DEVICE SECTOR SIZE
    sector_size = 4096

    if what not in FLASH_PARTS.values():
        raise Exception(f'Unknown what "{what}", must be one of {", ".join(FLASH_PARTS.values())}')

    if what == ROOTFS:
        if location is None:
            raise Exception(f'You need to specify a location to flash {what} to')

        path = ''
        if location.startswith("/dev/"):
            path = location
        else:
            if location not in LOCATIONS:
                raise Exception(f'Invalid location {location}. Choose one of {", ".join(LOCATIONS)}')

            dir = '/dev/disk/by-id'
            for file in os.listdir(dir):
                sanitized_file = file.replace('-', '').replace('_', '').lower()
                if f'jumpdrive{location.split("-")[0]}' in sanitized_file:
                    path = os.path.realpath(os.path.join(dir, file))
                    partprobe(path)
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

        loop_device = losetup_rootfs_image(minimal_image_path, sector_size)
        partprobe(loop_device)
        shrink_fs(loop_device, minimal_image_path, sector_size)

        result = dd_image(input=minimal_image_path, output=path)

        if result.returncode != 0:
            raise Exception(f'Failed to flash {minimal_image_path} to {path}')
    else:
        loop_device = losetup_rootfs_image(device_image_path, sector_size)
        if what == ABOOT:
            path = dump_aboot(f'{loop_device}p1')
            fastboot_flash('boot', path)
        elif what == LK2ND:
            path = dump_lk2nd(f'{loop_device}p1')
            fastboot_flash('lk2nd', path)
        elif what == QHYPSTUB:
            path = dump_qhypstub(f'{loop_device}p1')
            fastboot_flash('qhypstub', path)
        else:
            raise Exception(f'Unknown what "{what}", this must be a bug in kupferbootstrap!')
