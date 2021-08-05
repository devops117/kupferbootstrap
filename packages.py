from logger import *
import atexit
import click
import logging
import multiprocessing
import os
import shutil
import subprocess
from chroot import create_chroot

makepkg_env = os.environ.copy() | {
    'LANG': 'C',
    'MAKEFLAGS': f'-j{multiprocessing.cpu_count()}',
}

makepkg_cross_env = makepkg_env | {
     'PACMAN': '/app/bin/pacman_aarch64'
}

makepkg_cmd = ['makepkg',
               '--config', '/app/src/makepkg.conf',
               '--noconfirm',
               '--ignorearch',
               '--needed']

pacman_cmd = ['pacman',
              '--noconfirm',
              '--overwrite=*',
              '--needed', ]


class Package:
    name = ''
    names = []
    depends = []
    local_depends = None
    repo = ''
    mode = ''
    has_pkgver = False

    def __init__(self, path: str) -> None:
        self.path = path
        self._loadinfo()

    def _loadinfo(self):
        result = subprocess.run(makepkg_cmd+['--printsrcinfo'],
                                cwd=self.path,
                                stdout=subprocess.PIPE)
        lines = result.stdout.decode('utf-8').split('\n')
        names = []
        depends = []
        for line in lines:
            if line.startswith('\tpkgname'):
                self.name = line.split(' = ')[1]
                names.append(self.name)
            if line.startswith('pkgbase') or line.startswith('\tprovides'):
                names.append(line.split(' = ')[1])
            if line.startswith('\tdepends') or line.startswith('\tmakedepends') or line.startswith('\tcheckdepends') or line.startswith('\toptdepends'):
                depends.append(line.split(' = ')[1].split('=')[0])
        self.names = list(set(names))
        self.depends = list(set(depends))

        self.repo = self.path.split('/')[0]

        mode = ''
        with open(os.path.join(self.path, 'PKGBUILD'), 'r') as file:
            for line in file.read().split('\n'):
                if line.startswith('_mode='):
                    mode = line.split('=')[1]
                    break
        if mode not in ['host', 'cross']:
            logging.fatal(
                f'Package {self.path} has an invalid mode configured: \'{mode}\'')
            exit(1)
        self.mode = mode

        has_pkgver = False
        with open(os.path.join(self.path, 'PKGBUILD'), 'r') as file:
            for line in file.read().split('\n'):
                if line.startswith('pkgver()'):
                    has_pkgver = True
                    break
        self.has_pkgver = has_pkgver


def check_prebuilts():
    if not os.path.exists('prebuilts'):
        os.makedirs('prebuilts')
    for repo in ['main', 'device']:
        if not os.path.exists(os.path.join('prebuilts', repo)):
            os.makedirs(os.path.join('prebuilts', repo))
        for ext1 in ['db', 'files']:
            for ext2 in ['', '.tar.xz']:
                if not os.path.exists(os.path.join('prebuilts', repo, f'{repo}.{ext1}{ext2}')):
                    result = subprocess.run(['tar',
                                            '-czf',
                                             f'{repo}.{ext1}{ext2}',
                                             '-T', '/dev/null'],
                                            cwd=os.path.join('prebuilts', repo))
                    if result.returncode != 0:
                        logging.fatal('Failed create prebuilt repos')
                        exit(1)


def setup_chroot(chroot_path='/chroot/root'):
    logging.info('Initializing root chroot')
    create_chroot(chroot_path, packages=['base-devel'], pacman_conf='/app/src/pacman.conf', extra_repos={'main': {'Server': 'file:///src/prebuilts/main'}, 'device': {'Server': 'file:///src/prebuilts/device'}})

    logging.info('Updating root chroot')
    result = subprocess.run(pacman_cmd +
                            ['-Syuu',
                             '--root', chroot_path,
                             '--arch', 'aarch64',
                             '--config', chroot_path+'/etc/pacman.conf'])
    if result.returncode != 0:
        logging.fatal('Failed to update root chroot')
        exit(1)

    with open('/chroot/root/usr/bin/makepkg', 'r') as file:
        data = file.read()
    data = data.replace('EUID == 0', 'EUID == -1')
    with open('/chroot/root/usr/bin/makepkg', 'w') as file:
        file.write(data)

    with open('/chroot/root/etc/makepkg.conf', 'r') as file:
        data = file.read()
    data = data.replace('xz -c', 'xz -T0 -c')
    data = data.replace(' check ', ' !check ')
    with open('/chroot/root/etc/makepkg.conf', 'w') as file:
        file.write(data)

    logging.info('Syncing chroot copy')
    result = subprocess.run(
        ['rsync', '-a', '--delete', '-q', '-W', '-x', '/chroot/root/', '/chroot/copy'])
    if result.returncode != 0:
        logging.fatal('Failed to sync chroot copy')
        exit(1)


