# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2022 Frédéric Pierret (fepitre) <frederic@invisiblethingslab.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program. If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

DIST ?= fc37
DIST_VER = $(subst fc,,$(DIST))

BUILDER_DIR ?= /builder
PLUGINS_DIR ?= $(BUILDER_DIR)/plugins
INSTALLER_DIR ?= $(PLUGINS_DIR)/installer
SOURCES_DIR ?= $(BUILDER_DIR)/sources
INSTALLER_KICKSTART ?= $(SOURCES_DIR)/qubes-release/conf/qubes-kickstart.cfg
CREATEREPO := $(shell which createrepo_c createrepo 2>/dev/null |head -1)


ISO_VERSION ?= $(shell date -d @"$(SOURCE_DATE_EPOCH)" +%Y%m%d 2>/dev/null || date +%Y%m%d)
COMPS_FILE ?= $(SOURCES_DIR)/qubes-release/comps/comps-dom0.xml
QUBES_VERSION ?= $(shell cat $(SOURCES_DIR)/qubes-release/version)

ifneq (,$(ISO_FLAVOR))
ISO_NAME := Qubes-$(ISO_VERSION)-$(ISO_FLAVOR)-x86_64
else
ISO_NAME := Qubes-$(ISO_VERSION)-x86_64
endif
ISO_VOLID := $(shell echo $(ISO_NAME) | tr a-z A-Z | tr -s -c [:alnum:]'\n' - | head -c 32)

BASE_DIR := $(INSTALLER_DIR)/work/$(ISO_VERSION)/x86_64
TMP_DIR:= $(BUILDER_DIR)/tmp

DNF := /usr/bin/dnf
DNF_ROOT := $(BUILDER_DIR)/dnfroot
DNF_REPO := $(DNF_ROOT)/etc/yum.repos.d/installer.repo
DNF_PACKAGES := $(DNF_ROOT)/tmp/packages.list
DNF_OPTS := -y --releasever=$(DIST_VER) --installroot=$(DNF_ROOT) --config=$(DNF_ROOT)/etc/dnf/dnf.conf
DNF_OPTS_TEMPLATES := $(DNF_OPTS) --disablerepo=fedora --disablerepo=fedora-updates --disablerepo=installer
DNF_OPTS_TEMPLATES += --disablerepo=qubes-host --repofrompath builder-local,$(BUILDER_DIR)/repository

LORAX := /usr/sbin/lorax
LORAX_PACKAGES := $(DNF_ROOT)/tmp/lorax_packages.list
LORAX_OPTS := --product "Qubes OS" --variant "qubes" --macboot --force --rootfs-size=4
LORAX_OPTS += --version "$(ISO_VERSION)" --release "Qubes OS $(ISO_VERSION)" --volid $(ISO_VOLID)
LORAX_OPTS += --workdir $(INSTALLER_DIR)/work/work/x86_64 --logfile $(INSTALLER_DIR)/work/logs/lorax-x86_64.log
LORAX_OPTS += --repo $(INSTALLER_DIR)/yum/lorax.repo --skip-branding --disablerepo=fedora --disablerepo=fedora-updates --disablerepo=updates --disablerepo='qubes-*'

ifeq ($(ISO_USE_KERNEL_LATEST),1)
    LORAX_OPTS += --installpkgs kernel-latest --excludepkgs kernel
endif

ifeq ($(ISO_IS_FINAL),1)
    LORAX_OPTS += --isfinal
endif

#
# CAGE -> MOCK | CAGE
#

iso-prepare:
	#
	# Prepare repositories
	#

	rm -rf $(TMP_DIR)

	# Copy the comps file
	mkdir -p $(TMP_DIR)
	cp $(COMPS_FILE) $(TMP_DIR)/comps.xml

	mkdir /tmp/qubes-installer
	mount --bind $(INSTALLER_DIR) /tmp/qubes-installer

	cp -r $(SOURCES_DIR)/qubes-release /tmp/qubes-installer/

	mkdir -p /tmp/qubes-installer/yum/installer/rpm
	rm -rf $(INSTALLER_DIR)/yum/installer/repodata
	$(CREATEREPO) -q -g $(TMP_DIR)/comps.xml /tmp/qubes-installer/yum/installer

	mkdir -p $(BUILDER_DIR)/repository /tmp/qubes-installer/yum/qubes-host
	rm -rf $(BUILDER_DIR)/repository/repodata
	$(CREATEREPO) -q -g $(TMP_DIR)/comps.xml $(BUILDER_DIR)/repository
	mount --bind $(BUILDER_DIR)/repository /tmp/qubes-installer/yum/qubes-host

	#
	# Prepare DNF
	#

	# Destination directory for RPM
	mkdir -p $(BASE_DIR)/os/Packages

	# Create default DNF conf
	mkdir -p $(DNF_ROOT)/etc/dnf
	cp $(INSTALLER_DIR)/yum/dnf.conf $(DNF_ROOT)/etc/dnf/

	# Copy Fedora key to DNF installroot
	mkdir -p $(DNF_ROOT)/etc/pki/rpm-gpg
	cp $(SOURCES_DIR)/qubes-release/RPM-GPG-KEY-fedora-$(DIST_VER)-primary $(DNF_ROOT)/etc/pki/rpm-gpg

