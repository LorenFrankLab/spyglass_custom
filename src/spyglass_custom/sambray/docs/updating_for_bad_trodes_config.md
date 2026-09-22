# Updating NWB converted with wrong trodes config

**NOTE:** Deletes all affected entries downstream of `spyglass.common.Electrode`

```python
original_nwb_file = "demo20260609.nwb"
copy_file_name = "demo20260609_.nwb"
data_dir = "/stelmo/nwb/raw/"
```

## 1. Update the copy nwb file

Requirements:

- corrected trodes_config
- metadata.yaml used in original conversion
  - Note: This does NOT support changing of probe type and will error if attempted

Steps:

1. Close any process with the file open
2. Run (< 1 min)

   ```python
   trodes_to_nwb.update_electrodes.update_electrodes_from_config(
        nwb_file_name = data_dir+copy_file_name,
        metadata_path = metadata_path,
        probe_metadata_paths = trodes_to_nwb.convert.get_included_device_metadata_paths,
        trodesconf_path = trodesconf_path)
    ```

3. Confirm changes are as expected

## 2. Update spyglass

Steps:

1. Run in dry_mode (~30min):

    ```python
    ###
    ### import any custom tables you want logged here (see steps 3 below)
    ###

    to_update_pk, to_update = spyglass_custom.sambray.update_electrodes.update_electrode_from_nwbfile(
        copy_file_name, dry_run=True, log_deletes=True
    )
    ```

2. Check that the list of electrodes that will be update is correct.  Remember, all downstream sorts/lfp/entries of these electrodes will be deleted.
3. Check which entries in downstream tables will be deleted
   - All affected. The keys logged here will remain accessible after deletion
   and can be used to help repopulate (e.g. finding what parameters you used)

    ```python
     from spyglass_custom.sambray.cascade_logging import LogCascadeResult

     key = {
        "nwb_file_name":copy_file_name,
        "job_name"="update_electrodes_from_nwbfile"
     }
     LogCascadeResult.Table & key
    ```

    - A specific Table. This function compares to the current database state.
    Once the delete is run, it will show empty results

    ```python
    from spyglass_custom.sambray.cascade_logging import LogCascadeResult
    from spyglass.decoding.v1.clusterless import ClusterlessDecodingV1

    LogCascadeResult().check_db_table_entries(
        nwb_file_name=copy_file_name,
        job_name="update_electrodes_from_nwbfile",
        table=ClusterlessDecodingV1
    )
    ```

4. Once confirmed, run for real to delete and repopulate (**This is a destructive action**, ~1min)

    ```python
    to_update_pk, to_update = spyglass_custom.sambray.update_electrodes.update_electrode_from_nwbfile(
        copy_file_name, dry_run=False, log_deletes=False
    )
    ```

## 3. Update the original nwb file

Requirements:

- corrected trodes_config
- metadata.yaml used in original conversion
  - Note: This does NOT support changing of probe type and will error if attempted

Steps:

1. Close any process with the file open
2. Run (< 1 min)

    ```python
    trodes_to_nwb.update_electrodes.update_electrodes_from_config(
        nwb_file_name = data_dir+original_nwb_file,
        metadata_path = metadata_path,
        probe_metadata_paths = trodes_to_nwb.convert.get_included_device_metadata_paths,
        trodesconf_path = trodesconf_path)
    ```
