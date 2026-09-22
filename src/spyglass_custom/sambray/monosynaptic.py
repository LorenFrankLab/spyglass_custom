from spyglass.common.custom_nwbfile import AnalysisNwbfile
from spyglass.utils.dj_mixin import SpyglassMixin

import numpy as np
import pandas as pd
import datajoint as dj
import matplotlib.pyplot as plt

from cross_correlogram import CrossCorrelogram, ShuffleCrossCorrelogram

schema = dj.schema("sambray_monosynaptic")


@schema
class MonosynapticParams(SpyglassMixin, dj.Manual):
    definition = """
    # Parameters for monosynaptic analysis
    monosynaptic_params_name: varchar(24)  # Name of the monosynaptic parameters
    ---
    window_start: float  # Start of the window for monosynaptic analysis (s)
    window_end: float  # End of the window for monosynaptic analysis (s)
    """


@schema
class MonosynapticSelection(SpyglassMixin, dj.Manual):
    definition = """
    # Selection of monosynaptic analysis
    -> CrossCorrelogram
    -> ShuffleCrossCorrelogram
    -> MonosynapticParams
    ---
    """


@schema
class Monosynaptic(SpyglassMixin, dj.Computed):
    definition = """
    # Monosynaptic analysis results
    -> MonosynapticSelection
    ---
    -> AnalysisNwbfile
    monosynaptic_object_id: varchar(128)
    """

    def make(self, key):
        window_start, window_end = (MonosynapticParams() & key).fetch1(
            "window_start", "window_end"
        )

        # Fetch the cross-correlogram and shuffle cross-correlogram data
        shuff_df = (ShuffleCrossCorrelogram() & key).fetch1_dataframe()
        cross_df = (CrossCorrelogram() & key).fetch1_dataframe()
        test_df = cross_df.merge(
            shuff_df, on=["unit_id_1", "unit_id_2"], suffixes=("", "_shuff")
        )
        test_df = test_df.query("unit_id_1 != unit_id_2")
        bins = test_df["bins"].iloc[0]

        # Perform monosynaptic analysis (this is a placeholder for actual analysis)
        ind_test = (bins > window_start) & (bins < window_end)
        corr_test = np.array(
            [np.mean(corr[ind_test]) for corr in test_df["correlogram"]]
        )
        lb_test = np.array(
            [np.mean(lb[ind_test]) for lb in test_df["lower_bound"]]
        )
        ub_test = np.array(
            [np.mean(ub[ind_test]) for ub in test_df["upper_bound"]]
        )
        ind_excitatory = np.where(corr_test > ub_test)[0]
        ind_inhibitory = np.where(corr_test < lb_test)[0]

        results = []
        for i, ind in enumerate(ind_excitatory):
            result_i = {
                "unit_id_1": test_df["unit_id_1"].iloc[ind],
                "unit_id_2": test_df["unit_id_2"].iloc[ind],
                "interaction": "excitatory",
            }
            results.append(result_i)
        for i, ind in enumerate(ind_inhibitory):
            result_i = {
                "unit_id_1": test_df["unit_id_1"].iloc[ind],
                "unit_id_2": test_df["unit_id_2"].iloc[ind],
                "interaction": "inhibitory",
            }
            results.append(result_i)
        results = pd.DataFrame(results)

        # Make the analysis NWB file
        nwb_file_name = key["nwb_file_name"]
        analysis_table = self.analysis_table
        analysis_file_name = analysis_table.create(nwb_file_name)
        key["analysis_file_name"] = analysis_file_name
        key["monosynaptic_object_id"] = analysis_table.add_nwb_object(
            analysis_file_name, results, "monosynaptic"
        )
        analysis_table.add(nwb_file_name, analysis_file_name)
        self.insert1(key)

    def fetch1_dataframe(self):
        """Fetch the monosynaptic analysis data as a pandas DataFrame."""
        if not len(self) == 1:
            raise ValueError(
                "Must restrict to a single entry to fetch as DataFrame."
            )
        return self.fetch_nwb()[0]["monosynaptic"]

    def get_neuron_types(self):
        """Get the neuron types (excitatory/inhibitory) for the monosynaptic analysis."""
        df = self.fetch1_dataframe()
        inhibitory_cells = df[
            df["interaction"] == "inhibitory"
        ].unit_id_2.unique()
        excitatory_cells = df[
            df["interaction"] == "excitatory"
        ].unit_id_2.unique()
        return {"inhibitory": inhibitory_cells, "excitatory": excitatory_cells}

    def fetch_correlogram_df(self):
        """Fetch the cross-correlogram data for the monosynaptic analysis."""
        if not len(self) == 1:
            raise ValueError(
                "Must restrict to a single entry to fetch correlogram."
            )
        key = self.fetch1("KEY")
        cross_df = (CrossCorrelogram() & key).fetch1_dataframe()
        shuff_df = (ShuffleCrossCorrelogram() & key).fetch1_dataframe()
        shuff_df = shuff_df[
            ["unit_id_1", "unit_id_2", "lower_bound", "upper_bound"]
        ]
        cross_df = cross_df.merge(
            shuff_df, on=["unit_id_1", "unit_id_2"], suffixes=("", "_shuff")
        )
        return cross_df

    def validate_shank_source(self, key=True):
        """Validate that the monosynaptic analysis is consistent with the shank source."""

        df = (self & key).fetch1_dataframe()
        tick_labels = []
        width = 0.28
        fig = plt.figure()
        for i, type_ in enumerate(df.interaction.unique()):
            print(f"{type_}: {sum(df.interaction == type_)}")
            _df = df[df.interaction == type_]

            same_shank = 0
            shank_1 = [s.split("_")[0] for s in _df.unit_id_1]
            shank_2 = [s.split("_")[0] for s in _df.unit_id_2]
            same_shank = sum([s1 == s2 for s1, s2 in zip(shank_1, shank_2)])
            print(
                f"Number of monosynaptic pairs on the same shank: {same_shank} / {len(_df)}"
            )
            plt.bar(
                i,
                same_shank,
                color="dodgerblue",
                label="Same shank" if i == 0 else None,
                width=width,
            )
            plt.bar(
                i + 0.3,
                len(_df),
                color="gray",
                label="all" if i == 0 else None,
                width=width,
            )

            tick_labels.append(f"{type_} ({100*same_shank / len(_df):.1f}%)")

        plt.legend()
        plt.xticks(
            [i + width / 2 for i in range(len(tick_labels))], tick_labels
        )

        plt.ylabel("Number of monosynaptic pairs")
        ax = plt.gca()
        ax.spines[["top", "right"]].set_visible(False)
        return fig
