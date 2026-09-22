"""SLURM demo package.

Re-exports the demo schema tables so they can be imported directly::

    from spyglass_custom.cbroz1.SLURM import DemoAnalysis

Note: importing this package declares the schema and opens a connection.
"""

from spyglass_custom.cbroz1.SLURM.schema import (
    DemoAnalysis,
    DemoParams,
    DemoSelection,
    DemoSource,
    insert_demo_data,
    schema,
)

__all__ = [
    "DemoAnalysis",
    "DemoParams",
    "DemoSelection",
    "DemoSource",
    "insert_demo_data",
    "schema",
]
