from copy import deepcopy
import os
import subprocess

from chroot import Chroot
from constants import CHROOT_PATHS, MAKEPKG_CMD

from .package import PackageInfo


class Pkgbuild(PackageInfo):
    depends: list[str] = None
    provides: list[str] = None
    replaces: list[str] = None
    local_depends: list[PackageInfo] = None
    subpackages: list[PackageInfo] = None
    repo = ''
    mode = ''
    path = ''
    pkgver = ''
    pkgrel = ''

    def __init__(
        self,
        relative_path: str,
        depends: list[str] = [],
        provides: list[str] = [],
        replaces: list[str] = [],
        subpackages: list[PackageInfo] = [],
    ) -> None:
        self.version = None
        self.path = relative_path
        self.depends = deepcopy(depends)
        self.provides = deepcopy(provides)
        self.replaces = deepcopy(replaces)
        self.subpackages = deepcopy(subpackages)

    def __repr__(self):
        return f'Package({self.name},{repr(self.path)},{self.version},{self.mode})'

    def names(self):
        return list(set([self.name] + self.provides + self.replaces))


def parse_pkgbuild(relative_pkg_dir: str, native_chroot: Chroot) -> list[Pkgbuild]:
    mode = None
    with open(os.path.join(native_chroot.get_path(CHROOT_PATHS['pkgbuilds']), relative_pkg_dir, 'PKGBUILD'), 'r') as file:
        for line in file.read().split('\n'):
            if line.startswith('_mode='):
                mode = line.split('=')[1]
                break
    if mode not in ['host', 'cross']:
        raise Exception((f'{relative_pkg_dir}/PKGBUILD has {"no" if mode is None else "an invalid"} mode configured') +
                        (f': "{mode}"' if mode is not None else ''))

    base_package = Pkgbuild(relative_pkg_dir)
    base_package.mode = mode
    base_package.repo = relative_pkg_dir.split('/')[0]
    srcinfo = native_chroot.run_cmd(
        MAKEPKG_CMD + ['--printsrcinfo'],
        cwd=os.path.join(CHROOT_PATHS['pkgbuilds'], base_package.path),
        stdout=subprocess.PIPE,
    )
    lines = srcinfo.stdout.decode('utf-8').split('\n')

    current = base_package
    multi_pkgs = False
    for line_raw in lines:
        line = line_raw.strip()
        if not line:
            continue
        splits = line.split(' = ')
        if line.startswith('pkgbase'):
            base_package.name = splits[1]
            multi_pkgs = True
        elif line.startswith('pkgname'):
            if multi_pkgs:
                if current is not base_package:
                    base_package.subpackages.append(current)
                current = deepcopy(base_package)
            current.name = splits[1]
        elif line.startswith('pkgver'):
            current.pkgver = splits[1]
        elif line.startswith('pkgrel'):
            current.pkgrel = splits[1]
        elif line.startswith('provides'):
            current.provides.append(splits[1])
        elif line.startswith('replaces'):
            current.replaces.append(splits[1])
        elif line.startswith('depends') or line.startswith('makedepends') or line.startswith('checkdepends') or line.startswith('optdepends'):
            current.depends.append(splits[1].split('=')[0].split(': ')[0])
    current.depends = list(set(current.depends))

    results = base_package.subpackages or [base_package]
    for pkg in results:
        pkg.version = f'{pkg.pkgver}-{pkg.pkgrel}'
        if not (pkg.pkgver == base_package.pkgver and pkg.pkgrel == base_package.pkgrel):
            raise Exception('subpackage malformed! pkgver differs!')

    return results
