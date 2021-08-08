import atexit
import os
import subprocess
import sys
import appdirs

if os.getenv('KUPFERBOOTSTRAP_DOCKER') == '1':
    from main import cli
    cli(prog_name='kupferbootstrap')
else:
    script_path = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(script_path, 'version.txt')) as version_file:
        version = version_file.read().replace('\n', '')
        tag = f'registry.gitlab.com/kupfer/kupferbootstrap:{version}'
        if version == 'dev':
            result = subprocess.run(
                [
                    'docker',
                    'build',
                    '.',
                    '-t',
                    tag,
                ],
                cwd=script_path,
            )
            if result.returncode != 0:
                print(f'Failed to build kupferbootstrap docker image')
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
                print(f'Pulling kupferbootstrap docker image version \'{version}\'')
                subprocess.run([
                    'docker',
                    'pull',
                    tag,
                ])

        def at_exit():
            subprocess.run(
                [
                    'docker',
                    'kill',
                    'kupferbootstrap',
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        atexit.register(at_exit)

        # TODO: Remove the mount of /usr/share/i18n/locales. It's a trick so we don't need to generate the locales in the chroot.
        # Something like a prebuilt docker image as base or copying the files from it would be good.
        subprocess.run([
            'docker',
            'run',
            '--name',
            'kupferbootstrap',
            '--rm',
            '--interactive',
            '--tty',
            '--privileged',
            '-v',
            f'{os.getcwd()}:/src:z',
            '-v',
            f'{os.path.join(appdirs.user_cache_dir("kupfer"),"chroot")}:/chroot:z',
            '-v',
            f'{os.path.join(appdirs.user_cache_dir("kupfer"),"pacman")}:/var/cache/pacman/pkg:z',
            '-v',
            f'{os.path.join(appdirs.user_cache_dir("kupfer"),"jumpdrive")}:/var/cache/jumpdrive:z',
            '-v',
            '/dev:/dev',
            #'-v', '/mnt/kupfer:/mnt/kupfer:z',
            '-v',
            '/usr/share/i18n/locales:/usr/share/i18n/locales:ro'
        ] + [tag, 'kupferbootstrap'] + sys.argv[1:])
