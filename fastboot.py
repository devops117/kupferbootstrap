import logging
import subprocess


def fastboot_erase_dtbo():
    subprocess.run(
        [
            'fastboot',
            'erase',
            'dtbo',
        ],
        capture_output=True,
    )


def fastboot_flash(partition, file):
    result = subprocess.run([
        'fastboot',
        'flash',
        partition,
        file,
    ])
    if result.returncode != 0:
        logging.info(f'Failed to flash {file}')
        exit(1)


def fastboot_boot(file):
    result = subprocess.run([
        'fastboot',
        'boot',
        file,
    ])
    if result.returncode != 0:
        logging.fatal(f'Failed to boot {file} using fastboot')
        exit(1)
