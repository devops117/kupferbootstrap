import logging
import subprocess


def fastboot_erase_dtbo():
    logging.info("Fastboot: Erasing DTBO")
    subprocess.run(
        [
            'fastboot',
            'erase',
            'dtbo',
        ],
        capture_output=True,
    )


def fastboot_flash(partition, file):
    logging.info(f"Fastboot: Flashing {file} to {partition}")
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
    logging.info(f"Fastboot: booting {file}")
    result = subprocess.run([
        'fastboot',
        'boot',
        file,
    ])
    if result.returncode != 0:
        logging.fatal(f'Failed to boot {file} using fastboot')
        exit(1)
