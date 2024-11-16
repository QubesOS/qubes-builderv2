#!/bin/bash

set -efo pipefail

unset OUTDIR OPTS GETOPT_COMPATIBLE FILE_LIST SHA256 FILE_NAME FILE_URL FETCH_CMD UNTRUSTED_FILE_NAME LINES ROW

OUTDIR=$(pwd)

usage() {
    echo "Usage: $(basename "$0") [OPTIONS] file_list

This script downloads and verifies files specified in file_list.
file_list should contain lines in the following format:
  <expected sha256 of the file> <local file name> <URL>

Options:
    --output-dir  Output directory
"
}

# $1 = expected sha256, $2 = file path
verify() {(
    set +e
    if [[ $# -ne 2 ]]; then
        echo "[!] Bad arguments to verify()"
        return 1
    fi

    echo -n "[*] Verifying $2 ... "

    if echo "$1 $2" | sha256sum --check --status; then
        echo "OK"
    else
        echo "Failed!"
        rm -f "$2"
        return 1
    fi
)}


if ! OPTS=$(getopt -o ho: --long help,output-dir: -n "$0" -- "$@"); then
    exit 1
fi

eval set -- "$OPTS"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h | --help) usage; exit 0 ;;
        -o | --optput-dir) OUTDIR="$2"; shift ;;
        *) FILE_LIST="$1" ;;
    esac
    shift
done

if [ -z "${FILE_LIST}" ]; then
    usage
    exit 1
fi

FETCH_CMD='curl --proto =https --proto-redir =https --tlsv1.2 --http1.1 -sSfL -o'

# we don't use "while read... < $FILE_LIST" because qvm-run messes with stdin
readarray -t LINES < "${FILE_LIST}"

for ROW in "${LINES[@]}"; do
    read -r SHA256 FILE_NAME FILE_URL <<< "${ROW}"
    [[ "${SHA256}" = "#"* ]] && continue
    if [ -f "${FILE_NAME}" ]; then
        echo "[*] File ${FILE_NAME} already exists"
        verify "${SHA256}" "${OUTDIR}/${FILE_NAME}" || exit 1
    else
        echo "[*] Downloading ${FILE_NAME}..."
        UNTRUSTED_FILE_NAME="untrusted_${FILE_NAME}"
        # don't save the file in /tmp because it may be too large
        qvm-run-vm --dispvm "${FETCH_CMD} /home/user/${FILE_NAME} ${FILE_URL} && cat /home/user/${FILE_NAME}" > "${OUTDIR}/${UNTRUSTED_FILE_NAME}"
        ( verify "${SHA256}" "${OUTDIR}/${UNTRUSTED_FILE_NAME}" && mv "${OUTDIR}/${UNTRUSTED_FILE_NAME}" "${OUTDIR}/${FILE_NAME}" ) || exit 1
    fi
done
