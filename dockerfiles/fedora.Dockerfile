FROM docker.io/library/fedora@sha256:3ec60eb34fa1a095c0c34dd37cead9fd38afb62612d43892fcf1d3425c32bc1e
MAINTAINER Frédéric Pierret <frederic@invisiblethingslab.com>

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
        dnf-plugins-core \
        dpkg-dev \
        e2fsprogs \
        git \
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
RUN mkdir -p /builder/cache/mock
RUN chown -R user /builder

USER user
