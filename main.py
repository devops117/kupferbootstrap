from packages import cmd_packages
from cache import cmd_cache
from image import cmd_image
from boot import cmd_boot
from flash import cmd_flash
from ssh import cmd_ssh
from forwarding import cmd_forwarding
import click


@click.group()
def cli():
    pass


cli.add_command(cmd_cache)
cli.add_command(cmd_packages)
cli.add_command(cmd_image)
cli.add_command(cmd_boot)
cli.add_command(cmd_flash)
cli.add_command(cmd_ssh)
cli.add_command(cmd_forwarding)
