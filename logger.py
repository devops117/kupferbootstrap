import click
import coloredlogs
import logging
import sys


def setup_logging(verbose: bool):
    level_colors = coloredlogs.DEFAULT_LEVEL_STYLES | {'info': {'color': 'magenta', 'bright': True}, 'debug': {'color': 'blue', 'bright': True}}
    field_colors = coloredlogs.DEFAULT_FIELD_STYLES | {'asctime': {'color': 'white', 'faint': True}}
    level = logging.DEBUG if verbose else logging.INFO
    coloredlogs.install(
        stream=sys.stdout,
        fmt='%(asctime)s %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=level,
        level_styles=level_colors,
        field_styles=field_colors,
    )
    logging.debug('Logging set up.')


verbose_option = click.option(
    '-v',
    '--verbose',
    is_flag=True,
    help='Enables verbose logging',
)
