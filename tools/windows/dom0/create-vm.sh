#!/bin/bash

set -ef

VM_NAME="win-build"
VM_LABEL="purple"
VM_MEMORY=4096
VM_CPUS=2
VM_SIZE="40GiB"
BUILD_VM_NAME="work-qubesos"
VM_USER="user"

# make sure this matches the path in create-build-vm.sh
BUILD_SSH_KEY="/home/user/.ssh/win-build.key"

usage() {
    echo "Usage: $(basename "$0") [OPTIONS] ...

This script creates a Windows builder qube.

Options:
    --iso            Path to Windows image prepared for unattended setup (required)
    --name           Qube name (default: '$VM_NAME')
    --label          Qube label (default: '$VM_LABEL')
    --memory         RAM amount (MiB) (default: $VM_MEMORY)
    --cpus           Number of vcpus (default: $VM_CPUS)
    --build-vm-name  Name of the main builder qube (default: '$BUILD_VM_NAME')
"
}

if ! OPTS=$(getopt -o hi:n:l:m:c:b: --long help,iso:,name:,label:,memory:,cpus:,build-vm-name: -n "$0" -- "$@"); then
    exit 1
fi

eval set -- "$OPTS"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h | --help) usage; exit 0 ;;
        -i | --iso) ISO="$2"; shift ;;
        -n | --name) VM_NAME="$2"; shift ;;
        -l | --label) VM_LABEL="$2"; shift ;;
        -m | --memory) VM_MEMORY="$2"; shift ;;
        -c | --cpus) VM_CPUS="$2"; shift ;;
        -b | --build-vm-name) BUILD_VM_NAME="$2"; shift ;;
    esac
    shift
done

if [ -z "$ISO" ]; then
    usage && exit 1
fi

set +e

if qvm-check "$VM_NAME" 2> /dev/null; then
    echo "[!] Qube $VM_NAME already exists!"
    exit 1
fi
set -e

BUILD_VM_IP=$(qvm-prefs "$BUILD_VM_NAME" ip)
FW_VM_NAME=$(qvm-prefs "$BUILD_VM_NAME" netvm)

echo "[*] Creating qube: $VM_NAME"
qvm-create --class StandaloneVM \
           --property memory="$VM_MEMORY" \
           --property vcpus="$VM_CPUS" \
           --property stubdom_mem=1024 \
           --property virt_mode=hvm \
           --property kernel='' \
           --property netvm="$FW_VM_NAME" \
           --label "$VM_LABEL" \
           "$VM_NAME"

qvm-volume extend "$VM_NAME:root" "$VM_SIZE"

echo "[*] Configuring firewall"
# disallow outbound network connections from the windows vm
qvm-firewall "$VM_NAME" del --rule-no 0  # default "allow" rule
qvm-firewall "$VM_NAME" add drop

# allow network connections from the main builder vm to the windows vm
VM_IP=$(qvm-prefs "$VM_NAME" ip)

# TODO: use a custom fw chain
set +e

if ! qvm-run -p "$FW_VM_NAME" "sudo nft list chain ip qubes custom-forward" | grep "ip saddr $BUILD_VM_IP ip daddr $VM_IP .* accept"; then
    qvm-run -p "$FW_VM_NAME" "sudo nft add rule ip qubes custom-forward ip saddr $BUILD_VM_IP ip daddr $VM_IP ct state new,established,related counter accept"
fi
set -e

ssh_check() {
    qvm-run -p "$BUILD_VM_NAME" \
        "ssh -q -o 'BatchMode yes' -o 'StrictHostKeyChecking accept-new' -o 'ConnectTimeout 10' -i $BUILD_SSH_KEY ${VM_USER}@${VM_IP} exit"
}

# prep the main builder vm for ssh connection
qvm-run -p "$BUILD_VM_NAME" "ssh-keygen -R $VM_IP"

# unattended Windows install
echo "[*] Installing Windows, this will take a while..."
qvm-start --cdrom="$ISO" "$VM_NAME"
FINISHED=0
set +e
while [ "$FINISHED" == "0" ]; do
    if qvm-check --running "$VM_NAME" 2> /dev/null; then
        if ssh_check; then
            FINISHED=1
        fi
    else
        qvm-start "$VM_NAME"
    fi
done

# shutdown, builder will attach the EWDK iso
qvm-shutdown --wait "$VM_NAME"

echo "[*] Windows builder qube created successfully"
