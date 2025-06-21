FROM docker.io/library/debian@sha256:e83913597ca9deb9d699316a9a9d806c2a87ed61195ac66ae0a8ac55089a84b9
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
RUN useradd -m user -u 1000
RUN usermod -aG sudo user && echo '%sudo ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/sudo

# Create needed folders
RUN mkdir /builder /builder/plugins /builder/build /builder/distfiles /builder/cache /builder/repository /builder/sources
RUN chown -R user /builder

USER user
