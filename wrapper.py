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
    'pacman': '/var/cache/pacman',
    'packages': '/prebuilts',
    'pkgbuilds': '/pkgbuilds',
}


def wrap_docker():

    def _docker_volumes(volume_mappings: dict[str, str]) -> list[str]:
        result = []
        for source, destination in volume_mappings.items():
            result += ['-v', f'{source}:{destination}:z']
        return result

    def _filter_args(args):
        """hack. filter out --config since it doesn't apply in docker"""
        results = []
        done = False
        for i, arg in enumerate(args):
            if done:
                break
            if arg[0] != '-':
                results += args[i:]
                done = True
                break
            for argname in ['--config', '-C']:
                if arg.startswith(argname):
                    done = True
                    if arg != argname:  # arg is longer, assume --arg=value
                        offset = 1
                    else:
                        offset = 2
                    results += args[i + offset:]
                    break
            if not done:
                results.append(arg)
        return results

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
        ] + _docker_volumes(volumes) + [tag]

        kupfer_cmd = ['kupferbootstrap'] + _filter_args(sys.argv[1:])

        cmd = docker_cmd + kupfer_cmd
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
