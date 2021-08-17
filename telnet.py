import subprocess
import click
from logger import setup_logging, verbose_option


@click.command(name='telnet')
@verbose_option
def cmd_telnet(verbose):
    setup_logging(verbose)

    subprocess.run([
        'telnet',
        '172.16.42.1',
    ])
