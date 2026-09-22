<!-- markdownlint-disable MD013 -->

# Running Spyglass on SLURM

A practical guide to running Spyglass `populate` jobs on the lab cluster. No
prior cluster experience needed. If you just want the commands, see
[`README.md`](./README.md).

## What this is for

Some Spyglass computations are slow and you have many sessions to run. Instead
of looping through them on one machine, you hand the list to the cluster and
many **workers** chew through it at once. SLURM is the program that hands your
work to the lab's compute machines.

## The cluster at a glance

|                |                        |
| -------------- | ---------------------- |
| Machines       | `virga-06`, `virga-07` |
| Cores each     | 64                     |
| Memory each    | ~1.5 TB                |
| Max job length | 4 days                 |
| GPUs           | none                   |

## Getting on the cluster

You do everything from `virga-06`, the lab's submit machine. From your own
terminal, connect with (replace `you` with your username):

```bash
ssh -p 7777 you@virga-06.cin.ucsf.edu
```

Then `cd` to wherever this `SLURM/` folder lives (or your own project folder)
and run the commands below from there. You never log in to the other machine
(`virga-07`) yourself -- SLURM sends work there for you.

## A few words you'll see

- **submit** -- hand a job to SLURM. It waits in line, then runs on a compute
    machine when one is free. This is different from running `python ...`
    yourself, which starts immediately on the machine you are sitting on.
- **job** -- one thing you submit (here, one `./slurm.sh` launch).
- **worker** -- one copy of your script. You start several at once (the
    `WORKERS` setting) and they share the work. SLURM calls these "array tasks".
- **item** -- one unit of work: one row your table still needs to compute.
- **queue** -- the line of jobs waiting their turn. `monitor.sh` shows it.

## One username

Your **DataJoint username** (from your config file) is used to prefix your job
names so when you look at the job list, your work is easy to spot.

## The core workflow

```bash
cp example.env .env     # 1. copy the settings template
                        # 2. open .env and fill in your paths (see below)
python schema.py        # 3. create the tables + manual data (run once)
./slurm.sh              # 4. launch the workers
./monitor.sh            # 5. watch them run
./cancel.sh --all       # 6. (optional) stop them early
```

> Run `./slurm.sh` **directly** -- not `sbatch slurm.sh`. The launcher fills in
> the cluster details for you and submits the job itself.

Two ways to use it:

1. **Run from this directory**, or
2. **Copy `slurm.sh` and an `.env` into your own project folder**, point
    `PY_SCRIPT` at your own script, and run `./slurm.sh` there.

### Filling in `.env`

`.env` is your private settings file. The important choices:

| Setting                                      | What it is                                                    |
| -------------------------------------------- | ------------------------------------------------------------- |
| `SPYGLASS_CONDA_PATH` / `SPYGLASS_CONDA_ENV` | where conda lives + the env with spyglass                     |
| `DJ_CONFIG`                                  | your DataJoint config file (login)                            |
| `PY_SCRIPT`                                  | the script each worker runs (a filename here, or a full path) |
| `WORKERS`                                    | how many workers to run, e.g. `8`                             |
| `MAX_CONCURRENT`                             | cap on how many run at once (blank = no cap)                  |
| `CORES`, `MEMORY`, `MAX_HOURS`               | resources per worker                                          |
| `JOB_TAG`                                    | a short label for the job name                                |
| `LOG_EXPIRATION_DAYS`                        | delete your logs older than N days (blank = keep forever)     |

**Tip: smaller requests start sooner.** A worker that asks for fewer cores and
less memory is easier for the scheduler to slot in, so it usually begins faster.
Ask for what your job needs, not the maximum.

## How the work gets split

You launch several identical workers (the `WORKERS` count). Each one calls the
same `populate`. The trick that keeps them from stepping on each other is
`reserve_jobs=True`: before a worker starts an item, it **reserves** it in a
shared jobs table, so no two workers ever do the same item. The workers keep
grabbing unreserved items until the table is full. The work spreads itself --
you don't assign items to workers by hand.

You can launch more workers than you have items; the extras simply find nothing
left to reserve and exit. To fully populate a table, just launch some workers
and let them drain the list.

### Why `populate.py` looks the way it does

Two details in the example matter:

- **`order="random"`** -- without it, every worker would reach for the *same*
    first item, collide on the reservation, and most would do nothing. Random
    order makes workers pull different items and actually share the load.
