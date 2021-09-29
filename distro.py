from copy import deepcopy
from constants import ARCHES, BASE_DISTROS
from config import config


def resolve_url(url_template, repo_name: str, arch: str):
    result = url_template
    for template, replacement in {'$repo': repo_name, '$arch': config.runtime['arch']}.items():
        result = result.replace(template, replacement)


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


class Repo:
    name: str
    url_template: str
    resolved_url: str
    arch: str
    packages: dict[str, PackageInfo]
    options: dict[str, str]
    remote: bool

    def scan(self):
        self.resolved_url = resolve_url(self.url_template, repo_name=self.repo_name, arch=self.arch)
        self.remote = not self.resolved_url.startswith('file://')
        # TODO

    def __init__(self, name: str, url_template: str, arch: str, options={}, scan=True):
        self.name = name
        self.url_template = url_template
        self.arch = arch
        self.options = deepcopy(options)
        if scan:
            self.scan()

    def config_snippet(self) -> str:
        options = {'Server': self.url_template} | self.options.items()
        return ('[%s]\n' % self.name) + '\n'.join([f"{key} = {value}" for key, value in options])


class RepoInfo:
    options: dict[str, str] = {}
    url_template: str

    def __init__(self, url_template: str, options: dict[str, str] = {}):
        self.url_template = url_template
        self.options.update(options)


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

    def config_snippet(self) -> str:
        return '\n'.join(repo.config_snippet() for repo in self.repos)


_base_distros: dict[str, Distro] = None


def get_base_distros() -> dict[str, Distro]:
    global _base_distros
    if not _base_distros:
        _distros: dict[str, Distro] = {}
        for arch, distro_conf in BASE_DISTROS.items():
            repos = {name: RepoInfo(url_template=url) for name, url in distro_conf['repos'].items()}
            _distros[arch] = Distro(arch=arch, repo_infos=repos, scan=False)
        _base_distros = _distros
    return _base_distros
