"""Schema template for Spyglass Custom"""

import random
from typing import Union
from uuid import UUID

import datajoint as dj
from spyglass.common import RawPosition, Subject  # noqa: F401
from spyglass.utils import SpyglassMixin

from spyglass_custom.cbroz1.utils import process_df
from spyglass_custom.utils.schema_prefix import schema_prefix

schema = dj.schema(schema_prefix + "_example")  # CHANGE THIS


@schema
class ExampleTable(SpyglassMixin, dj.Manual):
    """Table Description"""

    definition = """ # Table comment
    primary_key1 : uuid # randomized string
    primary_key2 : int  # integer
    ---
    secondary_key1 : varchar(32) # string of max length 32
    -> Subject # Foreign key reference, inherit primary key of this table
    """


@schema
class SubjBlinded(SpyglassMixin, dj.Manual):
    """Blinded subject table."""

    definition = """
    subject_id: uuid # id
    ---
    -> Subject.proj(actual_id='subject_id')
    """

    @property  # Static information, Table.property
    def pk(self):
        """Return the primary key"""
        return self.heading.primary_key

    @staticmethod  # Basic func with no reference to self instance
    def _subj_dict(subj_uuid: UUID):
        """Return the subject dict"""
        return {"subject_id": subj_uuid}

    @classmethod  # Class, not instance. Table.func(), not Table().func()
    def example(cls, argument=None):  # Default value
        """Example class method"""
        return dj.hash.key_hash(argument)

    def blind_subjects(self, restriction: Union[str, dict]):
        """Import all subjects selected by the restriction"""
        insert_keys = [
            {
                **self._subj_dict(dj.hash.key_hash(key)),
                "actual_id": key["subject_id"],
            }
            for key in (Subject & restriction).fetch("KEY")
        ]
        self.insert(insert_keys, skip_duplicates=True)

    def return_subj(self, key: str):
        """Return the entry in subject table"""
        if isinstance(key, dict):  # get rid of extra values
            key = key["subject_id"]
        key = self._subj_dict(key)
        actual_ids = (self & key).fetch("actual_id")
        ret = [{"subject_id": actual_id} for actual_id in actual_ids]
        return ret[0] if len(ret) == 1 else ret


@schema
class MyParams(SpyglassMixin, dj.Lookup):
    """Parameter table."""

    definition = """
    param_name: varchar(32)
    ---
    params: blob
    """
    contents = [
        ["example1", {"A": 1, "B": 2}],
        ["example2", {"A": 3, "B": 4}],
    ]

    @classmethod
    def insert_default(cls):  # Not req for dj.Lookup, but Spyglass convention
        """Insert default values."""
        cls().insert(rows=cls.contents, skip_duplicates=True)


@schema
class MyAnalysisSelection(SpyglassMixin, dj.Manual):
    """Selection table."""

    definition = """
    -> SubjBlinded
    -> MyParams
    """

    def insert_all(self, param_name="example1"):
        """Insert all subjects with given param name"""
        self.insert(
            [
                {**subj_key, "param_name": param_name}
                for subj_key in SubjBlinded.fetch("KEY")
            ],
            skip_duplicates=True,
        )


@schema
class MyAnalysis(SpyglassMixin, dj.Computed):
    """Analysis table."""

    definition = """
    -> MyAnalysisSelection
    """

    class MyPart(SpyglassMixin, dj.Part):
        """Part table."""

        definition = """
        -> MyAnalysis
        ---
        result: int
        """

    def make(self, key):
        result = 0
        raise_error = False

        this_subj = SubjBlinded().return_subj(key["subject_id"])
        if "3" in this_subj["subject_id"]:
            raise_error = True
        param_key = {"param_name": key["param_name"]}
        these_param = (MyParams & param_key).fetch1("params")

        for pos_obj in RawPosition.PosObject * (Subject & this_subj):
            dataframe = RawPosition.PosObject & pos_obj  # .fetch1_dataframe()
            result += len(dataframe)
            _ = process_df(dataframe, raise_error=raise_error, **these_param)

        for value in these_param.values():
            result += value

        part_inserts = []
        for _ in range(10):
            result += self._get_random_result()
            part_inserts.append(dict(key, result=result))

        self.insert1(key)
        self.MyPart().insert(rows=part_inserts, skip_duplicates=True)

    def _get_random_result(self):
        """Return a random result."""
        return random.randint(0, 100)
