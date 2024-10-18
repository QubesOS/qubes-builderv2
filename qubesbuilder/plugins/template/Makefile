BUILDER_DIR := $(shell readlink -m $(dir $(abspath $(lastword $(MAKEFILE_LIST)))))

DIST_CODENAME ?= fc42
DIST_NAME ?= fedora
DIST_VER ?= 42
TEMPLATE_ENV_WHITELIST ?=

ifndef TEMPLATE_NAME
TEMPLATE_NAME := $(DIST_NAME)-$(DIST_VER)
ifdef TEMPLATE_FLAVOR
TEMPLATE_NAME := $(TEMPLATE_NAME)-$(TEMPLATE_FLAVOR)
endif
export TEMPLATE_NAME
endif

INSTALL_DIR ?= $(BUILDER_DIR)/mnt
ARTIFACTS_DIR ?= $(BUILDER_DIR)/artifacts
# copied/generate final template.conf
TEMPLATE_CONF ?= $(ARTIFACTS_DIR)/template.conf

# Required env variables for scripts
REQUIRED_ENV := DIST_CODENAME DIST_NAME DIST_VER PLUGINS_DIR ARTIFACTS_DIR CACHE_DIR \
 	TEMPLATE_CONTENT_DIR TEMPLATE_NAME TEMPLATE_VERSION PACKAGES_DIR TEMPLATE_SCRIPTS_DIR \
 	KEYS_DIR

# Expose those variables to template-building scripts
TEMPLATE_ENV_WHITELIST ?=
TEMPLATE_ENV_WHITELIST += $(REQUIRED_ENV) \
	TEMPLATE_FLAVOR TEMPLATE_OPTIONS \
	TEMPLATE_FLAVOR_DIR APPMENUS_DIR CONFIG_DIR \
	TEMPLATE_CONF \
	VERBOSE DEBUG PATH DISCARD_PREPARED_IMAGE \
	TEMPLATE_ROOT_WITH_PARTITIONS TEMPLATE_ROOT_SIZE \
	USE_QUBES_REPO_VERSION USE_QUBES_REPO_TESTING \
	BUILDER_TURBO_MODE REPO_PROXY FEDORA_MIRROR \
	CENTOS_MIRROR EPEL_MIRROR QUBES_MIRROR DEBIAN_MIRRORS \
	GENTOO_MIRROR ARCHLINUX_MIRROR FLAVORS_DIR RELEASE

# We may drop those legacy values. We add them to help into
# template plugins migration.
TEMPLATE_ENV_WHITELIST += DIST DISTRIBUTION

# Make sure names are < 32 characters, process aliases
fix_up := $(shell TEMPLATE_NAME=$(TEMPLATE_NAME) TEMPLATE_LABEL="$(TEMPLATE_LABEL)" \
	$(BUILDER_DIR)/scripts/builder-fix-filenames)

TEMPLATE_NAME := $(word 1,$(fix_up))

TEMPLATE_TIMESTAMP ?= $(shell date -u -d "@$(SOURCE_DATE_EPOCH)" +%Y%m%d%H%MZ 2>/dev/null || date -u +%Y%m%d%H%MZ)




.PHONY: template-name prepare build build-rootimg build-rpm

check:
	# Check that required env are defined
	$(foreach var,$(REQUIRED_ENV),$(if $(value $(var)),,$(error Undefined $(var))))

template-name:
	@echo $(TEMPLATE_NAME)

prepare:
	@echo "Building template: $(TEMPLATE_NAME) ($(TEMPLATE_TIMESTAMP))"
	mkdir -p $(ARTIFACTS_DIR)
	echo $(TEMPLATE_TIMESTAMP) > $(ARTIFACTS_DIR)/build_timestamp_$(TEMPLATE_NAME)

build: build-rootimg build-rpm

build-rootimg: build-rootimg-prepare build-rootimg-qubeize

build-rootimg-prepare: check
	sudo env -i $(foreach var,$(TEMPLATE_ENV_WHITELIST),$(var)="$($(var))") $(BUILDER_DIR)/scripts/prepare-image $(ARTIFACTS_DIR)/prepared_images/$(TEMPLATE_NAME).img $(INSTALL_DIR)

build-rootimg-qubeize: check
	sudo env -i $(foreach var,$(TEMPLATE_ENV_WHITELIST),$(var)="$($(var))")	$(BUILDER_DIR)/scripts/qubeize-image $(ARTIFACTS_DIR)/prepared_images/$(TEMPLATE_NAME).img $(INSTALL_DIR)

build-rpm: check
	sudo env -i $(foreach var,$(TEMPLATE_ENV_WHITELIST),$(var)="$($(var))") $(BUILDER_DIR)/scripts/build-template-rpm $(ARTIFACTS_DIR) $(TEMPLATE_NAME) $(TEMPLATE_VERSION) $(TEMPLATE_TIMESTAMP)

clean:
	sudo rm -fr $(ARTIFACTS_DIR)/qubeized_images/root.img.*
	sudo rm -fr $(ARTIFACTS_DIR)/qubeized_images/$(TEMPLATE_NAME)*
	sudo rm -fr $(ARTIFACTS_DIR)/rpmbuild/BUILDROOT/*
	sudo rm -fr $(ARTIFACTS_DIR)/rpmbuild/tmp/*
	# We're not removing any images from prepared_images/ intentionally
	# because the user might want to keep using the same image for a long time
	# and they are not dependent on any of the Qubes packages
