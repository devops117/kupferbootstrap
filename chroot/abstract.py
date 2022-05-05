import atexit
import logging
import os
import subprocess
from copy import deepcopy
from shlex import quote as shell_quote
from typing import Protocol, Union, Optional, Mapping
from uuid import uuid4

from config import config
from constants import Arch, CHROOT_PATHS
from distro.distro import get_base_distro, get_kupfer_local, RepoInfo
from generator import generate_makepkg_conf
from utils import mount, umount, check_findmnt, log_or_exception

from .helpers import BASE_CHROOT_PREFIX, BASIC_MOUNTS, base_chroot_name, make_abs_path


class AbstractChroot(Protocol):
    name: str
    arch: Arch
    path: str
    copy_base: bool
    initialized: bool
    active: bool
    active_mounts: list[str]
    extra_repos: Mapping[str, RepoInfo]
    base_packages: list[str]

    def __init__(
        self,
        name: str,
        arch: Arch,
        copy_base: bool,
        initialize: bool,
        extra_repos: Mapping[str, RepoInfo],
        base_packages: list[str],
        path_override: str = None,
    ):
        pass

    def initialize(self, reset: bool = False, fail_if_initialized: bool = False):
        raise NotImplementedError()

    def activate(self, fail_if_active: bool):
        pass

    def get_path(self, *joins: str):
        pass

    def run_cmd(
        self,
        script: Union[str, list[str]],
        inner_env: dict[str, str],
        outer_env: dict[str, str],
        attach_tty: bool,
        capture_output: bool,
        cwd: str,
        fail_inactive: bool,
        stdout: Optional[int],
    ):
        pass

    def mount_pacman_cache(self, fail_if_mounted: bool):
        pass

    def mount_packages(self, fail_if_mounted: bool):
        pass

    def mount_pkgbuilds(self, fail_if_mounted: bool):
        pass

    def try_install_packages(self, packages: list[str], refresh: bool, allow_fail: bool) -> dict[str, Union[int, subprocess.CompletedProcess]]:
        pass


