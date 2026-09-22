# Spyglass Custom

The Frank Lab's custom tools, tables, and documentation for our Spyglass
instance.

## Installation and Use

To use:

1. Install [Spyglass](https://lorenfranklab.github.io/spyglass/).
2. Fork this repository, and clone it.
3. Make install it as editable: `cd spyglass_custom; pip install -e .`
4. Decide your 'project name'.
    - If this work is unique to you, use your user name.
    - For any other name, you'll need to (a) talk to Chris about permissions and
        (b) set up your config with a 'database.prefix' (see
        `dj_local_conf_example.json`).
5. Make a new folder for your custom tables:
    `src/spyglass_custom/project-or-user/`. It is recommended that you copy the
    example in `cbroz1/template.py`

To automate style and bug checks, you can use `pre-commit`. Note that pre-commit
may edit your files to be `black` or `pep8` compliant.

```bash
pip install pre-commit
pre-commit # to automatically run checks whenever you git commit
pre-commit run --all-files # to manually run all checks
```

## Contents

Contributions are organized by project or user name, with shared helpers in
`utils/`.

- `cbroz1/` — Templates, teaching material, and maintenance tooling.
  - `schema_template.py`, `schema_demo.ipynb` — Starting point for a new
        custom schema.
  - `SLURM/` — Guide, launcher scripts, and a demo pipeline for running
        `populate` jobs as SLURM arrays.
  - `maintenance/` — Frank Lab-specific maintenance scripts (e.g. `.rec` file
        tracking and disk usage reports).
  - `talks/` — Slides for lab talks on schema design, code reuse, custom
        pipelines, and development tools.
  - `bad_examples/` — Intentionally broken scripts, paired with their fixes,
        for demonstrating linters.
  - `dev_scripts/` — One-off scripts used while developing upstream Spyglass
        (currently Position V2 stress tests and fixture generation).
  - `utils/` — Miscellaneous helpers.
- `sambray/` — Trial parsing, spike train analyses, and database repair tools.
  - `trials_parsing.py` — Parse trial structure from DIO events.
  - `cross_correlogram.py`, `monosynaptic.py` — Spike cross-correlograms and
        monosynaptic connection detection.
  - `cascade_logging.py`, `update_electrodes.py` — Log downstream entries
        affected by a delete, and re-ingest electrodes from a corrected NWB file.
  - `demos/`, `docs/` — Notebooks and notes for the above.
- `utils/` — Helpers shared across all projects.
  - `schema_prefix.py` — Resolve the schema prefix from your DataJoint config.
