#!/bin/bash
# Build the Cordon G2 QNX 8.0 QEMU image.
#
#   target/boot/build-g2-image.sh normal     image where the service starts
#                                            before the first client
#   target/boot/build-g2-image.sh violated   image where the first client runs
#                                            before the service exists
#
# Both images are built the same way, from the same sources, with the same
# policy; only the startup order in post_startup.sh differs. That is the point:
# scenario C5 must be able to tell the two apart.
#
# Mechanic: mkqnximage (part of SDP 8.0) generates the IFS and disk images. This
# script lays out a project directory, writes the two mkqnximage snippets that
# add Cordon to it, and runs the build. The QNX kernel in the installed SDP is
# already the symbol-stripped copy described in g0-environment.md (the linker in
# this container is killed on the 70 MB symbol-bearing kernel).
#
# QNX SDP 8.0 must be installed at $SDP (default /workspace/qnx-env/qnx800).
set -uo pipefail

MODE=${1:-normal}
case "$MODE" in
    normal|violated) ;;
    *) echo "usage: $0 [normal|violated] [--ifs-only]" >&2; exit 2 ;;
esac
IFS_ONLY=0
[ "${2:-}" = "--ifs-only" ] && IFS_ONLY=1

ROOT=$(cd "$(dirname "$0")/../." && pwd)          # build/cordon/target
CORDON=$(cd "$ROOT/.." && pwd)                    # build/cordon
if [ -z "${SDP:-}" ]; then
    if [ -n "${QNX_HOST:-}" ]; then
        SDP=$(cd "$QNX_HOST/../../.." && pwd)
    else
        SDP=/workspace/qnx-env/qnx800
    fi
fi
WORK=${WORK:-$CORDON/out/work-$MODE}
BUILD=$ROOT/build
OUT=$CORDON/out/g2-$MODE

set +u
# shellcheck disable=SC1090
. "$SDP/qnxsdp-env.sh" >/dev/null 2>&1
set -u
# Two helpers live outside the SDP on the machine this was written on: a shim
# for awk, and a scratch directory outside the size limited /tmp. Use them when
# they are there and leave the environment alone when they are not.
if [ -d /workspace/qnx-env/bin ]; then
    export PATH=/workspace/qnx-env/bin:$PATH
fi
if [ -d /workspace/qnx-env/tmp ]; then
    export TMPDIR=/workspace/qnx-env/tmp
fi

echo "== compile"
CC_ARGS="-Wall -Wextra -O1"
qcc $CC_ARGS -o "$BUILD/cordon"           "$ROOT/resmgr/cordon.c"             -lsecpol -lslog2 || exit 3
qcc $CC_ARGS -o "$BUILD/cordon-supervise" "$ROOT/supervise/cordon-supervise.c" -lsecpol -lslog2 || exit 3
qcc $CC_ARGS -o "$BUILD/cordon-client"    "$ROOT/client/cordon-client.c"      -lsecpol || exit 3
qcc $CC_ARGS -o "$BUILD/cordon-view"      "$ROOT/client/cordon-view.c"        -lsecpol || exit 3

echo "== check policy"
secpolcompile -o "$BUILD/secpol.bin" "$ROOT/secpol/cordon.policy" || exit 4

echo "== mkqnximage project: $WORK"
mkdir -p "$WORK/local/snippets"
cat > "$WORK/local/snippets/ifs_files.custom" <<EOF
# local/snippets/ifs_files.custom
# Cordon G2 payload: the guarded service, its supervisor, the two clients and
# the scenario runner, all in the boot image. secpol and secpolpush are added
# here because --policy only compiles the policy into the image; it does not add
# the utilities that push it (mkqnximage adds them only for its own --secpol
# modes, which this image does not use).
[uid=0 gid=0 perms=0755] /proc/boot/cordon=$BUILD/cordon
[uid=0 gid=0 perms=0755] /proc/boot/cordon-supervise=$BUILD/cordon-supervise
[uid=0 gid=0 perms=0755] /proc/boot/cordon-client=$BUILD/cordon-client
[uid=0 gid=0 perms=0755] /proc/boot/cordon-view=$BUILD/cordon-view
[uid=0 gid=0 perms=0755] /proc/boot/cordon-boot.sh=$ROOT/boot/cordon-boot.sh
[uid=0 gid=0 perms=0755] /proc/boot/secpolpush=bin/secpolpush
[uid=0 gid=0 perms=0755] /proc/boot/secpol=bin/secpol
EOF

cat > "$WORK/local/snippets/post_start.custom" <<EOF
# local/snippets/post_start.custom
# Cordon G2: run the scenario suite at the end of startup.
/proc/boot/cordon-boot.sh $MODE
EOF

: > "$WORK/local/snippets/ifs_start.custom"
: > "$WORK/local/snippets/system_files.custom"

# The image generates its sshd host keys on first boot when they are missing.
# Under TCG on the e2-micro that costs minutes, and sshd is not part of what
# this gate tests, so an RSA host key is generated here once and shipped in the
# data partition. ed25519 generation is quick and is left to the boot.
KEYDIR="$WORK/local/misc_files"
mkdir -p "$KEYDIR"
if [ ! -s "$KEYDIR/ssh_host_rsa_key" ]; then
    openssl genrsa -traditional -out "$KEYDIR/ssh_host_rsa_key" 2048 2>/dev/null || \
        echo "warning: no RSA host key generated; first boot will be slower" >&2
    chmod 600 "$KEYDIR/ssh_host_rsa_key" 2>/dev/null || true
fi
cat > "$WORK/local/snippets/data_files.custom" <<EOF
# local/snippets/data_files.custom
# sshd host key, pre-generated so the boot does not spend minutes on it.
[uid=15 gid=15 perms=400] var/ssh/ssh_host_rsa_key=$KEYDIR/ssh_host_rsa_key
EOF

echo "== mkqnximage --build ($MODE)"
cd "$WORK" || exit 5
mkdir -p output
if [ "$IFS_ONLY" = 1 ]; then
    # Fast path while iterating: rebuild only the IFS from the build file
    # mkqnximage already generated. The disk image stays byte-identical.
    mkifs output/build/ifs.build output/ifs.bin || exit 6
else
    mkqnximage --build --force --noprompt \
        --proc=procnto-smp-instr \
        --policy="$ROOT/secpol/cordon.policy" \
        --hostname=cordon-g2 || exit 6
fi

mkdir -p "$OUT"
cp output/ifs.bin output/disk-qemu "$OUT/"
{
    echo "mode: $MODE"
    echo "built: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "sdp: $SDP"
    echo "policy: target/secpol/cordon.policy sha256 $(sha256sum "$ROOT/secpol/cordon.policy" | cut -d' ' -f1)"
    echo "binaries:"
    for b in cordon cordon-supervise cordon-client cordon-view; do
        echo "  $b $(sha256sum "$BUILD/$b" | cut -d' ' -f1) $(stat -c %s "$BUILD/$b") bytes"
    done
    echo "images:"
    echo "  ifs.bin $(sha256sum "$OUT/ifs.bin" | cut -d' ' -f1) $(stat -c %s "$OUT/ifs.bin") bytes"
    echo "  disk-qemu $(sha256sum "$OUT/disk-qemu" | cut -d' ' -f1) $(stat -c %s "$OUT/disk-qemu") bytes"
} > "$OUT/build-record.txt"

echo "--- $OUT/build-record.txt"
cat "$OUT/build-record.txt"
