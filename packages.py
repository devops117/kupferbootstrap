from constants import REPOSITORIES
import atexit
import click
import logging
import multiprocessing
import os
import shutil
import subprocess
from config import config
from chroot import create_chroot
from joblib import Parallel, delayed

makepkg_env = os.environ.copy() | {
    'LANG': 'C',
    'MAKEFLAGS': f"-j{multiprocessing.cpu_count() if config.file['build']['threads'] < 1 else config.file['build']['threads']}",
}

makepkg_cross_env = makepkg_env | {'PACMAN': '/app/local/bin/pacman_aarch64'}

makepkg_cmd = [
    'makepkg',
    '--config',
    '/app/local/etc/makepkg.conf',
    '--noconfirm',
    '--ignorearch',
    '--needed',
]

pacman_cmd = [
    'pacman',
    '-Syuu',
    '--noconfirm',
    '--overwrite=*',
    '--needed',
]


class Package:
    name = ''
    names = []
    depends = []
    local_depends = None
    repo = ''
    mode = ''

    def __init__(self, path: str) -> None:
        self.path = path
        self._loadinfo()

    def _loadinfo(self):
        result = subprocess.run(
            makepkg_cmd + ['--printsrcinfo'],
            cwd=self.path,
            stdout=subprocess.PIPE,
        )
        lines = result.stdout.decode('utf-8').split('\n')
        names = []
        depends = []
        multi_pkgs = False

        for line_raw in lines:
            line = line_raw.lstrip()
            if line.startswith('pkgbase'):
                self.name = line.split(' = ')[1]
                names.append(self.name)
                multi_pkgs = True
            if line.startswith('pkgname'):
                names.append(line.split(' = ')[1])
                if not multi_pkgs:
                    self.name = line.split(' = ')[1]
            if line.startswith('pkgbase') or line.startswith('provides'):
                names.append(line.split(' = ')[1])
            if line.startswith('depends') or line.startswith('makedepends') or line.startswith('checkdepends') or line.startswith('optdepends'):
                depends.append(line.split(' = ')[1].split('=')[0].split(': ')[0])
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
            logging.fatal(f'Package {self.path} has an invalid mode configured: \'{mode}\'')
            exit(1)
        self.mode = mode

    def __repr__(self):
        return f'package({self.name},{repr(self.names)})'


def check_prebuilts():
    if not os.path.exists('prebuilts'):
        os.makedirs('prebuilts')
    for repo in REPOSITORIES:
        if not os.path.exists(os.path.join('prebuilts', repo)):
            os.makedirs(os.path.join('prebuilts', repo))
        for ext1 in ['db', 'files']:
            for ext2 in ['', '.tar.xz']:
                if not os.path.exists(os.path.join('prebuilts', repo, f'{repo}.{ext1}{ext2}')):
                    result = subprocess.run(
                        [
                            'tar',
                            '-czf',
                            f'{repo}.{ext1}{ext2}',
                            '-T',
                            '/dev/null',
                        ],
                        cwd=os.path.join('prebuilts', repo),
                    )
                    if result.returncode != 0:
                        logging.fatal('Failed to create prebuilt repos')
                        exit(1)


def setup_build_chroot(arch='aarch64'):
    chroot_name = f'build_{arch}'
    logging.info('Initializing {arch} build chroot')
    extra_repos = {}
    for repo in REPOSITORIES:
        extra_repos[repo] = {
            'Server': f'file:///src/prebuilts/{repo}',
        }
    chroot_path = create_chroot(
        chroot_name,
        packages=['base-devel'],
        pacman_conf='/app/local/etc/pacman.conf',
        extra_repos=extra_repos,
    )

    logging.info('Updating root chroot')
    result = subprocess.run(pacman_cmd + [
        '--root',
        chroot_path,
        '--arch',
        arch,
        '--config',
        chroot_path + '/etc/pacman.conf',
    ])
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
    result = subprocess.run([
        'rsync',
        '-a',
        '--delete',
        '-q',
        '-W',
        '-x',
        '/chroot/root/',
        '/chroot/copy',
    ])
    if result.returncode != 0:
        logging.fatal('Failed to sync chroot copy')
        exit(1)


