FROM docker.io/library/ubuntu@sha256:6e75a10070b0fcb0bead763c5118a369bc7cc30dfc1b0749c491bbb21f15c3c7
MAINTAINER Frédéric Pierret <frederic@invisiblethingslab.com>

ARG DEBIAN_FRONTEND=noninteractive

# Install dependencies for Qubes Builder
RUN apt-get update && \
    apt-get install -y \
      curl \
      debootstrap \
      devscripts \
      dpkg-dev \
      e2fsprogs \
      fdisk \
      git \
      pbuilder \
      psutils \
      python3-debian \
      python3-yaml \
      reprepro \
      rpm \
      sudo \
      tree \
      udev \
      wget \
    && apt-get clean all

# Create build user
RUN useradd -m user -u 1010
RUN usermod -aG sudo user && echo '%sudo ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/sudo

# Create needed folders
RUN mkdir /builder /builder/plugins /builder/build /builder/distfiles /builder/cache /builder/repository /builder/sources
RUN chown -R user /builder

USER user
