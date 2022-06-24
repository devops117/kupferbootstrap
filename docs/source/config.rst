#############
Configuration
#############


Kupferbootstrap uses `toml <https://en.wikipedia.org/wiki/TOML>`_ for its configuration file.

The file can either be edited manually or managed via the :doc:`cli/config` subcommand.

You can quickly generate a default config by running :code:`kupferbootstrap config init -N`.


File Location
#############

The configuration is stored in ``~/.config/kupfer/kupferbootstrap.toml``, where ``~`` is your user's home folder.


Sections
########

A config file is split into sections like so:

.. code-block:: toml

    [pkgbuilds]
    git_repo = "https://gitlab.com/kupfer/packages/pkgbuilds.git"
    git_branch = "dev"

    [pacman]
    parallel_downloads = 3


Here, we have two sections: ``pkgbuilds`` and ``pacman``.

Flavours
########

Flavours are preset collections of software and functionality to enable,
i.e. desktop environments like `Gnome <https://en.wikipedia.org/wiki/GNOME>`_
and `Phosh <https://en.wikipedia.org/wiki/Phosh>`_.


Profiles
########

The last section and currently the only one with subsections is the ``profiles`` section.

A profile is the configuration of a specific device image. It specifies (amongst others):

* the device model
* the flavour (desktop environment)
* the host- and user name
* extra packages to install

Using a profile's ``parent`` key,
you can inherit settings from another profile.

This allows you to easily keep a number of slight variations of the same target profile around
without the need to constantly modify your Kupferbootstrap configuration file.

You can easily create new profiles with
`kupferbootstrap config profile init <../cli/config/#kupferbootstrap-config-profile-init>`_.

Here's an example:

.. code:: toml

    [profiles]
    current = "graphical"

    [profiles.default]
    parent = ""
    device = "oneplus-enchilada"
    flavour = "phosh"
    pkgs_include = [ "wget", "rsync", "nano", "tmux", "zsh", "pv", ]
    pkgs_exclude = []
    hostname = "kupferphone"
    username = "prawn"
    size_extra_mb = 800

    [profiles.graphical]
    parent = "default"
    pkgs_include = [ "firefox", "tilix", "gnome-tweaks" ]
    size_extra_mb = "+3000"

    [profiles.hades]
    parent = "graphical"
    flavour = "phosh"
    hostname = "hades"

    [profiles.recovery]
    parent = "default"
    flavour = "debug-shell"

    [profiles.beryllium]
    parent = "graphical"
    device = "xiaomi-beryllium-ebbg"
    flavour = "gnome"
    hostname = "pocof1"



The ``current`` key in the ``profiles`` section controlls which profile gets used by Kupferbootstrap by default.

The first subsection (``profiles.default``) describes the `default` profile
which gets created by `config init <../cli/config/#kupferbootstrap-config-init>`_.

Next, we have a `graphical` profile that defines a couple of graphical programs for all but the `recovery` profile,
since that doesn't have a GUI.

``size_extra_mb``
-----------------

Note how ``size_extra_mb`` can either be a plain integer (``800``) or a string,
optionally leading with a plus sign (``+3000``),
which instructs Kupferbootstrap to add the value to the parent profile's ``size_extra_mb``.

``pkgs_include`` / ``pkgs_exclude``
-----------------------------------

Like ``size_extra_mb``, ``pkgs_include`` will be merged with the parent profile's ``pkgs_include``.

To exclude unwanted packages from being inherited from a parent profile, use ``pkgs_exclude`` in the child profile.

.. hint::
    ``pkgs_exclude`` has no influence on Pacman's dependency resolution.
    It only blocks packages during image build that would usually be explicitly installed
    due to being listed in a parent profile or the selected flavour.
