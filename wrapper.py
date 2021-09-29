import atexit
import os
import subprocess
import sys
import uuid
import click
import logging
from config import config, dump_file as dump_config_file
from utils import programs_available

DOCKER_PATHS = {
    'chroots': '/chroot',
    'jumpdrive': '/var/cache/jumpdrive',
    'pacman': '/var/cache/pacman/pkg',
    'packages': '/prebuilts',
    'pkgbuilds': '/src',
}


def wrap_docker():

    def _docker_volumes(volume_mappings: dict[str, str]) -> list[str]:
        result = []
        for source, destination in volume_mappings.items():
            result += ['-v', f'{source}:{destination}:z']
        return result
        os.readl

    script_path = os.path.dirname(os.path.realpath(__file__))
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
        wrapped_config = f'/tmp/kupfer/{container_name}_wrapped.toml'

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
            os.remove(wrapped_config)

        atexit.register(at_exit)

        dump_config_file(file_path=wrapped_config, config=(config.file | {'paths': DOCKER_PATHS}))
        volumes = {
            '/dev': '/dev',
            os.getcwd(): '/src',
            wrapped_config: '/root/.config/kupfer/kupferbootstrap.toml',
        }
        volumes |= dict({(config.file['paths'][vol_name], vol_dest) for vol_name, vol_dest in DOCKER_PATHS.items()})
        if os.getenv('KUPFERBOOTSTRAP_PREBUILTS'):
            volumes |= {os.getenv("KUPFERBOOTSTRAP_PREBUILTS"): '/prebuilts'}
        cmd = [
            'docker',
            'run',
            '--name',
            container_name,
            '--rm',
            '--interactive',
            '--tty',
            '--privileged',
        ] + _docker_volumes(volumes) + [tag, 'kupferbootstrap'] + sys.argv[1:]
        logging.debug('Wrapping in docker:' + repr(cmd))
        result = subprocess.run(cmd)

        exit(result.returncode)


def enforce_wrap(no_wrapper=False):
    if os.getenv('KUPFERBOOTSTRAP_DOCKER') != '1' and not config.runtime['no_wrap'] and not no_wrapper:
        wrap_docker()


def check_programs_wrap(programs):
    if not programs_available(programs):
        enforce_wrap()


nowrapper_option = click.option(
    '--no-wrapper',
    'no_wrapper',
    is_flag=True,
    default=False,
    help='Disable the docker wrapper. Defaults to autodetection.',
)
