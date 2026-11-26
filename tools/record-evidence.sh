#!/bin/bash
# Record the build and boot evidence that is published with this repository.
#
#   tools/record-evidence.sh [console-dir]
#
# What it writes, and nothing else:
#   evidence/build/environment.txt   host, SDP, toolchain and emulator versions
#   evidence/build/qcc.txt           the five cross build commands and exit codes
#   evidence/build/mkqnximage-normal.txt, mkqnximage-violated.txt
#   evidence/build/image.json        sha256 and size of every image, QNX version,
#                                    git commit the images were built from
#   evidence/run/console-*.log       the serial consoles, copied from console-dir
#
# It never writes a verdict. host/cordon computes verdicts from a console; this
# script stops at the raw evidence, because the license does not allow
# publishing evaluation results without written approval.
#
# Environment: SDP (QNX SDP 8.0 install), WORK (mkqnximage project directory),
# CONSOLE_DIR (directory holding normal-*.log and violated-*.log, optional),
# SKIP_BUILD=1 (record the images already in out/ instead of building again, so
# that the hashes in image.json stay the ones that were booted).
set -u
cd "$(dirname "$0")/.." || exit 2
REPO=$(pwd)
EVIDENCE=$REPO/evidence
CONSOLE_DIR=${1:-${CONSOLE_DIR:-}}
STAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)

if [ -z "${SDP:-}" ]; then
    if [ -n "${QNX_HOST:-}" ]; then
        SDP=$(cd "$QNX_HOST/../../.." && pwd)
    else
        SDP=/workspace/qnx-env/qnx800
    fi
fi

mkdir -p "$EVIDENCE/build" "$EVIDENCE/run"
if [ -d /workspace/qnx-env/bin ]; then
    export PATH=/workspace/qnx-env/bin:$PATH
fi

# ------------------------------------------------------------------ environment
{
    echo "recorded: $STAMP"
    echo "host: $(uname -srm)"
    echo "sdp: $SDP"
    # shellcheck disable=SC1090
    . "$SDP/qnxsdp-env.sh" >/dev/null 2>&1
    echo "qnx_target: ${QNX_TARGET:-unset}"
    echo "qnx_host: ${QNX_HOST:-unset}"
    echo
    echo "\$ qcc -V"
    qcc -V 2>&1
    echo
    echo "\$ command -v qcc mkqnximage secpolcompile qemu-system-x86_64"
    command -v qcc mkqnximage secpolcompile qemu-system-x86_64 2>&1
    echo
    echo "mkqnximage build logs: evidence/build/mkqnximage-normal.txt and"
    echo "evidence/build/mkqnximage-violated.txt"
    echo
    if command -v qemu-system-x86_64 >/dev/null 2>&1; then
        echo "\$ qemu-system-x86_64 --version"
        qemu-system-x86_64 --version 2>&1 | head -2
    else
        echo "qemu-system-x86_64: not installed on the build host. The guests run"
        echo "on a separate boot host, whose emulator and exact boot command are"
        echo "copied from the console headers at the end of this file."
    fi
} > "$EVIDENCE/build/environment.txt" 2>&1

# ------------------------------------------------------------------ cross build
if [ "${SKIP_BUILD:-0}" = "1" ]; then
    echo "SKIP_BUILD=1: keeping the existing build logs and images"
else
{
    echo "recorded: $STAMP"
    echo "sdp: $SDP"
    echo
    mkdir -p target/build
    run() {
        echo "\$ $*"
        "$@" 2>&1
        rc=$?
        echo "exit: $rc"
        echo
        return $rc
    }
    run qcc -Wall -Wextra -O1 -o target/build/cordon           target/resmgr/cordon.c             -lsecpol -lslog2
    run qcc -Wall -Wextra -O1 -o target/build/cordon-supervise target/supervise/cordon-supervise.c -lsecpol -lslog2
    run qcc -Wall -Wextra -O1 -o target/build/cordon-client    target/client/cordon-client.c      -lsecpol
    run qcc -Wall -Wextra -O1 -o target/build/cordon-view      target/client/cordon-view.c        -lsecpol
    run secpolcompile -o target/build/secpol.bin target/secpol/cordon.policy
    echo "artifacts:"
    for f in target/build/cordon target/build/cordon-supervise target/build/cordon-client \
             target/build/cordon-view target/build/secpol.bin; do
        if [ -f "$f" ]; then
            printf '  %s %s %s bytes\n' "$f" "$(sha256sum "$f" | cut -d' ' -f1)" "$(stat -c %s "$f")"
        else
            printf '  %s missing\n' "$f"
        fi
    done
} > "$EVIDENCE/build/qcc.txt" 2>&1

# ------------------------------------------------------------------ images
for mode in normal violated; do
    {
        echo "recorded: $STAMP"
        echo "mode: $mode"
        echo "sdp: $SDP"
        echo
        echo "\$ target/boot/build-g2-image.sh $mode"
        SDP=$SDP target/boot/build-g2-image.sh "$mode" 2>&1
        echo "exit: $?"
    } > "$EVIDENCE/build/mkqnximage-$mode.txt" 2>&1
done
fi

# ------------------------------------------------------------------ image.json
if [ -n "${CONSOLE_DIR:-}" ] && [ -d "${CONSOLE_DIR:-}" ]; then
    for mode in normal violated; do
        src=$(ls "$CONSOLE_DIR"/*"$mode"*.log 2>/dev/null | head -1)
        if [ -n "$src" ]; then
            cp "$src" "$EVIDENCE/run/console-$mode.log"
        fi
    done
fi

{
    echo
    echo "boot host and emulator, copied from the console headers:"
    grep -h -E "^# (host|qemu|cmd):" "$EVIDENCE"/run/console-*.log 2>/dev/null
} >> "$EVIDENCE/build/environment.txt"

python3 - "$EVIDENCE" <<'PY'
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

evidence = Path(sys.argv[1])
repo = evidence.parent


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def version(text: str) -> str:
    m = re.search(r"(QNX \S+ 8\.0\.0[^\r\n]*)", text)
    return m.group(1).strip() if m else "unknown"


commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                        capture_output=True, text=True, check=False).stdout.strip()
record = {
    "recorded": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "sources": {"repo_commit": commit},
    "images": {},
}
for mode in ("normal", "violated"):
    entry = {}
    for name in ("ifs.bin", "disk-qemu"):
        p = repo / "out" / f"g2-{mode}" / name
        if p.exists():
            entry[name] = {"sha256": sha256(p), "bytes": p.stat().st_size}
    console = evidence / "run" / f"console-{mode}.log"
    if console.exists():
        entry["qnx_version"] = version(console.read_text(encoding="utf-8", errors="replace"))
    entry["build_record"] = f"evidence/build/mkqnximage-{mode}.txt"
    record["images"][mode] = entry
(evidence / "build" / "image.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
print(f"wrote {evidence / 'build' / 'image.json'}")
PY

echo "evidence written under $EVIDENCE"
