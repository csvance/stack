# Common setup for bats tests. Sourced by each .bats file.

# Resolve STACK_HOME relative to this helper.
TEST_HELPER_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export STACK_HOME="$(cd -P "$TEST_HELPER_DIR/../.." && pwd)"

# Put mocks ahead of system PATH for every test.
export PATH="$TEST_HELPER_DIR/mocks:$PATH"

# Sensible non-interactive defaults.
export STACK_YES=1
export STACK_DRY_RUN=0
export STACK_VERBOSE=0
export STACK_STRUCTURED=0
unset STACK_MANIFEST || true

# Source the libraries the tests want to unit-test.
load_lib() {
    # shellcheck source=/dev/null
    source "$STACK_HOME/lib/$1"
}

# Helper to assert a command's exit status with a nice message.
assert_success() {
    if [[ "$status" -ne 0 ]]; then
        echo "expected success; got status=$status" >&2
        echo "--- output ---" >&2
        echo "$output" >&2
        return 1
    fi
}

assert_failure() {
    if [[ "$status" -eq 0 ]]; then
        echo "expected failure; got status=0 with output:" >&2
        echo "$output" >&2
        return 1
    fi
}

assert_output_contains() {
    local needle="$1"
    if [[ "$output" != *"$needle"* ]]; then
        echo "output does not contain: $needle" >&2
        echo "--- output ---" >&2
        echo "$output" >&2
        return 1
    fi
}

# Skip the test if branchless isn't usable in this environment.
require_branchless() {
    if ! git branchless --version >/dev/null 2>&1; then
        skip "git-branchless not installed"
    fi
}
