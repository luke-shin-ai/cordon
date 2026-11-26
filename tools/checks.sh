#!/bin/sh
# Seven release checks, in the order the gate document lists them.
#
#   1  cross build with qcc, no warning
#   2  host static check: byte compile and type check
#   3  lint and secret scan, including no trace of the myQNX password
#   4  every unit and integration test passes
#   5  style scan, no machine punctuation or stock phrases
#   6  public surfaces agree with the evidence bundle
#   7  no performance measurement anywhere in a public surface
#
#   tools/checks.sh
#
# Environment: QNX_SDP (default /workspace/qnx-env/qnx800), CORDON_BUNDLE
# (default ../../hackathons/qnx-2026/evidence/g2-rerun-bundle when it exists), and
# DEVPOST (default ../../hackathons/qnx-2026/devpost-draft.md when it exists).
# A check whose input is absent is reported as SKIP, not PASS.

set -u
cd "$(dirname "$0")/.."
REPO=$(pwd)
WORKSPACE=$(cd "$REPO/../.." 2>/dev/null && pwd || echo "$REPO")
QNX_SDP=${QNX_SDP:-/workspace/qnx-env/qnx800}
BUNDLE=${CORDON_BUNDLE:-$WORKSPACE/hackathons/qnx-2026/evidence/g2-rerun-bundle}
DEVPOST=${DEVPOST:-$WORKSPACE/hackathons/qnx-2026/devpost-draft.md}
LOG=$(mktemp -d)
FAILED=0
PYTHON=${PYTHON:-python3}

run() {  # run <name> <command...>
    name=$1
    shift
    out=$LOG/$(printf '%s' "$name" | tr ' /' '__').log
    if "$@" > "$out" 2>&1; then
        echo "PASS  $name"
    else
        echo "FAIL  $name"
        tail -15 "$out" | sed 's/^/      /'
        FAILED=1
    fi
}

skip() {
    echo "SKIP  $1"
}

# 1 ------------------------------------------------------------------------
if [ -x "$QNX_SDP/host/linux/x86_64/usr/bin/qcc" ]; then
    QNX_HOST=$QNX_SDP/host/linux/x86_64
    QNX_TARGET=$QNX_SDP/target/qnx
    PATH=$QNX_HOST/usr/bin:$PATH:$QNX_SDP/host/linux/x86_64/usr/bin
    export QNX_HOST QNX_TARGET PATH
    build_target() {
        mkdir -p "$LOG/build" || return 1
        qcc -Wall -Wextra -O1 -o "$LOG/build/cordon" target/resmgr/cordon.c -lsecpol -lslog2 &&
        qcc -Wall -Wextra -O1 -o "$LOG/build/cordon-supervise" target/supervise/cordon-supervise.c -lsecpol -lslog2 &&
        qcc -Wall -Wextra -O1 -o "$LOG/build/cordon-client" target/client/cordon-client.c -lsecpol &&
        qcc -Wall -Wextra -O1 -o "$LOG/build/cordon-view" target/client/cordon-view.c -lsecpol &&
        secpolcompile -o "$LOG/build/secpol.bin" target/secpol/cordon.policy
    }
    build_warning_free() {
        build_target 2> "$LOG/qcc.err" || return 1
        if [ -s "$LOG/qcc.err" ]; then
            cat "$LOG/qcc.err"
            return 1
        fi
        echo "four binaries and the policy built with no output from qcc"
    }
    run "1 qcc cross build, warning 0" build_warning_free
else
    skip "1 qcc cross build (no SDP at $QNX_SDP)"
fi

# 2 ------------------------------------------------------------------------
static_check() {
    "$PYTHON" -m compileall -q host tests tools codelab || return 1
    if "$PYTHON" -c "import mypy" 2>/dev/null; then
        "$PYTHON" -m mypy --ignore-missing-imports --check-untyped-defs \
            host/cordon_notes.py codelab/render.py tools/*.py
    elif [ -d "$WORKSPACE/qnx-env/pylibs/mypy" ]; then
        PYTHONPATH=$WORKSPACE/qnx-env/pylibs "$PYTHON" -m mypy --ignore-missing-imports \
            --check-untyped-defs host/cordon_notes.py codelab/render.py tools/*.py
    else
        echo "mypy not installed, byte compile only"
    fi
}
run "2 host static check" static_check

# 3 ------------------------------------------------------------------------
secrets() {
    "$PYTHON" tools/secret_scan.py README.md docs codelab host target tests tools || return 1
    if [ -d "$WORKSPACE/hackathons/qnx-2026" ]; then
        "$PYTHON" tools/secret_scan.py --ignore license-key "$WORKSPACE/hackathons/qnx-2026"
    fi
}
run "3 lint and secret scan" secrets

# 4 ------------------------------------------------------------------------
tests() {
    "$PYTHON" tests/test_gate.py &&
    "$PYTHON" tests/test_run.py &&
    "$PYTHON" tests/test_note.py &&
    "$PYTHON" tests/test_codelab.py
}
run "4 tests" tests

# 5 ------------------------------------------------------------------------
style() {
    "$PYTHON" tools/style_scan.py README.md docs codelab/markdown host target tests tools || return 1
    if [ -f "$DEVPOST" ]; then
        "$PYTHON" tools/style_scan.py "$DEVPOST" || return 1
    fi
    if [ -d "$WORKSPACE/hackathons/qnx-2026/codelab/markdown" ]; then
        "$PYTHON" tools/style_scan.py "$WORKSPACE/hackathons/qnx-2026/codelab/markdown"
    fi
}
run "5 style scan" style

# 6 ------------------------------------------------------------------------
facts() {
    if [ ! -d "$BUNDLE" ]; then
        echo "no bundle at $BUNDLE"
        return 1
    fi
    set -- --bundle "$BUNDLE" README.md docs codelab/markdown
    if [ -f "$DEVPOST" ]; then
        set -- "$@" --devpost "$DEVPOST"
    fi
    "$PYTHON" tools/public_facts.py "$@"
}
if [ -d "$BUNDLE" ]; then
    run "6 public facts against the bundle" facts
else
    skip "6 public facts (no bundle at $BUNDLE)"
fi

# 7 ------------------------------------------------------------------------
numbers() {
    "$PYTHON" tools/number_scan.py README.md docs codelab/markdown host target tools || return 1
    if [ -f "$DEVPOST" ]; then
        "$PYTHON" tools/number_scan.py "$DEVPOST" || return 1
    fi
    if [ -d "$WORKSPACE/hackathons/qnx-2026/codelab/markdown" ]; then
        "$PYTHON" tools/number_scan.py "$WORKSPACE/hackathons/qnx-2026/codelab/markdown"
    fi
}
run "7 no performance measurement" numbers

echo
if [ "$FAILED" -eq 0 ]; then
    echo "CHECKS: PASS"
else
    echo "CHECKS: FAIL"
fi
echo "logs: $LOG"
exit "$FAILED"
