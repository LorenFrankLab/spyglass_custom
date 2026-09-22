#!/bin/bash
# cancel.sh -- stop jobs you have submitted.
#
# Separate from monitor.sh on purpose: monitor.sh only ever looks, this one
# stops work. It never touches anyone else's jobs -- SLURM would refuse anyway,
# but every job id you name is checked against your own queue first.
#
# Cancelling is not destructive to your data. Anything a worker already
# finished stays in the database; whatever it was mid-way through is simply
# left for a later run. See SLURM.md for clearing stale reservations if a
# cancelled worker leaves an item marked "reserved".

set -uo pipefail

usage() {
    cat <<'EOF'
cancel.sh -- stop SLURM jobs you have submitted.

Usage:
  ./cancel.sh --all            stop all of your jobs (running + queued)
  ./cancel.sh --pending        stop only your queued jobs; let running finish
  ./cancel.sh 12345            stop one job (a whole array)
  ./cancel.sh 12345,12346      stop several jobs
  ./cancel.sh 12345_3          stop a single worker within an array

Flags:
  --all       every job of yours
  --pending   only jobs still waiting to start
  -y|--yes    skip the confirmation prompt
  -h|--help   show this help

Job ids come from ./monitor.sh (the JOBID column).
EOF
}

die() {
    echo "Error: $1" >&2
    exit 1
}

MODE="" # all | pending | ids
ASSUME_YES=0
ids=()

while [[ $# -gt 0 ]]; do
    case "$1" in
    --all) MODE="all" ;;
    --pending) MODE="pending" ;;
    -y | --yes) ASSUME_YES=1 ;;
    -h | --help)
        usage
        exit 0
        ;;
    -*)
        echo "Unknown option: $1" >&2
        usage
        exit 2
        ;;
    *)
        # A bare argument is one or more comma-separated job ids.
        MODE="${MODE:-ids}"
        IFS=',' read -r -a chunk <<<"$1"
        ids+=("${chunk[@]}")
        ;;
    esac
    shift
done

# No arguments cancels nothing -- too easy to run by accident otherwise.
[[ -n "$MODE" ]] || {
    usage
    exit 2
}
[[ "$MODE" == "ids" && ${#ids[@]} -eq 0 ]] && die "no job ids given"

# --- Work out exactly what would be cancelled --------------------------------
# %i is the job id (<array>_<task> for array jobs), %F the array job id, %T the
# state, %j the name. `-r` is what makes this reliable: without it a wholly
# pending array collapses into one row reading "59_[0-2]", and asking about a
# single worker in it would look like a job that is not yours.
sq=(squeue --me -r --noheader --format='%i %F %T %j')

# Each target line is "<id-to-cancel> <state> <name>".
case "$MODE" in
all)
    # "Everything" means whole arrays, so collapse workers onto their array id.
    targets=$("${sq[@]}" |
        awk '{n[$2]++; name[$2]=$4}
             END {for (j in n) printf "%s %d_worker(s) %s\n", j, n[j], name[j]}' |
        sort -n)
    ;;
pending)
    # Per worker, so that anything already computing is left to finish.
    targets=$("${sq[@]}" --states=PENDING | awk '{print $1, $3, $4}')
    ;;
ids)
    mine=$("${sq[@]}")
    targets=""
    for id in "${ids[@]}"; do
        # Match the worker id or the array id, so "12345" catches every worker
        # in array 12345 and "12345_3" catches only that one.
        hit=$(awk -v id="$id" '$1 == id || $2 == id' <<<"$mine")
        [[ -n "$hit" ]] || die "not one of your active jobs: $id"
        # Cancel exactly what was typed; summarise when it covers an array.
        n=$(wc -l <<<"$hit")
        if ((n > 1)); then
            targets+="$id ${n}_worker(s) $(awk 'NR==1{print $4}' <<<"$hit")"$'\n'
        else
            targets+="$id $(awk '{print $3, $4}' <<<"$hit")"$'\n'
        fi
    done
    targets=$(sed '/^$/d' <<<"$targets")
    ;;
esac

[[ -n "${targets//[[:space:]]/}" ]] || {
    echo "Nothing to cancel."
    exit 0
}

count=$(wc -l <<<"$targets")
echo "About to cancel ${count} job(s):"
# The "N_worker(s)" spelling above keeps each row to three fields; undo it here.
printf '%s\n' "$targets" |
    awk '{gsub(/_/, " ", $2); printf "  %-14s %-14s %s\n", $1, $2, $3}'

# --- Confirm -----------------------------------------------------------------
if ((!ASSUME_YES)); then
    read -r -p "Cancel these? [y/N] " reply
    [[ "$reply" =~ ^[Yy]$ ]] || {
        echo "Left alone."
        exit 0
    }
fi

# --- Cancel ------------------------------------------------------------------
# Cancel by the exact ids listed above, so jobs submitted between the listing
# and the confirmation are not swept up.
mapfile -t to_cancel < <(awk '{print $1}' <<<"$targets")
scancel "${to_cancel[@]}" || die "scancel failed"
echo "Cancelled ${count} job(s). Logs are kept under /stelmo/slurm/<dj_user>/."
