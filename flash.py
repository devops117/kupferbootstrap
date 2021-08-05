from image import get_device_and_flavour, get_image_name
from boot import dump_bootimg
import os
import subprocess
import click
from logger import *

ROOTFS = 'rootfs'
BOOTIMG = 'bootimg'

EMMC = 'emmc'
EMMCFILE = 'emmc-file'
MICROSD = 'microsd'
locations = [EMMC, EMMCFILE, MICROSD]


@click.command(name='flash')
@verbose_option
@click.argument('what')
@click.argument('location', required=False)
def cmd_flash(verbose, what, location):
    setup_logging(verbose)

    device, flavour = get_device_and_flavour()
    image_name = get_image_name(device, flavour)

    if what == ROOTFS:
        if location == None:
            logging.info(f'You need to specify a location to flash {what} to')
            exit(1)
        if location not in locations:
            logging.info(
                f'Invalid location {location}. Choose one of {", ".join(locations)} for location')
            exit(1)

        dir = '/dev/disk/by-id'
        for file in os.listdir(dir):
            sanitized_file = file.replace('-', '').replace('_', '').lower()
            if f'jumpdrive{location.split("-")[0]}' in sanitized_file:
                path = os.path.realpath(os.path.join(dir, file))
                result = subprocess.run(['lsblk',
                                        path,
                                        '-o', 'SIZE'],
                                        capture_output=True)
                if result.returncode != 0:
                    logging.info(f'Failed to lsblk {path}')
                    exit(1)
                if result.stdout == b'SIZE\n  0B\n':
                    logging.info(
                        f'Disk {path} has a size of 0B. That probably means it is not available (e.g. no microSD inserted or no microSD card slot installed in the device) or corrupt or defect')
                    exit(1)

        if location.endswith('-file'):
            logging.fatal('Not implemented yet')
            exit()
        else:
            result = subprocess.run(['dd',
                                     f'if={image_name}',
                                     f'of={path}',
                                     'bs=20M',
                                     'iflag=direct',
                                     'oflag=direct',
                                     'status=progress'])
            if result.returncode != 0:
                logging.info(f'Failed to flash {image_name} to {path}')
                exit(1)

    elif what == BOOTIMG:
        result = subprocess.run(['fastboot', 'erase', 'dtbo'])
        if result.returncode != 0:
            logging.info(f'Failed to erase dtbo')
            exit(1)

        path = dump_bootimg(image_name)
        result = subprocess.run(['fastboot', 'flash', 'boot', path])
        if result.returncode != 0:
            logging.info(f'Failed to flash boot.img')
            exit(1)
    else:
        logging.fatal(f'Unknown what {what}')
        exit(1)
