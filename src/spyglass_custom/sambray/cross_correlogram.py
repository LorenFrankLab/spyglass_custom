import itertools
from multiprocessing import Pool
from typing import Any
import numpy as np
from numba import njit

import datajoint as dj
import numpy as np
import pandas as pd
import scipy.signal
from spyglass.common import IntervalList
from spyglass.common.common_interval import Interval
from spyglass.common.custom_nwbfile import AnalysisNwbfile
from spyglass.spikesorting.analysis.v1.group import SortedSpikesGroup
from spyglass.utils.dj_mixin import SpyglassMixin
from spyglass.utils.nwb_helper_fn import close_nwb_files
from tqdm import tqdm
import multiprocessing as mp

schema = dj.schema("sambray_cross_correlations")

_WORKER_SPIKES: list[np.ndarray] | None = None
_WORKER_UNIT_IDS: list[str] | None = None
_WORKER_VALID_INTERVAL: np.ndarray | None = None
_WORKER_HISTOGRAM_BINS: np.ndarray | None = None
_WORKER_GAUSS_SMOOTH: float | None = None


@schema
class CrossCorrelogramParameters(SpyglassMixin, dj.Manual):
    definition = """
    cross_corr_params_name: varchar(128)
    ---
    gauss_smooth = 0.15: float # in seconds, if 0 then no smoothing
    max_lag = .5: float # in seconds, the maximum lag to compute the cross correlogram for
    bin_size = 0.0005: float # in seconds, the size of the bins for the histogram
    """

    def insert_default(self):
        self.insert1(
            {
                "cross_corr_params_name": "default",
                "gauss_smooth": 0.015,
                "max_lag": 0.5,
            },
            skip_duplicates=True,
        )


@schema
class CrossCorrelogramSelection(SpyglassMixin, dj.Manual):
    definition = """
    -> SortedSpikesGroup
    -> IntervalList
    -> CrossCorrelogramParameters
    """


