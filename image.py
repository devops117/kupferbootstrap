import atexit
import json
import os
import re
import subprocess
import click
import logging
from signal import pause
from subprocess import run, CompletedProcess
from typing import Optional

from chroot.device import DeviceChroot, get_device_chroot
from constants import Arch, BASE_PACKAGES, DEVICES, FLAVOURS
from config import config, Profile
from distro.distro import get_base_distro, get_kupfer_https
from packages import build_enable_qemu_binfmt, discover_packages, build_packages
from ssh import copy_ssh_keys
from wrapper import enforce_wrap

# image files need to be slightly smaller than partitions to fit
IMG_FILE_ROOT_DEFAULT_SIZE = "1800M"
IMG_FILE_BOOT_DEFAULT_SIZE = "90M"


def dd_image(input: str, output: str, blocksize='1M') -> CompletedProcess:
    cmd = [
        'dd',
        f'if={input}',
        f'of={output}',
        f'bs={blocksize}',
        'iflag=direct',
        'oflag=direct',
        'status=progress',
        'conv=sync,noerror',
    ]
    logging.debug(f'running dd cmd: {cmd}')
    return subprocess.run(cmd)


def partprobe(device: str):
    return subprocess.run(['partprobe', device])


def shrink_fs(loop_device: str, file: str, sector_size: int):
    # 8: 512 bytes sectors
    # 1: 4096 bytes sectors
    sectors_blocks_factor = 4096 // sector_size
    partprobe(loop_device)
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
    blocks = int(re.search('is now [0-9]+', result.stdout.decode('utf-8')).group(0).split(' ')[2])  # type: ignore
    sectors = blocks * sectors_blocks_factor  #+ 157812 - 25600

    logging.debug(f'Shrinking partition at {loop_device}p2 to {sectors} sectors')
    child_proccess = subprocess.Popen(
        ['fdisk', '-b', str(sector_size), loop_device],
        stdin=subprocess.PIPE,
    )
    child_proccess.stdin.write('\n'.join([  # type: ignore
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

    end_size = (end_sector + 1) * sector_size

    logging.debug(f'({end_sector} + 1) sectors * {sector_size} bytes/sector = {end_size} bytes')
    logging.info(f'Truncating {file} to {end_size} bytes')
    result = subprocess.run(['truncate', '-s', str(end_size), file])
    if result.returncode != 0:
        raise Exception(f'Failed to truncate {file}')
    partprobe(loop_device)


def get_device_and_flavour(profile_name: Optional[str] = None) -> tuple[str, str]:
    config.enforce_config_loaded()
    profile = config.get_profile(profile_name)
    if not profile['device']:
        raise Exception("Please set the device using 'kupferbootstrap config init ...'")

    if not profile['flavour']:
        raise Exception("Please set the flavour using 'kupferbootstrap config init ...'")

    return (profile['device'], profile['flavour'])


def get_image_name(device, flavour, img_type='full') -> str:
    return f'{device}-{flavour}-{img_type}.img'


def get_image_path(device, flavour, img_type='full') -> str:
    return os.path.join(config.get_path('images'), get_image_name(device, flavour, img_type))


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


def mount_chroot(rootfs_source: str, boot_src: str, chroot: DeviceChroot):
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


def create_img_file(image_path: str, size_str: str):
    result = subprocess.run([
        'truncate',
        '-s',
        size_str,
        image_path,
    ])
    if result.returncode != 0:
        raise Exception(f'Failed to allocate {image_path}')
    return image_path


def partition_device(device: str):
    boot_partition_size = '100MiB'
    create_partition_table = ['mklabel', 'msdos']
    create_boot_partition = ['mkpart', 'primary', 'ext2', '0%', boot_partition_size]
    create_root_partition = ['mkpart', 'primary', boot_partition_size, '100%']
    enable_boot = ['set', '1', 'boot', 'on']
    result = subprocess.run([
        'parted',
        '--script',
        device,
    ] + create_partition_table + create_boot_partition + create_root_partition + enable_boot)
    if result.returncode != 0:
        raise Exception(f'Failed to create partitions on {device}')


def create_filesystem(device: str, blocksize: int = 4096, label=None, options=[], fstype='ext4'):
    # blocksize can be 4k max due to pagesize
    blocksize = min(blocksize, 4096)
    if fstype.startswith('ext'):
        # blocksize for ext-fs must be >=1024
        blocksize = max(blocksize, 1024)

    labels = ['-L', label] if label else []
    cmd = [
        f'mkfs.{fstype}',
        '-F',
        '-b',
        str(blocksize),
    ] + labels + [device]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise Exception(f'Failed to create {fstype} filesystem on {device} with CMD: {cmd}')


def create_root_fs(device: str, blocksize: int):
    create_filesystem(device, blocksize=blocksize, label='kupfer_root', options=['-O', '^metadata_csum', '-N', '100000'])


def create_boot_fs(device: str, blocksize: int):
    create_filesystem(device, blocksize=blocksize, label='kupfer_boot', fstype='ext2')


def install_rootfs(
    rootfs_device: str,
    bootfs_device: str,
    device: str,
    flavour: str,
    arch: Arch,
    packages: list[str],
    use_local_repos: bool,
    profile: Profile,
):
    user = profile['username'] or 'kupfer'
    post_cmds = FLAVOURS[flavour].get('post_cmds', [])
    chroot = get_device_chroot(device=device, flavour=flavour, arch=arch, packages=packages, use_local_repos=use_local_repos)

    mount_chroot(rootfs_device, bootfs_device, chroot)

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
    files = {
        'etc/pacman.conf': get_base_distro(arch).get_pacman_conf(check_space=True, extra_repos=get_kupfer_https(arch).repos),
        'etc/sudoers.d/wheel': "# allow members of group wheel to execute any command\n%wheel ALL=(ALL:ALL) ALL\n",
    }
    for target, content in files.items():
        with open(os.path.join(chroot.path, target.lstrip('/')), 'w') as file:
            file.write(content)
    if post_cmds:
        result = chroot.run_cmd(' && '.join(post_cmds))
        assert isinstance(result, subprocess.CompletedProcess)
        if result.returncode != 0:
            raise Exception('Error running post_cmds')

    logging.info('Preparing to unmount chroot')
    res = chroot.run_cmd('sync && umount /boot', attach_tty=True)
    logging.debug(f'rc: {res}')
    chroot.deactivate()

    logging.debug(f'Unmounting rootfs at "{chroot.path}"')
    res = run(['umount', chroot.path])
    logging.debug(f'rc: {res.returncode}')


@click.group(name='image')
def cmd_image():
    """Build and manage device images"""


@cmd_image.command(name='build')
@click.argument('profile_name', required=False)
@click.option('--local-repos/--no-local-repos', '-l/-L', default=True, help='Whether to use local packages. Defaults to true.')
@click.option('--build-pkgs/--no-build-pkgs', '-p/-P', default=True, help='Whether to build missing/outdated local packages. Defaults to true.')
@click.option('--block-target', default=None, help='Override the block device file to target')
@click.option('--skip-part-images', default=False, help='Skip creating image files for the partitions and directly work on the target block device.')
def cmd_build(profile_name: str = None, local_repos: bool = True, build_pkgs: bool = True, block_target: str = None, skip_part_images: bool = False):
    """Build a device image"""
    enforce_wrap()
    profile: Profile = config.get_profile(profile_name)
    device, flavour = get_device_and_flavour(profile_name)
    size_extra_mb: int = int(profile["size_extra_mb"])

    # TODO: PARSE DEVICE ARCH AND SECTOR SIZE
    arch = 'aarch64'
    sector_size = 4096
    rootfs_size_mb = FLAVOURS[flavour].get('size', 2) * 1000

    packages = BASE_PACKAGES + DEVICES[device] + FLAVOURS[flavour]['packages'] + profile['pkgs_include']

    if arch != config.runtime['arch']:
        build_enable_qemu_binfmt(arch)

    if local_repos and build_pkgs:
        logging.info("Making sure all packages are built")
        repo = discover_packages()
        build_packages(repo, [p for name, p in repo.items() if name in packages], arch)

    image_path = block_target or get_image_path(device, flavour)

    os.makedirs(os.path.dirname(image_path), exist_ok=True)

    logging.info(f'Creating new file at {image_path}')
    create_img_file(image_path, f"{rootfs_size_mb + size_extra_mb}M")

    loop_device = losetup_rootfs_image(image_path, sector_size)

    partition_device(loop_device)
    partprobe(loop_device)

    boot_dev: str
    root_dev: str
    loop_boot = loop_device + 'p1'
    loop_root = loop_device + 'p2'
    if skip_part_images:
        boot_dev = loop_boot
        root_dev = loop_root
    else:
        logging.info('Creating per-partition image files')
        boot_dev = create_img_file(get_image_path(device, flavour, 'boot'), IMG_FILE_BOOT_DEFAULT_SIZE)
        root_dev = create_img_file(get_image_path(device, flavour, 'root'), f'{rootfs_size_mb + size_extra_mb - 200}M')

    create_boot_fs(boot_dev, sector_size)
    create_root_fs(root_dev, sector_size)

    install_rootfs(
        root_dev,
        boot_dev,
        device,
        flavour,
        arch,
        packages,
        local_repos,
        profile,
    )

    if not skip_part_images:
        logging.info('Copying partition image files into full image:')
        logging.info(f'Block-copying /boot to {image_path}')
        dd_image(input=boot_dev, output=loop_boot)
        logging.info(f'Block-copying rootfs to {image_path}')
        dd_image(input=root_dev, output=loop_root)

    logging.info(f'Done! Image saved to {image_path}')


@cmd_image.command(name='inspect')
@click.option('--shell', '-s', is_flag=True)
@click.argument('profile')
def cmd_inspect(profile: str = None, shell: bool = False):
    """Open a shell in a device image"""
    enforce_wrap()
    device, flavour = get_device_and_flavour(profile)
    # TODO: get arch from profile
    arch = 'aarch64'
    # TODO: PARSE DEVICE SECTOR SIZE
    sector_size = 4096
    chroot = get_device_chroot(device, flavour, arch)
    image_path = get_image_path(device, flavour)
    loop_device = losetup_rootfs_image(image_path, sector_size)
    partprobe(loop_device)
    mount_chroot(loop_device + 'p2', loop_device + 'p1', chroot)

    logging.info(f'Inspect the rootfs image at {chroot.path}')

    if shell:
        chroot.initialized = True
        chroot.activate()
        if arch != config.runtime['arch']:
            logging.info('Installing requisites for foreign-arch shell')
            build_enable_qemu_binfmt(arch)
        logging.info('Starting inspection shell')
        chroot.run_cmd('/bin/bash')
    else:
        pause()
