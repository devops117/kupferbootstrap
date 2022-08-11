from typing import Optional
import logging
import os
import pathlib
import subprocess
import click

from config import config
from constants import SSH_COMMON_OPTIONS, SSH_DEFAULT_HOST, SSH_DEFAULT_PORT


@click.command(name='ssh')
@click.argument('cmd', nargs=-1)
@click.option('--user', '-u', help='the SSH username', default=None)
@click.option('--host', '-h', help='the SSH host', default=SSH_DEFAULT_HOST)
@click.option('--port', '-p', help='the SSH port', type=int, default=SSH_DEFAULT_PORT)
def cmd_ssh(cmd: list[str], user: str, host: str, port: int):
    """Establish SSH connection to device"""
    run_ssh_command(list(cmd), user=user, host=host, port=port)


def run_ssh_command(cmd: list[str] = [],
                    user: Optional[str] = None,
                    host: str = SSH_DEFAULT_HOST,
                    port: int = SSH_DEFAULT_PORT,
                    alloc_tty: bool = False):
    if not user:
        user = config.get_profile()['username']
    keys = find_ssh_keys()
    extra_args = []
    if len(keys) > 0:
        extra_args += ['-i', keys[0]]
    if config.runtime['verbose']:
        extra_args += ['-v']
    if alloc_tty:
        extra_args += ['-t']
    logging.info(f'Opening SSH connection to {(user + "@") if user else ""}{host} ({port})')
    full_cmd = [
        'ssh',
    ] + extra_args + SSH_COMMON_OPTIONS + [
        '-p',
        str(port),
        f'{user}@{host}',
        '--',
    ] + cmd
    logging.debug(f"running cmd: {full_cmd}")
    return subprocess.run(full_cmd)


def scp_put_files(src: list[str], dst: str, user: str = None, host: str = SSH_DEFAULT_HOST, port: int = SSH_DEFAULT_PORT):
    if not user:
        user = config.get_profile()['username']
    keys = find_ssh_keys()
    key_args = []
    if len(keys) > 0:
        key_args = ['-i', keys[0]]
    cmd = [
        'scp',
    ] + key_args + SSH_COMMON_OPTIONS + [
        '-P',
        str(port),
    ] + src + [
        f'{user}@{host}:{dst}',
    ]
    logging.info(f"Copying files to {user}@{host}:{dst}:\n{src}")
    logging.debug(f"running cmd: {cmd}")
    return subprocess.run(cmd)


def find_ssh_keys():
    dir = os.path.join(pathlib.Path.home(), '.ssh')
    if not os.path.exists(dir):
        return []
    keys = []
    for file in os.listdir(dir):
        if file.startswith('id_') and not file.endswith('.pub'):
            keys.append(os.path.join(dir, file))
    return keys


def copy_ssh_keys(root_dir: str, user: str):
    authorized_keys_file = os.path.join(
        root_dir,
        'home',
        user,
        '.ssh',
        'authorized_keys',
    )
    if os.path.exists(authorized_keys_file):
        os.unlink(authorized_keys_file)

    keys = find_ssh_keys()
    if len(keys) == 0:
        logging.info("Could not find any ssh key to copy")
        create = click.confirm("Do you want me to generate an ssh key for you?", True)
        if not create:
            return
        result = subprocess.run([
            'ssh-keygen',
            '-f',
            os.path.join(pathlib.Path.home(), '.ssh', 'id_ed25519_kupfer'),
            '-t',
            'ed25519',
            '-C',
            'kupfer',
            '-N',
            '',
        ])
        if result.returncode != 0:
            logging.fatal("Failed to generate ssh key")
        keys = find_ssh_keys()

    ssh_dir = os.path.join(root_dir, 'home', user, '.ssh')
    if not os.path.exists(ssh_dir):
        os.makedirs(ssh_dir, exist_ok=True)

    with open(authorized_keys_file, 'a') as authorized_keys:
        for key in keys:
            pub = f'{key}.pub'
            if not os.path.exists('pub'):
                logging.debug(f'Skipping key {key}: {pub} not found')
                continue
            with open(pub, 'r') as file:
                authorized_keys.write(file.read())
