import shutil
import click
import os


@click.group(name='cache')
def cmd_cache():
    pass


@click.command(name='clean')
def cmd_clean():
    for dir in ['/chroot', '/var/cache/pacman/pkg', '/var/cache/jumpdrive']:
        for file in os.listdir(dir):
            path = os.path.join(dir, file)
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.unlink(path)


cmd_cache.add_command(cmd_clean)
