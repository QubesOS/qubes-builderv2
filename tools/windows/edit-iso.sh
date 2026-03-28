#!/bin/bash

set -efo pipefail

usage() {
    echo "Usage: $(basename "$0") [OPTIONS]

This script modifies a Windows installation ISO image.

Options:
    --input    Input (unmodified) .iso file path
    --output   Output file path
    --files    Directory containing files to add (should contain autounattend.xml)
               Installed partition's root directory corresponds to 'sources/\$OEM\$/\$1' on the image
    --verbose  Enable shell trace output (set -x)
"
}

if ! OPTS=$(getopt -o hi:o:f:v --long help,input:,output:,files:,verbose -n "$0" -- "$@"); then
    exit 1
fi

eval set -- "$OPTS"

VERBOSE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h | --help) usage; exit 0 ;;
        -i | --input) INPUT="$2"; shift ;;
        -o | --output) OUTPUT="$2"; shift ;;
        -f | --files) FILES="$2"; shift ;;
        -v | --verbose) VERBOSE=1 ;;
    esac
    shift
done

[ "${VERBOSE}" -eq 1 ] && set -x

if [ -z "${INPUT}" ] || [ -z "${OUTPUT}" ] || [ -z "${FILES}" ]; then
    usage
    exit 1
fi

# qvm-run requires more RPC permissions
# $1=target, $2=service, $3=input
qrexec_call() {
    readarray -t result <<< "$(echo -n "$3" | qrexec-client-vm "$1" "$2" | sed "s/\x00/\n/g")"
    if [ "${result[0]}" != "0" ]; then
        >&2 echo "qrexec call '$2' to '$1' failed: ${result[0]} ${result[1]}"
        exit 1
    fi
    echo "${result[1]}"
}

# $1=target, $2=input
shell_call() {
    echo "$2" | qrexec-client-vm "$1" qubes.VMShell
}

SCRIPT_DIR=$(dirname "$0")
SCRIPT_DIR=$(readlink -f "${SCRIPT_DIR}")

echo "[*] Setting up a loop device for the ISO..."
LODEV=$(sudo losetup -f)
sudo losetup "${LODEV}" "${INPUT}"
LOOP_ID="${LODEV#'/dev/'}"

echo "[*] Preparing a DispVM..."
SELF=$(qubesdb-read /name)
DISPVM=$(qrexec_call "dom0" admin.vm.CreateDisposable)

qrexec_call "${DISPVM}" "admin.vm.Start"
qrexec_call "${DISPVM}" "admin.vm.device.block.Attach+${SELF}+${LOOP_ID}" "read-only=true"
qvm-copy-to-vm --without-progress "${DISPVM}" "${SCRIPT_DIR}/edit-iso-dispvm.sh"
qvm-copy-to-vm --without-progress "${DISPVM}" "${FILES}"
shell_call "${DISPVM}" "mv ~/QubesIncoming/${SELF}/edit-iso-dispvm.sh ~"
shell_call "${DISPVM}" "mv ~/QubesIncoming/${SELF}/$(basename "$(realpath "${FILES}")") ~/iso"
shell_call "${DISPVM}" "chmod +x ~/edit-iso-dispvm.sh"
# shellcheck disable=SC2088
shell_call "${DISPVM}" "~/edit-iso-dispvm.sh${VERBOSE:+ --verbose}"

sudo losetup -d "${LODEV}"

echo "[*] Copying the final iso from '${DISPVM}' to '${OUTPUT}'..."

shell_call "${DISPVM}" "cat ~/win-build.iso" > "${OUTPUT}"
qrexec_call "${DISPVM}" "admin.vm.Kill"

echo "[*] Done!"
