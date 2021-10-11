import subprocess
import click
from logger import setup_logging, verbose_option


@click.command(name='ssh')
@click.option('--user', prompt='The SSH username', default='kupfer')
@click.option('--host', prompt='The SSH host', default='172.16.42.1')
@verbose_option
def cmd_ssh(verbose, user, host):
    setup_logging(verbose)

    subprocess.run([
        'ssh',
        '-o',
        'GlobalKnownHostsFile=/dev/null',
        '-o',
        'UserKnownHostsFile=/dev/null',
        '-o',
        'StrictHostKeyChecking=no',
        f'{user}@{host}',
    ])
