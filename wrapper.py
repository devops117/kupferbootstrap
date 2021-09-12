import atexit
import os
import subprocess
import sys
import appdirs
import uuid
import click
import logging
from config import config


def wrap_docker():
    script_path = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(script_path, 'version.txt')) as version_file:
        version = version_file.read().replace('\n', '')
        tag = f'registry.gitlab.com/kupfer/kupferbootstrap:{version}'
        if version == 'dev':
            logging.info(f'Building docker image "{tag}"')
            cmd = [
                'docker',
                'build',
                '.',
                '-t',
                tag,
            ] + (['-q'] if not config.runtime['verbose'] else [])
            result = subprocess.run(cmd, cwd=script_path, capture_output=True)
            if result.returncode != 0:
                logging.fatal('Failed to build docker image:\n' + result.stderr.decode())
                exit(1)
        else:
            # Check if the image for the version already exists
            result = subprocess.run(
                [
                    'docker',
                    'images',
                    '-q',
                    tag,
                ],
                capture_output=True,
            )
            if result.stdout == b'':
                logging.info(f'Pulling kupferbootstrap docker image version \'{version}\'')
                subprocess.run([
                    'docker',
                    'pull',
                    tag,
                ])
        container_name = f'kupferbootstrap-{str(uuid.uuid4())}'

        def at_exit():
            subprocess.run(
                [
                    'docker',
                    'kill',
                    container_name,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        atexit.register(at_exit)

        prebuilts_mount = []
        if os.getenv('KUPFERBOOTSTRAP_PREBUILTS') != '':
            prebuilts_mount = [
                '-v',
                f'{os.getenv("KUPFERBOOTSTRAP_PREBUILTS")}:/prebuilts:z',
            ]
        cmd = [
            'docker',
            'run',
            '--name',
            container_name,
            '--rm',
            '--interactive',
            '--tty',
            '--privileged',
            '-v',
            f'{os.getcwd()}:/src:z',
        ] + prebuilts_mount + [
            '-v',
            f'{os.path.join(appdirs.user_cache_dir("kupfer"),"chroot")}:/chroot:z',
            '-v',
            f'{os.path.join(appdirs.user_cache_dir("kupfer"),"pacman")}:/var/cache/pacman/pkg:z',
            '-v',
            f'{os.path.join(appdirs.user_cache_dir("kupfer"),"jumpdrive")}:/var/cache/jumpdrive:z',
            '-v',
            '/dev:/dev',
            #'-v', '/mnt/kupfer:/mnt/kupfer:z',
        ] + [tag, 'kupferbootstrap'] + sys.argv[1:]
        logging.debug('Wrapping in docker:' + repr(cmd))
        result = subprocess.run(cmd)

        exit(result.returncode)


def enforce_wrap(no_wrapper=False):
    if os.getenv('KUPFERBOOTSTRAP_DOCKER') != '1' and not no_wrapper:
        wrap_docker()


nowrapper_option = click.option(
    '--no-wrapper',
    'no_wrapper',
    is_flag=True,
    default=False,
    help='Disable the docker wrapper. Defaults to autodetection.',
)