def discover_packages(package_paths: list[str]) -> dict[str, Package]:
    packages = {}
    paths = []

    for repo in REPOSITORIES:
        for dir in os.listdir(repo):
            paths.append(os.path.join(repo, dir))

    results = Parallel(n_jobs=multiprocessing.cpu_count() * 4)(delayed(Package)(path) for path in paths)
    for package in results:
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
                logging.debug(f'Removing {dep} from dependencies')
                package.local_depends.remove(dep)
    """
    This figures out all dependencies and their sub-dependencies for the selection and adds those packages to the selection.
    First the top-level packages get selected by searching the paths.
    Then their dependencies and sub-dependencies and so on get added to the selection.
    """
    selection = []
    deps = []
    for package in packages.values():
        if 'all' in package_paths or package.path in package_paths:
            deps.append(package.name)
    while len(deps) > 0:
        for dep in deps.copy():
            found = False
            for p in selection:
                for name in p.names:
                    if name == dep:
                        deps.remove(dep)
                        found = True
                        break
            for p in packages.values():
                if found:
                    break
                for name in p.names:
                    if name == dep:
                        selection.append(packages[p.name])
                        deps.remove(dep)
                        # Add the sub-dependencies
                        deps += p.local_depends
                        found = True
                        break
            if not found:
                logging.fatal(f'Failed to find dependency {dep}')
                exit(1)

    selection = list(set(selection))
    packages = {package.name: package for package in selection}

    logging.debug(f'Figured out selection: {list(map(lambda p: p.path, selection))}')

    return packages


def generate_package_order(packages: list[Package]) -> list[Package]:
    unsorted = packages.copy()
    sorted = []
    """
    It goes through all unsorted packages and checks if the dependencies have already been sorted.
    If that is true, the package itself is added to the sorted packages
    """
    while len(unsorted) > 0:
        changed = False
        for package in unsorted.copy():
            if len(package.local_depends) == 0:
                sorted.append(package)
                unsorted.remove(package)
                changed = True
        for package in sorted:
            for name in package.names:
                for p in unsorted:
                    for dep in p.local_depends.copy():
                        if name == dep:
                            p.local_depends.remove(name)
                            changed = True
        if not changed:
            print('emergency break:', 'sorted:', repr(sorted), 'unsorted:', repr(unsorted))
            sorted += unsorted
            print('merged:', repr(sorted))
            break

    return sorted


def check_package_version_built(package: Package) -> bool:
    built = True

    result = subprocess.run(
        makepkg_cmd + [
            '--nobuild',
            '--noprepare',
            '--packagelist',
        ],
        env=makepkg_cross_env,
        cwd=package.path,
        capture_output=True,
    )
    if result.returncode != 0:
        logging.fatal(f'Failed to get package list for {package.path}')
        exit(1)

    for line in result.stdout.decode('utf-8').split('\n'):
        if line != "":
            file = os.path.basename(line)
            if not os.path.exists(os.path.join('prebuilts', package.repo, file)):
                built = False

    return built


def setup_dependencies_and_sources(package: Package, enable_crosscompile: bool = True):
    logging.info(f'Setting up dependencies and sources for {package.path}')
    """
    To make cross-compilation work for almost every package, the host needs to have the dependencies installed
    so that the build tools can be used
    """
    if package.mode == 'cross' and enable_crosscompile:
        for p in package.depends:
            # Don't check for errors here because there might be packages that are listed as dependencies but are not available on x86_64
            subprocess.run(
                pacman_cmd + [p],
                stderr=subprocess.DEVNULL,
            )

    result = subprocess.run(
        makepkg_cmd + [
            '--nobuild',
            '--holdver',
            '--syncdeps',
        ],
        env=makepkg_cross_env,
        cwd=package.path,
    )
    if result.returncode != 0:
        logging.fatal(f'Failed to check sources for {package.path}')
        exit(1)


