FROM archlinux:base-devel

RUN pacman -Syu --noconfirm \
    python python-pip \
    devtools rsync \
    aarch64-linux-gnu-gcc aarch64-linux-gnu-binutils aarch64-linux-gnu-glibc aarch64-linux-gnu-linux-api-headers \
    git

RUN sed -i "s/EUID == 0/EUID == -1/g" $(which makepkg)

RUN cd /tmp && \
    git clone https://aur.archlinux.org/aarch64-linux-gnu-pkg-config.git && \
    cd aarch64-linux-gnu-pkg-config && \
    makepkg -s --skippgpcheck && \
    pacman -U --noconfirm *.pkg*

RUN yes | pacman -Scc

RUN sed -i "s/SigLevel.*/SigLevel = Never/g" /etc/pacman.conf
RUN sed -i "s|run_pacman |run_pacman --root \"/chroot/copy\" --arch aarch64 --config \"/app/src/pacman_copy.conf\" |g" $(which makepkg)
RUN echo "Server = http://mirror.archlinuxarm.org/\$arch/\$repo" > /etc/pacman.d/aarch64_mirrorlist
RUN mkdir -p /app/bin
RUN ln -sf $(which aarch64-linux-gnu-strip) /app/bin/strip

RUN systemd-machine-id-setup

ENV KUPFERBOOTSTRAP_DOCKER=1
WORKDIR /app
ENV PATH=/app/bin:$PATH

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

WORKDIR /src
