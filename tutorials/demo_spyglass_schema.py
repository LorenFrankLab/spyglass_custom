import datajoint as dj
import numpy as np
import pandas as pd

from spyglass.common import IntervalList
from spyglass.common.custom_nwbfile import AnalysisNwbfile
from spyglass.utils.mixin import SpyglassMixin

user_prefix = None  # Put your custom prefix here, e.g., "myteam" or "username"
analysis_postfix = None  # Put your analysis name here, e.g., "my_analysis"

if not user_prefix or not analysis_postfix:
    raise ValueError(
        "Cannot create custom analysis tables: No prefix or analysis name configured.\n"
        "Custom tables require a database prefix and an analysis name to isolate your files.\n"
        "Set user_prefix and analysis_postfix variables in this file accordingly.\n\n"
    )

schema = dj.schema(f"{user_prefix}_{analysis_postfix}")


@schema
class MyAnalysisParams(SpyglassMixin, dj.Manual):
    # Include all parameters from `CELL 2` as secondary keys here
    definition = """
    my_analysis_params_name: varchar(64)
    ---
    param1: float
    param2: varchar(16)
    param_dict: longblob
    """


@schema
class MyAnalysisSelection(SpyglassMixin, dj.Manual):
    # Include all tables used for data fetching in `CELL 1` as primary keys here
    definition = """
    -> DataSourceTable1     #Location you fetch data from
    -> DataSourceTable2     #Location you fetch data from
    # .....                 #more source tables if needed
    -> MyParamsTable
    -> IntervalList.proj(analysis_interval_name="interval_list_name") #(Optional) If you want to restrict analysis to a specific interval
    """


@schema
class MyAnalysis(SpyglassMixin, dj.Computed):
    # Include:
    # - A seondary key for all SMALL results (e.g., metrics, scalars, etc.)
    #   that you want to store in the table
    # - A foreign key to the AnalysisNwbfile table for all BIG results
    #   (e.g, large arrays, dataframes, etc.) that you want to store in the NWB file
    # - A foreign key for each BIG result to store the object ID of the result in the
    #   NWB file (must end in '_object_id')
    definition = """
    -> MyAnalysisSelection
    ---
    result_metric: float
    -> AnalysisNwbfile #File where you store results
    result1_object_id: varchar(40)  #Object ID of the result object stored in NWB file (must end in '_object_id')
    result2_object_id: varchar(40)  #Object ID of the result object stored in NWB file (must end in '_object_id')
    """

    def make_fetch(self, key):
        # Put your code from `CELL 1` here to fetch data from the source tables based on the selection

        # Fetch data from the source tables based on the selection
        data1 = (DataSourceTable1 & key).fetch1_dataframe()
        data2 = (DataSourceTable2 & key).fetch1_dataframe()

        params = (MyAnalysisParams & key).fetch1()

        interval_key = {
            "nwb_file_name": key["nwb_file_name"],
            "interval_list_name": key["analysis_interval_name"],
        }
        interval = (IntervalList & interval_key).fetch_interval()

        return [data1, data2, params, interval]

    def make_compute(self, key, data1, data2, params, interval):
        ### pull parameters from `CELL 2` here
        param1 = params["param1"]
        param2 = params["param2"]
        ###

        ### Put your code from  `CELL 4` here to perform
        ### the analysis using the fetched data and parameters
        result1, result2, result_metric = perform_analysis(
            data1, data2, param1, param2, interval
        )
        ###

        ###Store all BIG Items listed in `CELL 5` in the NWB file
        # Build an analysis file with the results
        with AnalysisNwbfile().build(key["nwb_file_name"]) as builder:
            analysis_file_name = builder.analysis_file_name
            result1_object_id = builder.add_nwb_object(result1, name="result1")
            result2_object_id = builder.add_nwb_object(result2, name="result2")
        ###

        return [analysis_file_name, result1_object_id, result2_object_id, result_metric]

    def make_insert(
        self,
        key,
        analysis_file_name,
        result1_object_id,
        result2_object_id,
        result_metric,
    ):
        # Insert the results into the MyAnalysis table
        self.insert1(
            {
                **key,
                "analysis_file_name": analysis_file_name,
                "result1_object_id": result1_object_id,
                "result2_object_id": result2_object_id,
                "result_metric": result_metric,
            }
        )


# Helper functions from `CELL 3` here
def perform_analysis(data1, data2, param1, param2, interval):
    # Placeholder for the actual analysis logic
    # This function should implement the analysis using data1, data2, params, and interval
    # For demonstration purposes, we will just return dummy results
    result1 = pd.DataFrame(
        {
            "x": np.random.rand(len(data1)) * param1,
            "y": np.random.rand(len(data1)) * param2,
        }
    )
    result2 = np.random.rand(len(data2)) * (np.max(interval) - np.min(interval))
    result_metric = np.mean(result2)  # Example metric calculation

    return result1, result2, result_metric
