import click
import subprocess
from logger import logging
from ssh import cmd_ssh
from wrapper import check_programs_wrap


@click.command(name='forwarding')
def cmd_forwarding():
    check_programs_wrap(['syctl', 'iptables'])

    result = subprocess.run([
        'sysctl',
        'net.ipv4.ip_forward=1',
    ])
    if result.returncode != 0:
        logging.fatal(f'Failed to enable ipv4 forward via sysctl')
        exit(1)

    result = subprocess.run([
        'iptables',
        '-P',
        'FORWARD',
        'ACCEPT',
    ])
    if result.returncode != 0:
        logging.fatal(f'Failed set iptables rule')
        exit(1)

    result = subprocess.run([
        'iptables',
        '-A',
        'POSTROUTING',
        '-t',
        'nat',
        '-j',
        'MASQUERADE',
        '-s',
        '172.16.42.0/24',
    ])
    if result.returncode != 0:
        logging.fatal(f'Failed set iptables rule')
        exit(1)

    result = cmd_ssh(cmd=['sudo route add default gw 172.16.42.2'])
    if result.returncode != 0:
        logging.fatal(f'Failed to add gateway over ssh')
        exit(1)
