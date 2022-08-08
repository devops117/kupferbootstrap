from copy import deepcopy
import logging
import os
import tarfile
import tempfile
import urllib.request

from config import config

from .package import PackageInfo


def resolve_url(url_template, repo_name: str, arch: str):
    result = url_template
    for template, replacement in {'$repo': repo_name, '$arch': config.runtime['arch']}.items():
        result = result.replace(template, replacement)
    return result


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

    def resolve_url(self) -> str:
        return resolve_url(self.url_template, repo_name=self.name, arch=self.arch)

    def scan(self):
        self.resolved_url = self.resolve_url()
        self.remote = not self.resolved_url.startswith('file://')
        uri = f'{self.resolved_url}/{self.name}.db'
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

    def __init__(self, name: str, url_template: str, arch: str, options={}, scan=False):
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
