import click
import logging
import subprocess
import os
from config import config
from distro import get_base_distros, RepoInfo
from shlex import quote as shell_quote
from shutil import copy
from utils import mount
from distro import get_kupfer_local
from wrapper import enforce_wrap
from constants import GCC_HOSTSPECS, CROSSDIRECT_PKGS
from glob import glob

BIND_BUILD_DIRS = 'BINDBUILDDIRS'


def get_chroot_path(chroot_name, override_basepath: str = None) -> str:
    base_path = config.get_path('chroots') if not override_basepath else override_basepath
    return os.path.join(base_path, chroot_name)


def create_chroot(chroot_name: str,
                  arch: str,
                  packages: list[str] = ['base'],
                  extra_repos: dict[str, RepoInfo] = {},
                  chroot_base_path: str = None,
                  bind_mounts: dict[str, str] = BIND_BUILD_DIRS):
    base_chroot = f'base_{arch}'
    chroot_path = get_chroot_path(chroot_name, override_basepath=chroot_base_path)
    base_distro = get_base_distros()[arch]
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
            raise Exception(f'Failed to copy {base_chroot} to {chroot_name}')

        # patch makepkg
        with open(f'{chroot_path}/usr/bin/makepkg', 'r') as file:
            data = file.read()
        data = data.replace('EUID == 0', 'EUID == -1')
        with open(f'{chroot_path}/usr/bin/makepkg', 'w') as file:
            file.write(data)

        # configure makepkg
        with open(f'{chroot_path}/etc/makepkg.conf', 'r') as file:
            data = file.read()
        data = data.replace('xz -c', 'xz -T0 -c')
        data = data.replace(' check ', ' !check ')
        with open(f'{chroot_path}/etc/makepkg.conf', 'w') as file:
            file.write(data)

    os.makedirs(chroot_path + '/etc', exist_ok=True)

    if bind_mounts == BIND_BUILD_DIRS:
        bind_mounts = {p: p for p in [config.get_path(path) for path in ['packages', 'pkgbuilds']]}
    for src, _dest in bind_mounts.items():
        dest = os.path.join(chroot_path, _dest.lstrip('/'))
        os.makedirs(dest, exist_ok=True)
        result = mount(src, dest)
        if result.returncode != 0:
            raise Exception(f"Couldn't bind-mount {src} to {dest}")

    conf_text = base_distro.get_pacman_conf(extra_repos)
    with open(pacman_conf_target, 'w') as file:
        file.write(conf_text)

    logging.info(f'Installing packages to {chroot_name}: {", ".join(packages)}')

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
    ])
    if result.returncode != 0:
        raise Exception(f'Failed to install chroot "{chroot_name}"')
    return chroot_path


def run_chroot_cmd(script: str,
                   chroot_path: str,
                   inner_env: dict[str, str] = {},
                   outer_env: dict[str, str] = os.environ.copy() | {'QEMU_LD_PREFIX': '/usr/aarch64-linux-gnu'},
                   attach_tty=False) -> subprocess.CompletedProcess:
    if outer_env is None:
        outer_env = os.environ.copy()
    env_cmd = ['/usr/bin/env'] + [f'{shell_quote(key)}={shell_quote(value)}' for key, value in inner_env.items()]
    run_func = subprocess.call if attach_tty else subprocess.run
    result = run_func(['arch-chroot', chroot_path] + env_cmd + [
        '/bin/bash',
        '-c',
        script,
    ], env=outer_env)
    return result


def create_chroot_user(
    chroot_path: str,
    user='kupfer',
    password='123456',
    groups=['network', 'video', 'audio', 'optical', 'storage', 'input', 'scanner', 'games', 'lp', 'rfkill', 'wheel'],
):
    install_script = f'''
        set -e
        if ! id -u "{user}" >/dev/null 2>&1; then
          useradd -m {user}
        fi
        usermod -a -G {",".join(groups)} {user}
        chown {user}:{user} /home/{user} -R
    '''
    if password:
        install_script += f'echo "{user}:{password}" | chpasswd'
    else:
        install_script += 'echo "Set user password:" && passwd'
    result = run_chroot_cmd([install_script], chroot_path=chroot_path)
    if result.returncode != 0:
        raise Exception('Failed to setup user')


def try_install_packages(packages: list[str], chroot: str) -> dict[str, subprocess.CompletedProcess]:
    """Try installing packages one by one"""
    results = {}
    for pkg in set(packages):
        # Don't check for errors here because there might be packages that are listed as dependencies but are not available on x86_64
        results[pkg] = run_chroot_cmd(f'pacman -Syy --noconfirm --needed {pkg}', chroot)
    return results


def mount_crossdirect(native_chroot: str, target_chroot: str, target_arch: str, host_arch: str = None):
    if host_arch is None:
        host_arch = config.runtime['arch']
    gcc = f'{GCC_HOSTSPECS[host_arch][target_arch]}-gcc'

    native_mount = os.path.join(target_chroot, 'native')
    logging.debug(f'Activating crossdirect in {native_mount}')
    results = try_install_packages(CROSSDIRECT_PKGS + [gcc], native_chroot)
    if results[gcc].returncode != 0:
        logging.debug('Failed to install cross-compiler package {gcc}')
    if results['crossdirect'].returncode != 0:
        raise Exception('Failed to install crossdirect')

    os.makedirs(native_mount, exist_ok=True)

    ld_so = glob(f"{os.path.join('native_chroot', 'usr', 'lib', 'ld-linux-')}*")[0]
    copy(ld_so, os.path.join(target_chroot, 'usr', 'lib'))

    logging.debug(f'Mounting {native_chroot} to {native_mount}')
    result = mount(native_chroot, native_mount)
    if result.returncode != 0:
        raise Exception(f'Failed to mount native chroot {native_chroot} to {native_mount}')


@click.command('chroot')
@click.argument('type', required=False, default='build')
@click.argument('arch', required=False, default=None)
def cmd_chroot(type: str = 'build', arch: str = None, enable_crossdirect=True):
    chroot_path = ''
    if type not in ['base', 'build', 'rootfs']:
        raise Exception('Unknown chroot type: ' + type)

    enforce_wrap()
    if type == 'rootfs':
        if arch:
            name = 'rootfs_' + arch
        else:
            raise Exception('"rootfs" not yet implemented, sorry!')
            # TODO: name = config.get_profile()[...]
        chroot_path = os.path.join(config.get_path('chroots'), name)
        if not os.path.exists(chroot_path):
            raise Exception(f"rootfs {name} doesn't exist")
    else:
        if not arch:
            #TODO: arch = config.get_profile()[...]
            arch = 'aarch64'
        chroot_name = type + '_' + arch
        chroot_path = get_chroot_path(chroot_name)
        if not os.path.exists(os.path.join(chroot_path, 'bin')):
            create_chroot(
                chroot_name,
                arch=arch,
                packages=['base-devel', 'git'],
                extra_repos=get_kupfer_local(arch).repos,
            )
        if type == 'build' and config.file['build']['crossdirect']:
            native_arch = config.runtime['arch']
            native_chroot = create_chroot(
                'build_' + native_arch,
                native_arch,
                packages=['base-devel'] + (CROSSDIRECT_PKGS if enable_crossdirect else []),
                extra_repos=get_kupfer_local(native_arch).repos,
            )
            mount_crossdirect(native_chroot=native_chroot, target_chroot=chroot_path, target_arch=arch)

    cmd = ['arch-chroot', chroot_path, '/bin/bash']
    logging.debug('Starting chroot: ' + repr(cmd))
    subprocess.call(cmd)
