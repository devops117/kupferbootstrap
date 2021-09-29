from packages import cmd_packages
from cache import cmd_cache
from image import cmd_image
from boot import cmd_boot
from flash import cmd_flash
from ssh import cmd_ssh
from forwarding import cmd_forwarding
from telnet import cmd_telnet
from logger import logging, setup_logging, verbose_option
import click
from config import config, config_option, cmd_config
from wrapper import enforce_wrap, nowrapper_option
from traceback import format_exc as get_trace


@click.group()
@verbose_option
@config_option
@nowrapper_option
def cli(verbose: bool = False, config_file: str = None, no_wrapper: bool = False):
    setup_logging(verbose)
    config.runtime['verbose'] = verbose
    config.try_load_file(config_file)
    # TODO: move this only to CMDs where it's needed
    enforce_wrap(no_wrapper=no_wrapper)


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

if __name__ == '__main__':
    main()
