#!/bin/bash

set -efo pipefail

unset INPUT OUTPUT FILES OPTS GETOPT_COMPATIBLE INPUT_DIR OUTPUT_DIR LODEV

usage() {
    echo "Usage: $(basename "$0") [OPTIONS]

This script modifies a Windows installation ISO image.

Options:
    --input   Input (unmodified) .iso file path
    --output  Output file path
    --files   Directory containing files to add (should contain autounattend.xml)
              Installed partition's root directory corresponds to 'sources/\$OEM\$/\$1' on the image
"
}

if ! OPTS=$(getopt -o hi:o:f: --long help,input:,output:,files: -n "$0" -- "$@"); then
    exit 1
fi

eval set -- "$OPTS"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h | --help) usage; exit 0 ;;
        -i | --input) INPUT="$2"; shift ;;
        -o | --output) OUTPUT="$2"; shift ;;
        -f | --files) FILES="$2"; shift ;;
    esac
    shift
done

if [ -z "${INPUT}" ] || [ -z "${OUTPUT}" ] || [ -z "${FILES}" ]; then
    usage
    exit 1
fi

# extract original iso
echo "[*] Extracting unmodified iso..."
LODEV=$(losetup -f)
sudo losetup "${LODEV}" "${INPUT}"

INPUT_DIR="$(mktemp -d -p .)"
OUTPUT_DIR="$(mktemp -d -p .)"

sudo mount -r "${LODEV}" "${INPUT_DIR}"
cp -rp "${INPUT_DIR}/." "${OUTPUT_DIR}"
sudo umount "${LODEV}"
sudo losetup -d "${LODEV}"
rmdir "${INPUT_DIR}"

echo "[*] Adding files..."
sudo cp -r "${FILES}/." "${OUTPUT_DIR}"

echo "[*] Generating final image..."
genisoimage -quiet -bboot/etfsboot.com -no-emul-boot -boot-load-seg 1984 -boot-load-size 8 -iso-level 2 -J -l -D -N -joliet-long -allow-limited-size -relaxed-filenames -o "${OUTPUT}" "${OUTPUT_DIR}"

sudo rm -rf "${OUTPUT_DIR}"
echo "[*] Done!"
