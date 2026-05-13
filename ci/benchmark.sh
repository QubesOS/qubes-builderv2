#!/bin/bash

set -uo pipefail

usage() {
    cat <<EOF
Usage: $0 [OPTIONS]
  --executor container|qubes|both  (default: container)
  --dist vm-fc43|vm-trixie|vm-archlinux|all  (default: all)
  --dry-run                        print qb commands without executing
  --clean                          remove results and artifact dirs, then exit
  --qubes-dispvm NAME              disposable template (default: builder-dvm)
  --docker-image NAME              docker image (default: qubes-builder-fedora:latest)
EOF
}

CI_DIR=$(dirname "$(readlink -f "$0")")
QB=$(readlink -f "$CI_DIR/../qb")
BUILDER_CONF="$CI_DIR/benchmark.yml"

COMPONENT="gui-agent-linux"
RESULTS_FILE="benchmark-results.csv"

SELECTED_EXECUTORS="container"
SELECTED_DISTS="all"
DRY_RUN=false
CLEAN_RESULTS=false
QUBES_DISPVM="builder-dvm"
DOCKER_IMAGE="qubes-builder-fedora:latest"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --executor) SELECTED_EXECUTORS="$2"; shift 2 ;;
        --dist)     SELECTED_DISTS="$2";     shift 2 ;;
        --dry-run)  DRY_RUN=true;            shift ;;
        --clean)    CLEAN_RESULTS=true;      shift ;;
        --qubes-dispvm) QUBES_DISPVM="$2";   shift 2 ;;
        --docker-image) DOCKER_IMAGE="$2";   shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "ERROR: unknown option: $1" >&2; exit 1 ;;
    esac
done

if [[ "$CLEAN_RESULTS" == true ]]; then
    echo "INFO: cleaning $RESULTS_FILE artifacts-{container,qubes}-{nocache,withcache}/"
    rm -f "$RESULTS_FILE"
    for e in container qubes; do
        for m in nocache withcache; do
            rm -rf "artifacts-${e}-${m}"
        done
    done
    exit 0
fi

case "$SELECTED_DISTS" in
    all) DISTS=(vm-fc43 vm-trixie vm-archlinux) ;;
    *)   DISTS=("$SELECTED_DISTS") ;;
esac

case "$SELECTED_EXECUTORS" in
    both)      EXECUTORS=(qubes container) ;;
    container) EXECUTORS=(container) ;;
    qubes)     EXECUTORS=(qubes) ;;
    *)         echo "ERROR: unknown executor: $SELECTED_EXECUTORS" >&2; exit 1 ;;
esac

executor_opts() {
    local executor="$1"
    case "$executor" in
        container) echo "-o executor:type=docker -o executor:options:image=${DOCKER_IMAGE}" ;;
        qubes)     echo "-o executor:type=qubes -o executor:options:dispvm=${QUBES_DISPVM}" ;;
    esac
}

clean_dist() {
    local dist="$1" artifacts_dir="$2"
    echo "INFO: removing chroot and build artifacts for ${dist} in ${artifacts_dir}"
    rm -rf "${artifacts_dir}/cache/chroot/${dist}/"
    find "${artifacts_dir}/components/${COMPONENT}" -mindepth 2 -maxdepth 2 \
        -name "$dist" -type d -exec rm -rf {} + 2>/dev/null || true
}

time_qb() {
    local label="$1"
    shift
    echo "DEBUG: $QB $*" >&2
    if [[ "$DRY_RUN" == true ]]; then
        echo 0
        return 0
    fi
    local start end rc
    start=$(date +%s)
    "$QB" "$@" 1>&2
    rc=$?
    end=$(date +%s)
    if [[ $rc -ne 0 ]]; then
        echo "FAILED"
        echo "ERROR: qb failed" >&2
    else
        echo $(( end - start ))
    fi
    return $rc
}

printf "dist,executor,mode,init-cache,prep,build\n" > "$RESULTS_FILE"

fetch_sources() {
    local executor="$1" mode="$2"
    local artifacts_dir="artifacts-${executor}-${mode}"
    local cache_opt=""
    [[ "$mode" == "nocache" ]] && cache_opt="-o cache="
    echo "INFO: fetching sources for ${COMPONENT} (${executor}/${mode})"
    echo "DEBUG: $QB --builder-conf ""$BUILDER_CONF"" -o artifacts-dir=${artifacts_dir} ${cache_opt} -c $COMPONENT -o skip-git-fetch=false package fetch" >&2
    if [[ "$DRY_RUN" == true ]]; then
        return 0
    fi
    "$QB" --builder-conf "$BUILDER_CONF" \
        -o "artifacts-dir=${artifacts_dir}" \
        ${cache_opt:+$cache_opt} \
        -c "$COMPONENT" -o skip-git-fetch=false \
        package fetch || {
        echo "ERROR: fetch failed" >&2
        return 1
    }
}

for executor in "${EXECUTORS[@]}"; do
    fetch_sources "$executor" nocache || exit 1
    fetch_sources "$executor" withcache || exit 1
done

for dist in "${DISTS[@]}"; do
    for executor in "${EXECUTORS[@]}"; do
        for mode in nocache withcache; do
            label="${dist}_${executor}_${mode}"
            echo "INFO: ${label}"

            artifacts_dir="artifacts-${executor}-${mode}"
            clean_dist "$dist" "$artifacts_dir"

            read -ra eopts <<< "$(executor_opts "$executor")"
            common_opts=(
                --builder-conf "$BUILDER_CONF"
                -o "artifacts-dir=${artifacts_dir}"
                -c "$COMPONENT" -d "$dist"
                "${eopts[@]}"
            )
            [[ "$mode" == "nocache" ]] && common_opts+=(-o "cache=")

            echo "INFO: running init-cache"
            t_init=$(time_qb "${label}_init-cache" "${common_opts[@]}" package init-cache)
            init_rc=$?

            if [[ $init_rc -ne 0 ]]; then
                printf "%s,%s,%s,FAILED,-,-\n" \
                    "$dist" "$executor" "$mode" >> "$RESULTS_FILE"
                continue
            fi

            echo "INFO: running prep"
            t_prep=$(time_qb "${label}_prep" \
                "${common_opts[@]}" \
                -o skip-git-fetch=true \
                package prep)
            prep_rc=$?

            if [[ $prep_rc -ne 0 ]]; then
                printf "%s,%s,%s,%ss,FAILED,-\n" \
                    "$dist" "$executor" "$mode" "$t_init" >> "$RESULTS_FILE"
                continue
            fi

            echo "INFO: running build"
            t_build=$(time_qb "${label}_build" \
                "${common_opts[@]}" \
                -o skip-git-fetch=true \
                package build)
            build_rc=$?

            if [[ $build_rc -ne 0 ]]; then
                printf "%s,%s,%s,%ss,%ss,FAILED\n" \
                    "$dist" "$executor" "$mode" "$t_init" "$t_prep" >> "$RESULTS_FILE"
                continue
            fi

            printf "%s,%s,%s,%ss,%ss,%ss\n" \
                "$dist" "$executor" "$mode" "$t_init" "$t_prep" "$t_build" \
                >> "$RESULTS_FILE"
        done
    done
done

echo ""
column -t -s , "$RESULTS_FILE"
echo ""
echo "Results: $RESULTS_FILE"
