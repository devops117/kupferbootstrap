import logging
import subprocess
import os
import shutil
from config import config


def get_chroot_path(chroot_name, override_basepath: str = None) -> str:
    base_path = config.file['paths']['chroots'] if not override_basepath else override_basepath
    return os.path.join(base_path, chroot_name)


def create_chroot(chroot_name, packages=['base'], pacman_conf='/app/local/etc/pacman.conf', extra_repos={}, chroot_base_path: str = None):
    chroot_path = get_chroot_path(chroot_name, override_basepath=chroot_base_path)
    pacman_conf_target = chroot_path + '/etc/pacman.conf'

    os.makedirs(chroot_path + '/etc', exist_ok=True)
    shutil.copyfile(pacman_conf, pacman_conf_target)

    extra_conf = ''
    for repo_name, repo_options in extra_repos.items():
        extra_conf += f'\n\n[{repo_name}]\n'
        extra_conf += '\n'.join(['%s = %s' % (name, value) for name, value in repo_options.items()])
    with open(pacman_conf_target, 'a') as file:
        file.write(extra_conf)

    result = subprocess.run(['pacstrap', '-C', pacman_conf_target, '-c', '-G', chroot_path] + packages + [
        '--needed',
        '--overwrite=*',
        '-yyuu',
    ])
    if result.returncode != 0:
        raise Exception('Failed to install chroot')
    return chroot_path


def create_chroot_user(
    chroot_name,
    chroot_base_path: str = None,
    user='kupfer',
    password='123456',
    groups=['network', 'video', 'audio', 'optical', 'storage', 'input', 'scanner', 'games', 'lp', 'rfkill', 'wheel'],
):
    chroot_path = get_chroot_path(chroot_name, override_basepath=chroot_base_path)

    install_script = '\n'.join([
        f'if ! id -u "{user}" >/dev/null 2>&1; then',
        f'  useradd -m {user}',
        f'fi',
        f'usermod -a -G {",".join(groups)} {user}',
        f'echo "{user}:{password}" | chpasswd',
        f'chown {user}:{user} /home/{user} -R',
    ])
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
