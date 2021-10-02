import click
import atexit
import logging
import multiprocessing
import os
import shutil
import subprocess
from constants import REPOSITORIES
from config import config
from chroot import create_chroot, run_chroot_cmd, try_install_packages, mount_crossdirect
from joblib import Parallel, delayed
from distro import get_kupfer_local
from wrapper import enforce_wrap, check_programs_wrap
from utils import mount, umount

makepkg_env = os.environ.copy() | {
    'LANG': 'C',
    'MAKEFLAGS': f"-j{multiprocessing.cpu_count() if config.file['build']['threads'] < 1 else config.file['build']['threads']}",
}

makepkg_cross_env = makepkg_env | {'PACMAN': os.path.join(config.runtime['script_source_dir'], 'local/bin/pacman_aarch64')}

makepkg_cmd = [
    'makepkg',
    '--config',
    os.path.join(config.runtime['script_source_dir'], 'local/etc/makepkg.conf'),
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
    names: list[str] = []
    depends: list[str] = []
    local_depends = None
    repo = ''
    mode = ''

    def __init__(self, path: str, dir: str = None) -> None:
        self.path = path
        dir = dir if dir else config.get_path('pkgbuilds')
        self._loadinfo(dir)

    def _loadinfo(self, dir):
        result = subprocess.run(
            makepkg_cmd + ['--printsrcinfo'],
            cwd=os.path.join(dir, self.path),
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


def check_prebuilts(dir: str = None):
    prebuilts_dir = dir if dir else config.get_path('packages')
    os.makedirs(prebuilts_dir, exist_ok=True)
    for repo in REPOSITORIES:
        os.makedirs(os.path.join(prebuilts_dir, repo), exist_ok=True)
        for ext1 in ['db', 'files']:
            for ext2 in ['', '.tar.xz']:
                if not os.path.exists(os.path.join(prebuilts_dir, repo, f'{repo}.{ext1}{ext2}')):
                    result = subprocess.run(
                        [
                            'tar',
                            '-czf',
                            f'{repo}.{ext1}{ext2}',
                            '-T',
                            '/dev/null',
                        ],
                        cwd=os.path.join(prebuilts_dir, repo),
                    )
                    if result.returncode != 0:
                        logging.fatal('Failed to create prebuilt repos')
                        exit(1)


def discover_packages(dir: str = None) -> dict[str, Package]:
    dir = dir if dir else config.get_path('pkgbuilds')
    packages = {}
    paths = []

    for repo in REPOSITORIES:
        for _dir in os.listdir(os.path.join(dir, repo)):
            paths.append(os.path.join(repo, _dir))

    results = Parallel(n_jobs=multiprocessing.cpu_count() * 4)(delayed(Package)(path, dir) for path in paths)
    for package in results:
        packages[package.name] = package

    # This filters the deps to only include the ones that are provided in this repo
    for package in packages.values():
        package.local_depends = package.depends.copy()
        for dep in package.depends.copy():
            found = dep in packages
            for p in packages.values():
                if found:
                    break
                for name in p.names:
                    if dep == name:
                        logging.debug(f'Found {p.name} that provides {dep}')
                        found = True
                        break
            if not found:
                logging.debug(f'Removing {dep} from dependencies')
                package.local_depends.remove(dep)

    return packages


def filter_packages_by_paths(repo: dict[str, Package], paths: list[str]) -> list[Package]:
    if 'all' in paths:
        return repo.values()
    result = []
    for pkg in repo.values():
        if pkg.path in paths:
            result += [pkg]
    return result


def generate_dependency_chain(package_repo: dict[str, Package], to_build: list[Package]) -> list[set[Package]]:
    """
    This figures out all dependencies and their sub-dependencies for the selection and adds those packages to the selection.
    First the top-level packages get selected by searching the paths.
    Then their dependencies and sub-dependencies and so on get added to the selection.
    """
    visited = set[Package]()
    visited_names = set[str]()
    dep_levels: list[set[Package]] = [set(), set()]

    def visit(package: Package, visited=visited, visited_names=visited_names):
        visited.add(package)
        visited_names.update(package.names)

    def join_levels(levels: list[set[Package]]) -> dict[Package, int]:
        result = dict[Package, int]()
        for i, level in enumerate(levels):
            result[level] = i

    def get_dependencies(package: Package, package_repo: dict[str, Package] = package_repo) -> list[Package]:
        for dep_name in package.depends:
            if dep_name in visited_names:
                continue
            elif dep_name in package_repo:
                dep_pkg = package_repo[dep_name]
                visit(dep_pkg)
                yield dep_pkg

    def get_recursive_dependencies(package: Package, package_repo: dict[str, Package] = package_repo) -> list[Package]:
        for pkg in get_dependencies(package, package_repo):
            yield pkg
            for sub_pkg in get_recursive_dependencies(pkg, package_repo):
                yield sub_pkg

    logging.debug('Generating dependency chain:')
    # init level 0
    for package in to_build:
        visit(package)
        dep_levels[0].add(package)
        logging.debug(f'Adding requested package {package.name}')
        # add dependencies of our requested builds to level 0
        for dep_pkg in get_recursive_dependencies(package):
            logging.debug(f"Adding {package.name}'s dependency {dep_pkg.name} to level 0")
            dep_levels[0].add(dep_pkg)
            visit(dep_pkg)
    """
    Starting with `level` = 0, iterate over the packages in `dep_levels[level]`:
    1. Moving packages that are dependencies of other packages up to `level`+1
    2. Adding yet unadded local dependencies of all pkgs on `level` to `level`+1
    3. increment level
    """
    level = 0
    # protect against dependency cycles
    repeat_count = 0
    _last_level: set[Package] = None
    while dep_levels[level]:
        level_copy = dep_levels[level].copy()
        modified = False
        logging.debug(f'Scanning dependency level {level}')
        if level > 100:
            raise Exception('Dependency chain reached 100 levels depth, this is probably a bug. Aborting!')

        for pkg in level_copy:
            pkg_done = False
            if pkg not in dep_levels[level]:
                # pkg has been moved, move on
                continue
            # move pkg to level+1 if something else depends on it
            for other_pkg in level_copy:
                if pkg == other_pkg:
                    continue
                if pkg_done:
                    break
                if type(other_pkg) != Package:
                    logging.fatal('Wtf, this is not a package:' + repr(other_pkg))
                for dep_name in other_pkg.depends:
                    if dep_name in pkg.names:
                        dep_levels[level].remove(pkg)
                        dep_levels[level + 1].add(pkg)
                        logging.debug(f'Moving {pkg.name} to level {level+1} because {other_pkg.name} depends on it as {dep_name}')
                        modified = True
                        pkg_done = True
                        break
            for dep_name in pkg.depends:
                if dep_name in visited_names:
                    continue
                elif dep_name in package_repo:
                    dep_pkg = package_repo[dep_name]
                    logging.debug(f"Adding {pkg.name}'s dependency {dep_name} to level {level}")
                    dep_levels[level].add(dep_pkg)
                    visit(dep_pkg)
                    modified = True

        if _last_level == dep_levels[level]:
            repeat_count += 1
        else:
            repeat_count = 0
        if repeat_count > 10:
            raise Exception(f'Probable dependency cycle detected: Level has been passed on unmodifed multiple times: #{level}: {_last_level}')
        _last_level = dep_levels[level].copy()
        if not modified:  # if the level was modified, make another pass.
            level += 1
            dep_levels.append(set[Package]())
    # reverse level list into buildorder (deps first!), prune empty levels
    return list([lvl for lvl in dep_levels[::-1] if lvl])


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
        logging.fatal(f'Failed to get package list for {package.path}:' + '\n' + result.stdout.decode() + '\n' + result.stderr.decode())
        exit(1)

    for line in result.stdout.decode('utf-8').split('\n'):
        if line != "":
            file = os.path.join(config.get_path('packages'), package.repo, os.path.basename(line))
            logging.debug(f'Checking if {file} is built')
            if not os.path.exists(file):
                built = False

    return built


def setup_build_chroot(arch: str, extra_packages=[]) -> str:
    chroot_name = f'build_{arch}'
    logging.info(f'Initializing {arch} build chroot')
    chroot_path = create_chroot(
        chroot_name,
        arch=arch,
        packages=list(set(['base-devel', 'git', 'ccache'] + extra_packages)),
        extra_repos=get_kupfer_local(arch).repos,
    )

    logging.info(f'Updating chroot {chroot_name}')
    result = subprocess.run(
        pacman_cmd + [
            '--root',
            chroot_path,
            '--arch',
            arch,
            '--config',
            chroot_path + '/etc/pacman.conf',
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        logging.fatal(result.stdout)
        logging.fatal(result.stderr)
        raise Exception(f'Failed to update chroot {chroot_name}')
    return chroot_path


def setup_sources(package: Package, chroot: str, repo_dir: str = None, enable_crosscompile: bool = True):
    repo_dir = repo_dir if repo_dir else config.get_path('pkgbuilds')
    makepkg_setup_args = [
        '--nobuild',
        '--holdver',
        '--nodeps',
    ]

    logging.info(f'Setting up sources for {package.path} in {chroot}')
    result = subprocess.run(
        [os.path.join(chroot, 'usr/bin/makepkg')] + makepkg_cmd[1:] + makepkg_setup_args,
        env=makepkg_cross_env | {'PACMAN_CHROOT': chroot},
        cwd=os.path.join(repo_dir, package.path),
    )
    if result.returncode != 0:
        raise Exception(f'Failed to check sources for {package.path}')


def build_package(package: Package, arch: str, repo_dir: str = None, enable_crosscompile: bool = True, enable_crossdirect: bool = True):
    makepkg_compile_opts = [
        '--noextract',
        '--skipinteg',
        '--holdver',
    ]
    repo_dir = repo_dir if repo_dir else config.get_path('pkgbuilds')
    foreign_arch = config.runtime['arch'] != arch
    target_chroot = setup_build_chroot(arch=arch, extra_packages=package.depends)
    native_chroot = setup_build_chroot(arch=config.runtime['arch'], extra_packages=['base-devel']) if foreign_arch else target_chroot
    cross = foreign_arch and package.mode == 'cross' and enable_crosscompile
    env = {}

    # eliminate target_chroot == native_chroot with set()
    for chroot in set([target_chroot, native_chroot]):
        pkgs_path = config.get_path('packages')
        chroot_pkgs = chroot + pkgs_path  # NOTE: DO NOT USE PATH.JOIN, pkgs_path starts with /
        os.makedirs(chroot_pkgs, exist_ok=True)
        logging.debug(f'Mounting packages to {chroot_pkgs}')
        result = mount(pkgs_path, chroot_pkgs)
        if result.returncode != 0:
            raise Exception(f'Unable to mount packages to {chroot_pkgs}')

    if cross:
        logging.info(f'Cross-compiling {package.path}')
        build_root = native_chroot
        makepkg_compile_opts += ['--nodeps']
        env = makepkg_env

        logging.info('Setting up dependencies for cross-compilation')
        try_install_packages(package.depends, native_chroot)
    else:
        logging.info(f'Host-compiling {package.path}')
        build_root = target_chroot
        makepkg_compile_opts += ['--syncdeps']
        env = makepkg_env
        if not foreign_arch:
            logging.debug('Building for native arch, skipping crossdirect.')
        elif enable_crossdirect and not package.name in ['crossdirect', 'qemu-user-static-bin', 'binfmt-qemu-static-all-arch']:
            env['PATH'] = f"/native/usr/lib/crossdirect/{arch}:{env['PATH']}"
            mount_crossdirect(native_chroot=native_chroot, target_chroot=target_chroot, target_arch=arch)
        else:
            logging.debug('Skipping crossdirect.')

    src_dir = os.path.join(build_root, 'src')
    os.makedirs(f'{build_root}/src', exist_ok=True)
    setup_sources(package, build_root, enable_crosscompile=enable_crosscompile)

    result = mount(config.get_path('pkgbuilds'), src_dir)
    if result.returncode != 0:
        raise Exception(f'Failed to bind mount pkgdirs to {build_root}/src')

    build_cmd = f'cd /src/{package.path} && echo $PATH && makepkg --needed --noconfirm --ignorearch {" ".join(makepkg_compile_opts)}'
    result = run_chroot_cmd(build_cmd, chroot_path=build_root, inner_env=env)
    umount_result = umount(src_dir)
    if umount_result != 0:
        logging.warning(f'Failed to unmount {src_dir}')
    if result.returncode != 0:
        raise Exception(f'Failed to compile package {package.path}')


def add_package_to_repo(package: Package, arch: str):
    logging.info(f'Adding {package.path} to repo')
    binary_dir = os.path.join(config.get_path('packages'), package.repo)
    pkgbuild_dir = os.path.join(config.get_path('pkgbuilds'), package.path)
    pacman_cache_dir = os.path.join(config.get_path('pacman'), arch)
    os.makedirs(binary_dir, exist_ok=True)

    for file in os.listdir(pkgbuild_dir):
        # Forced extension by makepkg.conf
        if file.endswith('.pkg.tar.xz') or file.endswith('.pkg.tar.zst'):
            shutil.move(
                os.path.join(pkgbuild_dir, file),
                os.path.join(binary_dir, file),
            )
            # clean up same name package from pacman cache
            cache_file = os.path.join(pacman_cache_dir, file)
            if os.path.exists(cache_file):
                os.unlink(cache_file)
            result = subprocess.run([
                'repo-add',
                '--remove',
                '--new',
                '--prevent-downgrade',
                os.path.join(
                    binary_dir,
                    f'{package.repo}.db.tar.xz',
                ),
                os.path.join(binary_dir, file),
            ])
            if result.returncode != 0:
                logging.fatal(f'Failed add package {package.path} to repo')
                exit(1)
    for repo in REPOSITORIES:
        for ext in ['db', 'files']:
            if os.path.exists(os.path.join(binary_dir, f'{repo}.{ext}.tar.xz')):
                os.unlink(os.path.join(binary_dir, f'{repo}.{ext}'))
                shutil.copyfile(
                    os.path.join(binary_dir, f'{repo}.{ext}.tar.xz'),
                    os.path.join(binary_dir, f'{repo}.{ext}'),
                )
            if os.path.exists(os.path.join(binary_dir, f'{repo}.{ext}.tar.xz.old')):
                os.unlink(os.path.join(binary_dir, f'{repo}.{ext}.tar.xz.old'))


@click.group(name='packages')
def cmd_packages():
    pass


@cmd_packages.command(name='build')
@click.option('--force', is_flag=True, default=False)
@click.option('--arch', default=None)
@click.argument('paths', nargs=-1)
def cmd_build(paths: list[str], force=False, arch=None):
    enforce_wrap()
    if arch is None:
        # arch = config.get_profile()...
        arch = 'aarch64'

    check_prebuilts()

    paths = list(paths)
    repo = discover_packages()

    package_levels = generate_dependency_chain(
        repo,
        filter_packages_by_paths(repo, paths),
    )
    build_names = set[str]()
    build_levels = list[set[Package]]()
    i = 0
    for packages in package_levels:
        level = set[Package]()
        for package in packages:
            if ((not check_package_version_built(package)) or set.intersection(set(package.depends), set(build_names)) or
                (force and package.path in paths)):
                level.add(package)
                build_names.update(package.names)
        if level:
            build_levels.append(level)
            logging.debug(f'Adding to level {i}:' + '\n' + ('\n'.join([p.name for p in level])))
            i += 1

    if not build_levels:
        logging.info('Everything built already')
        return
    for level, need_build in enumerate(build_levels):
        logging.info(f"(Level {level}) Building {', '.join([x.name for x in need_build])}")
        crosscompile = config.file['build']['crosscompile']
        for package in need_build:
            build_package(package, arch=arch, enable_crosscompile=crosscompile)
            add_package_to_repo(package, arch)


@cmd_packages.command(name='clean')
def cmd_clean():
    check_programs_wrap('git')
    result = subprocess.run([
        'git',
        'clean',
        '-dffX',
    ] + REPOSITORIES)
    if result.returncode != 0:
        logging.fatal('Failed to git clean')
        exit(1)


@cmd_packages.command(name='check')
@click.argument('paths', nargs=-1)
def cmd_check(paths):
    enforce_wrap()
    paths = list(paths)
    packages = filter_packages_by_paths(discover_packages(), paths)

    for package in packages:
        name = package.name

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

                if line.startswith('#'):
                    line_index += 1
                    continue

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

                if '"' in line and '$' not in line and ' ' not in line:
                    formatted = False
                    reason = 'Found literal " although no "$" or " " was found in the line justifying the usage of a literal "'

                if '\'' in line:
                    formatted = False
                    reason = 'Found literal \' although either a literal " or no qoutes should be used'

                if ('=(' in line and ' ' in line and '"' not in line and not line.endswith('=(')) or (hold_key and line.endswith(')')):
                    formatted = False
                    reason = 'Multiple elements in a list need to be in separate lines'

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
