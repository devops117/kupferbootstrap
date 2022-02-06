import atexit
import json
import os
import re
import subprocess
import click
import logging

from signal import pause
from subprocess import run

from chroot import Chroot, get_device_chroot
from constants import BASE_PACKAGES, DEVICES, FLAVOURS
from config import config
from distro import get_base_distro, get_kupfer_https, get_kupfer_local
from packages import build_enable_qemu_binfmt, discover_packages, build_packages
from ssh import copy_ssh_keys
from wrapper import enforce_wrap


def partprobe(device: str):
    return subprocess.run(['partprobe', device])


def shrink_fs(loop_device: str, file: str, sector_size: int):
    # 8: 512 bytes sectors
    # 1: 4096 bytes sectors
    sectors_blocks_factor = 4096 // sector_size

    logging.debug(f"Checking filesystem at {loop_device}p2")
    result = subprocess.run(['e2fsck', '-fy', f'{loop_device}p2'])
    if result.returncode > 2:
        # https://man7.org/linux/man-pages/man8/e2fsck.8.html#EXIT_CODE
        raise Exception(f'Failed to e2fsck {loop_device}p2 with exit code {result.returncode}')

    logging.debug(f'Shrinking filesystem at {loop_device}p2')
    result = subprocess.run(['resize2fs', '-M', f'{loop_device}p2'], capture_output=True)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        raise Exception(f'Failed to resize2fs {loop_device}p2')

    logging.debug(f'Finding end block of shrunken filesystem on {loop_device}p2')
    blocks = int(re.search('is now [0-9]+', result.stdout.decode('utf-8')).group(0).split(' ')[2])
    sectors = blocks * sectors_blocks_factor  #+ 157812 - 25600

    logging.debug(f'Shrinking partition at {loop_device}p2 to {sectors} sectors')
    child_proccess = subprocess.Popen(
        ['fdisk', '-b', str(sector_size), loop_device],
        stdin=subprocess.PIPE,
    )
    child_proccess.stdin.write('\n'.join([
        'd',
        '2',
        'n',
        'p',
        '2',
        '',
        f'+{sectors}',
        'w',
        'q',
    ]).encode('utf-8'))

    child_proccess.communicate()

    returncode = child_proccess.wait()
    if returncode == 1:
        # For some reason re-reading the partition table fails, but that is not a problem
        subprocess.run(['partprobe'])
    if returncode > 1:
        raise Exception(f'Failed to shrink partition size of {loop_device}p2 with fdisk')

    partprobe(loop_device)

    logging.debug(f'Finding end sector of partition at {loop_device}p2')
    result = subprocess.run(['fdisk', '-b', str(sector_size), '-l', loop_device], capture_output=True)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        raise Exception(f'Failed to fdisk -l {loop_device}')

    end_sector = 0
    for line in result.stdout.decode('utf-8').split('\n'):
        if line.startswith(f'{loop_device}p2'):
            parts = list(filter(lambda part: part != '', line.split(' ')))
            end_sector = int(parts[2])

    if end_sector == 0:
        raise Exception(f'Failed to find end sector of {loop_device}p2')

    end_block = end_sector // sectors_blocks_factor

    logging.debug(f'Truncating {file} to {end_block} blocks')
    result = subprocess.run(['truncate', '-o', '-s', str(end_block), file])
    if result.returncode != 0:
        raise Exception(f'Failed to truncate {file}')
    partprobe(loop_device)


def get_device_and_flavour(profile: str = None) -> tuple[str, str]:
    config.enforce_config_loaded()
    profile = config.get_profile(profile)
    if not profile['device']:
        raise Exception("Please set the device using 'kupferbootstrap config init ...'")

    if not profile['flavour']:
        raise Exception("Please set the flavour using 'kupferbootstrap config init ...'")

    return (profile['device'], profile['flavour'])


def get_image_name(device_chroot: Chroot) -> str:
    return f'{device_chroot.name}.img'


def get_image_path(device_chroot: Chroot) -> str:
    return os.path.join(config.get_path('images'), get_image_name(device_chroot))


