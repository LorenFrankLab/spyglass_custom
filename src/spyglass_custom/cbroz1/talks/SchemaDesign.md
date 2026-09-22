---
title: Schema Design
author: Chris Broz
date: 04/2024
styles:
    style: dracula
---

# Schema Design

- This document gives a detailed overview of the schema template provided in `spyglass_custom`
- It includes explanations of the different components of a Python script that defines a pipeline.

1. General concepts
1. Goals of a schema
1. Front matter
    1. Imports
    1. Schema declaration
1. Table syntax
    1. Class inheritance
    1. Explicit table types
    1. Definitions
    1. Methods
1. Conceptual table types

---

## General concepts

- *Relational databases* store data in tables, with rows and columns.
- *Primary keys* are unique identifiers for each row.
- *Secondary keys* are fields for each row, but may repeat values.
- *Foreign keys* are references to primary keys in other tables.

<!-- stop -->

**Database design is the process of mapping our conceptual model of the data to
a set of tables, relaionships, fields, and contents.**

<!-- stop -->

For example...

- A `Subject` table will have some unique identifier for each subject.
- In some cases 'phenotype' can be an open description secondary field.
- In other cases, we may want  to compare phenotypes, and thus need a
    `Phenotype` table.
- The conceptual relationships between subject and phenotype will influence
    how we generate foreign key references.

---

## Goals of a schema

- At its core, DataJoint is just a mapping between Python and SQL.
- SQL is a language for managing relational databases.
- DataJoint is opinionated about how to structure the database, and limits
    the full power of SQL in way that promotes good practices.
- Python stores ...
  - A copy of table definitions, that may be out of sync with the database.
  - Methods for processing data, that may be out of sync with existing data.

Good data provenance requires good version control and documentation to
keep these in sync.

---

## Front matter

At the beginning of the schema file, you'll find ...

- Imports
  - Aliased imports
  - Package imports
  - Individual imports
  - Relative imports
- Schema declaration

```python
from typing import Union  # Individual class import
from itertools import chain as iter_chain  # Aliased function import

import datajoint as dj  # Aliased package import
from spyglass.common import Nwbfile  # noqa: F401 # Individual package import, quiet linter

from ..utils.schema_prefix import SCHEMA_PREFIX  # Relative import

schema = dj.schema(SCHEMA_PREFIX + "_example")  # Schema declaration
```

- Existing schema prefixes (e.g., `common`) should not be added to without discussion.
- Instead, use your username or discuss a project specific schema with Chris.

---

## Table syntax

Each table is defined as a Python class, with a `definition` attribute that
contains the SQL-like table definition.

### Class inheritance

The parentheses in the class definition indicate that the class inherits from.

This table is ...

- A `SpyglassMixin` table, which provides a number of useful methods specific to Spyglass.
- A DataJoint `Manual` table, which is a table that is manually populated.

```python
@schema
class ExampleTable(SpyglassMixin, dj.Manual):
    pass
```

---

### Table types

- [DataJoint types](https://datajoint.com/docs/core/datajoint-python/0.14/design/tables/tiers/):
  - `Manual` tables are manually populated.
  - `Lookup` tables can be populated on declaration, and rarely change.
  - `Computed` tables are populated by a method runs computations on upstream entries.
  - `Imported` tables are populated by a method that imports data from another source.
  - `Part` tables are used to store data that is conceptually part of another table.
- [Spyglass conceptual types](https://lorenfranklab.github.io/spyglass/latest/contribute/#types-of-tables):
  - Parameter tables (often `dj.Lookup`) store parameters for analysis.
  - Selection tables store pairings of parameters and data to be analyzed.
  - Data tables (often `dj.Computed`) store the results of analysis.
  - Merge tables combine data from multiple pipeline versions.

---

### Definitions

Each table can have a docstring that describes the table, and must have a
`definition` attribute that contains the SQL-like table definition.

- `#` comments are used to describe the table and its columns.
- `---` separates the primary key columns from the data columns.
- `field : datatype` defines a column using a [SQL datatype](https://datajoint.com/docs/core/datajoint-python/0.14/design/tables/attributes/)
- `->` indicates a foreign key reference to another table.

```python
@schema
class ExampleTable(SpyglassMixin, dj.Manual):
    """Table Description"""

    definition = """ # Table comment used as header
    primary_key1 : uuid # randomized string
    primary_key2 : int  # integer
    ---
    secondary_key1 : varchar(32) # string of max length 32
    -> Nwbfile # Foreign key reference, inherit primary key of this table
    -> Subject.proj(actual_id='subject_id') # Rename column
    """
```

---

### Methods

Many Spyglss tables have methods that provide functionality for the pipeline.

```python
@schema
class SubjBlinded(SpyglassMixin, dj.Manual):
    ...

    @property  # Static information, Table.property
    def pk(self):
        return "subject_id"

    @staticmethod  # Basic func with no reference to self instance
    def _subj_dict(id: UUID):
        """Return the subject dict"""
        return {"subject_id": id}

    @classmethod  # Class, not instance. Table.func(), not Table().func()
    def example(cls, argument=None):  # Default value
        pass  # Not doing anything

    def blind_subjs(self, restriction: Union[str, dict]):
        """Import all subjects selected by the restriction"""
        insert_keys = [
            {
                **self._subj_dict(dj.hash.key_hash(key)),
                "actual_id": key["subject_id"],
            }
            for key in (Subject & restriction).fetch("KEY")
        ]
        self.insert(insert_keys, skip_duplicates=True)
```

---

### Example Params Table

```python
@schema
class MyParams(SpyglassMixin, dj.Lookup):
    definition = """
    param_name: varchar(32)
    ---
    params: blob
    """
    contents = [  # Contents inserted on declaration
        ["example1", {"A": 1, "B": 2}],
        ["example2", {"A": 3, "B": 4}],
    ]

    @classmethod
    def insert_default(cls):
        cls.insert(cls.contents, skip_duplicates=True)
```

---

### Example Selection/Analysis Tables

```python
@schema
class MyAnalysisSelection(SpyglassMixin, dj.Manual):
    definition = """
    -> SubjBlinded
    -> MyParams
    """

@schema
class MyAnalysis(SpyglassMixin, dj.Computed):
    definition = """
    -> MyAnalysisSelection
    """

    class MyAnalysisPart(SpyglassMixin, dj.Part):
        definition = """
        -> MyAnalysis
        ---
        result: int
        """

    def make(self, key):
        ...
        self.insert1(key)
        self.MyAnalysisPart.insert1({**key, "result": 1})
```

Run the `make method` to populate the table with `MyAnalysis().populate()`.

---

### Example Schema Diagram

[Example Diagram](./ExampleDiagram.png)

```python
import datajoint as dj

from spyglass_custom.cbroz1 import schema_template as st
from spyglass.common import Subject

(
    dj.Diagram(st)  # Start a new diagram with a schema
    + dj.Diagram(Subject)  # Add a table to the diagram
    - 1  # Add parent tables one level up
    + 1 # Add child tables one level down
).draw()  # Render the diagram, or use .save("filename.png")
```

---

This is a presentation designed for use with `lookatme`:

```console
pip install lookatme
lookatme FileName.md --live
```
