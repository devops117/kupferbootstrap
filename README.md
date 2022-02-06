# kupferbootstrap

Kupfer Linux bootstrapping tool - drives pacstrap, makepkg, mkfs and fastboot, just to name a few.

## Installation
Install Docker, Python 3 with libraries `click`, `appdirs`, `joblib`, `toml` and put `bin/` into your `PATH`.
Then use `kupferbootstrap`.

## Usage
1. Initialise config with defaults: `kupferbootstrap config init -N`
1. Configure your device profile: `kupferbootstrap config profile init`
1. Build an image and packages along the way: `kupferbootstrap image build`


## Development
Put `dev` into `version.txt` to always rebuild kupferboostrap from this directory and use `kupferbootstrap` as normal.