def discover_packages() -> dict[str, Package]:
    packages = {}
    paths = []

    for dir in os.listdir('main'):
        paths.append(os.path.join('main', dir))
    for dir1 in os.listdir('device'):
        for dir2 in os.listdir(os.path.join('device', dir1)):
            paths.append(os.path.join('device', dir1, dir2))

    for path in paths:
        logging.debug(f'Discovered {path}')
        package = Package(path)
        packages[package.name] = package

    # This filters the deps to only include the ones that are provided in this repo
    for package in packages.values():
        package.local_depends = package.depends.copy()
        for dep in package.depends.copy():
            found = False
            for p in packages.values():
                for name in p.names:
                    if dep == name:
                        found = True
                        break
                if found:
                    break
            if not found:
                package.local_depends.remove(dep)

    return packages


def generate_package_order(packages: list[Package]) -> list[Package]:
    unsorted = packages.copy()
    sorted = []

    """
    It goes through all unsorted packages and checks if the dependencies have already been sorted.
    If that is true, the package itself is added to the sorted packages
    """
    while len(unsorted) > 0:
        for package in unsorted.copy():
            if len(package.local_depends) == 0:
                sorted.append(package)
                unsorted.remove(package)
        for package in sorted:
            for name in package.names:
                for p in unsorted:
                    for dep in p.local_depends.copy():
                        if name == dep:
                            p.local_depends.remove(name)

    return sorted


def update_package_version_and_sources(package: Package):
    """
    This updates the package version and the sources.
    It is done here already, because doing it while host-compiling takes longer.
    We decided to even pin the commit of every -git package so this won't update any version, but it would if possible.
    """
    cmd = makepkg_cmd+['--nobuild', '--noprepare', '--nodeps', '--skipinteg']
    if not package.has_pkgver:
        cmd.append('--noextract')
    logging.info(f'Updating package version for {package.path}')
    result = subprocess.run(cmd,
                            env=makepkg_cross_env,
                            cwd=package.path)
    if result.returncode != 0:
        logging.fatal(f'Failed to update package version for {package.path}')
        exit(1)


def check_package_version_built(package: Package) -> bool:
    built = True

    result = subprocess.run(makepkg_cmd +
                            ['--nobuild',
                             '--noprepare',
                             '--packagelist'],
                            env=makepkg_cross_env,
                            cwd=package.path,
                            capture_output=True)
    if result.returncode != 0:
        logging.fatal(f'Failed to get package list for {package.path}')
        exit(1)

    for line in result.stdout.decode('utf-8').split('\n'):
        if line != "":
            file = os.path.basename(line)
            if not os.path.exists(os.path.join('prebuilts', package.repo, file)):
                built = False

    return built


def setup_dependencies_and_sources(package: Package):
    logging.info(f'Setting up dependencies and sources for {package.path}')

    """
    To make cross-compilation work for almost every package, the host needs to have the dependencies installed
    so that the build tools can be used
    """
    if package.mode == 'cross':
        for p in package.depends:
            result = subprocess.run(pacman_cmd + ['-S', p], stderr=subprocess.DEVNULL)
            if result.returncode != 0:
                logging.fatal(
                    f'Failed to setup dependencies for {package.path}')
                exit(1)

    result = subprocess.run(makepkg_cmd +
                            ['--nobuild',
                             '--holdver',
                             '--syncdeps'],
                            env=makepkg_cross_env,
                            cwd=package.path)
    if result.returncode != 0:
        logging.fatal(
            f'Failed to check sources for {package.path}')
        exit(1)


