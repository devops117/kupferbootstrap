import subprocess
import click
from wrapper import check_programs_wrap


@click.command(name='telnet')
def cmd_telnet(hostname: str = '172.16.42.1'):
    """Establish Telnet connection to device (e.g in debug-initramfs)"""
    check_programs_wrap('telnet')
    subprocess.run([
        'telnet',
        hostname,
    ])
