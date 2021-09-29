import subprocess
import click
from wrapper import check_programs_wrap


@click.command(name='ssh')
def cmd_ssh(cmd: list[str] = [], host: str = '172.16.42.1', user: str = 'kupfer', port: int = 22):
    check_programs_wrap('ssh')
    return subprocess.run([
        'ssh',
        '-o',
        'GlobalKnownHostsFile=/dev/null',
        '-o',
        'UserKnownHostsFile=/dev/null',
        '-o',
        'StrictHostKeyChecking=no',
        '-p',
        str(port),
        f'{user}@{host}',
        '--',
    ] + cmd)
