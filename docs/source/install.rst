############
Installation
############


#.
  Install Python 3, Docker, and git.

  On Arch: ``pacman -S python docker git --needed --noconfirm``

  .. Hint::
      After installing Docker you will have to add your user to the ``docker`` group:

      ``sudo usermod -aG docker "$(whoami)"``

      Then restart your desktop session for the new group to take effect.

#. Pick which Kupferbootstrap branch to clone: usually either ``main`` or ``dev``

#. Clone the repository: ``git clone -b INSERT_BRANCHNAME_HERE https://gitlab.com/kupfer/kupferbootstrap``

#. Change into the folder: ``cd kupferbootstrap``

#.
  Install python dependencies: ``pip3 install -r requirements.txt``

  .. Note::
      Most of our python dependencies are available as distro packages on most distros,
      sadly it's incomplete on Arch.

      See ``requirements.txt`` for the list of required python packages.

#. Symlink ``kupferbootstrap`` into your ``$PATH``: ``sudo ln -s "$(pwd)/bin/kupferbootstrap" /usr/local/bin/``

#. You should now be able to run ``kupferbootstrap --help``!
