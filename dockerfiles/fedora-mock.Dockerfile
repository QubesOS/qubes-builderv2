FROM scratch
MAINTAINER Frédéric Pierret <frederic@invisiblethingslab.com>

# Use Mock chroot
ADD cache.tar.gz /

# Install dependencies for Qubes Builder
RUN dnf -y update && \
    dnf install -y \
        arch-install-scripts \
        archlinux-keyring \
        asciidoc \
        createrepo_c \
        debian-keyring \
        debootstrap \
        devscripts \
        dosfstools \
        dnf-plugins-core \
        dosfstools \
        dpkg-dev \
        e2fsprogs \
        git \
        hostname \
        m4 \
        mock \
        pacman \
        pbuilder \
        perl-Digest-MD5 \
        perl-Digest-SHA \
        psmisc \
        python3-debian \
        python3-jinja2-cli \
        python3-pyyaml \
        python3-sh \
        pykickstart \
        reprepro \
        rpm-build \
        rpmdevtools \
        rsync  \
        systemd-udev \
        sudo \
        tree \
        wget \
        which \
        zstd \
    && dnf clean all

# Install devtools for Archlinux
RUN git clone -n https://gitlab.archlinux.org/fepitre/devtools && \
	cd devtools && \
	git checkout f91a1ac64d96a7cb38dc581eb4bd2ba0118d234c && \
	make install DESTDIR=/ PREFIX=/usr/local && \
	ln -s /usr/local/bin/archbuild /usr/local/bin/qubes-x86_64-build

# Create build user
RUN useradd -m user
RUN usermod -aG wheel user && echo '%wheel ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/wheel

# Create needed folders
RUN mkdir /builder /builder/plugins /builder/build /builder/distfiles /builder/cache /builder/repository /builder/sources
RUN chown -R user /builder

USER user
