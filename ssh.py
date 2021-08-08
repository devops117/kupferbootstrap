import subprocess
import click
from logger import setup_logging, verbose_option


@click.command(name='ssh')
@verbose_option
def cmd_ssh(verbose):
    setup_logging(verbose)

    subprocess.run([
        'ssh',
        '-o',
        'GlobalKnownHostsFile=/dev/null',
        '-o',
        'UserKnownHostsFile=/dev/null',
        '-o',
        'StrictHostKeyChecking=no',
        'kupfer@172.16.42.1',
    ])
