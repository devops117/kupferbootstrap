import logging
import subprocess

def create_chroot(chroot_path, packages=['base'], pacman_conf='/app/src/pacman.conf'):
    result = subprocess.run(['pacstrap',
                             '-C', pacman_conf,
                             '-c',
                             '-G',
                             chroot_path]
                            + packages
                            + ['--needed', '--overwrite=*', '-yyuu'])
    if result.returncode != 0:
        logging.fatal('Failed to install system')
        exit(1)

    user = 'kupfer'
    password = '123456'
    groups = ['network', 'video', 'audio', 'optical', 'storage',
              'input', 'scanner', 'games', 'lp', 'rfkill', 'wheel']
    install_script = '\n'.join([
        f'if ! id -u "{user}" >/dev/null 2>&1; then',
        f'  useradd -m {user}',
        f'fi',
        f'usermod -a -G {",".join(groups)} {user}',
        f'echo "{user}:{password}" | chpasswd',
        f'chown {user}:{user} /home/{user} -R',
    ])
    result = subprocess.run(['arch-chroot',
                             chroot_path,
                             '/bin/bash',
                             '-c',
                             install_script])
    if result.returncode != 0:
        logging.fatal('Failed to setup user')
        exit(1)

