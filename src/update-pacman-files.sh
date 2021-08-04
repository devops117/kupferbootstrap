#!/bin/sh
set -e

wget https://raw.githubusercontent.com/archlinuxarm/PKGBUILDs/master/core/pacman/makepkg.conf -O makepkg.conf
sed -i "s/@CARCH@/aarch64/g" makepkg.conf
sed -i "s/@CHOST@/aarch64-unknown-linux-gnu/g" makepkg.conf
sed -i "s/@CARCHFLAGS@/-march=armv8-a /g" makepkg.conf
sed -i "s/xz /xz -T0 /g" makepkg.conf
sed -i "s/ check / !check /g" makepkg.conf
chroot="/chroot/copy"
include="-I\${CROOT}/usr/include -I$chroot/usr/include"
lib_croot="\${CROOT}/lib"
lib_chroot="$chroot/usr/lib"
cat >>makepkg.conf <<EOF

export CROOT="/usr/aarch64-linux-gnu"
export ARCH="arm64"
export CROSS_COMPILE="aarch64-linux-gnu-"
export CC="aarch64-linux-gnu-gcc $include -L$lib_croot -L$lib_chroot"
export CXX="aarch64-linux-gnu-g++ $include -L$lib_croot -L$lib_chroot"
export CFLAGS="\$CFLAGS $include"
export CXXFLAGS="\$CXXFLAGS $include"
export LDFLAGS="\$LDFLAGS,-L$lib_croot,-L$lib_chroot,-rpath-link,$lib_croot,-rpath-link,$lib_chroot"
EOF
# TODO: Set PACKAGER
wget https://raw.githubusercontent.com/archlinuxarm/PKGBUILDs/master/core/pacman/pacman.conf -O pacman.conf
sed -i "s/@CARCH@/aarch64/g" pacman.conf
sed -i "s/#ParallelDownloads.*/ParallelDownloads = 8/g" pacman.conf
sed -i "s/SigLevel.*/SigLevel = Never/g" pacman.conf
sed -i "s/^CheckSpace/#CheckSpace/g" pacman.conf
sed -i "s|/mirrorlist|/aarch64_mirrorlist|g" pacman.conf
