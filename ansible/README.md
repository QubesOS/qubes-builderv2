# Qubes Builder v2 - Ansible setup

Automates the full setup of qubes-builderv2 inside Qubes OS using
[qubes-ansible](https://github.com/QubesOS/qubes-ansible).

## Setup

**Clone in a qube**:

```bash
git clone --recurse-submodules https://github.com/QubesOS/qubes-builderv2
```

**Transfer to dom0:**

```bash
qvm-run -p work-qubesos tar -cvf - qubes-builderv2 | tar -xvf -
```

**Install prerequisites** (in dom0):

```bash
sudo qubes-dom0-update qubes-ansible-dom0
```

Also install `qubes-ansible` (or `qubes-ansible-vm`) in the template used by `default-mgmt-dvm`.

**Run** (in dom0):

```bash
cd qubes-builderv2/ansible

# Linux executor only
ansible-playbook playbooks/main.yml

# Include Windows Authenticode signing vault
ansible-playbook playbooks/main.yml -e enable_windows=true

# Override any variable inline
ansible-playbook playbooks/main.yml \
    -e builder_qube_name=my-builder \
    -e builder_dvm_name=my-builder-dvm

# Run only specific parts (inventory is built dynamically so --limit does not
# work; use --tags instead):
#
# Create VM and configure it:
ansible-playbook playbooks/main.yml --tags builder_dvm
ansible-playbook playbooks/main.yml --tags builder_qube
ansible-playbook playbooks/main.yml --tags vault_windows -e enable_windows=true
#
# Configure templates only:
ansible-playbook playbooks/main.yml --tags executor_template
ansible-playbook playbooks/main.yml --tags builder_qube_template
#
# RPC policies only:
ansible-playbook playbooks/main.yml --tags policy
ansible-playbook playbooks/main.yml --tags policy -e enable_windows=true
```

## Configuration

Variables are in [group_vars/all.yml](group_vars/all.yml). Common overrides:

| Variable | Default | Purpose |
|---|---|---|
| `builder_qube_name` | `build-qubesos` | Builder AppVM name |
| `builder_dvm_name` | `builder-dvm` | Disposable template name |
| `builder_executor_template` | `fedora-42-xfce` | Template for the DVM |
| `builder_qube_template` | `fedora-42-xfce` | Template for the builder qube |
| `builder_qube_netvm` | `sys-firewall` | NetVM for the builder qube |
| `builder_git_url` | GitHub qubes-builderv2 | Repository to clone |
| `builder_git_version` | `main` | Branch or tag to check out |
| `builder_dir` | `/home/user/qubes-builderv2` | Clone destination |
| `gpg_client` | `gpg` | `gpg` or `qubes-gpg-client-wrapper` (enables Split GPG) |
| `configure_split_gpg` | derived from `gpg_client` | Configure `~/.rpmmacros` for Split GPG; set automatically when `gpg_client` is `qubes-gpg-client-wrapper` |
| `skip_resize_dvm_volume` | `false` | Skip resizing `builder-dvm` private volume |
| `skip_resize_qube_volume` | `false` | Skip resizing builder AppVM private volume |
| `enable_windows` | `false` | Enable Windows executor and vault setup |
| `vault_windows_name` | `vault-sign` | Windows signing vault qube |
| `vault_windows_template` | `fedora-42-minimal` | Template for the vault qube |
| `windows_builder_name` | `win-build` | Windows worker qube (HVM) |
| `windows_ewdk_path` | `tools/windows/ewdk.iso` | Path to EWDK ISO in the builder qube |
| `windows_ssh_key_path` | `~/.ssh/win-build.key` | SSH key for windows-ssh executor |

Pass overrides with `-e key=value` or `-e @vars.yml`.

## Playbook

All setup steps are in a single playbook: `playbooks/main.yml`, executed from dom0.
