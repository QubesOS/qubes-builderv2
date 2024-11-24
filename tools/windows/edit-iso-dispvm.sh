#!/bin/bash

# This script is run in a dispvm

set -efo pipefail

ISO_DEV="/dev/xvdi"
ISO_FILES="/home/user/iso"
OUTPUT="/home/user/win-build.iso"

INPUT_DIR="$(mktemp -d -p ~)"
OUTPUT_DIR="$(mktemp -d -p ~)"

sudo mount -r "${ISO_DEV}" "${INPUT_DIR}"

echo "[*] Extracting unmodified iso..."
cp -rp "${INPUT_DIR}/." "${OUTPUT_DIR}"
sudo umount "${ISO_DEV}"
rmdir "${INPUT_DIR}"

echo "[*] Adding files..."
sudo cp -r "${ISO_FILES}/." "${OUTPUT_DIR}"

# Generate random password for the Windows user
set +e  # `head` below causes SEGPIPE...
WIN_PASS=$(tr -dc 'A-Za-z0-9!"#$%&'\''()*+,-.:;<=>?@[\]^_`{|}~' </dev/urandom | head -c 16)
set -e
sudo sed -i -e "s/@PASSWORD@/${WIN_PASS}/g" "${OUTPUT_DIR}/autounattend.xml"

echo "[*] Generating final image..."
genisoimage \
    -quiet \
    -bboot/etfsboot.com \
    -no-emul-boot \
    -boot-load-seg 1984 \
    -boot-load-size 8 \
    -iso-level 2 \
    -J -l -D -N \
    -joliet-long \
    -allow-limited-size \
    -relaxed-filenames \
    -o "${OUTPUT}" \
    "${OUTPUT_DIR}"
