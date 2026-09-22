#!/bin/bash
# slurm.sh -- submit a SLURM array job that runs a spyglass populate.
#
# Run it directly (NOT with `sbatch`):
#   ./slurm.sh
#
# It reads settings from a `.env` file sitting next to this script (copy
# example.env to .env first), then submits the job for you. You can also copy
# this script plus a `.env` into your own project directory and run it there,
# pointing PY_SCRIPT at your own populate script.
#
# What it does:
#   1. Loads and checks your .env settings.
#   2. Finds your DataJoint username (used to label your schemas and jobs).
#   3. Makes a per-user log folder under /stelmo/slurm.
#   4. Optionally clears old logs (LOG_EXPIRATION_DAYS).
#   5. Submits an array of workers; each one runs your populate script.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Fixed cluster constants -- shared by everyone, not meant to be tuned per user.
PARTITION="Frank-Lab"
LOG_ROOT="/stelmo/slurm"

die() {
    echo "Error: $1" >&2
    exit 1
}

# Delete this user's logs older than $1 days from directory $2. Logs belonging
# to jobs that are still queued or running are always kept -- removing one would
# cut off a worker mid-write. Days of 0 means "clear every log from past runs".
prune_logs() {
    local days="$1" dir="$2"
    local find_args=("$dir" -maxdepth 1 -type f -name '*.log')
    # find's `-mtime +n` matches ages strictly greater than n whole days, so
    # `days - 1` is what selects files at least $days old. With days=0 we add no
    # age filter at all and every past log matches.
    ((days > 0)) && find_args+=(-mtime "+$((days - 1))")

    # %F is the array job id -- the same number that starts each log filename.
    # Kept as a padded string so we can substring-match without arrays.
    local active
    active=" $(squeue --me --noheader --format='%F' 2>/dev/null |
        sort -u | tr '\n' ' ')"

    local removed=0 file jobid
    while IFS= read -r -d '' file; do
        jobid="${file##*/}" # <jobid>_<task>_<timestamp>.log
        jobid="${jobid%%_*}"
        [[ "$active" == *" $jobid "* ]] && continue
        rm -f -- "$file" && ((removed++))
    done < <(find "${find_args[@]}" -print0 2>/dev/null)

    ((removed > 0)) && echo "Cleared $removed old log(s) from $dir"
    return 0
}

# --- Load settings ----------------------------------------------------------
ENV_FILE="$SCRIPT_DIR/.env"
[[ -f "$ENV_FILE" ]] ||
    die "no .env found. Create one with: cp '$SCRIPT_DIR/example.env' '$ENV_FILE'"
# shellcheck disable=SC1090
source "$ENV_FILE"

# --- Validate required settings ---------------------------------------------
required=(
    SPYGLASS_CONDA_PATH SPYGLASS_CONDA_ENV DJ_CONFIG PY_SCRIPT
    WORKERS CORES MAX_HOURS JOB_TAG
)
missing=()
for v in "${required[@]}"; do
    [[ -n "${!v:-}" ]] || missing+=("$v")
