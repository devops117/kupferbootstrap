import click
import logging
import subprocess
import os
import atexit
from glob import glob
from shutil import rmtree

from config import config
from distro import get_base_distro, RepoInfo
from shlex import quote as shell_quote
from utils import mount, umount
from distro import get_kupfer_local
from wrapper import enforce_wrap
from constants import Arch, GCC_HOSTSPECS, CROSSDIRECT_PKGS, BASE_PACKAGES
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
    '/etc/resolv.conf': {
        'src': os.path.realpath('/etc/resolv.conf'),
        'type': None,
        'options': ['bind'],
    },
}

Chroot = None

chroots: dict[str, Chroot] = {}


def make_abs_path(path: str) -> str:
    """Simply ensures the path string starts with a '/'. Does no disk modifications!"""
    return '/' + path.lstrip('/')


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
        logging.debug(f'Adding chroot {name} to chroot map')
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
    if kwargs.pop('initialize', False):
        logging.debug('get_base_chroot: Had to remove "initialize" from args. This indicates a bug.')
    return get_chroot(name, **kwargs, initialize=False, default=default)


def get_build_chroot(arch: Arch, **kwargs) -> Chroot:
    name = build_chroot_name(arch)
    if 'extra_repos' in kwargs:
        raise Exception('extra_repos!')
    default = Chroot(name, arch, initialize=False, copy_base=True, extra_repos=get_kupfer_local(arch).repos)
    chroot = get_chroot(name, **kwargs, default=default)
    return chroot


def get_device_chroot(device: str, flavour: str, arch: Arch, packages: list[str] = BASE_PACKAGES, extra_repos={}, **kwargs) -> Chroot:
    name = f'rootfs_{device}-{flavour}'
    default = Chroot(name, arch, initialize=False, copy_base=False, base_packages=packages, extra_repos=extra_repos)
    return get_chroot(name, **kwargs, default=default)


