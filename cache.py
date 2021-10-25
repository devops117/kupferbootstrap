import shutil
import click
import os
from config import config
from wrapper import enforce_wrap
import logging

PATHS = ['chroots', 'pacman', 'jumpdrive', 'packages', 'images']


@click.group(name='cache')
def cmd_cache():
    pass


@cmd_cache.command(name='clean')
@click.option('--force', default=False)
@click.argument('paths', nargs=-1, required=False)
def cmd_clean(paths: list[str], force=False):
    if unknown_paths := (set(paths) - set(PATHS + ['all'])):
        raise Exception(f"Unknown paths: {' ,'.join(unknown_paths)}")
    if 'all' in paths or (not paths and force):
        paths = PATHS.copy()

    enforce_wrap()

    clear = {path: (path in paths) for path in PATHS}
    query = not paths
    if not query or force:
        click.confirm(f'Really clear {", ".join(paths)}?', abort=True)
    for path_name in PATHS:
        if query:
            clear[path_name] = click.confirm(f'Clear {path_name}?')
        if clear[path_name]:
            logging.info(f'Clearing {path_name}')
            dir = config.get_path(path_name)
            for file in os.listdir(dir):
                path = os.path.join(dir, file)
                logging.debug(f'Removing "{path_name}/{file}"')
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.unlink(path)
