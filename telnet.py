import subprocess
import click
from wrapper import check_programs_wrap


@click.command(name='telnet')
def cmd_telnet(hostname: str = '172.16.42.1'):
    check_programs_wrap('telnet')
    subprocess.run([
        'telnet',
        hostname,
    ])
