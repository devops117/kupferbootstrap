import subprocess
import click


@click.command(name='telnet')
def cmd_telnet(hostname: str = '172.16.42.1'):
    subprocess.run([
        'telnet',
        hostname,
    ])
