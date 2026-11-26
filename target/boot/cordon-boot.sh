#!/bin/sh
# Cordon G2 scenario runner. Runs on the target at the end of system startup
# (from post_startup.sh, see target/boot/build-g2-image.sh) and prints one
# observation line per fact. It asserts nothing: `cordon run` on the host decides
# the result of each scenario from these lines alone.
#
#   cordon-boot.sh normal     service first, then every scenario
#   cordon-boot.sh violated   first client before the service, to show that an
#                             image with the wrong startup order is caught
#
# Every client is started with an explicit security type using `on -T`, so the
# policy decides each request. Nothing here measures time or resource use.

MODE=${1:-normal}
B=/proc/boot
LOG=/data/var/cordon
mkdir -p $LOG

echo "CORDON-G2-START mode=$MODE"

# ---------------------------------------------------------------- policy ----
if secpolpush; then
    echo "CORDON-OBS policy push=OK"
else
    echo "CORDON-OBS policy push=ERR"
fi
echo "CORDON-OBS policy id=$(secpol -Q 2>/dev/null)"
for t in cordon_t cordon_writer_t cordon_probe_t cordon_view_t cordon_outsider_t; do
    echo "CORDON-OBS policy type $t=$(secpol -t $t 2>/dev/null)"
done

# ------------------------------------------------------------- utilities ----
client() {
    # client <type> <role> [client args...]  e.g. ... -w "text" write
    type=$1
    role=$2
    shift 2
    on -T "$type" "$B/cordon-client" -r "$role" "$@"
}

wait_service() {
    i=0
    while [ $i -lt 30 ]; do
        if [ -e /dev/cordon ]; then
            echo "CORDON-OBS service path=/dev/cordon state=present tries=$i"
            return 0
        fi
        sleep 1
        i=$((i + 1))
    done
    echo "CORDON-OBS service path=/dev/cordon state=absent tries=$i"
    return 1
}

start_service() {
    on -T cordon_t "$B/cordon-supervise" -s "$B/cordon" -p /dev/cordon \
        -e "$LOG/events.log" -n zone-main -m 4 &
    wait_service
}

field() {
    # field <name> ; reads one line on stdin and prints the value of key=value.
    # The guarded service status arrives as one field "status=pid=... seq=...",
    # so a leading status= is stripped before the key is compared.
    awk -v k="$1" '{ for (i = 1; i <= NF; i++) {
        f = $i; sub(/^status=/, "", f)
        n = index(f, "="); if (n > 1) {
            if (substr(f, 1, n - 1) == k) { print substr(f, n + 1); exit }
        } } }'
}

kill_pid() {
    # kill_pid <pid> <what> : never call kill with an empty argument
    if [ -n "$1" ]; then
        kill -9 "$1" 2>/dev/null
        echo "CORDON-OBS kill target=$2 pid=$1 result=OK"
    else
        echo "CORDON-OBS kill target=$2 pid=none result=ERR reason=no-pid"
    fi
}

# ------------------------------------------------------- wrong order image ----
if [ "$MODE" = "violated" ]; then
    # The first client runs before the service exists. An image built in this
    # order must be caught: the client cannot reach the guarded path.
    echo "CORDON-OBS order check=first-client-before-service attempting=yes"
    client cordon_view_t order-probe status
    echo "CORDON-OBS order check=first-client-before-service done=yes"
    # Then start the service, so the log also shows the image is otherwise
    # workable and only the order is wrong.
    start_service
    client cordon_view_t order-after-start status
    echo "CORDON-SLOG2-BEGIN"
    slog2info -S
    echo "CORDON-SLOG2-END"
    echo "CORDON-G2-COMPLETE mode=violated"
    exit 0
fi

# ------------------------------------------------------------- normal image --
start_service

# The type each long-lived process actually carries, from the kernel's view.
echo "CORDON-OBS pidin-types-begin"
pidin -F "%a %N %_" 2>/dev/null | grep -F cordon
echo "CORDON-OBS pidin-types-end"

echo "CORDON-OBS scenario name=boot-order step=begin"
client cordon_view_t order-probe status
echo "CORDON-OBS scenario name=boot-order step=end"

# C2: a client without the write permission must be refused, and the service
# must stay up. The outsider has no channel connect rule at all, so it never
# reaches the write handler: two different layers are shown.
echo "CORDON-OBS scenario name=deny-unprivileged-write step=begin"
client cordon_probe_t probe -w "zone=main state=probe" write
client cordon_outsider_t outsider open
client cordon_view_t deny-after status
echo "CORDON-OBS scenario name=deny-unprivileged-write step=end"

# C3: the permitted writer succeeds and one event is recorded.
echo "CORDON-OBS scenario name=allow-privileged-write step=begin"
client cordon_writer_t writer -w "zone=main state=sealed" write
client cordon_view_t writer-after status
echo "CORDON-OBS scenario name=allow-privileged-write step=end"

# C1: the status consumer is killed; the guarded service keeps answering.
echo "CORDON-OBS scenario name=kill-consumer step=begin"
rm -f /dev/shmem/cordon-view.pid
on -T cordon_view_t "$B/cordon-view" -n 60 -f /dev/shmem/cordon-view.pid &
i=0
viewpid=
while [ $i -lt 10 ]; do
    if [ -s /dev/shmem/cordon-view.pid ]; then
        viewpid=$(cat /dev/shmem/cordon-view.pid)
        break
    fi
    sleep 1
    i=$((i + 1))
done
if [ -n "$viewpid" ]; then
    kill_pid "$viewpid" view
else
    echo "CORDON-OBS kill target=view pid=none result=ERR reason=no-pidfile"
fi
sleep 1
if kill -0 "$viewpid" 2>/dev/null; then
    echo "CORDON-OBS view pid=$viewpid state=alive"
else
    echo "CORDON-OBS view pid=$viewpid state=gone"
fi
client cordon_view_t view-after status
echo "CORDON-OBS scenario name=kill-consumer step=end"

# C4: the supervisor starts the guarded service again after it dies.
echo "CORDON-OBS scenario name=supervised-restart step=begin"
st=$(client cordon_view_t pre-kill status)
echo "$st"
svcpid=$(echo "$st" | field pid)
seq0=$(echo "$st" | field seq)
kill_pid "$svcpid" service
i=0
while [ $i -lt 10 ]; do
    sleep 1
    st2=$(client cordon_view_t post-kill status)
    echo "$st2"
    seq1=$(echo "$st2" | field seq)
    if [ -n "$seq1" ] && [ "$seq1" != "0" ] && [ "$seq1" != "$seq0" ]; then
        break
    fi
    i=$((i + 1))
done
client cordon_view_t restart-after status
echo "CORDON-OBS scenario name=supervised-restart step=end"

# Event log as the service wrote it, and the slog2 dump of the whole run.
echo "CORDON-EVENTS-BEGIN"
cat "$LOG/events.log" 2>/dev/null
echo "CORDON-EVENTS-END"
echo "CORDON-SLOG2-BEGIN"
slog2info -S
echo "CORDON-SLOG2-END"
echo "CORDON-G2-COMPLETE mode=normal"
exit 0
