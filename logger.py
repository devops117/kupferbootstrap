import click
import logging
import sys


def setup_logging(verbose: bool):
    level = logging.INFO
    if verbose:
        level = logging.DEBUG
    logging.basicConfig(
        stream=sys.stdout,
        format='%(asctime)s %(levelname)s: %(message)s',
        datefmt='%m/%d/%Y %H:%M:%S',
        level=level
    )


verbose_option = click.option(
    '-v',
    '--verbose',
    is_flag=True,
    help='Enables verbose logging'
)
