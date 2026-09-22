"""Example Spyglass-style schema for the SLURM demo.

Declares a minimal, **self-contained** pipeline -- Params, Selection, and
Analysis tables -- and seeds the manual data. It deliberately does NOT call
``populate``; that is the job of the companion ``populate.py``, which imports
the tables defined here and runs the analysis across a SLURM array.

This module is importable like any installed package, e.g.::

    from spyglass_custom.cbroz1.SLURM import DemoAnalysis
    from spyglass_custom.cbroz1.SLURM.schema import DemoSelection

Environment variables (set by ``slurm.sh`` / ``.env``)
    DJ_CONFIG
        Path to a DataJoint config JSON (credentials). If set, it is loaded
        before the schema is declared so the connection -- and the schema name
        prefix -- come from the right database user. If unset, DataJoint's
        default lookup (``./dj_local_conf.json`` or ``~/.datajoint_config.json``)
        applies.

    SPYGLASS_CONDA_PATH / SPYGLASS_CONDA_ENV
        Consumed by ``slurm.sh`` to activate the environment; not read here.

Usage
    # declare the schema and seed the selection pairs
    # (params + source are Lookup tables and auto-load on declaration)
    python schema.py
"""

import os
import socket
import time

import datajoint as dj
from spyglass.utils import SpyglassMixin

# --- Load credentials from the env-provided config BEFORE declaring schema ---
# The schema name depends on database.user, and declaring a schema opens a
# connection, so the config must be in place first.
_DJ_CONFIG = os.environ.get("DJ_CONFIG")
if _DJ_CONFIG:
    dj.config.load(_DJ_CONFIG)

# Schema prefix = the DataJoint user
dj_user = dj.config["database.user"]
SCHEMA_SUFFIX = "test"  # rename if you want a dedicated schema
schema = dj.Schema(f"{dj_user}_{SCHEMA_SUFFIX}")


@schema
class DemoParams(SpyglassMixin, dj.Lookup):
    """Parameter sets for the demo analysis.

    Stand-in for tables like ``RippleParameters``. Each row pairs a name with a
    blob of parameters consumed by ``DemoAnalysis.make_compute``.
    """

    definition = """
    param_name : varchar(32)  # human-readable parameter-set name
    ---
    params     : blob         # dict of parameters
    """

    contents = [
        ["fast", {"sleep_s": 2.0, "factor": 1.0}],
        ["slow", {"sleep_s": 5.0, "factor": 2.0}],
    ]


@schema
class DemoSource(SpyglassMixin, dj.Lookup):
    """Toy upstream "session" table."""

    definition = """
    demo_id : int          # toy primary key
    ---
    label   : varchar(32)  # human-readable label
    value   : float        # toy input value for the analysis
    """

    contents = [
        {"demo_id": i, "label": f"sample_{i:02d}", "value": float(i) * 1.5}
        for i in range(8)  # 8 toy rows -> visible fan-out across array tasks
    ]


@schema
class DemoSelection(SpyglassMixin, dj.Manual):
    """Pairs a source row with a parameter set -- the populate key source.

    A Selection table is a staging area: only the (source, params) pairs
    inserted here will be computed by ``DemoAnalysis``.
    """

    definition = """
    -> DemoSource
    -> DemoParams
    """

    def insert_all(self, param_name="fast"):
        """Pair every ``DemoSource`` row with one parameter set.

        Parameters
        ----------
        param_name : str, optional
            Parameter-set name to pair with each source. Default ``"fast"``.
        """
        self.insert(
            [
                {**src_key, "param_name": param_name}
                for src_key in DemoSource.fetch("KEY")
            ],
            skip_duplicates=True,
        )


@schema
class DemoAnalysis(SpyglassMixin, dj.Computed):
    """Toy analysis populated by ``populate.py``.

    Uses the **tri-part make** pattern (``make_fetch`` / ``make_compute`` /
    ``make_insert``) so the long ``make_compute`` step runs OUTSIDE the database
    transaction -- no table lock is held while computing. See spyglass
    ``Features/Populate.md``.

    The ``worker`` and ``task_id`` columns record which host and SLURM array
    task produced each row, making the parallel fan-out visible in the table.
    """

    definition = """
    -> DemoSelection
    ---
    result       : float         # value * factor
    worker       : varchar(64)   # hostname that ran make_compute
    task_id      : varchar(16)   # SLURM array task id ('n/a' if run locally)
    compute_secs : float         # wall-clock seconds spent in make_compute
    """

    def make_fetch(self, key):
        """Read inputs from upstream tables. Read-only; no writes here.

        Parameters
        ----------
        key : dict
            The primary key of the ``DemoSelection`` row being computed.

        Returns
        -------
        list
            ``[value, params]`` passed positionally to ``make_compute``.
        """
        value = (DemoSource & key).fetch1("value")
        params = (DemoParams & key).fetch1("params")
        return [value, params]

    def make_compute(self, key, value, params):
        """Run the (deliberately slow) computation. No DB access allowed here.

        Parameters
        ----------
        key : dict
            The primary key being computed.
        value : float
            Input value fetched from ``DemoSource``.
        params : dict
            Parameter dict fetched from ``DemoParams``.

        Returns
        -------
        list
            ``[insert_dict]`` passed positionally to ``make_insert``.
        """
        start = time.time()
        time.sleep(float(params["sleep_s"]))  # stand in for heavy work
        result = float(value) * float(params["factor"])
        insert_dict = {
            "result": result,
            "worker": socket.gethostname(),
            "task_id": os.environ.get("SLURM_ARRAY_TASK_ID", "n/a"),
            "compute_secs": round(time.time() - start, 3),
        }
        return [insert_dict]

    def make_insert(self, key, insert_dict):
        """Write the result. Runs inside a transaction (the only writer).

        Parameters
        ----------
        key : dict
            The primary key being computed.
        insert_dict : dict
            Payload returned by ``make_compute``.
        """
        self.insert1({**key, **insert_dict})


def insert_demo_data(param_name="fast"):
    """Seed the Selection table (the only manual step).

    ``DemoParams`` and ``DemoSource`` are Lookup tables: their ``contents`` are
    inserted automatically when the schema is declared, so only the Selection
    pairs need to be created here.

    Parameters
    ----------
    param_name : str, optional
        Parameter set to pair with each source in ``DemoSelection``.
        Default ``"fast"``.
    """
    DemoSelection().insert_all(param_name=param_name)


if __name__ == "__main__":
    insert_demo_data()
    print(f"Schema       : {schema.database}")
    print(f"DemoParams   : {len(DemoParams())} rows")
    print(f"DemoSource   : {len(DemoSource())} rows")
    print(f"DemoSelection: {len(DemoSelection())} rows")
    print(
        f"DemoAnalysis : {len(DemoAnalysis())} populated "
        f"({len(DemoAnalysis().key_source - DemoAnalysis())} pending)"
    )