- **tri-part make** -- the analysis table splits its work into `make_fetch`
    (read inputs), `make_compute` (the slow part), and `make_insert` (write
    result). Spyglass runs the slow `make_compute` *outside* the database
    transaction, so a long computation does not hold a lock that blocks everyone
    else. For minutes-to-hours jobs this matters a lot.

## Watching your jobs: `monitor.sh`

```bash
./monitor.sh              # your running + queued jobs
./monitor.sh --pending    # only the ones still waiting to start
./monitor.sh --done       # your finished jobs from today
./monitor.sh --all        # everyone's jobs (be a good neighbor)
```

The live list (`squeue`) only shows jobs that are running or waiting. Once a job
finishes it moves to history (`sacct`); `--done` looks there for you.

## Stopping jobs: `cancel.sh`

```bash
./cancel.sh --all         # stop everything you have submitted
./cancel.sh --pending     # stop only what is still queued; let running finish
./cancel.sh 12345         # stop one job (the whole array of workers)
./cancel.sh 12345_3       # stop a single worker within an array
```

Job ids come from the `JOBID` column in `./monitor.sh`. The script lists what it
is about to stop and asks before doing it (`-y` skips the prompt). It only ever
touches your own jobs.

Cancelling is safe for your data: whatever a worker already finished stays in
the database, and whatever it was part-way through is simply left for a later
run. If a cancelled worker leaves an item marked `reserved`, clear it as
described under "When something goes wrong" below.

`--pending` is the gentler option when you have overshot -- workers that are
already computing get to finish their current item instead of throwing that work
away.

## Logs

Each worker writes its own log file:

```
/stelmo/slurm/<your-username>/<jobid>_<worker>_<timestamp>.log
```

So one launch of 8 workers leaves 8 files -- same `<jobid>`, one per `<worker>`
number. Open these to see progress and any errors. They are kept in one central
place so every worker's output is easy to find regardless of where you launched
from.

### Clearing old logs

Logs are kept forever unless you ask otherwise. To have `slurm.sh` tidy up as it
submits, set `LOG_EXPIRATION_DAYS` in your `.env`:

```bash
LOG_EXPIRATION_DAYS=       # keep everything (the default)
LOG_EXPIRATION_DAYS=30     # delete logs older than 30 days
LOG_EXPIRATION_DAYS=0      # delete all logs from previous runs
```

Only your own folder is touched, and only files ending in `.log`. Logs from jobs
that are still queued or running are never deleted, so a job that outlives the
expiry keeps the log it is writing.

## Use it on your own tables

The demo computes a toy table, but the pattern is identical for real Spyglass
pipelines. To run your own:

1. Copy `populate.py` into your own folder of the package --
    `src/spyglass_custom/<your-username>/` -- with a clear name, e.g.
    `populate_spikesorting.py`. That keeps it importable and
    version-controlled, and it lives on shared storage the compute machines can
    see (never `/tmp`).

2. In that copy, replace the two demo lines near the top -- the import and the
    `TABLE` assignment. Change:

    ```python
    from spyglass_custom.cbroz1.SLURM.schema import DemoAnalysis

    TABLE = DemoAnalysis
    ```

    to your table, for example:

    ```python
    from spyglass.spikesorting.v1 import SpikeSorting

    TABLE = SpikeSorting  # the class itself -- no parentheses
    ```

3. Point `PY_SCRIPT` in your `.env` at your new script and run `./slurm.sh`.

> Note: real Spyglass tables that read/write NWB files need your Spyglass config
> (base dir + stores) set up as usual.

## When something goes wrong

- **A worker failed.** Open its log under `/stelmo/slurm/<username>/`. With
    `suppress_errors=True`, one bad item is logged and the worker keeps going;
    the item is left for a later run.

- **Items look "stuck" / nothing populates.** A worker may have died mid-item,
    leaving a stale reservation. Clear stuck reservations and retry:

    ```python
    import datajoint as dj
    from spyglass.spikesorting.v1.sorting import schema  # or whatever schema

    dj_user = dj.config["database.user"]

    reserved = 'status="reserved"'  # or status="error"
    by_me = f'user LIKE "{dj_user}%"'
    my_jobs = schema.jobs & reserved & by_me
    my_jobs.delete()
    ```

- **A job was stopped at the time limit.** Raise `MAX_HOURS` in `.env`, or give
    each worker fewer items so each run is shorter.
