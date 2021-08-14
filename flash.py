import atexit
from cmath import log
from email.mime import image
import shutil
from image import get_device_and_flavour, get_image_name
import os
import subprocess
import click
import tempfile
from logger import logging, setup_logging, verbose_option

ROOTFS = 'rootfs'
BOOTIMG = 'bootimg'

EMMC = 'emmc'
EMMCFILE = 'emmc-file'
MICROSD = 'microsd'
locations = [EMMC, EMMCFILE, MICROSD]


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


def erase_dtbo():
    result = subprocess.run([
        'fastboot',
        'erase',
        'dtbo',
    ])
    if result.returncode != 0:
        logging.info(f'Failed to erase dtbo')
        exit(1)


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
            logging.info(f'Invalid location {location}. Choose one of {", ".join(locations)} for location')
            exit(1)

        path = ''
        dir = '/dev/disk/by-id'
        for file in os.listdir(dir):
            sanitized_file = file.replace('-', '').replace('_', '').lower()
            if f'jumpdrive{location.split("-")[0]}' in sanitized_file:
                path = os.path.realpath(os.path.join(dir, file))
                result = subprocess.run(['lsblk', path, '-o', 'SIZE'], capture_output=True)
                if result.returncode != 0:
                    logging.info(f'Failed to lsblk {path}')
                    exit(1)
                if result.stdout == b'SIZE\n  0B\n':
                    logging.info(
                        f'Disk {path} has a size of 0B. That probably means it is not available (e.g. no microSD inserted or no microSD card slot installed in the device) or corrupt or defect'
                    )
                    exit(1)
        if path == '':
            logging.fatal(f'Unable to discover Jumpdrive')
            exit(1)

        image_dir = tempfile.gettempdir()
        image_path = os.path.join(image_dir, f'minimal-{image_name}')

        def clean_dir():
            shutil.rmtree(image_dir)

        atexit.register(clean_dir)

        shutil.copyfile(image_name, image_path)

        result = subprocess.run([
            'e2fsck',
            '-fy',
            image_path,
        ])
        if result.returncode != 0:
            logging.fatal(f'Failed to e2fsck {image_path}')
            exit(1)

        result = subprocess.run([
            'resize2fs',
            '-M',
            image_path,
        ])
        if result.returncode != 0:
            logging.fatal(f'Failed to resize2fs {image_path}')
            exit(1)

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
                logging.fatal(f'Failed to mount {path} to {part_mount}')
                exit(1)

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
                logging.fatal(f'Failed to mount {path} to {part_mount}')
                exit(1)
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
                logging.info(f'Failed to flash {image_path} to {path}')
                exit(1)

    elif what == BOOTIMG:
        path = dump_bootimg(image_name)
        result = subprocess.run([
            'fastboot',
            'flash',
            'boot',
            path,
        ])
        if result.returncode != 0:
            logging.info(f'Failed to flash boot.img')
            exit(1)
    else:
        logging.fatal(f'Unknown what {what}')
        exit(1)