def build_package(package: Package, enable_crosscompile: bool = True):
    makepkg_compile_opts = [
        '--noextract',
        '--skipinteg',
        '--holdver',
        '--nodeps',
    ]

    setup_dependencies_and_sources(package, enable_crosscompile=enable_crosscompile)

    if package.mode == 'cross' and enable_crosscompile:
        logging.info(f'Cross-compiling {package.path}')

        def umount():
            subprocess.run(
                [
                    'umount',
                    '-lc',
                    '/usr/share/i18n/locales',
                ],
                stderr=subprocess.DEVNULL,
            )

        result = subprocess.run([
            'mount',
            '-o',
            'bind',
            '/chroot/copy/usr/share/i18n/locales',
            '/usr/share/i18n/locales',
        ])
        if result.returncode != 0:
            logging.fatal(f'Failed to bind mount glibc locales from chroot')
            exit(1)

        result = subprocess.run(
            makepkg_cmd + makepkg_compile_opts,
            env=makepkg_cross_env | {'QEMU_LD_PREFIX': '/usr/aarch64-linux-gnu'},
            cwd=package.path,
        )
        if result.returncode != 0:
            logging.fatal(f'Failed to cross-compile package {package.path}')
            exit(1)
    else:
        logging.info(f'Host-compiling {package.path}')

        def umount():
            subprocess.run(
                [
                    'umount',
                    '-lc',
                    '/chroot/copy',
                ],
                stderr=subprocess.DEVNULL,
            )

        atexit.register(umount)

        result = subprocess.run([
            'mount',
            '-o',
            'bind',
            '/chroot/copy',
            '/chroot/copy',
        ])
        if result.returncode != 0:
            logging.fatal('Failed to bind mount chroot to itself')
            exit(1)

        os.makedirs('/chroot/copy/src')
        result = subprocess.run([
            'mount',
            '-o',
            'bind',
            '.',
            '/chroot/copy/src',
        ])
        if result.returncode != 0:
            logging.fatal(f'Failed to bind mount folder to chroot')
            exit(1)

        env = [f'{key}={value}' for key, value in makepkg_env.items()]
        result = subprocess.run([
            'arch-chroot',
            '/chroot/copy',
            '/usr/bin/env',
        ] + env + [
            '/bin/bash',
            '-c',
            f'cd /src/{package.path} && makepkg --noconfirm --ignorearch {" ".join(makepkg_compile_opts)}',
        ])
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
            result = subprocess.run([
                'repo-add',
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
    for repo in REPOSITORIES:
        for ext in ['db', 'files']:
            if os.path.exists(os.path.join('prebuilts', repo, f'{repo}.{ext}.tar.xz')):
                os.unlink(os.path.join('prebuilts', repo, f'{repo}.{ext}'))
                shutil.copyfile(os.path.join('prebuilts', repo, f'{repo}.{ext}.tar.xz'), os.path.join('prebuilts', repo, f'{repo}.{ext}'))
            if os.path.exists(os.path.join('prebuilts', repo, f'{repo}.{ext}.tar.xz.old')):
                os.unlink(os.path.join('prebuilts', repo, f'{repo}.{ext}.tar.xz.old'))


@click.group(name='packages')
def cmd_packages():
    pass


@click.command(name='build')
@click.argument('paths', nargs=-1)
def cmd_build(paths, arch='aarch64'):
    check_prebuilts()

    paths = list(paths)
    packages = discover_packages(paths)

    package_order = generate_package_order(list(packages.values()))
    need_build = []
    for package in package_order:
        if not check_package_version_built(package):
            need_build.append(package)

    if len(need_build) == 0:
        logging.info('Everything built already')
        return
    logging.info('Building %s', ', '.join(map(lambda x: x.path, need_build)))
    crosscompile = config.file['build']['crosscompile']
    for package in need_build:
        setup_build_chroot(arch=arch)
        build_package(package, enable_crosscompile=crosscompile)
        add_package_to_repo(package)


@click.command(name='clean')
def cmd_clean():
    result = subprocess.run([
        'git',
        'clean',
        '-dffX',
    ] + REPOSITORIES)
    if result.returncode != 0:
        logging.fatal(f'Failed to git clean')
        exit(1)


@click.command(name='check')
@click.argument('paths', nargs=-1)
def cmd_check(paths):
    paths = list(paths)
    packages = discover_packages(paths)

    for name in packages:
        package = packages[name]

        is_git_package = False
        if name.endswith('-git'):
            is_git_package = True

        mode_key = '_mode'
        pkgbase_key = 'pkgbase'
        pkgname_key = 'pkgname'
        commit_key = '_commit'
        source_key = 'source'
        sha256sums_key = 'sha256sums'
        required = {
            mode_key: True,
            pkgbase_key: False,
            pkgname_key: True,
            'pkgdesc': False,
            'pkgver': True,
            'pkgrel': True,
            'arch': True,
            'license': True,
            'url': False,
            'provides': is_git_package,
            'conflicts': False,
            'depends': False,
            'optdepends': False,
            'makedepends': False,
            'backup': False,
            'install': False,
            'options': False,
            commit_key: is_git_package,
            source_key: False,
            sha256sums_key: False,
        }

        with open(os.path.join(package.path, 'PKGBUILD'), 'r') as file:
            lines = file.read().split('\n')
            if len(lines) == 0:
                logging.fatal(f'Empty PKGBUILD for {package.path}')
                exit(1)
            line_index = 0
            key_index = 0
            hold_key = False
            key = ""
            while True:
                line = lines[line_index]

                if line.startswith('_') and not line.startswith(mode_key) and not line.startswith(commit_key):
                    line_index += 1
                    continue

                formatted = True
                next_key = False
                next_line = False
                reason = ""

                if hold_key:
                    next_line = True
                else:
                    if key_index < len(required):
                        key = list(required)[key_index]
                        if line.startswith(key):
                            if key == pkgbase_key:
                                required[pkgname_key] = False
                            if key == source_key:
                                required[sha256sums_key] = True
                            next_key = True
                            next_line = True
                        elif key in required and not required[key]:
                            next_key = True

                if line == ')':
                    hold_key = False
                    next_key = True

                if package.repo != 'main':
                    missing_prefix = False
                    if key == pkgbase_key or (key == pkgname_key and required[pkgname_key]):
                        if not line.split('=')[1].startswith(f'{package.repo}-') and not line.split('=')[1].startswith(f'"{package.repo}-'):
                            missing_prefix = True
                    if key == pkgname_key and hold_key and not required[pkgname_key]:
                        if not line[4:].startswith(f'{package.repo}-') and not line[4:].startswith(f'"{package.repo}-'):
                            missing_prefix = True
                    if missing_prefix:
                        formatted = False
                        reason = f'Package name needs to have "{package.repo}-" as prefix'

                if line.endswith('=('):
                    hold_key = True

                if line.startswith('    ') or line == ')':
                    next_line = True

                if line.startswith('  ') and not line.startswith('    '):
                    formatted = False
                    reason = 'Multiline variables should be indented with 4 spaces'

                if '"' in line and not '$' in line and not ' ' in line:
                    formatted = False
                    reason = f'Found literal " although no "$" or " " was found in the line justifying the usage of a literal "'

                if '\'' in line:
                    formatted = False
                    reason = 'Found literal \' although either a literal " or no qoutes should be used'

                if ('=(' in line and ' ' in line and not '"' in line and not line.endswith('=(')) or (hold_key and line.endswith(')')):
                    formatted = False
                    reason = f'Multiple elements in a list need to be in separate lines'

                if formatted and not next_key and not next_line:
                    if key_index == len(required):
                        if lines[line_index] == '':
                            break
                        else:
                            formatted = False
                            reason = 'Expected final emtpy line after all variables'
                    else:
                        formatted = False
                        reason = f'Expected to find "{key}"'

                if not formatted:
                    logging.fatal(f'Line {line_index+1} in {os.path.join(package.path, "PKGBUILD")} is not formatted correctly: "{line}"')
                    if reason != "":
                        logging.fatal(reason)
                    exit(1)

                if next_key and not hold_key:
                    key_index += 1
                if next_line:
                    line_index += 1

        logging.info(f'{package.path} nicely formatted!')


cmd_packages.add_command(cmd_build)
cmd_packages.add_command(cmd_clean)
cmd_packages.add_command(cmd_check)