def losetup_rootfs_image(image_path: str, sector_size: int) -> str:
    logging.debug(f'Creating loop device for {image_path} with sector size {sector_size}')
    result = subprocess.run([
        'losetup',
        '-f',
        '-b',
        str(sector_size),
        '-P',
        image_path,
    ])
    if result.returncode != 0:
        logging.fatal(f'Failed to create loop device for {image_path}')
        exit(1)

    logging.debug(f'Finding loop device for {image_path}')

    result = subprocess.run(['losetup', '-J'], capture_output=True)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        logging.fatal('Failed to list loop devices')
        exit(1)

    data = json.loads(result.stdout.decode('utf-8'))
    loop_device = ''
    for d in data['loopdevices']:
        if d['back-file'] == image_path:
            loop_device = d['name']
            break

    if loop_device == '':
        raise Exception(f'Failed to find loop device for {image_path}')
    partprobe(loop_device)

    def losetup_destroy():
        logging.debug(f'Destroying loop device {loop_device} for {image_path}')
        subprocess.run(
            [
                'losetup',
                '-d',
                loop_device,
            ],
            stderr=subprocess.DEVNULL,
        )

    atexit.register(losetup_destroy)

    return loop_device


def mount_chroot(rootfs_source: str, boot_src: str, chroot: Chroot):
    logging.debug(f'Mounting {rootfs_source} at {chroot.path}')

    chroot.mount_rootfs(rootfs_source)
    assert (os.path.ismount(chroot.path))

    os.makedirs(chroot.get_path('boot'), exist_ok=True)

    logging.debug(f'Mounting {boot_src} at {chroot.path}/boot')
    chroot.mount(boot_src, '/boot', options=['defaults'])


def dump_aboot(image_path: str) -> str:
    path = '/tmp/aboot.img'
    result = subprocess.run([
        'debugfs',
        image_path,
        '-R',
        f'dump /aboot.img {path}',
    ])
    if result.returncode != 0:
        logging.fatal('Failed to dump aboot.img')
        exit(1)
    return path


def dump_lk2nd(image_path: str) -> str:
    """
    This doesn't append the image with the appended DTB which is needed for some devices, so it should get added in the future.
    """
    path = '/tmp/lk2nd.img'
    result = subprocess.run([
        'debugfs',
        image_path,
        '-R',
        f'dump /lk2nd.img {path}',
    ])
    if result.returncode != 0:
        logging.fatal('Failed to dump lk2nd.img')
        exit(1)
    return path


def dump_qhypstub(image_path: str) -> str:
    path = '/tmp/qhypstub.bin'
    result = subprocess.run([
        'debugfs',
        image_path,
        '-R',
        f'dump /qhypstub.bin {path}',
    ])
    if result.returncode != 0:
        logging.fatal('Failed to dump qhypstub.bin')
        exit(1)
    return path


@click.group(name='image')
def cmd_image():
    pass


