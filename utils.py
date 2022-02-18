import atexit
import logging
import subprocess
from shutil import which
from typing import Optional, Union, Sequence


def programs_available(programs: Union[str, Sequence[str]]) -> bool:
    if type(programs) is str:
        programs = [programs]
    for program in programs:
        if not which(program):
            return False
    return True


def umount(dest: str, lazy=False):
    return subprocess.run(
        [
            'umount',
            '-c' + ('l' if lazy else ''),
            dest,
        ],
        capture_output=True,
    )


def mount(src: str, dest: str, options: list[str] = ['bind'], fs_type: Optional[str] = None, register_unmount=True) -> subprocess.CompletedProcess:
    opts = []
    for opt in options:
        opts += ['-o', opt]

    if fs_type:
        opts += ['-t', fs_type]

    result = subprocess.run(
        ['mount'] + opts + [
            src,
            dest,
        ],
        capture_output=False,
    )
    if result.returncode == 0 and register_unmount:
        atexit.register(umount, dest)
    return result


def check_findmnt(path: str):
    result = subprocess.run(
        [
            'findmnt',
            '-n',
            '-o',
            'source',
            path,
        ],
        capture_output=True,
    )
    return result.stdout.decode().strip()


def git(cmd: list[str], dir='.', capture_output=False) -> subprocess.CompletedProcess:
    return subprocess.run(['git'] + cmd, cwd=dir, capture_output=capture_output)


def log_or_exception(raise_exception: bool, msg: str, exc_class=Exception, log_level=logging.WARNING):
    if raise_exception:
        raise exc_class(msg)
    else:
        logging.log(log_level, msg)
