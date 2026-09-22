#!/bin/bash
# AUTHOR: Chris Brozdowski
# DATE: 2025-02-20
#
# 1. Go to SPYGLASS_REPO_PATH and pull the latest changes from the main branch
# 2. Test the SPYGLASS_CONDA_ENV conda environment
# 3. Test the connection to the database
# 4. Run the cleanup script
#
# This script is intended to be run as a cron job, weekly or more frequently.
# It will store a log of its output in SPYGLASS_LOG.
# If any of the operations fail, an email will be sent to SPYGLASS_EMAIL_DEST

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/.env" # load environment variables from this directory

if [[ -z "${SPYGLASS_BASE_PATH}"
  || -z "${SPYGLASS_CONDA_PATH}" \
  || -z "${SPYGLASS_CONDA_ENV}" \
  || -z "${SPYGLASS_REPO_PATH}" \
  || -z "${SPYGLASS_LOG}" \
  || -z "${SPYGLASS_CSVS}" ]]; then
  echo "Error: the following must be set in an .env:
        SPYGLASS_BASE_PATH, SPYGLASS_CONDA_PATH, SPYGLASS_CONDA_ENV,
        SPYGLASS_REPO_PATH, SPYGLASS_LOG, and SPYGLASS_CSVS"
  exit 1
fi

source $SPYGLASS_CONDA_PATH

send_email() { # $1: email content (file or process substitution)
    if [ -z "$SPYGLASS_EMAIL_SRC" ]; then
        return 1
    fi
    curl -sS -o /dev/null --ssl-reqd \
        --url "smtps://smtp.gmail.com:465" \
        --user "${SPYGLASS_EMAIL_SRC}:${SPYGLASS_EMAIL_PASS}" \
        --mail-from "$SPYGLASS_EMAIL_SRC" \
        --mail-rcpt "$SPYGLASS_EMAIL_DEST" \
        -T "$1"
}

on_fail() { # $1: error message. Echo message and send as email
    echo "Error: $1"
    send_email <(cat <<-EOF
	From: "Spyglass" <$SPYGLASS_EMAIL_SRC>
	To: $SPYGLASS_EMAIL_DEST
	Subject: cron fail - $(date "+%Y-%m-%d")

	$1
	EOF
    )
}

send_email_with_attachments() {
    # $1: subject, $2: message body, $3: directory containing CSV files
    local boundary="----=_Part_$(date +%s)_$RANDOM"
    local email_file=$(mktemp)

    # Build email with MIME multipart structure
    {
        echo "From: Spyglass <$SPYGLASS_EMAIL_SRC>"
        echo "To: $SPYGLASS_EMAIL_DEST"
        echo "Subject: $1"
        echo "MIME-Version: 1.0"
        echo "Content-Type: multipart/mixed; boundary=\"$boundary\""
        echo ""
        echo "--$boundary"
        echo "Content-Type: text/plain; charset=utf-8"
        echo "Content-Transfer-Encoding: 7bit"
        echo ""
        echo "$2"
        echo ""

        # Attach CSV files if directory exists and contains files
        if [ -d "$3" ]; then
            for csv_file in "$3"/*.csv; do
                [ -f "$csv_file" ] || continue
                echo "--$boundary"
                echo "Content-Type: text/csv; name=\"$(basename "$csv_file")\""
                echo "Content-Transfer-Encoding: base64"
                echo "Content-Disposition: attachment; filename=\"$(basename "$csv_file")\""
                echo ""
                base64 "$csv_file"
                echo ""
            done
        fi

        echo "--$boundary--"
    } > "$email_file"

    send_email "$email_file"
    rm -f "$email_file"
}

exec >> $SPYGLASS_LOG 2>&1

# print the date and time
echo "SPYGLASS CUST JOB START: $(date +"%Y-%m-%d %H:%M:%S")"

# Run from the root of the spyglass repository
echo "    Fetching latest spyglass custom repo..."
cd $SPYGLASS_REPO_PATH || \
    { on_fail "Could not find repo path: $SPYGLASS_REPO_PATH"; exit 1; }


# Update the spyglass repository
git pull --quiet \
  https://github.com/LorenFrankLab/spyglass_custom.git main > /dev/null || \
    { on_fail "Could not update the spyglass repo $PWD"; exit 1; }

# Test conda environment
if ! conda env list | grep -q $SPYGLASS_CONDA_ENV; then
  on_fail "Conda environment $SPYGLASS_CONDA_ENV not found"
  exit 1
fi

# convenience function to run a command in the spyglass conda environment
conda_run() { conda run --name $SPYGLASS_CONDA_ENV "$@"; }

# Test connection to the database
echo "    Testing database connection..."
CONN_TEST="import datajoint as dj; dj.logger.setLevel('ERROR'); dj.conn()"
conda_run python -c "$CONN_TEST" > /dev/null || \
  { on_fail "Could not connect to the database"; exit 1; }

# Run cleanup script
echo "    Running file tracking script..."
conda_run python "$SCRIPT_DIR/file_tracking.py" || \
  { on_fail "File tracking script failed"; exit 1; }

# Send success email with CSV attachments
if [ -d "$SPYGLASS_CSVS" ] && compgen -G "$SPYGLASS_CSVS/*.csv" > /dev/null; then
    send_email_with_attachments \
        "Spyglass cleanup - $(date '+%Y-%m-%d')" \
        "Spyglass custom cleanup found expired .rec files." \
        "$SPYGLASS_CSVS"
    echo "Success email sent with CSV attachments"
else
    echo "No CSV files found in $SPYGLASS_CSVS, skipping email"
fi

echo "SPYGLASS CUST JOB END"

# truncate long log file
tail -n ${SPYGLASS_MAX_LOG:-1000} "$SPYGLASS_LOG" > "${SPYGLASS_LOG}.tmp" \
  && mv "${SPYGLASS_LOG}.tmp" "$SPYGLASS_LOG"