#
# CAGE | CAGE -> MOCK
#

iso-parse-kickstart:
	mkdir -p $(DNF_ROOT)/etc/yum.repos.d $(DNF_ROOT)/tmp
	$(INSTALLER_DIR)/scripts/ksparser --extract-repo-conf-to $(DNF_REPO) --extract-packages-to $(DNF_PACKAGES) $(INSTALLER_KICKSTART)

#
# CAGE -> MOCK
#

iso-parse-tmpl:
	$(INSTALLER_DIR)/scripts/tmplparser --repo $(DNF_REPO) --extract-packages-to $(LORAX_PACKAGES)

#
# CAGE
#

iso-packages-anaconda:
	$(DNF) $(DNF_OPTS) clean all
	# workaround for https://github.com/rpm-software-management/dnf/issues/1974
	rpmkeys --root=$(DNF_ROOT) --import $$(sed -n '/gpgkey *= *file:/{s,.*file://,,;p}' $(DNF_ROOT)/etc/yum.repos.d/*.repo)
	umask 022; $(DNF) $(DNF_OPTS) --downloaddir=$(BASE_DIR)/os/Packages --downloadonly install $(shell cat $(DNF_PACKAGES))
	pushd $(BASE_DIR)/os/ && $(CREATEREPO) -q -g $(TMP_DIR)/comps.xml .

iso-packages-lorax:
	$(DNF) $(DNF_OPTS) clean all
	umask 022; $(DNF) $(DNF_OPTS) --downloaddir=$(INSTALLER_DIR)/yum/installer/rpm --downloadonly install $(shell cat $(LORAX_PACKAGES))
	pushd $(INSTALLER_DIR)/yum/installer && $(CREATEREPO) -q -g $(TMP_DIR)/comps.xml --update .

iso-templates-cache: iso-prepare iso-parse-kickstart
	#mkdir -p $(DNF_ROOT)/etc/yum.repos.d/ $(DNF_ROOT)/etc/qubes/repo-templates/keys/
	#cp -r $(SOURCES_DIR)/repo-templates/repos/RPM-GPG-KEY-* $(DNF_ROOT)/etc/qubes/repo-templates/keys/
	#cp $(SOURCES_DIR)/repo-templates/repos/qubes-templates.repo $(DNF_ROOT)/etc/yum.repos.d/

	# Adapt repo file for new dnfroot
	#sed -i -e 's/$$releasever/$(QUBES_VERSION)/g' $(DNF_ROOT)/etc/yum.repos.d/qubes-templates.repo
	#sed -i 's|gpgkey *= *file://\(.*\)|gpgkey=file://$(DNF_ROOT)\1|g' $(DNF_ROOT)/etc/yum.repos.d/qubes-templates.repo

	$(DNF) $(DNF_OPTS) clean all
	rpmkeys --root=$(DNF_ROOT) --import $$(sed -n '/gpgkey *= *file:/{s,.*file://,,;p}' $(DNF_ROOT)/etc/yum.repos.d/*.repo)
	umask 022; $(DNF) $(DNF_OPTS_TEMPLATES) --downloaddir=$(INSTALLER_DIR)/yum/installer/rpm --downloaddir=$(BUILDER_DIR)/repository/templates download $(TEMPLATE_PACKAGES)

#
# CAGE -> MOCK
#

iso-installer-lorax:
	$(LORAX) $(LORAX_OPTS) $(BASE_DIR)/os

iso-installer-mkisofs:
	mkdir -p $(BASE_DIR)/iso/
	chmod og+rX -R $(BASE_DIR)/os/
	xorrisofs -o $(BASE_DIR)/iso/$(ISO_NAME).iso \
		-R -J -V $(ISO_VOLID) \
		--grub2-mbr /usr/lib/grub/i386-pc/boot_hybrid.img \
		--mbr-force-bootable \
		--gpt-iso-bootable \
		-partition_offset 16 \
		-append_partition 2 0xef $(BASE_DIR)/os/images/efiboot.img \
		-iso_mbr_part_type EBD0A0A2-B9E5-4433-87C0-68B6B72699C7 \
		-appended_part_as_gpt \
		-c boot.cat --boot-catalog-hide \
		-b images/eltorito.img \
		-no-emul-boot -boot-load-size 4 -boot-info-table --grub2-boot-info \
		-eltorito-alt-boot \
		-e '--interval:appended_partition_2:all::' -no-emul-boot \
		-graft-points \
		.=$(BASE_DIR)/os \
		boot/grub2/i386-pc=/usr/lib/grub/i386-pc
	/usr/bin/implantisomd5 $(BASE_DIR)/iso/$(ISO_NAME).iso
