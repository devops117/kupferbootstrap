import os
import urllib.request
from image import get_device_and_flavour, get_image_name
from logger import logging, setup_logging, verbose_option
from flash import dump_bootimg, erase_dtbo
import click
import subprocess

FASTBOOT = 'fastboot'

JUMPDRIVE = 'jumpdrive'
jumpdrive_version = '0.8'

boot_strategies = {
    'oneplus-enchilada': FASTBOOT,
    'xiaomi-beryllium-ebbg': FASTBOOT,
    'xiaomi-beryllium-tianma': FASTBOOT,
}


@click.command(name='boot', help=f'Leave TYPE empty or choose \'{JUMPDRIVE}\'')
@verbose_option
@click.argument('type', required=False)
def cmd_boot(verbose, type):
    setup_logging(verbose)

    device, flavour = get_device_and_flavour()
    image_name = get_image_name(device, flavour)
    strategy = boot_strategies[device]

    if strategy == FASTBOOT:
        if type == JUMPDRIVE:
            file = f'boot-{device}.img'
            path = os.path.join('/var/cache/jumpdrive', file)
            urllib.request.urlretrieve(f'https://github.com/dreemurrs-embedded/Jumpdrive/releases/download/{jumpdrive_version}/{file}', path)
        else:
            path = dump_bootimg(image_name)

        erase_dtbo()

        result = subprocess.run([
            'fastboot',
            'boot',
            path,
        ])
        if result.returncode != 0:
            logging.fatal(f'Failed to boot {path} using fastboot')
            exit(1)
