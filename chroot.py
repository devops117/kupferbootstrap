import click
import logging
import subprocess
import os
import atexit
from config import config
from distro import get_base_distro, RepoInfo
from shlex import quote as shell_quote
from utils import mount, umount
from distro import get_kupfer_local
from wrapper import enforce_wrap
from constants import Arch, GCC_HOSTSPECS, CROSSDIRECT_PKGS
from glob import glob
from generator import generate_makepkg_conf

BIND_BUILD_DIRS = 'BINDBUILDDIRS'
BASE_CHROOT_PREFIX = 'base_'
BUILD_CHROOT_PREFIX = 'build_'

# inspired by arch-chroot
# order of these matters!
BASIC_MOUNTS = {
    '/proc': {
        'src': 'proc',
        'type': 'proc',
        'options': ['nosuid,noexec,nodev']
    },
    '/sys': {
        'src': 'sys',
        'type': 'sysfs',
        'options': ['nosuid,noexec,nodev,ro'],
    },
    '/dev': {
        'src': 'udev',
        'type': 'devtmpfs',
        'options': ['mode=0755,nosuid'],
    },
    '/dev/pts': {
        'src': 'devpts',
        'type': 'devpts',
        'options': ['mode=0620,gid=5,nosuid,noexec'],
    },
    '/dev/shm': {
        'src': 'shm',
        'type': 'tmpfs',
        'options': ['mode=1777,nosuid,nodev'],
    },
    '/run': {
        'src': '/run',
        'type': 'tmpfs',
        'options': ['bind'],
    },
}

Chroot = None

chroots: dict[str, Chroot] = {}


def get_chroot_path(chroot_name, override_basepath: str = None) -> str:
    base_path = config.get_path('chroots') if not override_basepath else override_basepath
    return os.path.join(base_path, chroot_name)


def base_chroot_name(arch: Arch):
    return BASE_CHROOT_PREFIX + arch


def build_chroot_name(arch: Arch):
    return BUILD_CHROOT_PREFIX + arch


def get_chroot(
    name: str,
    initialize: bool = False,
    activate: bool = False,
    fail_if_exists: bool = False,
    default: Chroot = None,
) -> Chroot:
    global chroots
    if default and name not in chroots:
        chroots[name] = default
    elif fail_if_exists:
        raise Exception(f'chroot {name} already exists')
    chroot = chroots[name]
    if initialize:
        chroot.initialize()
    if activate:
        chroot.activate(fail_if_active=False)
    return chroot


def get_base_chroot(arch: Arch, **kwargs) -> Chroot:
    name = base_chroot_name(arch)
    default = Chroot(name, arch, initialize=False, copy_base=False)
    return get_chroot(**kwargs, default=default)


def get_build_chroot(arch: Arch, extra_repos=None, **kwargs) -> Chroot:
    name = base_chroot_name(arch)
    extra_repos = get_kupfer_local(arch).repos if extra_repos is None else extra_repos
    args = {'extra_repos': extra_repos}
    if kwargs:
        args |= kwargs
    default = Chroot(name, arch, initialize=False)
    return get_chroot(**args, default=default)


def get_device_chroot(name: str, arch: Arch, **kwargs) -> Chroot:
    default = Chroot(name, arch, initialize=False)
    return get_chroot(**kwargs, default=default)


