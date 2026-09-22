import datajoint as dj
from spyglass.utils.dj_graph import RestrGraph
from tqdm import tqdm

schema = dj.schema("franklab_cascade_log")


@schema
class LogCascadeResult(dj.Manual):
    definition = """
    # Log of cascade results
    nwb_file_name = "": varchar(128)
    job_name = "": varchar(128)
    """

    class Table(dj.Manual, dj.Part):
        definition = """
        # Table for log entries
        -> LogCascadeResult
        table_name: varchar(128)
        ---
        count: int
        matching_keys: longblob
        full_entries: longblob
        """

    def check_db_table_entries(
        self, nwb_file_name: str, job_name: str, table: dj.Table
    ) -> dj.expression.QueryExpression:
        keys = (
            self.Table
            & {
                "nwb_file_name": nwb_file_name,
                "job_name": job_name,
                "table_name": table.full_table_name,
            }
        ).fetch1("matching_keys")
        return table & keys

    def get_log_entries(
        self, nwb_file_name: str, job_name: str, table: dj.Table
    ) -> list[dict]:
        return (
            self.Table
            & {
                "nwb_file_name": nwb_file_name,
                "job_name": job_name,
                "table_name": table.full_table_name,
            }
        ).fetch1("full_entries")

    def run_and_log_cascade(
        self,
        nwb_file_name: str,
        job_name: str,
        leaves: list[dict],
        seed_table: dj.Table,
        direction: str,
    ) -> RestrGraph:
        run_entry = {
            "nwb_file_name": nwb_file_name,
            "job_name": job_name,
        }
        if self & run_entry:
            raise ValueError(
                f"Run entry {run_entry} already exists. "
                + "Delete before rerunning if necessary."
            )

        graph = RestrGraph(
            leaves=leaves,
            direction=direction,
            cascade=True,
            seed_table=seed_table,
        )
        restr_ft = graph.restr_ft
        log_entries = []
        for table in tqdm(restr_ft):
            primary_keys = table.fetch("KEY")
            full_keys = table.fetch(as_dict=True)
            entry = {
                "nwb_file_name": nwb_file_name,
                "job_name": job_name,
                "table_name": table.full_table_name,
                "matching_keys": primary_keys,
                "full_entries": full_keys,
                "count": len(full_keys),
            }
            log_entries.append(entry)

        with self.connection.transaction:
            self.insert1(run_entry)
            self.Table().insert(log_entries)
        return graph
