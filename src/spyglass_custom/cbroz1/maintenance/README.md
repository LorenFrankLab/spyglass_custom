# Frank Lab *Specific* Maintenance Scripts

This directory contains maintenance scripts specific to the Frank Lab, that
should not be part of the core Spyglass repository. For information on how
maintenance scripts are run, including environment variables, see the
corresponding section in the Sgyglass repository.

## Scripts

- `file_tracking.py`
    - Track all `.rec` files on disk.
    - Apply expiration policies.
    - Make best guesses about corresponding database entries.
    - Print reports of disk usage by lab member.
