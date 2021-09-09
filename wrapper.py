import atexit
import os
import subprocess
import sys
import appdirs
import uuid

if os.getenv('KUPFERBOOTSTRAP_DOCKER') == '1':
    from main import main
    main()
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

        result = subprocess.run([
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
        ] + [tag, 'kupferbootstrap'] + sys.argv[1:])

        exit(result.returncode)
