from packages import cmd_packages
from cache import cmd_cache
import click


@click.group()
def cli():
    pass


cli.add_command(cmd_cache)
cli.add_command(cmd_packages)
