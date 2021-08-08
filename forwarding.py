import click
import subprocess
from logger import logging, setup_logging, verbose_option


@click.command(name='forwarding')
@verbose_option
def cmd_forwarding(verbose):
    setup_logging(verbose)

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

    result = subprocess.run([
        'ssh',
        '-o',
        'GlobalKnownHostsFile=/dev/null',
        '-o',
        'UserKnownHostsFile=/dev/null',
        '-o',
        'StrictHostKeyChecking=no',
        '-t',
        'kupfer@172.16.42.1',
        'sudo route add default gw 172.16.42.2',
    ])
    if result.returncode != 0:
        logging.fatal(f'Failed to add gateway over ssh')
        exit(1)