done
[[ ${#missing[@]} -eq 0 ]] || die "missing in $ENV_FILE: ${missing[*]}"

# --- Resolve the populate script (absolute path, or relative to slurm.sh) ---
if [[ "$PY_SCRIPT" == /* ]]; then
    PY_PATH="$PY_SCRIPT"
else
    PY_PATH="$SCRIPT_DIR/$PY_SCRIPT"
fi
[[ -f "$PY_PATH" ]] || die "PY_SCRIPT not found: $PY_PATH"

# --- Turn the worker count into a SLURM array spec --------------------------
# Users give a plain count (WORKERS=8); SLURM wants indices (--array=0-7). The
# index only ever labels a worker's log file -- workers pull from a shared jobs
# table, so which number a worker gets carries no meaning. Anything that is not
# a plain integer passes through untouched, leaving a raw spec like "0-19%5"
# available to anyone who wants one.
if [[ "$WORKERS" =~ ^[0-9]+$ ]]; then
    ((WORKERS >= 1)) || die "WORKERS must be 1 or more (got: $WORKERS)"
    ARRAY_SPEC="0-$((WORKERS - 1))"
    # MAX_CONCURRENT caps how many run at once; SLURM spells that "%N".
    if [[ -n "${MAX_CONCURRENT:-}" ]]; then
        [[ "$MAX_CONCURRENT" =~ ^[0-9]+$ ]] && ((MAX_CONCURRENT >= 1)) ||
            die "MAX_CONCURRENT must be 1 or more (got: $MAX_CONCURRENT)"
        ARRAY_SPEC+="%$MAX_CONCURRENT"
    fi
else
    ARRAY_SPEC="$WORKERS"
fi

# --- Validate the optional log expiry ---------------------------------------
if [[ -n "${LOG_EXPIRATION_DAYS:-}" ]]; then
    [[ "$LOG_EXPIRATION_DAYS" =~ ^[0-9]+$ ]] ||
        die "LOG_EXPIRATION_DAYS must be a whole number of days, or blank to keep logs forever (got: $LOG_EXPIRATION_DAYS)"
fi

# --- Activate conda and read the DataJoint username -------------------------
[[ -f "$SPYGLASS_CONDA_PATH" ]] ||
    die "conda init script not found: $SPYGLASS_CONDA_PATH"
# shellcheck disable=SC1090
#
source "$SPYGLASS_CONDA_PATH"
set +u
conda activate "$SPYGLASS_CONDA_ENV"
conda_rc=$?
set -u
(( conda_rc == 0 )) ||
    die "could not activate conda env '$SPYGLASS_CONDA_ENV'"

[[ -f "$DJ_CONFIG" ]] || die "DJ_CONFIG not found: $DJ_CONFIG"
DJ_USER="$(python -c \
    "import json,sys;print(json.load(open(sys.argv[1]))['database.user'])" \
    "$DJ_CONFIG")" ||
    die "could not read database.user from $DJ_CONFIG"
[[ -n "$DJ_USER" ]] || die "database.user is empty in $DJ_CONFIG"

# --- Prepare the central log directory --------------------------------------
LOG_DIR="$LOG_ROOT/$DJ_USER"
mkdir -p "$LOG_DIR" || die "could not create log dir: $LOG_DIR"
# Opt-in cleanup: blank/unset keeps every log forever.
[[ -n "${LOG_EXPIRATION_DAYS:-}" ]] &&
    prune_logs "$LOG_EXPIRATION_DAYS" "$LOG_DIR"
TIMESTAMP="$(date +%Y%m%dT%H%M%S)"
# %A = array job id, %a = task index. One log file per worker.
LOG_PATTERN="$LOG_DIR/%A_%a_${TIMESTAMP}.log"

# --- Assemble sbatch options ------------------------------------------------
sbatch_opts=(
    --job-name="${DJ_USER}_${JOB_TAG}"
    --partition="$PARTITION"
    --array="$ARRAY_SPEC"
    --cpus-per-task="$CORES"
    --time="$MAX_HOURS"
    --output="$LOG_PATTERN"
)
# MEMORY is optional -- omit --mem entirely when blank (no limit).
[[ -n "${MEMORY:-}" ]] && sbatch_opts+=(--mem="$MEMORY")

echo "Submitting array '${ARRAY_SPEC}' as job '${DJ_USER}_${JOB_TAG}'"
echo "  script : $PY_PATH"
echo "  logs   : $LOG_PATTERN"
echo "  stop   : ./cancel.sh --all"

# --- Submit -----------------------------------------------------------------
# The job body (stdin heredoc) re-activates conda on the compute node, points
# DataJoint at the right config, and runs the populate. Submit-time values are
# expanded now; SLURM_ARRAY_TASK_ID is read by the python script at run time.
sbatch "${sbatch_opts[@]}" <<EOF
#!/bin/bash
source "$SPYGLASS_CONDA_PATH"
conda activate "$SPYGLASS_CONDA_ENV"
export DJ_CONFIG="$DJ_CONFIG"
exec python "$PY_PATH"
EOF
