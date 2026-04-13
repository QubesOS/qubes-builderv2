# Qubes Builder v2 - Ansible setup

Automates the full setup of qubes-builderv2 inside Qubes OS using
[qubes-ansible](https://github.com/QubesOS/qubes-ansible).

## Setup

**Clone in a qube**:

```bash
git clone --recurse-submodules https://github.com/QubesOS/qubes-builderv2
cd qubes-builderv2
git tag -v <release-tag>
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
    -e builder_qube_names=my-builder \
    -e builder_dvm_name=my-builder-dvm

# Create/configure multiple builder qubes in one run
ansible-playbook playbooks/main.yml \
    -e builder_qube_names=build-qubesos,work-qubesos

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
ansible-playbook playbooks/main.yml --tags windows_executor -e enable_windows=true
#
# RPC policies only:
ansible-playbook playbooks/main.yml --tags policy
ansible-playbook playbooks/main.yml --tags policy -e enable_windows=true
#
# Inventory-only tasks:
ansible-playbook playbooks/main.yml --tags inventory
```

## Configuration

Variables are in [group_vars/all.yml](group_vars/all.yml). Common overrides:

| Variable | Default | Purpose |
|---|---|---|
| `builder_qube_names` | `(required)` | Builder AppVMs to create/configure; also used as allowed sources in builder RPC/admin policies (accepts CSV like `build-qubesos,work-qubesos` or a YAML list) |
| `builder_dvm_name` | `builder-dvm` | Disposable template name |
| `builder_executor_template` | `fedora-42-xfce` | Template for the DVM |
| `builder_qube_template` | `fedora-42-xfce` | Template for the builder qube |
| `builder_qube_netvm` | `sys-firewall` | NetVM for the builder qube |
| `builder_git_url` | GitHub qubes-builderv2 | Repository to clone |
| `builder_git_version` | `main` | Branch, tag, or exact commit SHA to check out |
| `builder_git_verify` | `true` | Verify GPG signature of the cloned tag or commit; set to `false` for unsigned forks |
| `builder_git_verify_key` | Marek's signing key | GPG key fingerprint for signature verification; fetched at runtime from `builder_git_verify_keyserver`. Set to `""` to accept any key in the default keyring |
| `builder_git_verify_keyserver` | `keys.openpgp.org` | Keyserver used to fetch `builder_git_verify_key` |
| `builder_dir` | `/home/user/qubes-builderv2` | Clone destination |
| `gpg_client` | `gpg` | `gpg` or `qubes-gpg-client-wrapper` (enables Split GPG) |
| `configure_split_gpg` | derived from `gpg_client` | Configure `~/.rpmmacros` for Split GPG; set automatically when `gpg_client` is `qubes-gpg-client-wrapper` |
| `skip_resize_dvm_volume` | `false` | Skip resizing `builder-dvm` private volume |
| `skip_resize_qube_volume` | `false` | Skip resizing builder AppVM private volume |
| `enable_windows` | `false` | Enable Windows executor policy/validation and vault setup |
| `vault_windows_name` | `vault-sign` | Windows signing vault qube |
| `vault_windows_template` | `fedora-42-minimal` | Template for the vault qube |
| `windows_builder_dvm_name` | `win-builder-dvm` | Windows disposable-executor qube (Windows equivalent of `builder-dvm`) |
| `windows_builder_executor_template` | `windows-10` | Template for `windows_builder_dvm_name` AppVM; must already exist (generally built using [qvm-create-windows-qube](https://github.com/QubesOS/qvm-create-windows-qube)). Set equal to `windows_builder_dvm_name` to skip AppVM creation. |
| `windows_builder_dvm_label` | `orange` | Label for `windows_builder_dvm_name` when auto-created |
| `windows_builder_dvm_netvm` | `""` | NetVM for `windows_builder_dvm_name` when auto-created |
| `windows_ewdk_path` | `tools/windows/ewdk.iso` | Path to EWDK ISO in the builder qube |
| `windows_ssh_key_path` | `~/.ssh/win-build.key` | SSH key for windows-ssh executor |

Pass overrides with `-e key=value` or `-e @vars.yml`.

## Windows executor prerequisites

When `enable_windows=true`, this playbook validates `windows_builder_dvm_name` and enforces `template_for_dispvms=true` so it can be used by the `windows` executor.
By default, Ansible auto-creates `win-builder-dvm` from `windows-10` before applying disposable-template settings.

## Playbook

All setup steps are in a single playbook: `playbooks/main.yml`, executed from dom0.
