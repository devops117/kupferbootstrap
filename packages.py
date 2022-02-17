import click
import logging
import multiprocessing
import os
import shutil
import subprocess
from copy import deepcopy
from joblib import Parallel, delayed
from glob import glob
from shutil import rmtree

from constants import REPOSITORIES, CROSSDIRECT_PKGS, QEMU_BINFMT_PKGS, GCC_HOSTSPECS, ARCHES, Arch, CHROOT_PATHS
from config import config
from chroot import get_build_chroot, Chroot
from ssh import run_ssh_command, scp_put_files
from wrapper import enforce_wrap
from utils import git
from binfmt import register as binfmt_register

makepkg_cmd = [
    'makepkg',
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


def get_makepkg_env():
    # has to be a function because calls to `config` must be done after config file was read
    threads = config.file['build']['threads'] or multiprocessing.cpu_count()
    return os.environ.copy() | {
        'LANG': 'C',
        'CARGO_BUILD_JOBS': str(threads),
        'MAKEFLAGS': f"-j{threads}",
        'QEMU_LD_PREFIX': '/usr/aarch64-unknown-linux-gnu',
    }


class Package:
    name = ''
    names: list[str] = []
    depends: list[str] = []
    local_depends = None
    repo = ''
    mode = ''

    def __init__(
        self,
        path: str,
        native_chroot: Chroot,
    ) -> None:
        self.path = path
        self._loadinfo(native_chroot)

    def _loadinfo(self, native_chroot: Chroot):
        result = native_chroot.run_cmd(
            makepkg_cmd + ['--printsrcinfo'],
            cwd=os.path.join(CHROOT_PATHS['pkgbuilds'], self.path),
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
        logging.debug(config)
        with open(os.path.join(native_chroot.get_path(CHROOT_PATHS['pkgbuilds']), self.path, 'PKGBUILD'), 'r') as file:
            for line in file.read().split('\n'):
                if line.startswith('_mode='):
                    mode = line.split('=')[1]
                    break
        if mode not in ['host', 'cross']:
            logging.fatal(f'Package {self.path} has an invalid mode configured: \'{mode}\'')
            exit(1)
        self.mode = mode

    def __repr__(self):
        return f'Package({self.name},{repr(self.names)},{repr(self.path)})'


def clone_pkbuilds(pkgbuilds_dir: str, repo_url: str, branch: str, interactive=False, update=True):
    git_dir = os.path.join(pkgbuilds_dir, '.git')
    if not os.path.exists(git_dir):
        logging.info('Cloning branch {branch} from {repo}')
        result = git(['clone', '-b', branch, repo_url, pkgbuilds_dir])
        if result.returncode != 0:
            raise Exception('Error cloning pkgbuilds')
    else:
        result = git(['--git-dir', git_dir, 'branch', '--show-current'], capture_output=True)
        current_branch = result.stdout.decode().strip()
        if current_branch != branch:
            logging.warning(f'pkgbuilds repository is on the wrong branch: {current_branch}, requested: {branch}')
            if interactive and click.confirm('Would you like to switch branches?', default=False):
                result = git(['switch', branch], dir=pkgbuilds_dir)
                if result.returncode != 0:
                    raise Exception('failed switching branches')
        if update:
            if interactive:
                if not click.confirm('Would you like to try updating the PKGBUILDs repo?'):
                    return
            result = git(['pull'], pkgbuilds_dir)
            if result.returncode != 0:
                raise Exception('failed to update pkgbuilds')


def init_pkgbuilds(interactive=False):
    pkgbuilds_dir = config.get_path('pkgbuilds')
    repo_url = config.file['pkgbuilds']['git_repo']
    branch = config.file['pkgbuilds']['git_branch']
    clone_pkbuilds(pkgbuilds_dir, repo_url, branch, interactive=interactive, update=False)


def init_prebuilts(arch: Arch, dir: str = None):
    """Ensure that all `constants.REPOSITORIES` inside `dir` exist"""
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


def discover_packages() -> dict[str, Package]:
    pkgbuilds_dir = config.get_path('pkgbuilds')
    packages = {}
    paths = []
    init_pkgbuilds(interactive=False)
    for repo in REPOSITORIES:
        for dir in os.listdir(os.path.join(pkgbuilds_dir, repo)):
            paths.append(os.path.join(repo, dir))

    native_chroot = setup_build_chroot(config.runtime['arch'], add_kupfer_repos=False)
    results = Parallel(n_jobs=multiprocessing.cpu_count() * 4)(delayed(Package)(path, native_chroot) for path in paths)
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


def filter_packages_by_paths(repo: dict[str, Package], paths: list[str], allow_empty_results=True) -> list[Package]:
    if 'all' in paths:
        return repo.values()
    result = []
    for pkg in repo.values():
        if pkg.path in paths:
            result += [pkg]

    if not allow_empty_results and not result:
        raise Exception('No packages matched by paths: ' + ', '.join([f'"{p}"' for p in paths]))
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


def add_file_to_repo(file_path: str, repo_name: str, arch: Arch):
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
    cmd = [
        'repo-add',
        '--remove',
        os.path.join(
            repo_dir,
            f'{repo_name}.db.tar.xz',
        ),
        target_file,
    ]
    logging.debug(f'repo: running cmd: {cmd}')
    result = subprocess.run(cmd)
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


def add_package_to_repo(package: Package, arch: Arch):
    logging.info(f'Adding {package.path} to repo {package.repo}')
    pkgbuild_dir = os.path.join(config.get_path('pkgbuilds'), package.path)

    files = []
    for file in os.listdir(pkgbuild_dir):
        # Forced extension by makepkg.conf
        if file.endswith('.pkg.tar.xz') or file.endswith('.pkg.tar.zst'):
            repo_dir = os.path.join(config.get_package_dir(arch), package.repo)
            files.append(os.path.join(repo_dir, file))
            add_file_to_repo(os.path.join(pkgbuild_dir, file), package.repo, arch)
    return files


def check_package_version_built(package: Package, arch: Arch) -> bool:
    native_chroot = setup_build_chroot(config.runtime['arch'])
    config_path = '/' + native_chroot.write_makepkg_conf(
        target_arch=arch,
        cross_chroot_relative=os.path.join('chroot', arch),
        cross=True,
    )

    cmd = ['cd', os.path.join(CHROOT_PATHS['pkgbuilds'], package.path), '&&'] + makepkg_cmd + [
        '--config',
        config_path,
        '--nobuild',
        '--noprepare',
        '--skippgpcheck',
        '--packagelist',
    ]
    result = native_chroot.run_cmd(
        cmd,
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


def setup_build_chroot(
    arch: Arch,
    extra_packages: list[str] = [],
    add_kupfer_repos: bool = True,
    clean_chroot: bool = False,
) -> Chroot:
    init_prebuilts(arch)
    chroot = get_build_chroot(arch, add_kupfer_repos=add_kupfer_repos)
    chroot.mount_packages()
    logging.info(f'Initializing {arch} build chroot')
    chroot.initialize(reset=clean_chroot)
    chroot.write_pacman_conf()  # in case it was initialized with different repos
    chroot.activate()
    chroot.mount_pacman_cache()
    chroot.mount_pkgbuilds()
    if extra_packages:
        chroot.try_install_packages(extra_packages, allow_fail=False)
    return chroot


def setup_sources(package: Package, chroot: Chroot, makepkg_conf_path='/etc/makepkg.conf', pkgbuilds_dir: str = None):
    pkgbuilds_dir = pkgbuilds_dir if pkgbuilds_dir else CHROOT_PATHS['pkgbuilds']
    makepkg_setup_args = [
        '--config',
        makepkg_conf_path,
        '--nobuild',
        '--holdver',
        '--nodeps',
        '--skippgpcheck',
    ]

    logging.info(f'Setting up sources for {package.path} in {chroot.name}')
    result = chroot.run_cmd(makepkg_cmd + makepkg_setup_args, cwd=os.path.join(CHROOT_PATHS['pkgbuilds'], package.path))
    if result.returncode != 0:
        raise Exception(f'Failed to check sources for {package.path}')


def build_package(
    package: Package,
    arch: Arch,
    repo_dir: str = None,
    enable_crosscompile: bool = True,
    enable_crossdirect: bool = True,
    enable_ccache: bool = True,
    clean_chroot: bool = False,
):
    makepkg_compile_opts = ['--holdver']
    makepkg_conf_path = 'etc/makepkg.conf'
    repo_dir = repo_dir if repo_dir else config.get_path('pkgbuilds')
    foreign_arch = config.runtime['arch'] != arch
    deps = (list(set(package.depends) - set(package.names)))
    target_chroot = setup_build_chroot(
        arch=arch,
        extra_packages=deps,
        clean_chroot=clean_chroot,
    )
    native_chroot = target_chroot if not foreign_arch else setup_build_chroot(
        arch=config.runtime['arch'],
        extra_packages=['base-devel'] + CROSSDIRECT_PKGS,
        clean_chroot=clean_chroot,
    )
    cross = foreign_arch and package.mode == 'cross' and enable_crosscompile

    target_chroot.initialize()

    if cross:
        logging.info(f'Cross-compiling {package.path}')
        build_root = native_chroot
        makepkg_compile_opts += ['--nodeps']
        env = deepcopy(get_makepkg_env())
        if enable_ccache:
            env['PATH'] = f"/usr/lib/ccache:{env['PATH']}"
        logging.info('Setting up dependencies for cross-compilation')
        # include crossdirect for ccache symlinks and qemu-user
        results = native_chroot.try_install_packages(package.depends + CROSSDIRECT_PKGS + [f"{GCC_HOSTSPECS[native_chroot.arch][arch]}-gcc"])
        if results['crossdirect'].returncode != 0:
            raise Exception('Unable to install crossdirect')
        # mount foreign arch chroot inside native chroot
        chroot_relative = os.path.join(CHROOT_PATHS['chroots'], target_chroot.name)
        makepkg_path_absolute = native_chroot.write_makepkg_conf(target_arch=arch, cross_chroot_relative=chroot_relative, cross=True)
        makepkg_conf_path = os.path.join('etc', os.path.basename(makepkg_path_absolute))
        native_chroot.mount_crosscompile(target_chroot)
    else:
        logging.info(f'Host-compiling {package.path}')
        build_root = target_chroot
        makepkg_compile_opts += ['--syncdeps']
        env = deepcopy(get_makepkg_env())
        if foreign_arch and enable_crossdirect and package.name not in CROSSDIRECT_PKGS:
            env['PATH'] = f"/native/usr/lib/crossdirect/{arch}:{env['PATH']}"
            target_chroot.mount_crossdirect(native_chroot)
        else:
            if enable_ccache:
                logging.debug('ccache enabled')
                env['PATH'] = f"/usr/lib/ccache:{env['PATH']}"
                deps += ['ccache']
            logging.debug(('Building for native arch. ' if not foreign_arch else '') + 'Skipping crossdirect.')
        dep_install = target_chroot.try_install_packages(deps, allow_fail=False)
        failed_deps = [name for name, res in dep_install.items() if res.returncode != 0]
        if failed_deps:
            raise Exception(f'Dependencies failed to install: {failed_deps}')

    makepkg_conf_absolute = os.path.join('/', makepkg_conf_path)
    setup_sources(package, build_root, makepkg_conf_path=makepkg_conf_absolute)

    build_cmd = f'makepkg --config {makepkg_conf_absolute} --skippgpcheck --needed --noconfirm --ignorearch {" ".join(makepkg_compile_opts)}'
    logging.debug(f'Building: Running {build_cmd}')
    result = build_root.run_cmd(build_cmd, inner_env=env, cwd=os.path.join(CHROOT_PATHS['pkgbuilds'], package.path))

    if result.returncode != 0:
        raise Exception(f'Failed to compile package {package.path}')


def get_unbuilt_package_levels(repo: dict[str, Package], packages: list[Package], arch: Arch, force: bool = False) -> list[set[Package]]:
    package_levels = generate_dependency_chain(repo, packages)
    build_names = set[str]()
    build_levels = list[set[Package]]()
    i = 0
    for level_packages in package_levels:
        level = set[Package]()
        for package in level_packages:
            if ((not check_package_version_built(package, arch)) or set.intersection(set(package.depends), set(build_names)) or
                (force and package in packages)):
                level.add(package)
                build_names.update(package.names)
        if level:
            build_levels.append(level)
            logging.debug(f'Adding to level {i}:' + '\n' + ('\n'.join([p.name for p in level])))
            i += 1
    return build_levels


def build_packages(
    repo: dict[str, Package],
    packages: list[Package],
    arch: Arch,
    force: bool = False,
    enable_crosscompile: bool = True,
    enable_crossdirect: bool = True,
    enable_ccache: bool = True,
    clean_chroot: bool = False,
):
    build_levels = get_unbuilt_package_levels(repo, packages, arch, force=force)

    if not build_levels:
        logging.info('Everything built already')
        return

    files = []
    for level, need_build in enumerate(build_levels):
        logging.info(f"(Level {level}) Building {', '.join([x.name for x in need_build])}")
        for package in need_build:
            build_package(
                package,
                arch=arch,
                enable_crosscompile=enable_crosscompile,
                enable_crossdirect=enable_crossdirect,
                enable_ccache=enable_ccache,
                clean_chroot=clean_chroot,
            )
            files += add_package_to_repo(package, arch)
    return files


def build_packages_by_paths(
    paths: list[str],
    arch: Arch,
    repo: dict[str, Package],
    force=False,
    enable_crosscompile: bool = True,
    enable_crossdirect: bool = True,
    enable_ccache: bool = True,
    clean_chroot: bool = False,
):
    if isinstance(paths, str):
        paths = [paths]

    for _arch in set([arch, config.runtime['arch']]):
        init_prebuilts(_arch)
    packages = filter_packages_by_paths(repo, paths, allow_empty_results=False)
    return build_packages(
        repo,
        packages,
        arch,
        force=force,
        enable_crosscompile=enable_crosscompile,
        enable_crossdirect=enable_crossdirect,
        enable_ccache=enable_ccache,
        clean_chroot=clean_chroot,
    )


def build_enable_qemu_binfmt(arch: Arch, repo: dict[str, Package] = None):
    if arch not in ARCHES:
        raise Exception(f'Unknown architecture "{arch}". Choices: {", ".join(ARCHES)}')
    enforce_wrap()
    if not repo:
        repo = discover_packages()
    native = config.runtime['arch']
    # build qemu-user, binfmt, crossdirect
    chroot = setup_build_chroot(native)
    logging.info('Installing qemu-user (building if necessary)')
    build_packages_by_paths(
        ['cross/' + pkg for pkg in CROSSDIRECT_PKGS],
        native,
        repo,
        enable_crosscompile=False,
        enable_crossdirect=False,
        enable_ccache=False,
    )
    subprocess.run(['pacman', '-Syy', '--noconfirm', '--needed', '--config', os.path.join(chroot.path, 'etc/pacman.conf')] + QEMU_BINFMT_PKGS)
    if arch != native:
        binfmt_register(arch)


@click.group(name='packages')
def cmd_packages():
    """Build and manage packages and PKGBUILDs"""


@cmd_packages.command(name='update')
@click.option('--non-interactive', is_flag=True)
def cmd_update(non_interactive: bool = False):
    """Update PKGBUILDs git repo"""
    enforce_wrap()
    init_pkgbuilds(interactive=not non_interactive)


@cmd_packages.command(name='build')
@click.option('--force', is_flag=True, default=False, help='Rebuild even if package is already built')
@click.option('--arch', default=None, help="The CPU architecture to build for")
@click.argument('paths', nargs=-1)
def cmd_build(paths: list[str], force=False, arch=None):
    """
    Build packages by paths.

    The paths are specified relative to the PKGBUILDs dir, eg. "cross/crossdirect".

    Multiple paths may be specified as separate arguments.
    """
    build(paths, force, arch)


def build(paths: list[str], force: bool, arch: Arch):
    if arch is None:
        # TODO: arch = config.get_profile()...
        arch = 'aarch64'

    if arch not in ARCHES:
        raise Exception(f'Unknown architecture "{arch}". Choices: {", ".join(ARCHES)}')
    enforce_wrap()
    config.enforce_config_loaded()
    repo: dict[str, Package] = discover_packages()
    if arch != config.runtime['arch']:
        build_enable_qemu_binfmt(arch, repo=repo)

    return build_packages_by_paths(
        paths,
        arch,
        repo,
        force=force,
        enable_crosscompile=config.file['build']['crosscompile'],
        enable_crossdirect=config.file['build']['crossdirect'],
        enable_ccache=config.file['build']['ccache'],
        clean_chroot=config.file['build']['clean_mode'],
    )


@cmd_packages.command(name='sideload')
@click.argument('paths', nargs=-1)
def cmd_sideload(paths: list[str]):
    """Build packages, copy to the device via SSH and install them"""
    files = build(paths, True, None)
    scp_put_files(files, '/tmp')
    run_ssh_command([
        'sudo',
        '-S',
        'pacman',
        '-U',
    ] + [os.path.join('/tmp', os.path.basename(file)) for file in files] + [
        '--noconfirm',
        '--overwrite=*',
    ])


@cmd_packages.command(name='clean')
@click.option('-f', '--force', is_flag=True, default=False, help="Don't prompt for confirmation")
@click.option('-n', '--noop', is_flag=True, default=False, help="Print what would be removed but dont execute")
@click.argument('what', type=click.Choice(['all', 'src', 'pkg']), nargs=-1)
def cmd_clean(what: list[str] = ['all'], force: bool = False, noop: bool = False):
    """Remove files and directories not tracked in PKGBUILDs.git"""
    enforce_wrap()
    if noop:
        logging.debug('Running in noop mode!')
    if force:
        logging.debug('Running in FORCE mode!')
    pkgbuilds = config.get_path('pkgbuilds')
    if 'all' in what:
        warning = "Really reset PKGBUILDs to git state completely?\nThis will erase any untracked changes to your PKGBUILDs directory."
        if not (noop or force or click.confirm(warning)):
            return
        result = git(
            [
                'clean',
                '-dffX' + ('n' if noop else ''),
            ] + REPOSITORIES,
            dir=pkgbuilds,
        )
        if result.returncode != 0:
            logging.fatal('Failed to git clean')
            exit(1)
    else:
        what = set(what)
        dirs = []
        for loc in ['pkg', 'src']:
            if loc in what:
                logging.info(f'gathering {loc} directories')
                dirs += glob(os.path.join(pkgbuilds, '*', '*', loc))

        dir_lines = '\n'.join(dirs)
        verb = 'Would remove' if noop or force else 'Removing'
        logging.info(verb + ' directories:\n' + dir_lines)

        if not (noop or force):
            if not click.confirm("Really remove all of these?", default=True):
                return

        for dir in dirs:
            if not noop:
                rmtree(dir)


@cmd_packages.command(name='check')
@click.argument('paths', nargs=-1)
def cmd_check(paths):
    """Check that specified PKGBUILDs are formatted correctly"""
    enforce_wrap()
    paths = list(paths)
    packages = filter_packages_by_paths(discover_packages(), paths, allow_empty_results=False)

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
        pkgbuild_path = os.path.join(config.get_path('pkgbuilds'), package.path, 'PKGBUILD')
        with open(pkgbuild_path, 'r') as file:
            content = file.read()
            if '\t' in content:
                logging.fatal(f'\\t is not allowed in {pkgbuild_path}')
                exit(1)
            lines = content.split('\n')
            if len(lines) == 0:
                logging.fatal(f'Empty {pkgbuild_path}')
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

                if key == arches_key:
                    required_arches = line.split('=')[1]

                if line.endswith('=('):
                    hold_key = True

                if line.startswith('    ') or line == ')':
                    next_line = True

                if line.startswith('  ') and not line.startswith('    '):
                    formatted = False
                    reason = 'Multiline variables should be indented with 4 spaces'

                if '"' in line and '$' not in line and ' ' not in line and ';' not in line:
                    formatted = False
                    reason = 'Found literal " although no "$", " " or ";" was found in the line justifying the usage of a literal "'

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
                    logging.fatal(f'Formatting error in {pkgbuild_path}: Line {line_index+1}: "{line}"')
                    if reason != "":
                        logging.fatal(reason)
                    exit(1)

                if key == arch_key:
                    if line.endswith(')'):
                        if line.startswith(f'{arch_key}=('):
                            check_arches_hint(pkgbuild_path, required_arches, [line[6:-1]])
                        else:
                            check_arches_hint(pkgbuild_path, required_arches, provided_arches)
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