def build_package(package: Package):
    makepkg_compile_opts = ['--noextract',
                            '--skipinteg',
                            '--holdver',
                            '--nodeps']

    if package.mode == 'cross':
        logging.info(f'Cross-compiling {package.path}')
        result = subprocess.run(makepkg_cmd+makepkg_compile_opts,
                                env=makepkg_cross_env | {
                                    'QEMU_LD_PREFIX': '/usr/aarch64-linux-gnu'},
                                cwd=package.path)
        if result.returncode != 0:
            logging.fatal(
                f'Failed to cross-compile package {package.path}')
            exit(1)
    else:
        logging.info(f'Host-compiling {package.path}')

        def umount():
            subprocess.run(['umount', '-lc', '/chroot/copy'],
                           stderr=subprocess.DEVNULL)
        atexit.register(umount)

        result = subprocess.run(
            ['mount', '-o', 'bind', '/chroot/copy', '/chroot/copy'])
        if result.returncode != 0:
            logging.fatal('Failed to bind mount chroot to itself')
            exit(1)

        os.makedirs('/chroot/copy/src')
        result = subprocess.run(
            ['mount', '-o', 'bind', '.', '/chroot/copy/src'])
        if result.returncode != 0:
            logging.fatal(
                f'Failed to bind mount folder to chroot')
            exit(1)

        env = [f'{key}={value}' for key, value in makepkg_env.items()]
        result = subprocess.run(
            ['arch-chroot', '/chroot/copy', '/usr/bin/env'] + env + [ '/bin/bash', '-c', f'cd /src/{package.path} && makepkg --noconfirm --ignorearch {" ".join(makepkg_compile_opts)}'])
        if result.returncode != 0:
            logging.fatal(f'Failed to host-compile package {package.path}')
            exit(1)

        umount()


def add_package_to_repo(package: Package):
    logging.info(f'Adding {package.path} to repo')
    dir = os.path.join('prebuilts', package.repo)
    if not os.path.exists(dir):
        os.mkdir(dir)

    for file in os.listdir(package.path):
        # Forced extension by makepkg.conf
        if file.endswith('.pkg.tar.xz'):
            shutil.move(
                os.path.join(package.path, file),
                os.path.join(dir, file),
            )
            result = subprocess.run(['repo-add',
                                     '--remove',
                                     '--new',
                                     '--prevent-downgrade',
                                     os.path.join(
                                         'prebuilts',
                                         package.repo,
                                         f'{package.repo}.db.tar.xz',
                                     ),
                                     os.path.join(dir, file),
                                     ])
            if result.returncode != 0:
                logging.fatal(f'Failed add package {package.path} to repo')
                exit(1)
    for repo in ['main', 'device']:
        for ext in ['db', 'files']:
            if os.path.exists(os.path.join('prebuilts', repo, f'{repo}.{ext}.tar.xz')):
                os.unlink(os.path.join('prebuilts', repo, f'{repo}.{ext}'))
                shutil.copyfile(
                    os.path.join('prebuilts', repo, f'{repo}.{ext}.tar.xz'),
                    os.path.join('prebuilts', repo, f'{repo}.{ext}')
                )
            if os.path.exists(os.path.join('prebuilts', repo, f'{repo}.{ext}.tar.xz.old')):
                os.unlink(os.path.join('prebuilts', repo,
                          f'{repo}.{ext}.tar.xz.old'))


@click.group(name='packages')
def cmd_packages():
    pass


@click.command(name='build')
@verbose_option
@click.argument('path')
def cmd_build(verbose, path):
    setup_logging(verbose)

    check_prebuilts()

    packages = discover_packages()

    if path != 'all':
        selection = []
        for package in packages.values():
            if package.path == path:
                # TODO: currently matches through package.name only, no provides
                selection += [ packages[pkg] for pkg in package.local_depends ] + [package]
        packages = { package.name:package for package in selection }

    package_order = generate_package_order(list(packages.values()))
    need_build = []
    for package in package_order:
        update_package_version_and_sources(package)
        if not check_package_version_built(package):
            need_build.append(package)

    if len(need_build) == 0:
        logging.info('Everything built already')
        return
    logging.info('Building %s', ', '.join(
        map(lambda x: x.path, need_build)))
    with open('.last_built', 'w') as file:
        file.write('\n'.join(
            map(lambda x: x.path, need_build)))

    for package in need_build:
        setup_chroot()
        setup_dependencies_and_sources(package)
        build_package(package)
        add_package_to_repo(package)



@click.command(name='clean')
@verbose_option
def cmd_clean(verbose):
    setup_logging(verbose)
    result = subprocess.run(['git',
                             'clean',
                             '-dffX',
                             'main', 'device'])
    if result.returncode != 0:
        logging.fatal(f'Failed to git clean')
        exit(1)


cmd_packages.add_command(cmd_build)
cmd_packages.add_command(cmd_clean)
