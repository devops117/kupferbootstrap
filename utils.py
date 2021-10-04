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


def mount(src: str, dest: str, options=['bind'], type=None) -> subprocess.CompletedProcess:
    opts = []
    type = []

    for opt in options:
        opts += ['-o', opt]

    if type:
        type = ['-t', type]

    result = subprocess.run(['mount'] + type + opts + [
        src,
        dest,
    ])
    if result.returncode == 0:
        atexit.register(umount, dest)
    return result
    if result.returncode == 0:
        atexit.register(umount, dest)
    return result
