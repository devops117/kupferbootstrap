import logging
import subprocess
import os
import shutil
from config import config


def get_chroot_path(chroot_name, override_basepath: str = None) -> str:
    base_path = config.file['paths']['chroots'] if not override_basepath else override_basepath
    return os.path.join(base_path, chroot_name)


def create_chroot(
    chroot_name,
    arch='aarch64',
    packages=['base'],
    pacman_conf='/app/local/etc/pacman.conf',
    extra_repos={},
    chroot_base_path: str = None,
):
    base_chroot = f'base_{arch}'
    chroot_path = get_chroot_path(chroot_name, override_basepath=chroot_base_path)
    pacman_conf_target = chroot_path + '/etc/pacman.conf'

    # copy base_chroot instead of creating from scratch every time
    if not (chroot_base_path or chroot_name == base_chroot):
        # only install base package in base_chroot
        base_chroot_path = create_chroot(base_chroot, arch=arch)
        logging.info(f'Copying {base_chroot} chroot to {chroot_name}')
        result = subprocess.run([
            'rsync',
            '-a',
            '--delete',
            '-q',
            '-W',
            '-x',
            f'{base_chroot_path}/',
            f'{chroot_path}/',
        ])
        if result.returncode != 0:
            logging.fatal('Failed to sync chroot copy')
            exit(1)

    os.makedirs(chroot_path + '/etc', exist_ok=True)
    shutil.copyfile(pacman_conf, pacman_conf_target)

    extra_conf = ''
    for repo_name, repo_options in extra_repos.items():
        extra_conf += f'\n\n[{repo_name}]\n'
        extra_conf += '\n'.join(['%s = %s' % (name, value) for name, value in repo_options.items()])
    with open(pacman_conf_target, 'a') as file:
        file.write(extra_conf)

    result = subprocess.run([
        'pacstrap',
        '-C',
        pacman_conf_target,
        '-c',
        '-G',
        chroot_path,
    ] + packages + [
        '--needed',
        '--overwrite=*',
        '-yyuu',
    ],
                            capture_output=True)
    if result.returncode != 0:
        raise Exception('Failed to install chroot:' + result.stdout.decode() + '\n' + result.stderr.decode())
    return chroot_path


def create_chroot_user(
    chroot_name,
    chroot_base_path: str = None,
    user='kupfer',
    password='123456',
    groups=['network', 'video', 'audio', 'optical', 'storage', 'input', 'scanner', 'games', 'lp', 'rfkill', 'wheel'],
):
    chroot_path = get_chroot_path(chroot_name, override_basepath=chroot_base_path)

    install_script = f'''
        if ! id -u "{user}" >/dev/null 2>&1; then
          useradd -m {user}
        fi
        usermod -a -G {",".join(groups)} {user}
        echo "{user}:{password}" | chpasswd
        chown {user}:{user} /home/{user} -R
    '''
    result = subprocess.run([
        'arch-chroot',
        chroot_path,
        '/bin/bash',
        '-c',
        install_script,
    ])
    if result.returncode != 0:
        logging.fatal('Failed to setup user')
        exit(1)
