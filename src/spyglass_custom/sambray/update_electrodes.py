from spyglass.common import Electrode
from cascade_logging import LogCascadeResult

from datajoint.condition import make_condition


def update_electrode_from_nwbfile(
    nwb_file_name, dry_run=True, log_deletes=True
):
    """
    Update electrode records from an NWB file. Will delete downstream records.

    This function will compare the electrode data in the NWB file with the
    existing records in the database. If there are any discrepancies, it will
    log affected tables and update the records.

    Parameters
    ----------
    nwb_file_name : str
        The name of the NWB file to update from.
    dry_run : bool, optional
        If True, no changes will be made to the database. Default is True.
    log_deletes : bool, optional
        If True, affected table entries will be logged. Default is True.
    """

    table = Electrode()
    inserts = table.insert_from_nwbfile(nwb_file_name, dry_run=True)[table]
    to_update = []
    to_update_pk = []
    for insert in inserts:
        insert_pk = {k: v for k, v in insert.items() if k in table.primary_key}
        if not (table & insert_pk):
            raise ValueError(
                f"No existing record found for primary key: {insert_pk}"
            )

        existing = (table & insert_pk).fetch1()
        for k, v in insert.items():
            if v != existing[k]:
                print(
                    f"Mismatch for {k}: {v} (new) vs {existing[k]} (existing)"
                )
                to_update.append(insert)
                to_update_pk.append(insert_pk)
                break
    print(f"Total records to update: {len(to_update)}")

    if log_deletes:
        print("Logging records for deletion...")
        restriction = make_condition(Electrode(), to_update_pk, set())
        leaves = [
            {
                "table_name": Electrode.full_table_name,
                "restriction": restriction,
            }
        ]
        seed_table = Electrode()
        LogCascadeResult().run_and_log_cascade(
            nwb_file_name=nwb_file_name,
            job_name="update_electrodes_from_nwbfile",
            leaves=leaves,
            seed_table=seed_table,
            direction="down",
        )

    if dry_run:
        print("Dry run mode: No updates will be made.")
        return to_update_pk, to_update

    if not (
        LogCascadeResult()
        & {
            "nwb_file_name": nwb_file_name,
            "job_name": "update_electrodes_from_nwbfile",
        }
    ):
        raise ValueError(
            "No log entries for this delete."
            + f"Please re-run with log_deletes=True"
        )
    # delete existing records that need to be updated to ensure database integrity
    (table & to_update_pk).delete()
    # re-insert updated records
    table.insert(to_update, allow_direct_insert=True)
    print(f"Updated {len(to_update)} records in the database.")
    return to_update_pk, to_update
