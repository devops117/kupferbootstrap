import subprocess
import click
from wrapper import enforce_wrap


@click.command(name='telnet')
def cmd_telnet(hostname: str = '172.16.42.1'):
    enforce_wrap()
    subprocess.run([
        'telnet',
        hostname,
    ])
