import os
import urllib.request
from image import get_device_and_flavour, get_image_name, dump_bootimg, dump_lk2nd
from logger import setup_logging, verbose_option
from fastboot import fastboot_boot, fastboot_erase_dtbo
from constants import BOOT_STRATEGIES, FASTBOOT, JUMPDRIVE, LK2ND, JUMPDRIVE_VERSION
import click


@click.command(name='boot')
@verbose_option
@click.argument('type', required=False)
def cmd_boot(verbose, type):
    setup_logging(verbose)

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
        else:
            path = dump_bootimg(image_name)

        fastboot_erase_dtbo()
        fastboot_boot(path)
