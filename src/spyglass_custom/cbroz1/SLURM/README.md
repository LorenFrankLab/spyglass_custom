<!-- markdownlint-disable MD013 -->

# SLURM demo & guidance

## Files

Tools and examples for running `populate` jobs on the lab SLURM cluster. **Start
with [`SLURM.md`](./SLURM.md).**

| File          | Purpose                                                               |
| ------------- | --------------------------------------------------------------------- |
| `SLURM.md`    | Lab-member guide: cluster overview, workflow, monitoring.             |
| `slurm.sh`    | Parameterized `sbatch` launcher. Reads `.env`.                        |
| `monitor.sh`  | Wrapper for `squeue`/`sacct` (`--mine`/`--all`/`--pending`/`--done`). |
| `cancel.sh`   | Stop your jobs (`--all`/`--pending`/job ids). Confirms first.         |
| `example.env` | Template for `.env` (copy → `.env`, edit, never commit).              |
| `schema.py`   | Declares the demo schema + seeds manual data. Run once.               |
| `populate.py` | Runs the array `populate` of the demo analysis table.                 |

The Python modules are importable (the project is an installable package):

```python
from spyglass_custom.cbroz1.SLURM import DemoAnalysis
```

## Quick start

```bash
cp example.env .env          # then edit paths/credentials
python schema.py             # seed params/source/selection (once)
./slurm.sh                   # fan out the populate across the cluster
./monitor.sh                 # watch your jobs (also --all/--pending/--done)
./cancel.sh --all            # stop them early if you need to
```

You can either run from this directory, or copy `slurm.sh` + your `.env` into
your own project directory (pointing `PY_SCRIPT` at your script) and run there.

Logs are written to `/stelmo/slurm/<dj_user>/<job_id>_<timestamp>.log`.

## Workers vs. processes

There are two knobs for running several items at once:

| Knob        | Set in                       | What it does                                   |
| ----------- | ---------------------------- | ---------------------------------------------- |
| `WORKERS`   | `.env`                       | N independent SLURM tasks, may span both nodes |
| `processes` | `POP_KWARGS` (`populate.py`) | N forked processes inside one task, one node   |

Both coordinate through the same `jobs` reservation table, so they are
interchangeable — and **you should pick one, not both**. Nesting them multiplies
your footprint (`WORKERS=8` with `processes=4` is 32 concurrent items) and makes
`MEMORY` hard to reason about. Two supported shapes:

```bash
# A. N workers, 1 process each  (the default; populate.py ships this way)
WORKERS=8   CORES=1   MEMORY=<one item>        # processes: 1

# B. 1 worker, N processes
WORKERS=1   CORES=8   MEMORY=<one item × 8>    # processes: 8
```

`MEMORY` is **per SLURM task**. In shape A that is the budget for a single item;
in shape B the whole pool shares it, so multiply by the number of processes.

### Rule of thumb

Two questions decide it:

1. **How much memory does one item need?** A node has 64 cores and ~1.5 TB, so
    an even split is ~24 GB per core. If one item needs much more than that,
    memory is your limit rather than cores — run fewer concurrent items than
    you have cores, and prefer shape A so each one gets its own enforced
    ceiling.

2. **How busy is the cluster?** Check with `./monitor.sh --all` or `sinfo`.

    - **Busy** → shape A. The backfill scheduler drops 1-core tasks into gaps, so
        some workers start right away and you make partial progress while the
        rest queue. One 8-core task must wait for 8 cores free at once.
    - **Idle** → either works. Shape B is simpler to follow: one job, one log.
    - **Many quick items** → shape B. Forking costs less than starting an array
        task, so per-item overhead stays small.

### If you do set `processes`

Never use `processes=None`. DataJoint caps the pool with `mp.cpu_count()`, which
reports the machine's 64 CPUs — not the `--cpus-per-task` you were allocated —
so you would get 64 processes contending for your `CORES` cores, at everyone
else's expense. Set it to the same number as `CORES`.
