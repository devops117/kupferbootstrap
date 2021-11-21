from copy import deepcopy
import urllib.request
import tempfile
import os
import tarfile
import logging

from constants import ARCHES, BASE_DISTROS, REPOSITORIES, KUPFER_HTTPS
from config import config


def resolve_url(url_template, repo_name: str, arch: str):
    result = url_template
    for template, replacement in {'$repo': repo_name, '$arch': config.runtime['arch']}.items():
        result = result.replace(template, replacement)
    return result


class PackageInfo:
    name: str
    version: str
    filename: str
    resolved_url: str

    def __init__(
        self,
        name: str,
        version: str,
        filename: str,
        resolved_url: str = None,
    ):
        self.name = name
        self.version = version
        self.filename = filename
        self.resolved_url = resolved_url

    def __repr__(self):
        return f'{self.name}@{self.version}'

    def parse_desc(desc_str: str, resolved_url=None):
        """Parses a desc file, returning a PackageInfo"""

        pruned_lines = ([line.strip() for line in desc_str.split('%') if line.strip()])
        desc = {}
        for key, value in zip(pruned_lines[0::2], pruned_lines[1::2]):
            desc[key.strip()] = value.strip()
        return PackageInfo(desc['NAME'], desc['VERSION'], desc['FILENAME'], resolved_url=resolved_url)


class RepoInfo:
    options: dict[str, str] = {}
    url_template: str

    def __init__(self, url_template: str, options: dict[str, str] = {}):
        self.url_template = url_template
        self.options.update(options)


class Repo(RepoInfo):
    name: str
    resolved_url: str
    arch: str
    packages: dict[str, PackageInfo]
    remote: bool
    scanned: bool = False

    def scan(self):
        self.resolved_url = resolve_url(self.url_template, repo_name=self.name, arch=self.arch)
        self.remote = not self.resolved_url.startswith('file://')
        uri = f'{self.resolved_url}/{self.name}.db'
        file_handle = None
        path = ''
        if self.remote:
            logging.debug(f'Downloading repo file from {uri}')
            with urllib.request.urlopen(uri) as request:
                fd, path = tempfile.mkstemp()
                with open(fd, 'wb') as writable:
                    writable.write(request.read())
        else:
            path = uri.split('file://')[1]
        logging.debug(f'Parsing repo file at {path}')
        with tarfile.open(path) as index:
            for node in index.getmembers():
                if os.path.basename(node.name) == 'desc':
                    logging.debug(f'Parsing desc file for {os.path.dirname(node.name)}')
                    pkg = PackageInfo.parse_desc(index.extractfile(node).read().decode(), self.resolved_url)
                    self.packages[pkg.name] = pkg

        self.scanned = True

    def __init__(self, name: str, url_template: str, arch: str, options={}, scan=True):
        self.packages = {}
        self.name = name
        self.url_template = url_template
        self.arch = arch
        self.options = deepcopy(options)
        if scan:
            self.scan()

    def config_snippet(self) -> str:
        options = {'Server': self.url_template} | self.options
        return ('[%s]\n' % self.name) + '\n'.join([f"{key} = {value}" for key, value in options.items()])

    def get_RepoInfo(self):
        return RepoInfo(url_template=self.url_template, options=self.options)


class Distro:
    repos: dict[str, Repo]
    arch: str

    def __init__(self, arch: str, repo_infos: dict[str, RepoInfo], scan=True):
        assert (arch in ARCHES)
        self.arch = arch
        self.repos = dict[str, Repo]()
        for repo_name, repo_info in repo_infos.items():
            self.repos[repo_name] = Repo(
                name=repo_name,
                arch=arch,
                url_template=repo_info.url_template,
                options=repo_info.options,
                scan=scan,
            )

    def get_packages(self):
        """ get packages from all repos, semantically overlaying them"""
        results = dict[str, PackageInfo]()
        for repo in self.repos.values().reverse():
            assert (repo.packages is not None)
            for package in repo.packages:
                results[package.name] = package

    def _repos_config_snippet(self, extra_repos: dict[str, RepoInfo] = {}) -> str:
        extras = [Repo(name, url_template=info.url_template, arch=self.arch, options=info.options, scan=False) for name, info in extra_repos.items()]
        return '\n\n'.join(repo.config_snippet() for repo in (list(self.repos.values()) + extras))

    def get_pacman_conf(self, extra_repos: dict[str, RepoInfo] = {}, check_space=False):
        header = f'''
#
# /etc/pacman.conf
#
# See the pacman.conf(5) manpage for option and repository directives

#
# GENERAL OPTIONS
#
[options]
# The following paths are commented out with their default values listed.
# If you wish to use different paths, uncomment and update the paths.
#RootDir     = /
#DBPath      = /var/lib/pacman/
CacheDir    = /var/cache/pacman/{self.arch}/
#LogFile     = /var/log/pacman.log
#GPGDir      = /etc/pacman.d/gnupg/
#HookDir     = /etc/pacman.d/hooks/
HoldPkg     = pacman glibc
#XferCommand = /usr/bin/curl -L -C - -f -o %o %u
#XferCommand = /usr/bin/wget --passive-ftp -c -O %o %u
#CleanMethod = KeepInstalled
Architecture = {self.arch}

# Pacman won't upgrade packages listed in IgnorePkg and members of IgnoreGroup
#IgnorePkg   =
#IgnoreGroup =

#NoUpgrade   =
#NoExtract   =

# Misc options
#UseSyslog
Color
#NoProgressBar
{'' if check_space else '#'}CheckSpace
VerbosePkgLists
ParallelDownloads = 8

# By default, pacman accepts packages signed by keys that its local keyring
# trusts (see pacman-key and its man page), as well as unsigned packages.
SigLevel    = Required DatabaseOptional
LocalFileSigLevel = Optional
#RemoteFileSigLevel = Required

# NOTE: You must run `pacman-key --init` before first using pacman; the local
# keyring can then be populated with the keys of all official Arch Linux ARM
# packagers with `pacman-key --populate archlinuxarm`.

#
# REPOSITORIES
#   - can be defined here or included from another file
#   - pacman will search repositories in the order defined here
#   - local/custom mirrors can be added here or in separate files
#   - repositories listed first will take precedence when packages
#     have identical names, regardless of version number
#   - URLs will have $repo replaced by the name of the current repo
#   - URLs will have $arch replaced by the name of the architecture
#
# Repository entries are of the format:
#       [repo-name]
#       Server = ServerName
#       Include = IncludePath
#
# The header [repo-name] is crucial - it must be present and
# uncommented to enable the repo.
#

'''
        return header + self._repos_config_snippet(extra_repos)


def get_base_distro(arch: str) -> Distro:
    repos = {name: RepoInfo(url_template=url) for name, url in BASE_DISTROS[arch]['repos'].items()}
    return Distro(arch=arch, repo_infos=repos, scan=False)


def get_kupfer(arch: str, url_template: str) -> Distro:
    repos = {name: RepoInfo(url_template=url_template, options={'SigLevel': 'Never'}) for name in REPOSITORIES}
    return Distro(
        arch=arch,
        repo_infos=repos,
    )


def get_kupfer_https(arch: str) -> Distro:
    return get_kupfer(arch, KUPFER_HTTPS)


def get_kupfer_local(arch: str) -> Distro:
    return get_kupfer(arch, f"file://{config.get_path('packages')}/$arch/$repo")