@cmd_image.command(name='build')
@click.argument('profile_name', required=False)
@click.option('--build-pkgs/--no-build-pkgs', '-p/-P', default=True, help='Whether to build missing/outdated packages. Defaults to true.')
def cmd_build(profile_name: str = None, build_pkgs: bool = True):
    enforce_wrap()
    profile = config.get_profile(profile_name)
    device, flavour = get_device_and_flavour(profile_name)
    post_cmds = FLAVOURS[flavour].get('post_cmds', [])

    user = profile['username'] or 'kupfer'
    # TODO: PARSE DEVICE ARCH AND SECTOR SIZE
    arch = 'aarch64'
    sector_size = 4096

    build_enable_qemu_binfmt(arch)

    packages_dir = config.get_package_dir(arch)
    if os.path.exists(os.path.join(packages_dir, 'main')):
        extra_repos = get_kupfer_local(arch).repos
    else:
        extra_repos = get_kupfer_https(arch).repos
    packages = BASE_PACKAGES + DEVICES[device] + FLAVOURS[flavour]['packages'] + profile['pkgs_include']

    if build_pkgs:
        repo = discover_packages()
        build_packages(repo, [p for name, p in repo.items() if name in packages], arch)

    chroot = get_device_chroot(device=device, flavour=flavour, arch=arch, packages=packages, extra_repos=extra_repos)
    image_path = get_image_path(chroot)

    os.makedirs(config.get_path('images'), exist_ok=True)
    new_image = not os.path.exists(image_path)
    if new_image:
        result = subprocess.run([
            'truncate',
            '-s',
            f"{FLAVOURS[flavour].get('size',2)}G",
            image_path,
        ])
        if result.returncode != 0:
            raise Exception(f'Failed to allocate {image_path}')

    loop_device = losetup_rootfs_image(image_path, sector_size)

    if new_image:
        boot_partition_size = '100MiB'
        create_partition_table = ['mklabel', 'msdos']
        create_boot_partition = ['mkpart', 'primary', 'ext2', '0%', boot_partition_size]
        create_root_partition = ['mkpart', 'primary', boot_partition_size, '100%']
        enable_boot = ['set', '1', 'boot', 'on']
        result = subprocess.run([
            'parted',
            '--script',
            loop_device,
        ] + create_partition_table + create_boot_partition + create_root_partition + enable_boot)
        if result.returncode != 0:
            raise Exception(f'Failed to create partitions on {loop_device}')
        partprobe(loop_device)
        result = subprocess.run([
            'mkfs.ext2',
            '-F',
            '-L',
            'kupfer_boot',
            f'{loop_device}p1',
        ])
        if result.returncode != 0:
            raise Exception(f'Failed to create ext2 filesystem on {loop_device}p1')

        result = subprocess.run([
            'mkfs.ext4',
            '-O',
            '^metadata_csum',
            '-F',
            '-L',
            'kupfer_root',
            '-N',
            '100000',
            f'{loop_device}p2',
        ])
        if result.returncode != 0:
            raise Exception(f'Failed to create ext4 filesystem on {loop_device}p2')

    mount_chroot(loop_device + 'p2', loop_device + 'p1', chroot)

    chroot.mount_pacman_cache()
    chroot.initialize()
    chroot.activate()
    chroot.create_user(
        user=user,
        password=profile['password'],
    )

    copy_ssh_keys(
        chroot.path,
        user=user,
    )
    with open(os.path.join(chroot.path, 'etc', 'pacman.conf'), 'w') as file:
        file.write(get_base_distro(arch).get_pacman_conf(check_space=True, extra_repos=get_kupfer_https(arch).repos))
    if post_cmds:
        result = chroot.run_cmd(' && '.join(post_cmds))
        if result.returncode != 0:
            raise Exception('Error running post_cmds')

    logging.info('Preparing to unmount chroot')
    res = chroot.run_cmd('sync && umount /boot', attach_tty=True)
    logging.debug(f'rc: {res}')
    chroot.deactivate()

    logging.debug(f'Unmounting rootfs at "{chroot.path}"')
    res = run(['umount', chroot.path])
    logging.debug(f'rc: {res.returncode}')

    logging.info(f'Done! Image saved to {image_path}')


@cmd_image.command(name='inspect')
@click.option('--shell', '-s', is_flag=True)
def cmd_inspect(shell: bool = False):
    enforce_wrap()
    device, flavour = get_device_and_flavour()
    # TODO: get arch from profile
    arch = 'aarch64'
    # TODO: PARSE DEVICE SECTOR SIZE
    sector_size = 4096
    chroot = get_device_chroot(device, flavour, arch)
    image_path = get_image_path(chroot)
    loop_device = losetup_rootfs_image(image_path, sector_size)
    partprobe(loop_device)
    mount_chroot(loop_device + 'p2', loop_device + 'p1', chroot)

    logging.info(f'Inspect the rootfs image at {chroot.path}')

    if shell:
        chroot.initialized = True
        chroot.activate()
        build_enable_qemu_binfmt(arch)
        chroot.run_cmd('/bin/bash')
    else:
        pause()
