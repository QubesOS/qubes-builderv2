#!/bin/bash
# Run tests locally exactly as GitLab CI would.
# Usage: ./run-tests.sh [PYTEST_TARGETS]
# Example: ./run-tests.sh "tests/test_check_release_status.py"

set -e

CI_PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTEST_TARGETS="${*:-tests/}"

PYTEST_ARGS=(
    -vv --color=yes --showlocals
    --tb=long
    --capture=no
    -rA
    -o truncation_limit_chars=0
    -o truncation_limit_lines=0
    -o junit_logging=all
    --junitxml=artifacts/qubesbuilder.xml
)

# before_script
mkdir -p "$CI_PROJECT_DIR/artifacts"

# script
# shellcheck disable=SC2086
PYTHONPATH=".:$PYTHONPATH" BASE_ARTIFACTS_DIR=~/results TMPDIR=~/tmp \
    pytest-3 "${PYTEST_ARGS[@]}" $PYTEST_TARGETS 2>&1 | tee artifacts/pytest.log
exit "${PIPESTATUS[0]}"
