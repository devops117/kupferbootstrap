import logging
import subprocess
import os
import shutil

def create_chroot(chroot_path, packages=['base'], pacman_conf='/app/src/pacman.conf', chroot_base_path='/chroot', extra_repos={}):
    pacman_conf_target=chroot_path+'/etc/pacman.conf'

    os.makedirs(chroot_path+'/etc', exist_ok=True)
    shutil.copyfile(pacman_conf, pacman_conf_target)

    extra_conf = ''
    for repo_name, repo_options in extra_repos.items():
        extra_conf += f'\n\n[{repo_name}]\n'
        extra_conf += '\n'.join(['%s = %s' % (name,value) for name,value in repo_options.items()])
    with open(pacman_conf_target, 'a') as file:
        file.write(extra_conf)


    result = subprocess.run(['pacstrap',
                             '-C', pacman_conf_target,
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