class Chroot:
    """Do not instantiate directly, use get_chroot() externally!"""
    name: str
    full_path: str
    arch: Arch
    initialized: bool = False
    active: bool = False
    active_mounts: list[str] = []
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
        path_override: str = None,
    ):
        if copy_base is None:
            logging.debug(f'{name}: copy_base is none!')
            copy_base = (name == base_chroot_name(arch))
        self.name = name
        self.arch = arch
        self.path = os.path.join(config.get_path('chroots'), name) if not path_override else path_override
        self.copy_base = copy_base
        self.extra_repos |= extra_repos
        self.base_packages = base_packages
        if initialize:
            self.initialize()

    # TODO: when we go multithreaded, activate() and initialize() probably need a reader-writer lock

    def get_path(self, *joins) -> str:
        if joins:
            joins = (joins[0].lstrip('/'),) + joins[1:]

        return os.path.join(self.path, *joins)

    def initialize(
        self,
        reset: bool = False,
        fail_if_initialized: bool = False,
    ):
        pacman_conf_target = self.get_path('etc/pacman.conf')

        if self.initialized and not reset:
            # chroot must have been initialized already!
            if fail_if_initialized:
                raise Exception(f"Chroot {self.name} is already initialized, this seems like a bug")
            return

        active_previously = self.active
        self.deactivate_core()

        if self.copy_base:
            if reset or not os.path.exists(self.get_path('usr/bin')):
                base_chroot = get_base_chroot(self.arch)
                if base_chroot == self:
                    raise Exception('base_chroot == self, bailing out. this is a bug')
                base_chroot.initialize()
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

            else:
                logging.debug(f'{self.name}: Reusing existing installation')

            if set(get_kupfer_local(self.arch).repos).intersection(set(self.extra_repos)):
                self.mount_packages()

            self.mount_pacman_cache()
            self.write_pacman_conf()
            self.initialized = True
            self.activate()
            self.try_install_packages(self.base_packages, refresh=True, allow_fail=False)
            self.deactivate_core()

            # patch makepkg
            with open(self.get_path('/usr/bin/makepkg'), 'r') as file:
                data = file.read()
            data = data.replace('EUID == 0', 'EUID == -1')
            with open(self.get_path('/usr/bin/makepkg'), 'w') as file:
                file.write(data)

            # configure makepkg
            self.write_makepkg_conf(self.arch, cross_chroot_relative=None, cross=False)
        else:
            # base chroot
            if reset:
                logging.info(f'Resetting {self.name}')
                for dir in glob(os.join(self.path, '*')):
                    rmtree(dir)

            self.write_pacman_conf()

            logging.info(f'Pacstrapping chroot {self.name}: {", ".join(self.base_packages)}')

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
            self.initialized = True

        if active_previously:
            self.activate()

    def mount(
        self,
        absolute_source: str,
        relative_destination: str,
        options=['bind'],
        fs_type: str = None,
        fail_if_mounted: bool = True,
        makedir: bool = True,
    ):
        """returns the absolute path `relative_target` was mounted at"""
        relative_destination = relative_destination.lstrip('/')
        absolute_destination = self.get_path(relative_destination)
        if os.path.ismount(absolute_destination):
            if fail_if_mounted:
                raise Exception(f'{self.name}: {absolute_destination} is already mounted')
            logging.debug(f'{self.name}: {absolute_destination} already mounted. Skipping.')
        else:
            if makedir and os.path.isdir(absolute_source):
                os.makedirs(absolute_destination, exist_ok=True)
            result = mount(absolute_source, absolute_destination, options=options, fs_type=fs_type, register_unmount=False)
            if result.returncode != 0:
                raise Exception(f'{self.name}: failed to mount {absolute_source} to {absolute_destination}')
            logging.debug(f'{self.name}: {absolute_source} successfully mounted to {absolute_destination}.')
            self.active_mounts += [make_abs_path(relative_destination)]
            atexit.register(self.deactivate)
        return absolute_destination

    def umount(self, relative_path: str):
        if not self:
            return
        path = self.get_path(relative_path)
        result = umount(path)
        if result.returncode == 0 and make_abs_path(relative_path) in self.active_mounts:
            self.active_mounts.remove(relative_path)
        return result

    def umount_many(self, relative_paths: list[str]):
        # make sure paths start with '/'. Important: also copies the collection and casts to list, which will be sorted!
        mounts = [make_abs_path(path) for path in relative_paths]
        mounts.sort(reverse=True)
        for mount in mounts:
            if mount == '/proc':
                continue
            self.umount(mount)
        if '/proc' in mounts:
            self.umount('/proc')

    def activate(self, fail_if_active: bool = False):
        """mount /dev, /sys and /proc"""
        if self.active and fail_if_active:
            raise Exception(f'chroot {self.name} already active!')
        if not self.initialized:
            self.initialize(fail_if_initialized=False)
        for dst, opts in BASIC_MOUNTS.items():
            self.mount(opts['src'], dst, fs_type=opts['type'], options=opts['options'], fail_if_mounted=fail_if_active)
        self.active = True

    def deactivate_core(self):
        self.umount_many(BASIC_MOUNTS.keys())
        # TODO: so this is a weird one. while the basic bind-mounts get unmounted
        # additional mounts like crossdirect are intentionally left intact. Is such a chroot still `active` afterwards?
        self.active = False

    def deactivate(self, fail_if_inactive: bool = False):
        if not self.active:
            if fail_if_inactive:
                raise Exception(f"Chroot {self.name} not activated, can't deactivate!")
        self.umount_many(self.active_mounts)
        self.active = False

    def run_cmd(self,
                script: str,
                inner_env: dict[str, str] = {},
                outer_env: dict[str, str] = os.environ.copy() | {'QEMU_LD_PREFIX': '/usr/aarch64-linux-gnu'},
                attach_tty: str = False,
                capture_output: str = False,
                cwd: str = None,
                fail_inactive: bool = True) -> subprocess.CompletedProcess:
        if not self.active and fail_inactive:
            raise Exception(f'Chroot {self.name} is inactive, not running command! Hint: pass `fail_inactive=False`')
        if outer_env is None:
            outer_env = os.environ.copy()
        env_cmd = ['/usr/bin/env'] + [f'{shell_quote(key)}={shell_quote(value)}' for key, value in inner_env.items()]
        run_func = subprocess.call if attach_tty else subprocess.run
        kwargs = {
            'env': outer_env,
        }
        if not attach_tty:
            kwargs |= {'capture_output': capture_output}

        if not isinstance(script, str) and isinstance(script, list):
            script = ' '.join(script)
        if cwd:
            script = f"cd {shell_quote(cwd)} && ( {script} )"
        cmd = ['chroot', self.path] + env_cmd + [
            '/bin/bash',
            '-c',
            script,
        ]
        logging.debug(f'{self.name}: Running cmd: "{cmd}"')
        result = run_func(cmd, **kwargs)
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
        results = {}
        if refresh:
            results['refresh'] = self.run_cmd('pacman -Syy --noconfirm')
        cmd = 'pacman -S --noconfirm --needed'
        result = self.run_cmd(f'{cmd} {" ".join(packages)}')
        results |= {package: result for package in packages}
        if result.returncode != 0 and allow_fail:
            results = {}
            logging.debug('Falling back to serial installation')
            for pkg in set(packages):
                # Don't check for errors here because there might be packages that are listed as dependencies but are not available on x86_64
                results[pkg] = self.run_cmd(f'{cmd} {pkg}')
        return results

    def mount_rootfs(self, source_path: str, fs_type: str = None, options: list[str] = ['loop'], allow_overlay: bool = False):
        if self.active:
            raise Exception(f'{self.name}: Chroot is marked as active, not mounting a rootfs over it.')
        if not os.path.exists(source_path):
            raise Exception('Source does not exist')
        if not allow_overlay:
            if self.active_mounts:
                raise Exception(f'{self.name}: Chroot has submounts active: {self.active_mounts}')
            if os.path.ismount(self.path):
                raise Exception(f'{self.name}: There is already something mounted at {self.path}, not mounting over it.')
            if os.path.exists(os.path.join(self.path, 'usr/bin')):
                raise Exception(f'{self.name}: {self.path}/usr/bin exists, not mounting over existing rootfs.')
        os.makedirs(self.path, exist_ok=True)
        atexit.register(self.deactivate)
        self.mount(source_path, '/', fs_type=fs_type, options=options)

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
        rustc = os.path.join(native_chroot.path, 'usr/lib/crossdirect', target_arch, 'rustc')
        if os.path.exists(rustc):
            logging.debug('Disabling crossdirect rustc')
            os.unlink(rustc)

        os.makedirs(native_mount, exist_ok=True)
        logging.debug(f'Mounting {native_chroot.name} to {native_mount}')
        self.mount(native_chroot.path, 'native')
        return native_mount

    def mount_pkgbuilds(self, fail_if_mounted: bool = False) -> str:
        pkgbuilds = config.get_path('pkgbuilds')
        return self.mount(absolute_source=pkgbuilds, relative_destination=pkgbuilds.lstrip('/'), fail_if_mounted=fail_if_mounted)

    def mount_pacman_cache(self, fail_if_mounted: bool = False) -> str:
        arch_cache = os.path.join(config.get_path('pacman'), self.arch)
        rel_target = os.path.join('var/cache/pacman', self.arch)
        for dir in [arch_cache, self.get_path(rel_target)]:
            os.makedirs(dir, exist_ok=True)
        return self.mount(arch_cache, rel_target, fail_if_mounted=fail_if_mounted)

    def mount_packages(self, fail_if_mounted: bool = False) -> str:
        packages = config.get_path('packages')
        return self.mount(absolute_source=packages, relative_destination=packages.lstrip('/'), fail_if_mounted=fail_if_mounted)

    def mount_crosscompile(self, foreign_chroot: Chroot):
        mount_dest = os.path.join('chroot', os.path.basename(foreign_chroot.path))
        os.makedirs(os.path.join(self.path, mount_dest), exist_ok=True)
        return self.mount(absolute_source=foreign_chroot.path, relative_destination=mount_dest)

    def write_makepkg_conf(self, target_arch: Arch, cross_chroot_relative: str, cross: bool = True) -> str:
        """
        Generate a `makepkg.conf` or `makepkg_cross_$arch.conf` file in /etc.
        If `cross` is set makepkg will be configured to crosscompile for the foreign chroot at `cross_chroot_relative`
        Returns the relative (to `self.path`) path to the written file, e.g. `etc/makepkg_cross_aarch64.conf`.
        """
        makepkg_cross_conf = generate_makepkg_conf(target_arch, cross=cross, chroot=cross_chroot_relative)
        filename = 'makepkg' + (f'_cross_{target_arch}' if cross else '') + '.conf'
        makepkg_conf_path_relative = os.path.join('etc', filename)
        makepkg_conf_path = os.path.join(self.path, makepkg_conf_path_relative)
        with open(makepkg_conf_path, 'w') as f:
            f.write(makepkg_cross_conf)
        return makepkg_conf_path_relative

    def write_pacman_conf(self):
        os.makedirs(self.get_path('/etc'), exist_ok=True)
        conf_text = get_base_distro(self.arch).get_pacman_conf(self.extra_repos)
        with open(self.get_path('etc/pacman.conf'), 'w') as file:
            file.write(conf_text)


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