class Chroot(AbstractChroot):

    def __repr__(self):
        return f'Chroot({self.name})'

    def __init__(
        self,
        name: str,
        arch: Arch,
        copy_base: bool = None,
        initialize: bool = False,
        extra_repos: Mapping[str, RepoInfo] = {},
        base_packages: list[str] = ['base', 'base-devel', 'git'],
        path_override: str = None,
    ):
        self.uuid = uuid4()
        if copy_base is None:
            logging.debug(f'{name}: copy_base is none!')
            copy_base = (name == base_chroot_name(arch))
        self.active = False
        self.initialized = False
        self.active_mounts = list[str]()
        self.name = name
        self.arch = arch
        self.path = path_override or os.path.join(config.get_path('chroots'), name)
        self.copy_base = copy_base
        self.extra_repos = deepcopy(extra_repos)
        self.base_packages = base_packages.copy()
        if initialize:
            self.initialize()
        if self.name.startswith(BASE_CHROOT_PREFIX) and set(get_kupfer_local(self.arch).repos).intersection(set(self.extra_repos)):
            raise Exception(f'Base chroot {self.name} had local repos specified: {self.extra_repos}')

    def create_rootfs(self, reset: bool, pacman_conf_target: str, active_previously: bool):
        raise NotImplementedError()

    def initialize(self, reset: bool = False, fail_if_initialized: bool = False):
        pacman_conf_target = self.get_path('etc/pacman.conf')

        if self.initialized and not reset:
            # chroot must have been initialized already!
            if fail_if_initialized:
                raise Exception(f"Chroot {self.name} is already initialized, this seems like a bug")
            return

        active_previously = self.active
        self.deactivate_core()

        self.create_rootfs(reset, pacman_conf_target, active_previously)

    def get_path(self, *joins: str) -> str:
        if joins:
            joins = (joins[0].lstrip('/'),) + joins[1:]

        return os.path.join(self.path, *joins)

    def mount(
        self,
        absolute_source: str,
        relative_destination: str,
        options=['bind'],
        fs_type: str = None,
        fail_if_mounted: bool = True,
        makedir: bool = True,
        strict_cache_consistency: bool = False,
    ):
        """returns the absolute path `relative_target` was mounted at"""

        def log_or_exc(msg):
            log_or_exception(strict_cache_consistency, msg, log_level=logging.ERROR)

        relative_destination = relative_destination.lstrip('/')
        absolute_destination = self.get_path(relative_destination)
        pseudo_absolute = make_abs_path(relative_destination)
        if check_findmnt(absolute_destination):
            if pseudo_absolute not in self.active_mounts:
                raise Exception(f'{self.name}: We leaked the mount for {pseudo_absolute} ({absolute_destination}).')
            elif fail_if_mounted:
                raise Exception(f'{self.name}: {absolute_destination} is already mounted')
            logging.debug(f'{self.name}: {absolute_destination} already mounted. Skipping.')
        else:
            if pseudo_absolute in self.active_mounts:
                log_or_exc(f'{self.name}: Mount {pseudo_absolute} was in active_mounts but not actually mounted. ({absolute_destination})')
            if makedir and os.path.isdir(absolute_source):
                os.makedirs(absolute_destination, exist_ok=True)
            result = mount(absolute_source, absolute_destination, options=options, fs_type=fs_type, register_unmount=False)
            if result.returncode != 0:
                raise Exception(f'{self.name}: failed to mount {absolute_source} to {absolute_destination}')
            logging.debug(f'{self.name}: {absolute_source} successfully mounted to {absolute_destination}.')
            self.active_mounts += [pseudo_absolute]
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

    def run_cmd(
        self,
        script: Union[str, list[str]],
        inner_env: dict[str, str] = {},
        outer_env: dict[str, str] = os.environ.copy() | {'QEMU_LD_PREFIX': '/usr/aarch64-linux-gnu'},
        attach_tty: bool = False,
        capture_output: bool = False,
        cwd: Optional[str] = None,
        fail_inactive: bool = True,
        stdout: Optional[int] = None,
    ) -> Union[int, subprocess.CompletedProcess]:
        if not self.active and fail_inactive:
            raise Exception(f'Chroot {self.name} is inactive, not running command! Hint: pass `fail_inactive=False`')
        if outer_env is None:
            outer_env = os.environ.copy()
        env_cmd = ['/usr/bin/env'] + [f'{shell_quote(key)}={shell_quote(value)}' for key, value in inner_env.items()]
        kwargs: dict = {
            'env': outer_env,
        }
        if not attach_tty:
            kwargs |= {'stdout': stdout} if stdout else {'capture_output': capture_output}

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
        if attach_tty:
            return subprocess.call(cmd, **kwargs)
        else:
            return subprocess.run(cmd, **kwargs)

    def mount_pkgbuilds(self, fail_if_mounted: bool = False) -> str:
        return self.mount(
            absolute_source=config.get_path('pkgbuilds'),
            relative_destination=CHROOT_PATHS['pkgbuilds'].lstrip('/'),
            fail_if_mounted=fail_if_mounted,
        )

    def mount_pacman_cache(self, fail_if_mounted: bool = False) -> str:
        arch_cache = os.path.join(config.get_path('pacman'), self.arch)
        rel_target = os.path.join(CHROOT_PATHS['pacman'].lstrip('/'), self.arch)
        for dir in [arch_cache, self.get_path(rel_target)]:
            os.makedirs(dir, exist_ok=True)
        return self.mount(
            arch_cache,
            rel_target,
            fail_if_mounted=fail_if_mounted,
        )

    def mount_packages(self, fail_if_mounted: bool = False) -> str:
        return self.mount(
            absolute_source=config.get_path('packages'),
            relative_destination=CHROOT_PATHS['packages'].lstrip('/'),
            fail_if_mounted=fail_if_mounted,
        )

    def write_makepkg_conf(self, target_arch: Arch, cross_chroot_relative: Optional[str], cross: bool = True) -> str:
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

    def write_pacman_conf(self, check_space: bool = False):
        os.makedirs(self.get_path('/etc'), exist_ok=True)
        conf_text = get_base_distro(self.arch).get_pacman_conf(self.extra_repos, check_space=check_space)
        with open(self.get_path('etc/pacman.conf'), 'w') as file:
            file.write(conf_text)

    def create_user(
        self,
        user='kupfer',
        password='123456',
        groups=['network', 'video', 'audio', 'optical', 'storage', 'input', 'scanner', 'games', 'lp', 'rfkill', 'wheel'],
    ):
        user = user or 'kupfer'
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
            install_script += f'echo "Set user password:" && passwd {user}'
        result = self.run_cmd(install_script)
        if result.returncode != 0:
            raise Exception('Failed to setup user')

    def try_install_packages(
        self,
        packages: list[str],
        refresh: bool = False,
        allow_fail: bool = True,
    ) -> dict[str, Union[int, subprocess.CompletedProcess]]:
        """Try installing packages, fall back to installing one by one"""
        results = {}
        if refresh:
            results['refresh'] = self.run_cmd('pacman -Syy --noconfirm')
        cmd = "pacman -S --noconfirm --needed --overwrite='/*'"
        result = self.run_cmd(f'{cmd} -y {" ".join(packages)}')
        assert isinstance(result, subprocess.CompletedProcess)
        results |= {package: result for package in packages}
        if result.returncode != 0 and allow_fail:
            results = {}
            logging.debug('Falling back to serial installation')
            for pkg in set(packages):
                # Don't check for errors here because there might be packages that are listed as dependencies but are not available on x86_64
                results[pkg] = self.run_cmd(f'{cmd} {pkg}')
        return results


chroots: dict[str, Chroot] = {}


def get_chroot(
    name: str,
    initialize: bool = False,
    activate: bool = False,
    fail_if_exists: bool = False,
    extra_repos: Optional[Mapping[str, RepoInfo]] = None,
    default: Chroot = None,
) -> Chroot:
    global chroots
    if default and name not in chroots:
        logging.debug(f'Adding chroot {name} to chroot map: {default.uuid}')
        chroots[name] = default
    else:
        existing = chroots[name]
        if fail_if_exists:
            raise Exception(f'chroot {name} already exists: {existing.uuid}')
        logging.debug(f"returning existing chroot {name}: {existing.uuid}")
    chroot = chroots[name]
    if extra_repos is not None:
        chroot.extra_repos = dict(extra_repos)  # copy to new dict
    if initialize:
        chroot.initialize()
    if activate:
        chroot.activate(fail_if_active=False)
    return chroot
