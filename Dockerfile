FROM archlinux:base-devel

RUN pacman -Syu --noconfirm \
    python python-pip \
    arch-install-scripts rsync \
    aarch64-linux-gnu-gcc aarch64-linux-gnu-binutils aarch64-linux-gnu-glibc aarch64-linux-gnu-linux-api-headers \
    git \
    android-tools openssh inetutils \
    parted

RUN sed -i "s/EUID == 0/EUID == -1/g" $(which makepkg)

RUN cd /tmp && \
    git clone https://aur.archlinux.org/aarch64-linux-gnu-pkg-config.git && \
    cd aarch64-linux-gnu-pkg-config && \
    makepkg -s --skippgpcheck && \
    pacman -U --noconfirm *.pkg*

RUN yes | pacman -Scc

RUN sed -i "s/SigLevel.*/SigLevel = Never/g" /etc/pacman.conf

ENV KUPFERBOOTSTRAP_DOCKER=1
ENV PATH=/app/bin:/app/local/bin:$PATH
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN python -c "import distro; distro.get_kupfer_local(arch=None,in_chroot=False).repos_config_snippet()" | tee -a /etc/pacman.conf

WORKDIR /
