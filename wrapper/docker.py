import logging
import os
import pathlib
import subprocess
import sys

from config import config
from constants import CHROOT_PATHS
from .wrapper import BaseWrapper

DOCKER_PATHS = CHROOT_PATHS.copy()


def docker_volumes_args(volume_mappings: dict[str, str]) -> list[str]:
    result = []
    for source, destination in volume_mappings.items():
        result += ['-v', f'{source}:{destination}:z']
    return result


class DockerWrapper(BaseWrapper):
    type = 'docker'

    def wrap(self):
        script_path = config.runtime['script_source_dir']
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
                logging.debug('Running docker cmd: ' + ' '.join(cmd))
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
            container_name = f'kupferbootstrap-{self.uuid}'

            wrapped_config = self.generate_wrapper_config()

            ssh_dir = os.path.join(pathlib.Path.home(), '.ssh')
            if not os.path.exists(ssh_dir):
                os.makedirs(ssh_dir)
            volumes = self.get_bind_mounts_default(wrapped_config)
            volumes |= dict({config.get_path(vol_name): vol_dest for vol_name, vol_dest in DOCKER_PATHS.items()})
            docker_cmd = [
                'docker',
                'run',
                '--name',
                container_name,
                '--rm',
                '--interactive',
                '--tty',
                '--privileged',
            ] + docker_volumes_args(volumes) + [tag]

            kupfer_cmd = ['kupferbootstrap', '--config', '/root/.config/kupfer/kupferbootstrap.toml'] + self.filter_args_wrapper(sys.argv[1:])

            cmd = docker_cmd + kupfer_cmd
            logging.debug('Wrapping in docker:' + repr(cmd))
            result = subprocess.run(cmd)

            exit(result.returncode)

    def stop(self):
        subprocess.run(
            [
                'docker',
                'kill',
                self.identifier,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


wrapper = DockerWrapper()
