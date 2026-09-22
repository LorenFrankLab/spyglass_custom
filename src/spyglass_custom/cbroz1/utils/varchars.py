"""
Setting aside for now, but this is a good start to a general solution for
datajoint tables with varchar columns that are too long. Errors on trying to
drop the primary key of tables without the offending column.
Changing to an approach of editing the sql backup files instead
"""

import datajoint as dj
import networkx as nx
from datajoint_utilities.dj_search.lists import list_tables
from spyglass import (  # noqa: F401
    common,
    data_import,
    decoding,
    lfp,
    lfp_band,
    position,
    position_linearization,
    ripple,
    spikesorting,
)

# NOTES: what's the diff btwn diagram nodes and sorted tables?
LEAD = "common_nwbfile.nwbfile"
SCHEMA = "common_nwbfile"
TABLE = "nwbfile"
COLUMN = "nwb_file_name"
OLD_LEN = 64
NEW_LEN = 60


class ReduceVarchar:
    def __init__(
        self,
        schema=SCHEMA,
        table=TABLE,
        column=COLUMN,
        old_len=OLD_LEN,
        new_len=NEW_LEN,
        exec_direct=False,
        limit=None,
    ):
        self.schema = schema
        self.table = table
        self.full_table = f"`{self.schema}`.`{self.table}`"
        self.column = column
        self.old_len = old_len
        self.new_len = new_len
        self._target_tables = []
        self._sorted_tables = []
        self._diagram = None
        self.limit = int(limit) if limit else None
        self.exec_direct = exec_direct
        self._all_table_defs = {}
        self._all_full_table_defs = {}
        self._connection = dj.conn()
        self._do_nothing = "SELECT NULL FROM DUAL WHERE 1 = 0"

    def get_target_tables(self):
        tables = list(set(list_tables(attribute=COLUMN)[::-1]))
        if self.limit and len(tables) > self.limit:
            return tables[: self.limit]
        return tables

    @property
    def target_tables(self):
        if not self._target_tables:
            self._target_tables = self.get_target_tables()
        return self._target_tables

    @property
    def diagram(self):
        if not self._diagram:
            self._diagram = self.get_diagram()
        return self._diagram

    def _name_to_table(self, table):
        return dj.FreeTable(dj.conn(), table)

    def _name_to_diagram(self, table):
        return dj.Diagram(self._name_to_table(table))

    def get_diagram(self):
        return self._name_to_diagram(self.full_table).from_sequence(
            [self._name_to_diagram(t) for t in self.target_tables]
        )

    def _sort_tables(self):
        G = self.diagram
        ordered = []

        while G:
            added_node = None
            for node in list(G.nodes()):
                successors = list(G.successors(node))
                if all(successor in ordered for successor in successors):
                    ordered.append(node)
                    added_node = node
                    break  # Break out of the loop after adding a node

            if added_node:  # Remove the added node from the graph
                G.remove_node(added_node)
            else:  # no node can be added (e.g., due to cycles)
                break

        return [o for o in ordered if not o.isdigit()]

    @property
    def sorted_tables(self):
        if not self._sorted_tables:
            self._sorted_tables = self._sort_tables()
        return self._sorted_tables

    def exec(self, sql, exec_direct=None):
        if not exec_direct:
            exec_direct = self.exec_direct
        if not exec_direct:
            with open("temp.sql", "a") as f:
                f.write(sql + "\n")
            return
        sql = sql.replace("\n", " ").replace("\t", " ")
        dj.conn().query(sql).fetchall()

    def _alt_tbl(self, table):
        return f"ALTER TABLE {table} \n\t"

    def _split_table_name(self, table):
        return table.replace("`", "").split(".")

    def _add_col_sql(self, table):
        return (
            self._alt_tbl(table)
            + f"\tADD COLUMN {self.column}_new VARCHAR({self.new_len}) "
            + f"NOT NULL AFTER {self.column};"
        )

    def _if_col_sql(self, table_full, exists=False, if_true="", if_false=""):
        schema, table = self._split_table_name(table_full)
        if_true = if_true or self._do_nothing
        if_false = if_false or self._do_nothing
        when = "count1 > 0" if exists else "count1 < 1"
        return (
            f"SELECT \n\tCASE WHEN \n\t\t{when} \n\tTHEN\n\t(\n\t"
            + f"\tCONCAT('{if_true}')\n\t)\n\tELSE CONCAT('{if_false}')\n\tEND "
            + "INTO @sql\nFROM\n (\n\t SELECT\n\t\t (\n\t\t\t"
            + "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS\n\t\t\t"
            + f"WHERE table_name = '{table}'\n\t\t\t"
            + f"AND table_schema = '{schema}'\n\t\t\t"
            + f"AND column_name IN ('{self.column}_new')\n\t\t"
            + ") AS count1\n\t)\nAS counts;\n"
            + "PREPARE stmt FROM @sql;\nEXECUTE stmt;\nDEALLOCATE PREPARE stmt;"
        )

    def _add_col_if_not_exist_sql(self, table):
        return self._if_col_sql(
            table, if_true=self._add_col_sql(table), exists=False
        )

    def add_col_if_not_exist(self, table):
        self.exec(self._add_col_if_not_exist_sql(table))

    def _update_col_sql(self, table):
        return f"UPDATE {table} SET {self.column}_new = {self.column};"

    def update_col(self, table):
        self.exec(self._update_col_sql(table))

    def _rename_col_sql(self, table):
        str_templ = (
            self._alt_tbl(table)
            + "CHANGE COLUMN {0} {1} VARCHAR({2}) NOT NULL;\n"
        )
        return str_templ.format(
            self.column, self.column + "_old", self.old_len
        ) + str_templ.format(self.column + "_new", self.column, self.new_len)

    def rename_col(self, table):
        self.exec(self._rename_col_sql(table))

    def _drop_col_sql(self, table):
        return f"{self._alt_tbl(table)}DROP COLUMN {self.column}_old;"

    def drop_col(self, table):
        self.exec(self._drop_col_sql(table))

    def _filter_defs(self, table, type="all"):
        defs = self._all_table_defs.get(table)
        if type == "all":
            return [item for sublist in defs.values() for item in sublist]
        elif type == "sk":
            return defs["id"] + defs["fk"]
        return defs[type]

    def _full_table_def(self, table, force=False) -> list:
        if not force and self._all_full_table_defs.get(table):
            return self._all_full_table_defs[table]

        self._all_full_table_defs[table] = [
            line.rstrip(",")
            for line in (
                dj.conn().query(f"SHOW CREATE TABLE {table};").fetchall()[0][1]
            ).split("\n")
        ]

        return self._all_full_table_defs[table]

    def table_def(self, table, type="all", force=False):
        if not force and self._all_table_defs.get(table):
            return self._filter_defs(table, type)

        start = {"pk": "PRIMARY KEY", "fk": "CONSTRAINT", "id": "KEY"}
        all = [
            line.strip()
            for line in self._full_table_def(table, force=force)
            if COLUMN in line
        ]
        self._all_table_defs[table] = {
            k: [line for line in all if line.startswith(v)]
            for k, v in start.items()
        }

        return self._filter_defs(table, type)

    def _add_keys_sql(self, lines, table):
        if not lines:
            return ""
        if not isinstance(lines, list):
            lines = [lines]
        return (
            "\n".join([f"{self._alt_tbl(table)}ADD {line};" for line in lines])
            + "\n"
        )

    def add_keys(self, table, type="all"):
        lines = self.table_def(table, type=type)
        self.exec(self._add_keys_sql(lines, table))

    def _drop_keys_sql(self, lines, table):
        if not lines:
            return ""
        if not isinstance(lines, list):
            lines = [lines]
        ret = ""
        for line in lines:
            this_drop = self._alt_tbl(table) + "DROP "
            if line.startswith("CONSTRAINT"):
                ret += this_drop + "FOREIGN KEY " + line.split(" ")[1] + ";\n"
                continue
            ret += this_drop + line.split(" (")[0] + ";\n"
        return ret

    def drop_keys(self, table, type="all"):
        lines = self.table_def(table, type=type)
        self.exec(self._drop_keys_sql(lines, table))

    def main(self):
        if not self.exec_direct:  # clear temp.sql
            with open("temp.sql", "w") as _:
                pass

        self.exec("USE information_schema;\n")  # select information_schema db

        for table in self.target_tables:
            self.add_col_if_not_exist(table)  # add all cols

        for table in self.sorted_tables:
            self.drop_keys(table, type="fk")  # drop foreign keys
            self.drop_keys(table, type="id")  # drop index keys

        for table in self.sorted_tables:
            self.drop_keys(table, type="pk")  # drop primary keys
            self.rename_col(table)  # rename column
            self.drop_col(table)  # drop old column

        for table in self.sorted_tables[::-1]:  # add keys back
            self.add_keys(table, type="id")
            self.add_keys(table, type="fk")
            self.add_keys(table, type="pk")


if __name__ == "__main__":
    import os
    import sys

    os.environ["DJ_SUPPORT_FILEPATH_MANAGEMENT"] = "TRUE"
    limit = sys.argv[1] if len(sys.argv) > 1 else None
    ReduceVarchar(limit=limit).main()