@schema
class CrossCorrelogram(SpyglassMixin, dj.Computed):
    definition = """
    -> CrossCorrelogramSelection
    ---
    -> AnalysisNwbfile
    corr_object_id: varchar(128)
    """
    _n_jobs = 64

    @property
    def _parallel_make(self):
        return self._n_jobs > 1

    @staticmethod
    def parse_unit_ids(unit_ids):
        """parse unit_ids from from dict to unique string for hashability"""
        if type(unit_ids[0]) is dict:
            unit_ids = [
                f"{x['spikesorting_merge_id']}_{x['unit_id']}" for x in unit_ids
            ]
        return unit_ids

    # --------- Revision update for ripple cross corr ---------
    def make(self, key):
        # get the parameters
        gauss_smooth = (CrossCorrelogramParameters & key).fetch1("gauss_smooth")
        max_lag = (CrossCorrelogramParameters & key).fetch1("max_lag")
        bin_size = (CrossCorrelogramParameters & key).fetch1("bin_size")

        # get spike data
        spikes_list, unit_ids = (SortedSpikesGroup & key).fetch_spike_data(
            key, return_unit_ids=True
        )
        unit_ids = self.parse_unit_ids(unit_ids)

        # define what intervals to use
        valid_interval = np.array((IntervalList & key).fetch1("valid_times"))
        valid_interval_obj = Interval(valid_interval)
        spikes_list = [valid_interval_obj.contains(s) for s in spikes_list]

        results = []
        histogram_bins = np.arange(-max_lag, max_lag, bin_size)
        # loop through unit pairs
        if self._n_jobs == 1:
            for n_s1, (spikes, unit_id_1) in tqdm(
                enumerate(zip(spikes_list, unit_ids))
            ):
                for n_s2, (spikes_2, unit_id_2) in enumerate(
                    zip(spikes_list, unit_ids)
                ):
                    result_ij = compute_cross_correlation(
                        spikes,
                        spikes_2,
                        valid_interval,
                        histogram_bins,
                        histogram_bins,
                        unit_id_1,
                        unit_id_2,
                        gauss_smooth,
                    )
                    results.append(result_ij)
        else:
            close_nwb_files()  # Close any open NWB files before starting multiprocessing
            histogram_pairwise_delays(
                spikes_1=np.array([0.0], dtype=np.float64),
                spikes_2=np.array([0.0], dtype=np.float64),
                bin_edges=np.array([-1.0, 0.0, 1.0], dtype=np.float64),
            )  # Warm up Numba JIT compilation in the main process to avoid overhead in workers
            n_units = len(spikes_list)
            n_pairs = n_units**2
            n_jobs = self._n_jobs

            chunksize = min(
                4,
                max(1, n_pairs // (8 * n_jobs)),
            )
            chunksize = 1

            pair_iterator = itertools.product(range(n_units), repeat=2)
            context = mp.get_context()

            with context.Pool(
                processes=n_jobs,
                initializer=initialize_cross_correlation_worker,
                initargs=(
                    spikes_list,
                    unit_ids,
                    valid_interval,
                    histogram_bins,
                    gauss_smooth,
                ),
            ) as pool:
                result_iterator = pool.imap_unordered(
                    compute_pair_from_indices,
                    pair_iterator,
                    chunksize=chunksize,
                )

                indexed_results = list(
                    tqdm(
                        result_iterator,
                        total=n_pairs,
                        desc="Computing cross-correlograms",
                    )
                )

            indexed_results.sort(key=lambda item: (item[0], item[1]))
            result_rows = [item[2] for item in indexed_results]

            bin_centers = 0.5 * (histogram_bins[:-1] + histogram_bins[1:])

            results = pd.DataFrame(result_rows)
            results["bins"] = [bin_centers] * len(results)

        results = pd.DataFrame(results)
        nwb_file_name = key["nwb_file_name"]
        analysis_table = self.analysis_table
        analysis_file_name = analysis_table.create(nwb_file_name)
        key["analysis_file_name"] = analysis_file_name
        key["corr_object_id"] = analysis_table.add_nwb_object(
            analysis_file_name, results, "cross_correlogram"
        )
        analysis_table.add(nwb_file_name, analysis_file_name)
        self.insert1(key)

    def fetch1_dataframe(self):
        """Fetch the cross-correlogram data as a pandas DataFrame."""
        if not len(self) == 1:
            raise ValueError(
                "Must restrict to a single entry to fetch as DataFrame."
            )
        return self.fetch_nwb()[0]["corr"]


@schema
class ShuffleCrossCorrelogramParameters(SpyglassMixin, dj.Manual):
    definition = """
    shuffle_params_name: varchar(128)
    ---
    n_shuffles: int # number of shuffles to perform
    shuffle_window: float # in seconds, the window to shuffle spikes within
    """


@schema
class ShuffleCrossCorrelogramSelection(SpyglassMixin, dj.Manual):
    definition = """
    -> SortedSpikesGroup
    -> IntervalList
    -> CrossCorrelogramParameters
    -> ShuffleCrossCorrelogramParameters
    """


@schema
class ShuffleCrossCorrelogram(SpyglassMixin, dj.Computed):
    definition = """
    -> ShuffleCrossCorrelogramSelection
    ---
    -> AnalysisNwbfile
    shuffle_corr_object_id: varchar(128)
    """
    _n_jobs = 64

    def make(self, key):
        # get the parameters
        n_shuffles = (ShuffleCrossCorrelogramParameters & key).fetch1(
            "n_shuffles"
        )
        shuffle_window = (ShuffleCrossCorrelogramParameters & key).fetch1(
            "shuffle_window"
        )
        gauss_smooth = (CrossCorrelogramParameters & key).fetch1("gauss_smooth")
        max_lag = (CrossCorrelogramParameters & key).fetch1("max_lag")
        bin_size = (CrossCorrelogramParameters & key).fetch1("bin_size")
        histogram_bins = np.arange(-max_lag, max_lag, bin_size)

        # define what intervals to use
        valid_interval = np.array((IntervalList & key).fetch1("valid_times"))
        valid_interval_obj = Interval(valid_interval)

        # get spike data
        spikes_list, unit_ids = (SortedSpikesGroup & key).fetch_spike_data(
            key, return_unit_ids=True
        )
        unit_ids = CrossCorrelogram.parse_unit_ids(unit_ids)
        spikes_list = [valid_interval_obj.contains(s) for s in spikes_list]

        # Perform shuffling and compute cross-correlograms for each shuffle
        results = []
        # loop through unit pairs
        if self._n_jobs == 1:
            for n_s1, (spikes, unit_id_1) in tqdm(
                enumerate(zip(spikes_list, unit_ids))
            ):
                for n_s2, (spikes_2, unit_id_2) in enumerate(
                    zip(spikes_list, unit_ids)
                ):
                    result_ij = compute_shuffled_cross_correlation(
                        spikes,
                        spikes_2,
                        valid_interval,
                        histogram_bins,
                        histogram_bins,
                        unit_id_1,
                        unit_id_2,
                        gauss_smooth,
                        n_shuffles,
                        shuffle_window,
                    )
                    results.append(result_ij)
        else:
            # Close any open NWB files before starting multiprocessing
            close_nwb_files()
            # Warm up Numba JIT compilation in the main process to avoid overhead in workers
            histogram_pairwise_delays(
                spikes_1=np.array([0.0], dtype=np.float64),
                spikes_2=np.array([0.0], dtype=np.float64),
                bin_edges=np.array([-1.0, 0.0, 1.0], dtype=np.float64),
            )
            numba_compute_shuffled_cross_correlation(
                spikes_1=np.array([0.0], dtype=np.float64),
                spikes_2=np.array([0.0], dtype=np.float64),
                valid_interval=valid_interval,
                histogram_bins=histogram_bins,
                unit_id_1="a",
                unit_id_2="b",
                gauss_smooth=gauss_smooth,
                n_shuffles=n_shuffles,
                shuffle_window=shuffle_window,
            )

            n_units = len(spikes_list)
            n_pairs = n_units**2
            n_jobs = self._n_jobs
            chunksize = min(
                4,
                max(1, n_pairs // (8 * n_jobs)),
            )
            chunksize = 1

            pair_iterator = itertools.product(range(n_units), repeat=2)
            pair_iterator = (
                (i, j, n_shuffles, shuffle_window) for i, j in pair_iterator
            )

            context = mp.get_context()

            with context.Pool(
                processes=n_jobs,
                initializer=initialize_cross_correlation_worker,
                initargs=(
                    spikes_list,
                    unit_ids,
                    valid_interval,
                    histogram_bins,
                    gauss_smooth,
                ),
            ) as pool:
                result_iterator = pool.imap_unordered(
                    compute_shuffled_pair_from_indices,
                    pair_iterator,
                    chunksize=chunksize,
                )

                indexed_results = list(
                    tqdm(
                        result_iterator,
                        total=n_pairs,
                        desc="Computing cross-correlograms",
                    )
                )

            indexed_results.sort(key=lambda item: (item[0], item[1]))
            result_rows = [item[2] for item in indexed_results]

            bin_centers = 0.5 * (histogram_bins[:-1] + histogram_bins[1:])

            results = pd.DataFrame(result_rows)
            results["bins"] = [bin_centers] * len(results)

        results = pd.DataFrame(results)
        nwb_file_name = key["nwb_file_name"]
        analysis_table = self.analysis_table
        analysis_file_name = analysis_table.create(nwb_file_name)
        key["analysis_file_name"] = analysis_file_name
        key["shuffle_corr_object_id"] = analysis_table.add_nwb_object(
            analysis_file_name, results, "cross_correlogram"
        )
        analysis_table.add(nwb_file_name, analysis_file_name)
        self.insert1(key)

    def fetch1_dataframe(self):
        """Fetch the cross-correlogram data as a pandas DataFrame."""
        if not len(self) == 1:
            raise ValueError(
                "Must restrict to a single entry to fetch as DataFrame."
            )
        return self.fetch_nwb()[0]["shuffle_corr"]


def initialize_cross_correlation_worker(
    spikes_list: list[np.ndarray],
    unit_ids: list[str],
    valid_interval: np.ndarray,
    histogram_bins: np.ndarray,
    gauss_smooth: float,
) -> None:
    """Initialize read-only data used by each worker.

    Parameters
    ----------
    spikes_list : list of numpy.ndarray
        Spike times for each unit. Each element has shape ``(n_spikes,)``.
    unit_ids : list of str
        Unit identifiers with shape conceptually ``(n_units,)``.
    valid_interval : numpy.ndarray
        Valid time intervals with shape ``(n_intervals, 2)``.
    histogram_bins : numpy.ndarray
        Cross-correlogram bin edges with shape ``(n_bins + 1,)``.
    gauss_smooth : float
        Gaussian smoothing width in seconds.
    """
    global _WORKER_SPIKES
    global _WORKER_UNIT_IDS
    global _WORKER_VALID_INTERVAL
    global _WORKER_HISTOGRAM_BINS
    global _WORKER_GAUSS_SMOOTH

    _WORKER_SPIKES = spikes_list
    _WORKER_UNIT_IDS = unit_ids
    _WORKER_VALID_INTERVAL = valid_interval
    _WORKER_HISTOGRAM_BINS = histogram_bins
    _WORKER_GAUSS_SMOOTH = gauss_smooth


def compute_pair_from_indices(
    pair: tuple[int, int],
) -> tuple[int, int, dict[str, Any]]:
    """Compute one cross-correlogram from unit indices.

    Parameters
    ----------
    pair : tuple of int
        Indices ``(unit_index_1, unit_index_2)``.

    Returns
    -------
    tuple
        Unit indices and cross-correlation result.
    """
    if (
        _WORKER_SPIKES is None
        or _WORKER_UNIT_IDS is None
        or _WORKER_VALID_INTERVAL is None
        or _WORKER_HISTOGRAM_BINS is None
        or _WORKER_GAUSS_SMOOTH is None
    ):
        raise RuntimeError("Cross-correlation worker was not initialized.")

    unit_index_1, unit_index_2 = pair

    result = compute_cross_correlation_dict(
        spikes_1=_WORKER_SPIKES[unit_index_1],
        spikes_2=_WORKER_SPIKES[unit_index_2],
        valid_interval=_WORKER_VALID_INTERVAL,
        histogram_bins=_WORKER_HISTOGRAM_BINS,
        unit_id_1=_WORKER_UNIT_IDS[unit_index_1],
        unit_id_2=_WORKER_UNIT_IDS[unit_index_2],
        gauss_smooth=_WORKER_GAUSS_SMOOTH,
    )

    # This value is identical for every pair. Add it once in the parent.
    result.pop("bins", None)

    return unit_index_1, unit_index_2, result


@njit(cache=True, nogil=True)
def compute_cross_correlation(
    spikes_1,
    spikes_2,
    valid_interval,
    histogram_bins,
    unit_id_1,
    unit_id_2,
    gauss_smooth,
):
    max_lag = np.max(np.abs(histogram_bins))
    bin_centers = histogram_bins[:-1] + np.diff(histogram_bins) / 2

    s1_count = 0
    s2_count = 0

    bin_edges = np.asarray(histogram_bins)
    bin_centers = 0.5 * (bin_edges[:-1] + bin_edges[1:])
    bin_width = np.diff(bin_edges).mean()
    max_lag = np.max(np.abs(bin_edges))

    hist_counts = np.zeros(len(bin_edges) - 1, dtype=np.int64)
    valid_bin_count = np.zeros_like(hist_counts, dtype=np.int64)

    for interval in valid_interval:

        start = interval[0]
        stop = interval[1]
        i1_lo = np.searchsorted(spikes_1, start, side="left")
        i1_hi = np.searchsorted(spikes_1[i1_lo:], stop, side="right") + i1_lo
        i2_lo = np.searchsorted(spikes_2, start, side="left")
        i2_hi = np.searchsorted(spikes_2[i2_lo:], stop, side="right") + i2_lo

        s1 = spikes_1[i1_lo:i1_hi]
        s2 = spikes_2[i2_lo:i2_hi]

        s1_count += s1.size
        s2_count += s2.size
        if s1.size == 0 or s2.size == 0:
            continue

        hist_counts += histogram_pairwise_delays(
            spikes_1=s1,
            spikes_2=s2,
            bin_edges=bin_edges,
        )
        left = np.searchsorted(s1, start - bin_centers, side="left")
        right = np.searchsorted(s1, stop - bin_centers, side="right")
        valid_bin_count += right - left

    vals = hist_counts.astype(np.float64)
    if gauss_smooth:
        sigma = int(
            gauss_smooth / np.mean(np.diff(histogram_bins))
        )  # turn gauss_smooth from seconds to bins
        # print(sigma)
        vals = smooth(vals, 3 * sigma, sigma)

    denom = bin_width * np.maximum(valid_bin_count, 1)
    vals = vals / denom
    return unit_id_1, unit_id_2, vals, bin_centers, s1_count, s2_count


def compute_cross_correlation_dict(
    spikes_1,
    spikes_2,
    valid_interval,
    histogram_bins,
    unit_id_1,
    unit_id_2,
    gauss_smooth,
):
    unit_id_1, unit_id_2, vals, bin_centers, s1_count, s2_count = (
        compute_cross_correlation(
            spikes_1,
            spikes_2,
            valid_interval,
            histogram_bins,
            unit_id_1,
            unit_id_2,
            gauss_smooth,
        )
    )

    return {
        "unit_id_1": unit_id_1,
        "unit_id_2": unit_id_2,
        "correlogram": vals,
        "bins": bin_centers,
        "counts_1": s1_count,
        "counts_2": s2_count,
    }


# -------------------------------------------------------------------------
# Shuffled


@njit(cache=True, nogil=True)
def numba_compute_shuffled_cross_correlation(
    spikes_1,
    spikes_2,
    valid_interval,
    histogram_bins,
    unit_id_1,
    unit_id_2,
    gauss_smooth,
    n_shuffles,
    shuffle_window,
):
    corr_compiled = np.empty(
        (n_shuffles, len(histogram_bins) - 1), dtype=np.float64
    )
    for shuffle_idx in range(n_shuffles):
        s1_shuffled = spikes_1 + np.random.uniform(
            -shuffle_window, shuffle_window, size=spikes_1.shape
        )
        s2_shuffled = spikes_2 + np.random.uniform(
            -shuffle_window, shuffle_window, size=spikes_2.shape
        )
        r = compute_cross_correlation(
            spikes_1=s1_shuffled,
            spikes_2=s2_shuffled,
            valid_interval=valid_interval,
            histogram_bins=histogram_bins,
            unit_id_1=unit_id_1,
            unit_id_2=unit_id_2,
            gauss_smooth=gauss_smooth,
        )
        corr_compiled[shuffle_idx] = r[2]  # Append the correlogram values
        # corr_compiled.append(r["correlogram"])
    lo = np.empty(len(histogram_bins) - 1, dtype=np.float64)
    hi = np.empty(len(histogram_bins) - 1, dtype=np.float64)
    for bin_idx in range(len(histogram_bins) - 1):
        lo[bin_idx] = np.min(corr_compiled[:, bin_idx])
        hi[bin_idx] = np.max(corr_compiled[:, bin_idx])
    return lo, hi


def compute_shuffled_cross_correlation(
    spikes_1: np.ndarray,
    spikes_2: np.ndarray,
    valid_interval: np.ndarray,
    histogram_bins: np.ndarray,
    unit_id_1: str,
    unit_id_2: str,
    gauss_smooth: float,
    n_shuffles: int,
    shuffle_window: float,
) -> dict[str, Any]:
    """Compute shuffled cross-correlograms for one unit pair.

    Parameters
    ----------
    spikes_1 : numpy.ndarray
        First spike train with shape ``(n_spikes_1,)``.
    spikes_2 : numpy.ndarray
        Second spike train with shape ``(n_spikes_2,)``.
    valid_interval : numpy.ndarray
        Valid intervals with shape ``(n_intervals, 2)``.
    histogram_bins : numpy.ndarray
        Histogram edges with shape ``(n_bins + 1,)``.
    unit_id_1 : str
        First unit identifier.
    unit_id_2 : str
        Second unit identifier.
    gauss_smooth : float
        Gaussian smoothing width in seconds.
    n_shuffles : int
        Number of shuffled realizations.
    shuffle_window : float
        Maximum absolute jitter in seconds.

    Returns
    -------
    dict
        Unit metadata and shuffled correlograms. The ``correlogram`` field
        has shape ``(n_shuffles, n_bins)``.
    """
    lo, hi = numba_compute_shuffled_cross_correlation(
        spikes_1=spikes_1,
        spikes_2=spikes_2,
        valid_interval=valid_interval,
        histogram_bins=histogram_bins,
        unit_id_1=unit_id_1,
        unit_id_2=unit_id_2,
        gauss_smooth=gauss_smooth,
        n_shuffles=n_shuffles,
        shuffle_window=shuffle_window,
    )
    default_result = compute_cross_correlation_dict(
        spikes_1=spikes_1,
        spikes_2=spikes_2,
        valid_interval=valid_interval,
        histogram_bins=histogram_bins,
        unit_id_1=unit_id_1,
        unit_id_2=unit_id_2,
        gauss_smooth=gauss_smooth,
    )
    default_result.pop("correlogram")
    default_result["lower_bound"] = lo
    default_result["upper_bound"] = hi
    return default_result


def compute_shuffled_pair_from_indices(
    task: tuple[int, int, int, float],
) -> tuple[int, int, dict[str, Any]]:
    """Compute one shuffled cross-correlogram from unit indices.

    Parameters
    ----------
    task : tuple of int, int, int, float
        Tuple containing ``(unit_index_1, unit_index_2, n_shuffles,
        shuffle_window)``.

    Returns
    -------
    unit_index_1 : int
        Index of the first unit.
    unit_index_2 : int
        Index of the second unit.
    result : dict
        Shuffled cross-correlation result.
    """
    if (
        _WORKER_SPIKES is None
        or _WORKER_UNIT_IDS is None
        or _WORKER_VALID_INTERVAL is None
        or _WORKER_HISTOGRAM_BINS is None
        or _WORKER_GAUSS_SMOOTH is None
    ):
        raise RuntimeError("Cross-correlation worker was not initialized.")
    (
        unit_index_1,
        unit_index_2,
        n_shuffles,
        shuffle_window,
    ) = task

    result = compute_shuffled_cross_correlation(
        spikes_1=_WORKER_SPIKES[unit_index_1],
        spikes_2=_WORKER_SPIKES[unit_index_2],
        valid_interval=_WORKER_VALID_INTERVAL,
        histogram_bins=_WORKER_HISTOGRAM_BINS,
        unit_id_1=_WORKER_UNIT_IDS[unit_index_1],
        unit_id_2=_WORKER_UNIT_IDS[unit_index_2],
        gauss_smooth=_WORKER_GAUSS_SMOOTH,
        n_shuffles=n_shuffles,
        shuffle_window=shuffle_window,
    )
    result.pop("bins", None)

    return unit_index_1, unit_index_2, result


# ----------------------------------------------------------------------------
# utils


@njit(cache=True, nogil=True)
def gkern(length: int = 5, sig: float = 1.0):
    """
    creates gaussian kernel with side length `length` and a sigma of `sig`
    """
    ax = np.linspace(-(length - 1) / 2.0, (length - 1) / 2.0, length)
    gauss = np.exp(-0.5 * np.square(ax) / np.square(sig))
    return gauss / np.sum(gauss)


@njit(cache=True, nogil=True)
def convolve_axis0_edge(
    data: np.ndarray,
    kernel: np.ndarray,
) -> np.ndarray:
    """Convolve two-dimensional data along axis 0 with edge padding.

    This is equivalent to constructing edge-replicated padding and calling
    ``scipy.signal.convolve2d(..., mode="valid")`` with a kernel having shape
    ``(kernel_length, 1)``.

    Parameters
    ----------
    data : numpy.ndarray
        Input data with shape ``(n_rows, n_columns)``.
    kernel : numpy.ndarray
        One-dimensional convolution kernel with shape ``(kernel_length,)``.
        The kernel length must be odd.

    Returns
    -------
    numpy.ndarray
        Convolved data with shape ``(n_rows, n_columns)``.

    Raises
    ------
    ValueError
        Raised when the kernel is empty or has an even length.
    """
    n_rows, n_columns = data.shape
    kernel_length = kernel.size

    if kernel_length == 0:
        raise ValueError("kernel must not be empty.")

    if kernel_length % 2 == 0:
        raise ValueError("kernel length must be odd.")

    radius = kernel_length // 2
    output = np.empty(
        (n_rows, n_columns),
        dtype=np.float64,
    )

    for row_index in range(n_rows):
        for column_index in range(n_columns):
            value = 0.0

            for kernel_index in range(kernel_length):
                source_row = row_index + kernel_index - radius

                # Reproduce the edge values used by your explicit padding.
                if source_row < 0:
                    source_row = 0
                elif source_row >= n_rows:
                    source_row = n_rows - 1

                # Convolution reverses the kernel. This matches SciPy's
                # convolve2d semantics. For a symmetric Gaussian kernel,
                # reversing it has no numerical effect.
                reversed_kernel_index = kernel_length - 1 - kernel_index

                value += (
                    data[source_row, column_index]
                    * kernel[reversed_kernel_index]
                )

            output[row_index, column_index] = value

    return output


@njit(cache=True, nogil=True)
def smooth(
    data: np.ndarray,
    n: int = 5,
    sigma: float = -1.0,
    hamming: bool = False,
) -> np.ndarray:
    """Smooth two-dimensional data along axis 0.

    Parameters
    ----------
    data : numpy.ndarray
        Input data with shape ``(n_rows, n_columns)``.
    n : int, default=5
        Kernel length.
    sigma : float, default=-1.0
        Gaussian standard deviation. A nonpositive value uses ``n / 2``.
    hamming : bool, default=False
        When true, use a uniform moving-average kernel instead of a Gaussian
        kernel.

    Returns
    -------
    numpy.ndarray
        Smoothed data with shape ``(n_rows, n_columns)``.
    """
    if n % 2 == 0:
        n += 1

    if sigma <= 0.0:
        sigma = n / 2.0

    if hamming:
        kernel = np.full(
            n,
            1.0 / n,
            dtype=np.float64,
        )
    else:
        kernel = gkern_numba(
            length=n,
            sigma=sigma,
        )
    # if data.ndim == 1:
    #     data = data[:, None]
    data = data[:, None]

    return convolve_axis0_edge(
        data=data,
        kernel=kernel,
    )[:, 0]


@njit(cache=True, nogil=True)
def gkern_numba(
    length: int = 5,
    sigma: float = 1.0,
) -> np.ndarray:
    """Create a normalized one-dimensional Gaussian kernel.

    Parameters
    ----------
    length : int, default=5
        Number of kernel samples. Must be positive.
    sigma : float, default=1.0
        Gaussian standard deviation in samples.

    Returns
    -------
    numpy.ndarray
        Normalized Gaussian kernel with shape ``(length,)``.
    """
    if length <= 0:
        raise ValueError("length must be positive.")

    if sigma <= 0.0:
        raise ValueError("sigma must be positive.")

    kernel = np.empty(length, dtype=np.float64)
    center = 0.5 * (length - 1)
    kernel_sum = 0.0

    for index in range(length):
        position = index - center
        value = np.exp(-0.5 * position * position / (sigma * sigma))
        kernel[index] = value
        kernel_sum += value

    for index in range(length):
        kernel[index] /= kernel_sum

    return kernel


@njit(cache=True, nogil=True)
def histogram_pairwise_delays(
    spikes_1: np.ndarray,
    spikes_2: np.ndarray,
    bin_edges: np.ndarray,
) -> np.ndarray:
    """Histogram pairwise spike delays without an outer subtraction.

    This function assumes that both spike arrays are sorted in ascending
    order and that the histogram bins are uniformly spaced.

    Parameters
    ----------
    spikes_1 : numpy.ndarray
        Reference spike times with shape ``(n_spikes_1,)``.
    spikes_2 : numpy.ndarray
        Comparison spike times with shape ``(n_spikes_2,)``.
    bin_edges : numpy.ndarray
        Uniformly spaced histogram edges with shape ``(n_bins + 1,)``.

    Returns
    -------
    numpy.ndarray
        Histogram counts with shape ``(n_bins,)``.
    """
    n_spikes_2 = spikes_2.size
    n_bins = bin_edges.size - 1

    counts = np.zeros(n_bins, dtype=np.int64)

    if spikes_1.size == 0 or n_spikes_2 == 0 or n_bins <= 0:
        return counts

    minimum_delay = bin_edges[0]
    maximum_delay = bin_edges[-1]
    bin_width = bin_edges[1] - bin_edges[0]

    left_index = 0
    right_index = 0

    for spike_1 in spikes_1:
        # delay = spike_1 - spike_2
        # s
        # minimum_delay <= spike_1 - spike_2 <= maximum_delay
        #
        # Therefore:
        #
        # spike_1 - maximum_delay <= spike_2
        # spike_2 <= spike_1 - minimum_delay
        lower_spike_2 = spike_1 - maximum_delay
        upper_spike_2 = spike_1 - minimum_delay

        while left_index < n_spikes_2 and spikes_2[left_index] < lower_spike_2:
            left_index += 1

        if right_index < left_index:
            right_index = left_index

        while (
            right_index < n_spikes_2 and spikes_2[right_index] <= upper_spike_2
        ):
            right_index += 1

        for spike_2_index in range(left_index, right_index):
            delay = spike_1 - spikes_2[spike_2_index]
            bin_index = int(np.floor((delay - minimum_delay) / bin_width))

            # Match np.histogram behavior: a value exactly equal to the
            # final edge belongs in the final bin.
            if bin_index == n_bins and delay <= maximum_delay:
                bin_index = n_bins - 1

            if 0 <= bin_index < n_bins:
                counts[bin_index] += 1

    return counts
