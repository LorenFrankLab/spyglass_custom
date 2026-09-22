#!/bin/bash
# monitor.sh -- friendly wrapper around `squeue` and `sacct`.
#
# The live queue (`squeue`) only shows jobs that are running or waiting.
# Finished jobs live in the accounting history (`sacct`). This script picks the
# right tool for you and prints a tidy table.

set -uo pipefail

usage() {
    cat <<'EOF'
monitor.sh -- show SLURM jobs (a friendly wrapper for squeue/sacct).

Usage:
  ./monitor.sh                 your active jobs (running + queued)
  ./monitor.sh --all           everyone's active jobs
  ./monitor.sh --pending       your queued (not-yet-running) jobs
  ./monitor.sh --done          your finished jobs from today
  ./monitor.sh --all --done    everyone's finished jobs from today

Flags (scope and mode can be combined):
  Scope:
    --mine     only your jobs (default)
    --all      all users' jobs
  Mode:
    --pending  only queued jobs (waiting to start)        [live queue]
    --done     finished jobs from today                   [history]
  Other:
    -h|--help  show this help

To stop jobs, use ./cancel.sh (job ids come from the JOBID column here).
EOF
}

SCOPE="mine" # mine | all
MODE="active" # active | pending | done

while [[ $# -gt 0 ]]; do
    case "$1" in
    --mine) SCOPE="mine" ;;
    --all) SCOPE="all" ;;
    --pending) MODE="pending" ;;
    --done) MODE="done" ;;
    -h | --help)
        usage
        exit 0
        ;;
    *)
        echo "Unknown option: $1" >&2
        usage
        exit 2
        ;;
    esac
    shift
done

# Live-queue columns: id, name, state, time used, node(s), why-waiting.
SQ_FMT="JobID:.14,Name:.24,StateCompact:.5,TimeUsed:.11,NodeList:.14,Reason:.16"
# History columns: id, name, final state, elapsed, start, end.
SA_FMT="JobID%-14,JobName%-24,State%-14,Elapsed,Start,End"
# Terminal states worth listing under --done.
SA_STATES="COMPLETED,FAILED,CANCELLED,TIMEOUT,OUT_OF_MEMORY,NODE_FAIL"

if [[ "$MODE" == "done" ]]; then
    # Finished jobs from today. -X = one row per job (hide .batch/.extern steps).
    SA_USER=(--user "$USER")
    [[ "$SCOPE" == "all" ]] && SA_USER=(--allusers)
    echo "Finished jobs since 00:00 today (${SCOPE}):"
    # NOTE: sacct's --state filter is ignored unless BOTH --starttime and
    # --endtime are given, so we pass --endtime now explicitly.
    sacct -X "${SA_USER[@]}" --starttime today --endtime now \
        --state "$SA_STATES" --format="$SA_FMT"
else
    # squeue shows all users by default; restrict to me unless --all.
    if [[ "$SCOPE" == "all" ]]; then
        SQ_SCOPE=()
        who="All users'"
    else
        SQ_SCOPE=(--me)
        who="Your"
    fi
    [[ "$MODE" == "pending" ]] && SQ_SCOPE+=(--states=PENDING)
    label="active"
    [[ "$MODE" == "pending" ]] && label="queued"
    echo "${who} ${label} jobs:"
    squeue "${SQ_SCOPE[@]}" --Format="$SQ_FMT"
fi
