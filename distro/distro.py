from typing import Optional, Mapping

from constants import ARCHES, BASE_DISTROS, REPOSITORIES, KUPFER_HTTPS, CHROOT_PATHS
from generator import generate_pacman_conf_body
from config import config

from .package import PackageInfo
from .repo import RepoInfo, Repo


class Distro:
    repos: Mapping[str, Repo]
    arch: str

    def __init__(self, arch: str, repo_infos: dict[str, RepoInfo], scan=False):
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

    def get_packages(self) -> dict[str, PackageInfo]:
        """ get packages from all repos, semantically overlaying them"""
        results = dict[str, PackageInfo]()
        for repo in list(self.repos.values())[::-1]:
            assert (repo.packages is not None)
            for package in repo.packages:
                results[package.name] = package
        return results

    def repos_config_snippet(self, extra_repos: Mapping[str, RepoInfo] = {}) -> str:
        extras = [Repo(name, url_template=info.url_template, arch=self.arch, options=info.options, scan=False) for name, info in extra_repos.items()]
        return '\n\n'.join(repo.config_snippet() for repo in (extras + list(self.repos.values())))

    def get_pacman_conf(self, extra_repos: Mapping[str, RepoInfo] = {}, check_space: bool = True):
        body = generate_pacman_conf_body(self.arch, check_space=check_space)
        return body + self.repos_config_snippet(extra_repos)


def get_base_distro(arch: str) -> Distro:
    repos = {name: RepoInfo(url_template=url) for name, url in BASE_DISTROS[arch]['repos'].items()}
    return Distro(arch=arch, repo_infos=repos, scan=False)


def get_kupfer(arch: str, url_template: str, scan: bool = False) -> Distro:
    repos = {name: RepoInfo(url_template=url_template, options={'SigLevel': 'Never'}) for name in REPOSITORIES}
    return Distro(
        arch=arch,
        repo_infos=repos,
    )


def get_kupfer_https(arch: str, scan: bool = False) -> Distro:
    return get_kupfer(arch, KUPFER_HTTPS.replace('%branch%', config.file['pacman']['repo_branch']), scan)


def get_kupfer_local(arch: Optional[str] = None, in_chroot: bool = True) -> Distro:
    arch = arch or config.runtime['arch']
    dir = CHROOT_PATHS['packages'] if in_chroot else config.get_path('packages')
    return get_kupfer(arch, f"file://{dir}/$arch/$repo")
