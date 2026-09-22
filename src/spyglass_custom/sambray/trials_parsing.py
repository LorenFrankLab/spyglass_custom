from spyglass.common import DIOEvents, Session, IntervalList
from spyglass.common.custom_nwbfile import AnalysisNwbfile
from spyglass.utils.dj_mixin import SpyglassMixin

import pandas as pd
import numpy as np
import datajoint as dj

schema = dj.schema('sambray_trial_parsing')

@schema
class DIOGroup(SpyglassMixin, dj.Manual):
    definition = """
    -> Session
    dio_group_name: varchar(32)
    """
    class DIOGroupMember(SpyglassMixin, dj.Part):
        definition = """
        -> master
        -> DIOEvents
        description: varchar(64)
        """

@schema
class TrialsSelection(SpyglassMixin, dj.Manual):
    definition = """
    -> DIOGroup
    -> IntervalList
    task_name: varchar(32)
    """

@schema
class Trials(SpyglassMixin, dj.Computed):
    definition = """
    -> TrialsSelection
    ---
    -> AnalysisNwbfile
    trials_object_id: varchar(64)  # Object ID of the trials table in the NWB file
    """

    def make_fetch(self, key):
        dio_df = []
        for dio_key in (DIOGroup.DIOGroupMember & key).fetch('KEY'):
            description = dio_key.pop("description")
            query = (DIOEvents & dio_key)
            assert len(query) == 1, f"Expected 1 DIOEvents entry for {description}, found {len(query)}"
            dio_events = query.fetch_nwb()[0]['dio']
            dio_times = dio_events.timestamps[:]
            dio_vals = dio_events.data[:]
            dio_df.extend([{"time": t, "value": v, "description": description} for t, v in zip(dio_times, dio_vals)])
        dio_df = pd.DataFrame(dio_df)
        dio_df = dio_df.sort_values(by="time").reset_index(drop=True)

        diff_t = np.diff(dio_df["time"].values)
        valid_ind = np.where((diff_t >= 0.001) & (diff_t <= 200.0))[0]+1
        dio_df = dio_df.iloc[valid_ind].reset_index(drop=True)

        valid_times = (IntervalList & key).fetch1("valid_times")

        return dio_df, valid_times


    def make_compute(self, key, dio_df, valid_times):
        dio_df = dio_df[(dio_df["time"] >= np.min(valid_times)) & (dio_df["time"] <= np.max(valid_times))].reset_index(drop=True)
        if key["task_name"] not in trial_functions:
            raise ValueError(f"Task '{key['task_name']}' not supported. Available tasks: {list(trial_functions.keys())}")
        trial_df = trial_functions[key["task_name"]](dio_df)
        return (trial_df,)

    def make_insert(self, key, trial_df):
        analysis_table = self.analysis_table
        nwb_file_name = key["nwb_file_name"]
        analysis_file_name = analysis_table.create(nwb_file_name)
        key["analysis_file_name"] = analysis_file_name
        key["trials_object_id"] = analysis_table.add_nwb_object(
            analysis_file_name, trial_df, "trials"
        )
        analysis_table.add(nwb_file_name, analysis_file_name)
        self.insert1(key)


def _compute_wtrack_trials(dio_df):
    """
    Compute trials for W-track task.
    """
    if not all(arm in dio_df['description'].unique() for arm in ['center_arm', 'left_arm', 'right_arm']):
        raise ValueError("DIO events must include 'center_arm', 'left_arm', and 'right_arm' descriptions.")
    dio_df.rename(columns={'description': 'arm'}, inplace=True)

    trial_df = []
    for (i,row),(j,prev_row) in zip(dio_df.iterrows(), dio_df.shift(1).iterrows()):
        if i==0 or i==len(dio_df)-1:
            continue
        if row['arm']==prev_row['arm']:
            continue
        assert prev_row.value==0 and row.value==1, f"Expected transition from 0 to 1, got {prev_row.value} to {row.value}"

        trial = {
            "run_start": prev_row["time"],
            "t_poke": row["time"],
            "origin": prev_row["arm"],
            "destination": row["arm"],
        }
        trial_type = "outbound" if trial["origin"]=="center_arm" else "inbound"
        trial["trial_type"] = trial_type
        trial_df.append(trial)
    return pd.DataFrame(trial_df)

trial_functions = {
    "wtrack": _compute_wtrack_trials,
    # Add other task-specific trial parsing functions here
}


