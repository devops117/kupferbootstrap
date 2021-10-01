#!/usr/bin/env python3

import click
from traceback import format_exc as get_trace
from logger import logging, setup_logging, verbose_option
from wrapper import nowrapper_option
from config import config, config_option, cmd_config
from forwarding import cmd_forwarding
from packages import cmd_packages
from telnet import cmd_telnet
from chroot import cmd_chroot
from cache import cmd_cache
from image import cmd_image
from boot import cmd_boot
from flash import cmd_flash
from ssh import cmd_ssh


@click.group()
@verbose_option
@config_option
@nowrapper_option
def cli(verbose: bool = False, config_file: str = None, no_wrapper: bool = False):
    setup_logging(verbose)
    config.runtime['verbose'] = verbose
    config.runtime['no_wrap'] = no_wrapper
    config.try_load_file(config_file)


def main():
    try:
        return cli(prog_name='kupferbootstrap')
    except Exception:
        logging.fatal(get_trace())
        exit(1)


cli.add_command(cmd_config)
cli.add_command(cmd_cache)
cli.add_command(cmd_packages)
cli.add_command(cmd_image)
cli.add_command(cmd_boot)
cli.add_command(cmd_flash)
cli.add_command(cmd_ssh)
cli.add_command(cmd_forwarding)
cli.add_command(cmd_telnet)
cli.add_command(cmd_chroot)

if __name__ == '__main__':
    main()
