---
title: Custom Pipelines
author: Chris Broz
date: 10/2024
styles:
    style: dracula
---

# Schema Design

- This document gives an overview of translating an analysis into a custom
    DataJoint schema.
- It builds on other Spyglass documentation, and assumes you have a basic
    understanding of DataJoint syntax. See ...
  - [Pipeline Overview](https://datajoint.com/docs/core/datajoint-python/latest/quick-start/)
  - [Table Types](https://lorenfranklab.github.io/spyglass/latest/ForDevelopers/TableTypes/)
  - [Understanding a Schema](https://lorenfranklab.github.io/spyglass/latest/ForDevelopers/Schema/)

1. Analysis Structure
    1. Parameters
    1. Data
    1. Selection (optional)
    1. Analysis
1. Deciding foreign key references
1. Using Merge Tables

---

## Analysis Structure

### Parameters

- Key-value pairs that define the analysis.
- Choice of explicit fields or `blob` for arbitrary data.
  - Fields are more readable and searchable.
  - `blob` is more flexible and easier to add new parameters.

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
```

---

### Data (1/2)

- Input data might ...
  - Include minor preprocessing within your pipeline (e.g., blinding)
  - Be raw (e.g., `common.Raw`) or processed by another pipeline
    (e.g., `lfp.LFPOutput`), and referenced by a `Selection` table.
- Data tables might be structured differently depending on the unit of analysis.
  - One-to-one with existing tables: link directly to `Selection` table.
  - One-to-many or many-to-one: use `Part` tables.

<!-- stop -->

```python
@schema
class SubjBlinded(SpyglassMixin, dj.Manual): # Preprocessed data
    """Blinded subject table."""

    definition = """
    subject_id: uuid # id
    ---
    -> Subject.proj(actual_id='subject_id')
    """

    def insert(self, data):
        """Insert data with blinded subject IDs."""
        ...
```

---

### Data (2/2)

- Input data might ...
  - Include minor preprocessing within your pipeline (e.g., blinding)
  - Be raw (e.g., `common.Raw`) or processed by another pipeline
    (e.g., `lfp.LFPOutput`), and referenced by a `Selection` table.
- Data tables might be structured differently depending on the unit of analysis.
  - One-to-one with existing tables: link directly to upstream table.
  - Many-to-one: use `Part` tables.

```python
@schema
class MyGrouping(SpyglassMixin, dj.Manual): # many-to-one
    definition = """
    group_id: int
    """

    class MyUnit(SpyglassMixinPart):
        definition = """
        -> MyGrouping
        -> UpstreamUnit
        """
```

---

### Selection

- `make` will pair all combinations of upstream primary keys.
- A `Selection` table can be used to filter the upstream data, as a staging
    table for analysis.

```python
@schema
class MyAnalysisSelection(SpyglassMixin, dj.Manual):
    definition = """
    -> SubjBlinded
    -> MyParams
    """
```

---

### Analysis

- `make` method populates the table, running core analysis.
- `MyAnalysis.populate()` will run the analysis on all combinations of
    upstream primary keys.
- By convention, results are stored as NWB files, with a reference to the
    `AnalysisNwbfile` table.
- Postpone `insert` calls until the end of the `make` (see also 'atomicity').

```python
@schema
class MyAnalysis(SpyglassMixin, dj.Computed):
    definition = """
    -> MyAnalysisSelection
    ---
    -> AnalysisNwbfile
    """

    class MyAnalysisPart(SpyglassMixin, dj.Part):
        definition = """
        -> MyAnalysis
        ---
        result: int
        """

    def make(self, key):
        params = (MyParams & key).fetch1("params")
        ...
        self.MyAnalysisPart.insert1({**key, "result": 1})
        self.insert1(key)
```

---

## Foreign Key References

How to reference upstream tables?

Primary vs Secondary and Foreign Key vs Field are orthogonal concepts.

- A primary field is a unique identifier for that table.
- A secondary field is often arbitrary data associated with the primary key.
- A foreign primary key is likely another processing step for that data.
- A foreign secondary key is likely other data associated with that primary key,
    like `AnalysisNwbfile`.

---

### Foreign Primary Key

- As sole primary key indicates a one-to-one relationship, as another
processing step.

```python
definition = """
-> UpstreamTable
---
anything: varchar(32)
"""
```

<!-- stop -->

- As one of multiple primary keys indicates pairing of upstream data.

```python
definition = """
-> UpstreamTable1
-> UpstreamTable2
"""
```

---

### Foreign Secondary Key

As a secondary key indicates either ...

- Linking to non-indexed fields. Does not uniquely identify the row.

```python
definition = """
-> UpstreamTable1
---
-> AnalysisNwbfile
"""
```
<!-- stop -->

- An aliasing of the primary key(s), useful for SQL limitations.

```python
definition = """
my_id: int
---
-> UpstreamTable1
-> UpstreamTable2
"""
```

---

## Using Merge Tables

- Merge tables signify that an analysis can be done in multiple ways.
- When attaching downstream, you can either ...
  - Assume your data comes from one path, or 'source'.
  - Allow for multiple sources and let merge-specific functions handle it.

```python
@schema
class MyAnalysis(SpyglassMixin, dj.Computed):
    definition = """
    -> SomeMergeTable
    -> MyParams
    ---
    -> AnalysisNwbfile
    """

    def make(self, key):
        params = (MyParams & key).fetch1("params")
        parent = SomeMergeTable().merge_get_parent(key)
        ...
        self.insert1(key)
        self.MyAnalysisPart.insert1({**key, "result": 1})
```

See
[docs](https://lorenfranklab.github.io/spyglass/latest/api/utils/dj_merge_tables/)
for more information on available methods.
[Existing tables](https://github.com/LorenFrankLab/spyglass/blob/master/src/spyglass/decoding/decoding_merge.py)
also provide a good reference.

---

This is a presentation designed for use with `lookatme`:

```console
pip install lookatme
lookatme FileName.md --live
```
