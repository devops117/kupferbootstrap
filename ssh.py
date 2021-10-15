import logging
import os
import pathlib
import subprocess
import click
from config import config
from constants import SSH_COMMON_OPTIONS, SSH_DEFAULT_HOST, SSH_DEFAULT_PORT
from wrapper import enforce_wrap


@click.command(name='ssh')
def cmd_ssh():
    enforce_wrap()
    run_ssh_command()


def run_ssh_command(cmd: list[str] = [], user: str = None, host: str = SSH_DEFAULT_HOST, port: int = SSH_DEFAULT_PORT):
    if not user:
        user = config.get_profile()['username']
    keys = find_ssh_keys()
    key_args = []
    if len(keys) > 0:
        key_args = ['-i', keys[0]]
    return subprocess.run([
        'ssh',
    ] + key_args + SSH_COMMON_OPTIONS + [
        '-p',
        str(port),
        f'{user}@{host}',
        '--',
    ] + cmd)


def scp_put_files(src: list[str], dst: str, user: str = None, host: str = SSH_DEFAULT_HOST, port: int = SSH_DEFAULT_PORT):
    if not user:
        user = config.get_profile()['username']
    keys = find_ssh_keys()
    key_args = []
    if len(keys) > 0:
        key_args = ['-i', keys[0]]
    return subprocess.run([
        'scp',
    ] + key_args + SSH_COMMON_OPTIONS + [
        '-P',
        str(port),
    ] + src + [
        f'{user}@{host}:{dst}',
    ])


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
            with open(f'{key}.pub', 'r') as file:
                authorized_keys.write(file.read())
