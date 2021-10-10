import click
import logging
import multiprocessing
import os
import shutil
import subprocess
from copy import deepcopy
from joblib import Parallel, delayed

from constants import REPOSITORIES, CROSSDIRECT_PKGS, GCC_HOSTSPECS, ARCHES
from config import config
from chroot import create_chroot, run_chroot_cmd, try_install_packages, mount_crossdirect, write_cross_makepkg_conf, mount_packages, mount_pacman_cache
from distro import get_kupfer_local
from wrapper import enforce_wrap
from utils import mount, umount

makepkg_env = os.environ.copy() | {
    'LANG': 'C',
    'MAKEFLAGS': f"-j{multiprocessing.cpu_count() if config.file['build']['threads'] < 1 else config.file['build']['threads']}",
    'QEMU_LD_PREFIX': '/usr/aarch64-unknown-linux-gnu'
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


def check_prebuilts(arch: str, dir: str = None):
    prebuilts_dir = dir if dir else config.get_package_dir(arch)
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
                    raise Exception('Not a Package object:' + repr(other_pkg))
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


def add_file_to_repo(file_path: str, repo_name: str, arch: str):
    repo_dir = os.path.join(config.get_package_dir(arch), repo_name)
    pacman_cache_dir = os.path.join(config.get_path('pacman'), arch)
    file_name = os.path.basename(file_path)
    target_file = os.path.join(repo_dir, file_name)

    os.makedirs(repo_dir, exist_ok=True)
    if file_path != target_file:
        logging.debug(f'moving {file_path} to {target_file} ({repo_dir})')
        shutil.copy(
            file_path,
            repo_dir,
        )
        os.unlink(file_path)

    # clean up same name package from pacman cache
    cache_file = os.path.join(pacman_cache_dir, file_name)
    if os.path.exists(cache_file):
        os.unlink(cache_file)
    result = subprocess.run([
        'repo-add',
        '--remove',
        '--prevent-downgrade',
        os.path.join(
            repo_dir,
            f'{repo_name}.db.tar.xz',
        ),
        target_file,
    ])
    if result.returncode != 0:
        raise Exception(f'Failed add package {target_file} to repo {repo_name}')
    for ext in ['db', 'files']:
        file = os.path.join(repo_dir, f'{repo_name}.{ext}')
        if os.path.exists(file + '.tar.xz'):
            os.unlink(file)
            shutil.copyfile(file + '.tar.xz', file)
        old = file + '.tar.xz.old'
        if os.path.exists(old):
            os.unlink(old)


def add_package_to_repo(package: Package, arch: str):
    logging.info(f'Adding {package.path} to repo {package.repo}')
    pkgbuild_dir = os.path.join(config.get_path('pkgbuilds'), package.path)

    for file in os.listdir(pkgbuild_dir):
        # Forced extension by makepkg.conf
        if file.endswith('.pkg.tar.xz') or file.endswith('.pkg.tar.zst'):
            add_file_to_repo(os.path.join(pkgbuild_dir, file), package.repo, arch)


def check_package_version_built(package: Package, arch) -> bool:

    config_path = '/' + write_cross_makepkg_conf(native_chroot='/', arch=arch, target_chroot_relative=None, cross=False)

    result = subprocess.run(
        makepkg_cmd + [
            '--config',
            config_path,
            '--nobuild',
            '--noprepare',
            '--packagelist',
        ],
        env=makepkg_cross_env,
        cwd=package.path,
        capture_output=True,
    )
    if result.returncode != 0:
        raise Exception(f'Failed to get package list for {package.path}:' + '\n' + result.stdout.decode() + '\n' + result.stderr.decode())

    missing = False
    for line in result.stdout.decode('utf-8').split('\n'):
        if line != "":
            file = os.path.join(config.get_package_dir(arch), package.repo, os.path.basename(line))
            logging.debug(f'Checking if {file} is built')
            if os.path.exists(file):
                add_file_to_repo(file, repo_name=package.repo, arch=arch)
            else:
                missing = True

    return not missing


def setup_build_chroot(arch: str, extra_packages=[]) -> str:
    chroot_name = f'build_{arch}'
    logging.info(f'Initializing {arch} build chroot')
    chroot_path = create_chroot(
        chroot_name,
        arch=arch,
        packages=list(set(['base', 'base-devel', 'git'] + extra_packages)),
        extra_repos=get_kupfer_local(arch).repos,
    )
    pacman_cache = mount_pacman_cache(chroot_path, arch)

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
    umount(pacman_cache)
    return chroot_path


def setup_sources(package: Package, chroot: str, arch: str, pkgbuilds_dir: str = None):
    pkgbuilds_dir = pkgbuilds_dir if pkgbuilds_dir else config.get_path('pkgbuilds')
    makepkg_setup_args = [
        '--nobuild',
        '--holdver',
        '--nodeps',
    ]

    logging.info(f'Setting up sources for {package.path} in {chroot}')
    result = subprocess.run(
        [os.path.join(chroot, 'usr/bin/makepkg')] + makepkg_cmd[1:] + makepkg_setup_args,
        env=makepkg_cross_env | {'PACMAN_CHROOT': chroot},
        cwd=os.path.join(pkgbuilds_dir, package.path),
    )
    if result.returncode != 0:
        raise Exception(f'Failed to check sources for {package.path}')


def build_package(
    package: Package,
    arch: str,
    repo_dir: str = None,
    enable_crosscompile: bool = True,
    enable_crossdirect: bool = True,
    enable_ccache=True,
):
    makepkg_compile_opts = [
        '--holdver',
    ]
    makepkg_conf_path = 'etc/makepkg.conf'
    repo_dir = repo_dir if repo_dir else config.get_path('pkgbuilds')
    foreign_arch = config.runtime['arch'] != arch
    target_chroot = setup_build_chroot(arch=arch, extra_packages=(list(set(package.depends) - set(package.names))))
    native_chroot = setup_build_chroot(arch=config.runtime['arch'], extra_packages=['base-devel']) if foreign_arch else target_chroot
    cross = foreign_arch and package.mode == 'cross' and enable_crosscompile
    umount_dirs = []
    set([target_chroot, native_chroot])

    # eliminate target_chroot == native_chroot with set()
    for chroot, _arch in set([(native_chroot, config.runtime['arch']), (target_chroot, arch)]):
        logging.debug(f'Mounting packages to {chroot}')
        dir = mount_packages(chroot, _arch)
        umount_dirs += [dir]

    if cross:
        logging.info(f'Cross-compiling {package.path}')
        build_root = native_chroot
        makepkg_compile_opts += ['--nodeps']
        #env = makepkg_cross_env
        env = makepkg_env
        if enable_ccache:
            env['PATH'] = f"/usr/lib/ccache:{env['PATH']}"
        logging.info('Setting up dependencies for cross-compilation')
        # include crossdirect for ccache symlinks.
        results = try_install_packages(package.depends + ['crossdirect', f"{GCC_HOSTSPECS[config.runtime['arch']][arch]}-gcc"], native_chroot)
        if results['crossdirect'].returncode != 0:
            raise Exception('Unable to install crossdirect')
        # mount foreign arch chroot inside native chroot
        chroot_relative = os.path.join('chroot', os.path.basename(target_chroot))
        chroot_mount_path = os.path.join(native_chroot, chroot_relative)
        makepkg_relative = write_cross_makepkg_conf(native_chroot=native_chroot, arch=arch, target_chroot_relative=chroot_relative)
        makepkg_conf_path = os.path.join('/', makepkg_relative)
        os.makedirs(chroot_mount_path)
        mount(target_chroot, chroot_mount_path)
        umount_dirs += [chroot_mount_path]
    else:
        logging.info(f'Host-compiling {package.path}')
        build_root = target_chroot
        makepkg_compile_opts += ['--syncdeps']
        env = deepcopy(makepkg_env)
        if foreign_arch and enable_crossdirect and package.name not in CROSSDIRECT_PKGS:
            env['PATH'] = f"/native/usr/lib/crossdirect/{arch}:{env['PATH']}"
            umount_dirs += [mount_crossdirect(native_chroot=native_chroot, target_chroot=target_chroot, target_arch=arch)]
        else:
            if enable_ccache:
                logging.debug('ccache enabled')
                env['PATH'] = f"/usr/lib/ccache:{env['PATH']}"
            logging.debug(('Building for native arch. ' if not foreign_arch else '') + 'Skipping crossdirect.')

    src_dir = os.path.join(build_root, 'src')
    os.makedirs(src_dir, exist_ok=True)
    #setup_sources(package, build_root, enable_crosscompile=enable_crosscompile)

    result = mount(config.get_path('pkgbuilds'), src_dir)
    if result.returncode != 0:
        raise Exception(f'Failed to bind mount pkgbuilds to {build_root}/src')
    umount_dirs += [src_dir]

    makepkg_conf_absolute = os.path.join('/', makepkg_conf_path)
    build_cmd = f'cd /src/{package.path} && makepkg --config {makepkg_conf_absolute} --needed --noconfirm --ignorearch {" ".join(makepkg_compile_opts)}'
    logging.debug(f'Building: Running {build_cmd}')
    result = run_chroot_cmd(build_cmd, chroot_path=build_root, inner_env=env)

    if result.returncode != 0:
        raise Exception(f'Failed to compile package {package.path}')

    # cleanup
    for dir in umount_dirs:
        umount_result = umount(dir)
        if umount_result != 0:
            logging.warning(f'Failed to unmount {dir}')


@click.group(name='packages')
def cmd_packages():
    pass


@cmd_packages.command(name='build')
@click.option('--force', is_flag=True, default=False)
@click.option('--arch', default=None)
@click.argument('paths', nargs=-1)
def cmd_build(paths: list[str], force=False, arch=None):
    if arch is None:
        # arch = config.get_profile()...
        arch = 'aarch64'

    if arch not in ARCHES:
        raise Exception(f'Unknown architecture "{arch}". Choices: {", ".join(ARCHES)}')
    enforce_wrap()

    for _arch in set([arch, config.runtime['arch']]):
        check_prebuilts(_arch)

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
            if ((not check_package_version_built(package, arch)) or set.intersection(set(package.depends), set(build_names)) or
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
        for package in need_build:
            build_package(
                package,
                arch=arch,
                enable_crosscompile=config.file['build']['crosscompile'],
                enable_crossdirect=config.file['build']['crossdirect'],
                enable_ccache=config.file['build']['ccache'],
            )
            add_package_to_repo(package, arch)


@cmd_packages.command(name='clean')
def cmd_clean():
    enforce_wrap()
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
    paths = list(paths)
    packages = filter_packages_by_paths(discover_packages(), paths)

    for package in packages:
        name = package.name

        is_git_package = False
        if name.endswith('-git'):
            is_git_package = True

        required_arches = ''
        provided_arches = []

        mode_key = '_mode'
        pkgbase_key = 'pkgbase'
        pkgname_key = 'pkgname'
        arches_key = '_arches'
        arch_key = 'arch'
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
            arches_key: True,
            arch_key: True,
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
            content = file.read()
            if '\t' in content:
                logging.fatal(f'\\t is not allowed in {os.path.join(package.path, "PKGBUILD")}')
                exit(1)
            lines = content.split('\n')
            if len(lines) == 0:
                logging.fatal(f'Empty {os.path.join(package.path, "PKGBUILD")}')
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

                if line.startswith('_') and not line.startswith(mode_key) and not line.startswith(arches_key) and not line.startswith(commit_key):
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

                if key == arches_key:
                    required_arches = line.split('=')[1]

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

                if key == arch_key:
                    if line.endswith(')'):
                        if line.startswith(f'{arch_key}=('):
                            check_arches_hint(os.path.join(package.path, "PKGBUILD"), required_arches, [line[6:-1]])
                        else:
                            check_arches_hint(os.path.join(package.path, "PKGBUILD"), required_arches, provided_arches)
                    elif line.startswith('    '):
                        provided_arches.append(line[4:])

                if next_key and not hold_key:
                    key_index += 1
                if next_line:
                    line_index += 1

        logging.info(f'{package.path} nicely formatted!')


def check_arches_hint(path: str, required: str, provided: list[str]):
    if required == 'all':
        for arch in ARCHES:
            if arch not in provided:
                logging.warning(f'Missing {arch} in arches list in {path}, because hint is `all`')
