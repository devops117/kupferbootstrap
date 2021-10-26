import os
import urllib.request
import click

from config import config
from constants import BOOT_STRATEGIES, FLASH_PARTS, FASTBOOT, JUMPDRIVE, JUMPDRIVE_VERSION
from fastboot import fastboot_boot, fastboot_erase_dtbo
from image import get_device_and_flavour, losetup_rootfs_image, get_image_path, dump_bootimg, dump_lk2nd
from chroot import get_device_chroot
from wrapper import enforce_wrap

LK2ND = FLASH_PARTS['LK2ND']
BOOTIMG = FLASH_PARTS['BOOTIMG']

TYPES = [LK2ND, JUMPDRIVE, BOOTIMG]


@click.command(name='boot')
@click.argument('type', required=False, default=BOOTIMG, type=click.Choice(TYPES))
def cmd_boot(type):
    f"""Flash one of {', '.join(TYPES)}"""
    enforce_wrap()
    device, flavour = get_device_and_flavour()
    # TODO: parse arch and sector size
    chroot = get_device_chroot(device, flavour, 'aarch64')
    sector_size = 4096
    image_path = get_image_path(chroot)
    loop_device = losetup_rootfs_image(image_path, sector_size)
    strategy = BOOT_STRATEGIES[device]

    if strategy == FASTBOOT:
        if type == JUMPDRIVE:
            file = f'boot-{device}.img'
            path = os.path.join(config.get_path('jumpdrive'), file)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if not os.path.exists(path):
                urllib.request.urlretrieve(f'https://github.com/dreemurrs-embedded/Jumpdrive/releases/download/{JUMPDRIVE_VERSION}/{file}', path)
        elif type == LK2ND:
            path = dump_lk2nd(loop_device + 'p1')
        elif type == BOOTIMG:
            path = dump_bootimg(loop_device + 'p1')
        else:
            raise Exception(f'Unknown boot image type {type}')
        fastboot_erase_dtbo()
        fastboot_boot(path)
