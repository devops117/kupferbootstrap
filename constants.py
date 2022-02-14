FASTBOOT = 'fastboot'
FLASH_PARTS = {
    'ROOTFS': 'rootfs',
    'ABOOT': 'aboot',
    'LK2ND': 'lk2nd',
    'QHYPSTUB': 'qhypstub',
}
EMMC = 'emmc'
MICROSD = 'microsd'
LOCATIONS = [EMMC, MICROSD]

JUMPDRIVE = 'jumpdrive'
JUMPDRIVE_VERSION = '0.8'

BOOT_STRATEGIES = {
    'oneplus-enchilada': FASTBOOT,
    'xiaomi-beryllium-ebbg': FASTBOOT,
    'xiaomi-beryllium-tianma': FASTBOOT,
    'bq-paella': FASTBOOT,
}

DEVICES = {
    'oneplus-enchilada': ['device-sdm845-oneplus-enchilada'],
    'xiaomi-beryllium-ebbg': ['device-sdm845-xiaomi-beryllium-ebbg'],
    'xiaomi-beryllium-tianma': ['device-sdm845-xiaomi-beryllium-tianma'],
    'bq-paella': ['device-msm8916-bq-paella'],
}

BASE_PACKAGES = [
    'base',
    'base-kupfer',
    'nano',
    'vim',
]

FLAVOURS = {
    'barebone': {
        'packages': [],
    },
    'debug-shell': {
        'packages': ['hook-debug-shell'],
    },
    'gnome': {
        'packages': ['gnome', 'archlinux-appstream-data', 'gnome-software-packagekit-plugin'],
        'post_cmds': ['systemctl enable gdm'],
        'size': 8,
    },
    'phosh': {
        'packages': [
            'phosh',
            #'squeekboard', #temporarily disabled
            'gnome-control-center',
            'gnome-software',
            'gnome-software-packagekit-plugin',
            'archlinux-appstream-data',
            'gnome-initial-setup',
            'kgx',
            'iio-sensor-proxy',
        ],
        'post_cmds': ['systemctl enable phosh'],
        'size': 5,
    }
}

REPOSITORIES = [
    'boot',
    'cross',
    'device',
    'firmware',
    'linux',
    'main',
    'phosh',
]

Arch = str
ARCHES = [
    'x86_64',
    'aarch64',
]

BASE_DISTROS = {
    'x86_64': {
        'repos': {
            'core': 'http://ftp.halifax.rwth-aachen.de/archlinux/$repo/os/$arch',
            'extra': 'http://ftp.halifax.rwth-aachen.de/archlinux/$repo/os/$arch',
            'community': 'http://ftp.halifax.rwth-aachen.de/archlinux/$repo/os/$arch',
        },
    },
    'aarch64': {
        'repos': {
            'core': 'http://mirror.archlinuxarm.org/$arch/$repo',
            'extra': 'http://mirror.archlinuxarm.org/$arch/$repo',
            'community': 'http://mirror.archlinuxarm.org/$arch/$repo',
            'alarm': 'http://mirror.archlinuxarm.org/$arch/$repo',
            'aur': 'http://mirror.archlinuxarm.org/$arch/$repo',
        },
    },
}

KUPFER_HTTPS = 'https://gitlab.com/kupfer/packages/prebuilts/-/raw/main/$repo'

DistroArch = TargetArch = Arch

COMPILE_ARCHES: dict[Arch, str] = {
    'x86_64': 'amd64',
    'aarch64': 'arm64',
}

GCC_HOSTSPECS: dict[DistroArch, dict[TargetArch, str]] = {
    'x86_64': {
        'x86_64': 'x86_64-pc-linux-gnu',
        'aarch64': 'aarch64-linux-gnu',
    },
    'aarch64': {
        'aarch64': 'aarch64-unknown-linux-gnu',
    }
}

CFLAGS_GENERAL = ['-O2', '-pipe', '-fstack-protector-strong']
CFLAGS_ARCHES: dict[Arch, list[str]] = {
    'x86_64': ['-march=x86-64', '-mtune=generic'],
    'aarch64': [
        '-march=armv8-a',
        '-fexceptions',
        '-Wp,-D_FORTIFY_SOURCE=2',
        '-Wformat',
        '-Werror=format-security',
        '-fstack-clash-protection',
    ]
}

QEMU_BINFMT_PKGS = ['qemu-user-static-bin', 'binfmt-qemu-static']
CROSSDIRECT_PKGS = ['crossdirect'] + QEMU_BINFMT_PKGS

SSH_DEFAULT_HOST = '172.16.42.1'
SSH_DEFAULT_PORT = 22
SSH_COMMON_OPTIONS = [
    '-o',
    'GlobalKnownHostsFile=/dev/null',
    '-o',
    'UserKnownHostsFile=/dev/null',
    '-o',
    'StrictHostKeyChecking=no',
]

CHROOT_PATHS = {
    'chroots': '/chroot',
    'jumpdrive': '/var/cache/jumpdrive',
    'pacman': '/var/cache/pacman',
    'packages': '/prebuilts',
    'pkgbuilds': '/pkgbuilds',
    'images': '/images',
}
