#!/bin/bash

set -ef

EDITED_ISO="win-build.iso"
SCRIPT_DIR=$(dirname "$0")
SCRIPT_DIR=$(readlink -f "$SCRIPT_DIR")
SSH_KEY="/home/user/.ssh/win-build.key"

usage() {
    echo "Usage: $(basename "$0") [OPTIONS]

This script prepares an .iso image for the Windows builder executor qube.

Options:
    --iso   Path to unmodified Windows installation .iso file
"
}

if ! OPTS=$(getopt -o hi: --long help,iso: -n "$0" -- "$@"); then
    exit 1
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h | --help) usage; exit 0 ;;
        -i | --iso) ISO="$2"; shift ;;
    esac
    shift
done

if [ -z "$ISO" ]; then
    usage
    exit 1
fi

# download/verify prerequisites
"$SCRIPT_DIR/get-files.sh" "-o $SCRIPT_DIR" "$SCRIPT_DIR/deps.txt"

# sshd installer
cp -f "${SCRIPT_DIR}/win-opensshd.msi" "${SCRIPT_DIR}/iso-files/sources/\$OEM\$/\$1/qubes"

# ssh key
if [ -f "${SSH_KEY}" ]; then
    echo "[*] Using existing ssh key: ${SSH_KEY}"
else
    echo "[*] Creating ssh key: ${SSH_KEY}"
    ssh-keygen -q -t ed25519 -N '' -f "${SSH_KEY}"
fi

cp -f "${SSH_KEY}.pub" "${SCRIPT_DIR}/iso-files/sources/\$OEM\$/\$1/qubes"

# prepare edited iso
"${SCRIPT_DIR}/edit-iso.sh" --input "$ISO" --output "$EDITED_ISO" --files "${SCRIPT_DIR}/iso-files"