class Chroot:
    """Do not instantiate directly, use get_chroot() externally!"""
    name: str
    full_path: str
    arch: Arch
    initialized: bool = False
    active: bool = False
    active_mounts = []
    copy_base: bool = True
    extra_repos: dict[str, RepoInfo] = {}
    base_packages: list[str] = ['base']

    def __repr__(self):
        return f'Chroot({self.name})'

    def __init__(
        self,
        name: str,
        arch: Arch,
        copy_base: bool = None,
        initialize: bool = False,
        extra_repos: dict[str, RepoInfo] = {},
        base_packages: list[str] = ['base', 'base-devel', 'git'],
    ):
        if copy_base is None:
            copy_base = (name == base_chroot_name(arch))
        self.name = name
        self.arch = arch
        self.path = os.path.join(config.get_path('chroots'), name)
        self.copy_base = copy_base
        self.extra_repos |= extra_repos
        if initialize:
            self.initialize()

    # TODO: when we go multithreaded, activate() and initialize() probably need a reader-writer lock

    def get_path(self, *joins) -> str:
        if joins:
            joins[0] = joins[0].lstrip('/')
        return os.path.join(self.path, *joins)

    def initialize(
        self,
        fail_if_initialized: bool = False,
    ):
        base_distro = get_base_distro(self.arch)
        pacman_conf_target = self.get_path('etc/pacman.conf')

        if self.initialized:
            # chroot must have been initialized already!
            if fail_if_initialized:
                raise Exception(f"Chroot {self.name} is already initialized, this seems like a bug")
            return

        if self.copy_base:
            base_chroot = get_base_chroot(self.arch, initialize=True)
            logging.info(f'Copying {base_chroot.name} chroot to {self.name}')
            result = subprocess.run([
                'rsync',
                '-a',
                '--delete',
                '-q',
                '-W',
                '-x',
                '--exclude',
                'pkgbuilds',
                '--exclude',
                'prebuilts',
                f'{base_chroot.path}/',
                f'{self.path}/',
            ])
            if result.returncode != 0:
                raise Exception(f'Failed to copy {base_chroot.name} to {self.name}')

            # patch makepkg
            with open(self.get_path('/usr/bin/makepkg'), 'r') as file:
                data = file.read()
            data = data.replace('EUID == 0', 'EUID == -1')
            with open(self.get_path('/usr/bin/makepkg'), 'w') as file:
                file.write(data)

            # configure makepkg
            data = generate_makepkg_conf(self.arch, cross=False)
            data = data.replace('xz -c', 'xz -T0 -c')
            data = data.replace(' check ', ' !check ')
            with open(self.get_path('/etc/makepkg.conf'), 'w') as file:
                file.write(data)

        os.makedirs(self.get_path('/etc'), exist_ok=True)

        conf_text = base_distro.get_pacman_conf(self.extra_repos)
        with open(pacman_conf_target, 'w') as file:
            file.write(conf_text)

        logging.info(f'Installing packages to {self.name}: {", ".join(self.base_packages)}')

        result = subprocess.run([
            'pacstrap',
            '-C',
            pacman_conf_target,
            '-c',
            '-G',
            self.path,
        ] + self.base_packages + [
            '--needed',
            '--overwrite=*',
            '-yyuu',
        ])
        if result.returncode != 0:
            raise Exception(f'Failed to initialize chroot "{self.name}"')

    def mount(
        self,
        absolute_source: str,
        relative_destination: str,
        options=['bind'],
        fs_type: str = None,
        fail_if_mounted: bool = True,
    ):
        """returns the absolute path `relative_target` was mounted at"""
        relative_destination = relative_destination.lstrip('/')
        absolute_destination = self.get_path(relative_destination)
        if relative_destination in self.active_mounts or os.path.ismount(absolute_destination):
            if fail_if_mounted:
                raise Exception(f'{self.name}: {relative_destination} is already mounted')
            logging.warning(f'{self.name}: {relative_destination} already mounted. Skipping.')
        else:
            result = mount(absolute_source, absolute_destination, options=options, fs_type=fs_type, register_unmount=False)
            if result.returncode != 0:
                raise Exception(f'{self.name}: failed to mount {absolute_source} to {relative_destination}')
            self.active_mounts += relative_destination
            atexit.register(self.deactivate)
        return absolute_destination

    def umount(self, relative_path: str):
        if not self:
            return
        path = self.get_path(relative_path)
        result = umount(path)
        if result.returncode == 0:
            self.active_mounts.remove(relative_path)
        return result

    def activate(self, fail_if_active: bool = False):
        """mount /dev, /sys and /proc"""
        if self.active:
            if fail_if_active:
                raise Exception(f'chroot {self.name} already active!')
            return
        if not self.initialised:
            self.init(fail_if_active=False)
        for dst, opts in BASIC_MOUNTS.items():
            self.mount(
                opts['src'],
                dst,
                fs_type=opts['type'],
                options=opts['options']
            )
        self.active = True

    def deactivate(self, fail_if_inactive: bool = False):
        if not self.active:
            if fail_if_inactive:
                raise Exception(f"Chroot {self.name} not activated, can't deactivate!")
        for mount in self.active_mounts[::-1]:
            if mount == 'proc':
                continue
            self.umount(mount)
        self.umount('proc')
        self.active = False

    def install_packages(packages: list[str]):
        pass

    def run_cmd(self,
                script: str,
                inner_env: dict[str, str] = {},
                outer_env: dict[str, str] = os.environ.copy() | {'QEMU_LD_PREFIX': '/usr/aarch64-linux-gnu'},
                attach_tty=False) -> subprocess.CompletedProcess:
        self.activate()
        if outer_env is None:
            outer_env = os.environ.copy()
        env_cmd = ['/usr/bin/env'] + [f'{shell_quote(key)}={shell_quote(value)}' for key, value in inner_env.items()]
        run_func = subprocess.call if attach_tty else subprocess.run
        result = run_func(['chroot', self.path] + env_cmd + [
            '/bin/bash',
            '-c',
            script,
        ], env=outer_env)
        return result

    def create_user(
        self,
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
        result = self.run_cmd(install_script)
        if result.returncode != 0:
            raise Exception('Failed to setup user')

    def try_install_packages(self, packages: list[str], refresh: bool = False, allow_fail: bool = True) -> dict[str, subprocess.CompletedProcess]:
        """Try installing packages, fall back to installing one by one"""
        if refresh:
            self.run_cmd('pacman -Syy --noconfirm')
        cmd = 'pacman -S --noconfirm --needed'
        result = self.run_cmd(f'{cmd} {" ".join(packages)}')
        results = {package: result for package in packages}
        if result.returncode != 0 and allow_fail:
            results = {}
            logging.debug('Falling back to serial installation')
            for pkg in set(packages):
                # Don't check for errors here because there might be packages that are listed as dependencies but are not available on x86_64
                results[pkg] = self.run_cmd(f'{cmd} {pkg}')
        return results

    def mount_crossdirect(self, native_chroot: Chroot = None):
        """
        mount `native_chroot` at `target_chroot`/native
        returns the absolute path that `native_chroot` has been mounted at.
        """
        target_arch = self.arch
        if not native_chroot:
            native_chroot = get_build_chroot(config.runtime['arch'])
        host_arch = native_chroot.arch
        hostspec = GCC_HOSTSPECS[host_arch][target_arch]
        cc = f'{hostspec}-cc'
        gcc = f'{hostspec}-gcc'

        native_mount = os.path.join(self.path, 'native')
        logging.debug(f'Activating crossdirect in {native_mount}')
        results = native_chroot.try_install_packages(CROSSDIRECT_PKGS + [gcc], refresh=True, allow_fail=False)
        if results[gcc].returncode != 0:
            logging.debug('Failed to install cross-compiler package {gcc}')
        if results['crossdirect'].returncode != 0:
            raise Exception('Failed to install crossdirect')

        cc_path = os.path.join(native_chroot.path, 'usr', 'bin', cc)
        target_lib_dir = os.path.join(self.path, 'lib64')
        # TODO: crosscompiler weirdness, find proper fix for /include instead of /usr/include
        target_include_dir = os.path.join(self.path, 'include')

        for target, source in {cc_path: gcc, target_lib_dir: 'lib', target_include_dir: 'usr/include'}.items():
            if not os.path.exists(target):
                logging.debug(f'Symlinking {source} at {target}')
                os.symlink(source, target)
        ld_so = os.path.basename(glob(f"{os.path.join(native_chroot.path, 'usr', 'lib', 'ld-linux-')}*")[0])
        ld_so_target = os.path.join(target_lib_dir, ld_so)
        if not os.path.islink(ld_so_target):
            os.symlink(os.path.join('/native', 'usr', 'lib', ld_so), ld_so_target)
        else:
            logging.debug('ld-linux.so symlink already exists, skipping for {target_chroot.name}')

        # TODO: find proper fix
        logging.debug('Disabling crossdirect rustc')
        os.unlink(os.path.join(native_chroot.path, 'usr/lib/crossdirect', target_arch, 'rustc'))

        os.makedirs(native_mount, exist_ok=True)
        logging.debug(f'Mounting {native_chroot.name} to {native_mount}')
        result = self.mount(native_chroot, native_mount)
        if result.returncode != 0:
            raise Exception(f'Failed to mount native chroot {native_chroot.name} to {native_mount}')
        return native_mount

    def mount_pkgbuilds(self) -> str:
        packages = config.get_path('pkgbuilds')
        return self.mount(absolute_source=packages, relative_destination=packages.lstrip('/'))

    def mount_pacman_cache(self) -> str:
        return self.mount(config.get_path('pacman'), '/var/cache/pacman')

    def mount_packages(self) -> str:
        packages = config.get_package_dir(self.arch)
        return self.mount(absolute_source=packages, relative_destination=packages.lstrip('/'))

    def write_cross_makepkg_conf(self, target_arch: str, target_chroot_relative: str, cross: bool = True) -> str:
        """
        Generate a makepkg_cross_$arch.conf file in /etc, building for `target_chroot_relative`
        Returns the relative (to `self.path`) path to written file, e.g. `etc/makepkg_cross_aarch64.conf`.
        """
        makepkg_cross_conf = generate_makepkg_conf(target_arch, cross=cross, chroot=target_chroot_relative)
        makepkg_conf_path_relative = os.path.join('etc', f'makepkg_cross_{target_arch}.conf')
        makepkg_conf_path = os.path.join(self.path, makepkg_conf_path_relative)
        with open(makepkg_conf_path, 'w') as f:
            f.write(makepkg_cross_conf)
        return makepkg_conf_path_relative


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
            raise Exception('"rootfs" without args not yet implemented, sorry!')
            # TODO: name = config.get_profile()[...]
        chroot_path = os.path.join(config.get_path('chroots'), name)
        if not os.path.exists(chroot_path):
            raise Exception(f"rootfs {name} doesn't exist")
    else:
        if not arch:
            #TODO: arch = config.get_profile()[...]
            arch = 'aarch64'
        if type == 'base':
            chroot = get_build_chroot(arch)
            if not os.path.exists(os.path.join(chroot.path, 'bin')):
                chroot.init()
            chroot.initialized = True
        elif type == 'build':
            chroot = get_build_chroot(arch)
            if not os.path.exists(os.path.join(chroot.path, 'bin')):
                chroot.init()
            chroot.initialized = True
            if config.file['build']['crossdirect'] and enable_crossdirect:
                chroot.mount_crossdirect()
        else:
            raise Exception('Really weird bug')

    logging.debug(f'Starting shell in {chroot.name}:')
    chroot.run_cmd('bash', attach_tty=True)
