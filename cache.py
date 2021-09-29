import shutil
import click
import os
from config import config
from wrapper import enforce_wrap
import logging


@click.group(name='cache')
def cmd_cache():
    pass


@cmd_cache.command(name='clean')
def cmd_clean():
    enforce_wrap()
    for path_name in ['chroots', 'pacman', 'jumpdrive']:
        dir = config.file['paths'][path_name]
        for file in os.listdir(dir):
            path = os.path.join(dir, file)
            logging.debug('Removing "{path}"')
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.unlink(path)
