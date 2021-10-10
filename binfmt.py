# modifed from pmbootstrap's binfmt.py, Copyright 2018 Oliver Smith, GPL-licensed

import os
import logging
import subprocess

from utils import mount


def binfmt_info():
    # Parse the info file
    full = {}
    info = "/usr/lib/binfmt.d/qemu-static.conf"
    logging.debug("parsing: " + info)
    with open(info, "r") as handle:
        for line in handle:
            if line.startswith('#') or ":" not in line:
                continue
            splitted = line.split(":")
            result = {
                # _ = splitted[0] # empty
                'name': splitted[1],
                'type': splitted[2],
                'offset': splitted[3],
                'magic': splitted[4],
                'mask': splitted[5],
                'interpreter': splitted[6],
                'flags': splitted[7],
                'line': line,
            }
            if not result['name'].startswith('qemu-'):
                logging.fatal(f'Unknown binfmt handler "{result["name"]}"')
                logging.debug(f'binfmt line: {line}')
                continue
            arch = ''.join(result['name'].split('-')[1:])
            full[arch] = result

    return full


def is_registered(arch: str) -> bool:
    return os.path.exists("/proc/sys/fs/binfmt_misc/qemu-" + arch)


def register(arch):
    if is_registered(arch):
        return

    lines = binfmt_info()

    # Build registration string
    # https://en.wikipedia.org/wiki/Binfmt_misc
    # :name:type:offset:magic:mask:interpreter:flags
    info = lines[arch]
    code = info['line']
    binfmt = '/proc/sys/fs/binfmt_misc'
    register = binfmt + '/register'
    if not os.path.exists(register):
        logging.info('mounting binfmt_misc')
        result = mount('binfmt_misc', binfmt, options=[], fs_type='binfmt_misc')
        if result.returncode != 0:
            raise Exception(f'Failed mounting binfmt_misc to {binfmt}')

    # Register in binfmt_misc
    logging.info(f"Registering qemu binfmt ({arch})")
    subprocess.run(["sh", "-c", 'echo "' + code + '" > ' + register + ' 2>/dev/null'])
    if not is_registered(arch):
        logging.debug(f'binfmt line: {code}')
        raise Exception(f'Failed to register qemu-user for {arch} with binfmt_misc, {binfmt}/{info["name"]} not found')


def unregister(args, arch):
    binfmt_file = "/proc/sys/fs/binfmt_misc/qemu-" + arch
    if not os.path.exists(binfmt_file):
        return
    logging.info(f"Unregistering qemu binfmt ({arch})")
    subprocess.run(["sh", "-c", "echo -1 > " + binfmt_file])
