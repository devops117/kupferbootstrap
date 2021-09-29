import os
import urllib.request
from image import get_device_and_flavour, get_image_name, dump_bootimg, dump_lk2nd
from fastboot import fastboot_boot, fastboot_erase_dtbo
from constants import BOOT_STRATEGIES, FLASH_PARTS, FASTBOOT, JUMPDRIVE, JUMPDRIVE_VERSION
import click

LK2ND = FLASH_PARTS['LK2ND']
BOOTIMG = FLASH_PARTS['BOOTIMG']

TYPES = [LK2ND, JUMPDRIVE, BOOTIMG]


@click.command(name='boot')
@click.argument('type', required=False, default=BOOTIMG)
def cmd_boot(type):
    f"""Flash one of {', '.join(TYPES)}"""
    device, flavour = get_device_and_flavour()
    image_name = get_image_name(device, flavour)
    strategy = BOOT_STRATEGIES[device]

    if strategy == FASTBOOT:
        if type == JUMPDRIVE:
            file = f'boot-{device}.img'
            path = os.path.join('/var/cache/jumpdrive', file)
            if not os.path.exists(path):
                urllib.request.urlretrieve(f'https://github.com/dreemurrs-embedded/Jumpdrive/releases/download/{JUMPDRIVE_VERSION}/{file}', path)
        elif type == LK2ND:
            path = dump_lk2nd(image_name)
        elif type == BOOTIMG:
            path = dump_bootimg(image_name)
        else:
            raise Exception(f'Unknown boot image type {type}')
        fastboot_erase_dtbo()
        fastboot_boot(path)
