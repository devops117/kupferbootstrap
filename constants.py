FASTBOOT = 'fastboot'
FLASH_PARTS = {
    'ROOTFS': 'rootfs',
    'BOOTIMG': 'bootimg',
    'LK2ND': 'lk2nd',
    'QHYPSTUB': 'qhypstub',
}
EMMC = 'emmc'
EMMCFILE = 'emmc-file'
MICROSD = 'microsd'
LOCATIONS = [EMMC, EMMCFILE, MICROSD]

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
}

REPOSITORIES = [
    'boot',
    'device',
    'firmware',
    'linux',
    'main',
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

GCC_HOSTSPECS: dict[DistroArch, dict[TargetArch, str]] = {
    'x86_64': {
        'x86_64': 'x86_64-pc-linux-gnu',
        'aarch64': 'aarch64-linux-gnu',
    },
    'aarch64': {
        'aarch64': 'aarch64-unknown-linux-gnu',
    }
}

CROSSDIRECT_PKGS = ['crossdirect', 'qemu-user-static-bin', 'binfmt-qemu-static-all-arch']
