from typing import Optional


class PackageInfo:
    name: str
    version: str
    filename: str
    resolved_url: Optional[str]

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

    @staticmethod
    def parse_desc(desc_str: str, resolved_url=None):
        """Parses a desc file, returning a PackageInfo"""

        pruned_lines = ([line.strip() for line in desc_str.split('%') if line.strip()])
        desc = {}
        for key, value in zip(pruned_lines[0::2], pruned_lines[1::2]):
            desc[key.strip()] = value.strip()
        return PackageInfo(desc['NAME'], desc['VERSION'], desc['FILENAME'], resolved_url='/'.join([resolved_url, desc['FILENAME']]))
