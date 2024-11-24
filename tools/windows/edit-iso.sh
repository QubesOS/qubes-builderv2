#!/bin/bash

set -efo pipefail

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
LODEV=$(losetup -f)
sudo losetup "${LODEV}" "${INPUT}"
LOOP_ID="${LODEV#'/dev/'}"

echo "[*] Preparing a DispVM..."
SELF=$(qubesdb-read /name)
read -r -a result <<< "$(qrexec_call "${SELF}" admin.vm.property.Get+default_dispvm)"
dispvm_template="${result[2]}"  # default=False type=vm vm-name
DISPVM=$(qrexec_call "$dispvm_template" admin.vm.CreateDisposable)

qrexec_call "${DISPVM}" "admin.vm.Start"
qrexec_call "${DISPVM}" "admin.vm.device.block.Attach+${SELF}+${LOOP_ID}" "read-only=true"
qvm-copy-to-vm --without-progress "${DISPVM}" "${SCRIPT_DIR}/edit-iso-dispvm.sh"
qvm-copy-to-vm --without-progress "${DISPVM}" "${FILES}"
shell_call "${DISPVM}" "mv ~/QubesIncoming/${SELF}/edit-iso-dispvm.sh ~"
shell_call "${DISPVM}" "mv ~/QubesIncoming/${SELF}/$(basename "$(realpath "${FILES}")") ~/iso"
shell_call "${DISPVM}" "chmod +x ~/edit-iso-dispvm.sh"
# shellcheck disable=SC2088  # (~ expansion)
shell_call "${DISPVM}" "~/edit-iso-dispvm.sh"

sudo losetup -d "${LODEV}"

echo "[*] Copying the final iso from '${DISPVM}' to '${OUTPUT}'..."

shell_call "${DISPVM}" "cat ~/win-build.iso" | cat > "${OUTPUT}"
qrexec_call "${DISPVM}" "admin.vm.Kill"

echo "[*] Done!"
