"""Run a ``populate`` across a SLURM array -- the script each worker executes.

Every worker in the array runs this same script with the IDENTICAL populate
call. ``reserve_jobs=True`` makes DataJoint record each key it starts in a
shared "jobs" table, so no two workers ever grab the same key. The work
therefore self-balances across workers with no index math on our part.

To run YOUR OWN pipeline, change just two things below: the ``import`` and the
``TABLE`` assignment. Everything else stays the same.

This is the companion to ``schema.py``: that module declares the demo schema
and seeds its manual data (run it once first); this script computes the pending
rows. The schema is imported like any installed package -- this is why we keep
the project importable (see ``SLURM.md``).

Run
    # normally launched by slurm.sh, but works standalone too:
    DJ_CONFIG=/path/to/creds.json python populate.py
"""

import os
import sys

import datajoint as dj

from spyglass_custom.cbroz1.SLURM.schema import DemoAnalysis

# --- What to populate -------------------------------------------------------
# The table whose `populate` we run. Point this at your own Computed/Imported
# table (and update the import above) to reuse this script for your pipeline.
TABLE = DemoAnalysis

# --- Which keys to populate -------------------------------------------------
# A DataJoint restriction, passed as the first argument to populate() to select
# which keys to compute. True (the default) means "all pending keys". Narrow it
# to a subset with a dict or string, e.g.:
#     RESTRICTION = {"nwb_file_name": "my_session_.nwb"}
#     RESTRICTION = "demo_id > 3"
RESTRICTION = True

# --- Populate options -------------------------------------------------------
# Passed as **kwargs to TABLE.populate(). Kept here, outside main(), so each
# option can be documented on its own line.
#
# `limit` is the main knob: None (default) drains ALL pending keys, so the
# table fully populates no matter how many workers you launch. Set a small
# integer only to CAP how much one worker does (e.g. very long jobs you want to
# checkpoint and resubmit). It relies on order="random" -- without random order
# every worker would fetch the same first key, collide on the reservation, and
# most would do nothing.
POP_KWARGS = {
    "reserve_jobs": True,  # REQUIRED: coordinate workers via the jobs table
    "suppress_errors": True,  # log a bad key instead of killing the worker
    "display_progress": True,  # per-worker progress bar in the log
    "order": "random",  # workers pull different keys, avoiding collisions
    "limit": None,  # None = drain all pending; int = per-worker cap
    # "max_calls": 10,  # cap keys attempted, handy when debugging
    # "processes": 1,   # we fan out via SLURM workers, so leave at 1
}


def main():
    """Reserve and compute pending ``TABLE`` keys, then report status.

    Returns
    -------
    int
        Process exit code -- 0 on success, 1 if any key errored.
    """
    task = os.environ.get("SLURM_ARRAY_TASK_ID", "n/a")
    table = TABLE()
    prev_len = len(TABLE())
    pending = len((table.key_source & RESTRICTION) - table)
    print(f"[worker {task}] {pending} keys pending; running populate ...")

    result = table.populate(RESTRICTION, **POP_KWARGS)

    # With suppress_errors=True, populate returns a dict
    # {"success_count": int, "error_list": [(key, error), ...]} on modern
    # DataJoint (older versions returned the error list directly).
    if isinstance(result, dict):
        error_list = result.get("error_list", [])
    else:
        error_list = result or []

    # The "jobs" reservation table belongs to the table's schema.
    jobs = dj.Schema(table.database).jobs
    n_done = len(table) - prev_len
    n_err = len(jobs & 'status="error"')
    n_res = len(jobs & 'status="reserved"')
    print(
        f"[worker {task}] done. {n_done} rows populated; "
        f"jobs table: {n_res} reserved, {n_err} error."
    )

    # Surface a nonzero exit code so SLURM marks the worker FAILED on error.
    if error_list:
        print(
            f"[worker {task}] {len(error_list)} key(s) errored.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
