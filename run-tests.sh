#!/bin/bash
# Run tests locally exactly as GitLab CI would.
#
# Usage:
#   ./run-tests.sh [PYTEST_TARGETS]             Run pytest jobs
#   ./run-tests.sh prepare-cache [DIR]          Init chroot caches
#   ./run-tests.sh cache <dist>                 Run a single cache CI job
#   ./run-tests.sh component <executor> <dist>  Run a component build CI job
#   ./run-tests.sh windows <dist>               Run a Windows build CI job
#   ./run-tests.sh template <template-name>     Run a template build CI job
#
# Executors: docker | podman | qubes | local
#
# Examples:
#   ./run-tests.sh component docker vm-fc43
#   ./run-tests.sh component qubes vm-bookworm
#   ./run-tests.sh component local host-fc37
#   ./run-tests.sh windows vm-win10
#   ./run-tests.sh cache vm-bookworm
#   ./run-tests.sh template fedora-43-xfce

set -e

CI_PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILDER_CONF="$CI_PROJECT_DIR/tests/builder-ci.yml"
RELEASE="${RELEASE:-4.3}"

die() { echo "ERROR: $*" >&2; exit 1; }

setup_gnupg() {
    export GNUPGHOME="$CI_PROJECT_DIR/tests/gnupg"
}

setup_docker_image() {
    docker pull registry.gitlab.com/qubesos/docker-images/qubes-builder-fedora:latest
    docker tag  registry.gitlab.com/qubesos/docker-images/qubes-builder-fedora:latest qubes-builder-fedora:latest
}

setup_podman_image() {
    podman pull docker.io/fepitre/qubes-builder-fedora:latest
    podman tag  docker.io/fepitre/qubes-builder-fedora:latest qubes-builder-fedora:latest
}

setup_ubuntu_docker_image() {
    docker pull registry.gitlab.com/qubesos/docker-images/qubes-builder-ubuntu:latest
    docker tag  registry.gitlab.com/qubesos/docker-images/qubes-builder-ubuntu:latest qubes-builder-ubuntu:latest
}

setup_debian_docker_image() {
    docker pull registry.gitlab.com/qubesos/docker-images/qubes-builder-debian:latest
    docker tag  registry.gitlab.com/qubesos/docker-images/qubes-builder-debian:latest qubes-builder-debian:latest
}

qb() {
    PYTHONPATH=".:${PYTHONPATH:-}" python3 "$CI_PROJECT_DIR/qb" \
        --builder-conf "$BUILDER_CONF" "$@"
}

prepare_cache() {
    local cache_dir="${1:-$HOME/qb-cache}"
    local dists=(host-fc37 vm-bookworm vm-trixie vm-archlinux)

    mkdir -p "$cache_dir"
    for dist in "${dists[@]}"; do
        qb --option "artifacts-dir=$cache_dir" -d "$dist" package init-cache
    done
}

run_cache() {
    local dist="${1:?dist required}"
    setup_docker_image
    qb \
        -o "use-qubes-repo:version=${RELEASE}" \
        -o "qubes-release=r${RELEASE}" \
        -o "+distributions+${dist}" \
        -d "$dist" \
        package init-cache
}

run_component() {
    local executor="${1:?executor required (docker|podman|qubes|local)}"
    local dist="${2:?dist required}"
    local qb_extra_args=()

    setup_gnupg

    case "$executor" in
        docker)
            case "$dist" in
                vm-jammy)
                    setup_debian_docker_image
                    qb_extra_args=(-o "executor:options:image=qubes-builder-debian:latest")
                    ;;
                vm-noble)
                    setup_ubuntu_docker_image
                    qb_extra_args=(-o "executor:options:image=qubes-builder-ubuntu:latest")
                    ;;
                *)
                    setup_docker_image
                    ;;
            esac
            qb -d "$dist" "${qb_extra_args[@]}" package all
            ;;
        podman)
            setup_podman_image
            local tmp_conf
            tmp_conf=$(mktemp --suffix=.yml)
            sed 's/docker/podman/' "$BUILDER_CONF" > "$tmp_conf"
            BUILDER_CONF="$tmp_conf" qb -d "$dist" "${qb_extra_args[@]}" package all
            rm -f "$tmp_conf"
            ;;
        qubes)
            local dispvm="${DISPVM:-builder-dvm}"
            qb -d "$dist" \
                -o executor:type=qubes \
                -o "executor:options:dispvm=$dispvm" \
                "${qb_extra_args[@]}" package all
            ;;
        local)
            local tmpdir="${LOCAL_TMP:-$HOME/tmp}"
            mkdir -p "$tmpdir"
            qb -d "$dist" \
                -o executor:type=local \
                -o "executor:options:directory=$tmpdir" \
                "${qb_extra_args[@]}" package all
            ;;
        *)
            die "unknown executor '$executor': choose docker podman qubes local"
            ;;
    esac
}

run_windows() {
    local dist="${1:?dist required}"
    local dispvm="${DISPVM:-builder-dvm}"

    setup_gnupg

    qb -d "$dist" -c example-advanced \
        -o executor:type=qubes \
        -o "executor:options:dispvm=$dispvm" \
        package fetch init-cache prep

    qb -d "$dist" -c example-advanced package build

    qb -d "$dist" -c example-advanced \
        -o executor:type=qubes \
        -o "executor:options:dispvm=$dispvm" \
        package sign publish
}

run_template() {
    local tmpl="${1:?template name required}"
    local dispvm="${DISPVM:-builder-dvm}"

    setup_gnupg
    setup_docker_image

    qb -c builder-rpm -c builder-debian -c template-whonix \
       -c template-kicksecure -c builder-archlinux -c qubes-release \
       package fetch

    qb \
        -o "use-qubes-repo:version=${RELEASE}" \
        -o "qubes-release=r${RELEASE}" \
        -t "$tmpl" \
        -o executor:type=qubes \
        -o "executor:options:dispvm=$dispvm" \
        template all
}

run_pytest() {
    local pytest_args=(
        -vv --color=yes --showlocals
        --tb=long
        --capture=no
        -rA
        -o truncation_limit_chars=0
        -o truncation_limit_lines=0
        -o junit_logging=all
        --junitxml=artifacts/qubesbuilder.xml
    )

    if [[ -n "${CACHE_ARTIFACTS_DIR:-}" ]]; then
        pytest_args+=(--cache-dir "$CACHE_ARTIFACTS_DIR")
    fi

    mkdir -p "$CI_PROJECT_DIR/artifacts" ~/tmp

    PYTHONPATH=".:${PYTHONPATH:-}" BASE_ARTIFACTS_DIR=~/results TMPDIR=~/tmp \
        pytest-3 "${pytest_args[@]}" "${@:-tests/}" 2>&1 | tee artifacts/pytest.log
    exit "${PIPESTATUS[0]}"
}

case "${1:-}" in
    prepare-cache) shift; prepare_cache "$@" ;;
    cache)         shift; run_cache "$@" ;;
    component)     shift; run_component "$@" ;;
    windows)       shift; run_windows "$@" ;;
    template)      shift; run_template "$@" ;;
    *)             run_pytest "$@" ;;
esac
