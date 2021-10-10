from shutil import which
import atexit
import subprocess


def programs_available(programs) -> bool:
    if type(programs) is str:
        programs = [programs]
    for program in programs:
        if not which(program):
            return False
    return True


def umount(dest):
    return subprocess.run(
        [
            'umount',
            '-lc',
            dest,
        ],
        capture_output=True,
    )


def mount(src: str, dest: str, options=['bind'], fs_type=None) -> subprocess.CompletedProcess:
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
    if result.returncode == 0:
        atexit.register(umount, dest)
    return result
