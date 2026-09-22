"""Multi-camera triangulation stress test against a real Anipose dataset.

Unlike ``stress_test_multicam.py`` (synthetic cam1) and
``stress_test_calib_ingestion.py`` (synthetic stereo over openfield pixels),
this test drives ``triangulate_pose_df`` with a **fully real** 2-camera Anipose
project and validates V2's 3D reconstruction directly against Anipose's own
``pose-3d`` output.

## Dataset
Karashchuk et al. 2021, *Anipose: a toolkit for robust markerless 3D pose
estimation* — the mouse reaching project (``mouse-anipose.zip``, 942 MB).

  DOI:   https://doi.org/10.5061/dryad.nzs7h44s4
  Mirror (public, scriptable): https://zenodo.org/records/5733431

Download + extract one session (no auth token needed via Zenodo)::

    cd /stelmo/cbroz/deeplabcut/anipose_dryad
    curl -L https://zenodo.org/api/records/5733431/files/mouse-anipose.zip/content \
        -o mouse-anipose.zip
    S=mouse-testing/020820_preDTreaches_day1/020820_JiDT13
    unzip -q mouse-anipose.zip "$S/calibration/*" "$S/pose-2d/*" "$S/pose-3d/*" \
        -d extracted/

## Why this is a strong validation

The Anipose project config (``mouse-testing/config.toml``) disables every
post-processing step that would make Anipose's output diverge from a plain
linear triangulation::

    [triangulation] ransac = false   optim = false
    [filter]        enabled = false
    [triangulation] score_threshold = 0.3   reproj_error_threshold = 5

So Anipose used the *same* algorithm V2 uses (linear DLT / SVD over the
per-camera projection matrices).  Its ``pose-3d`` CSVs store coordinates
directly in the calibration frame (the per-frame ``M`` transform is identity
and ``center`` is zero), in millimetres.  V2's ``triangulate_pose_df``, fed the
same calibration and the same top 2D detection per body part, must reproduce
those coordinates to within rounding / SVD conditioning noise.

## Calibration conversion

Anipose's ``calibration.toml`` stores, per camera, the OpenCV intrinsic matrix,
distortion coefficients, and the **world->camera** pose as a Rodrigues
``rotation`` vector + ``translation`` vector.  V2's ``build_projection_matrix``
expects the inverse (**camera->rig**) convention, so we convert::

    R_wc = Rodrigues(rotation)        # world(rig) -> camera
    R    = R_wc.T                     # camera -> rig   (stored extrinsics["R"])
    t    = -R_wc.T @ translation      # camera -> rig   (stored extrinsics["t"])

## What this test exercises

1. Parsing a real Anipose ``calibration.toml`` into V2 calibration dicts.
2. The full ``triangulate_pose_df`` pipeline (undistortion -> DLT -> reprojection
   gate) on real, noisy two-view DLC detections.
3. Numerical equivalence of V2's 3D output to Anipose's reference 3D, per body
   part and overall, in millimetres.

## Run
    conda run -n pv2 python maintenance_scripts/stress_test_anipose_multicam.py
"""

import importlib.util
import time
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import tomllib as toml_lib  # py3.11+
except ModuleNotFoundError:
    import tomli as toml_lib  # py3.10

_REPO_ROOT = "placeholder"
raise RuntimeError("Please replace _REPO_ROOT with your spyglass path")

# ---------------------------------------------------------------------------
# Direct import of triangulation utils (bypass DataJoint package __init__)
# ---------------------------------------------------------------------------

_TRI_PATH = _REPO_ROOT / "src/spyglass/position/v2/utils/triangulation.py"
_spec = importlib.util.spec_from_file_location("triangulation", _TRI_PATH)
_tri = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tri)

build_projection_matrix = _tri.build_projection_matrix
triangulate_pose_df = _tri.triangulate_pose_df
compute_reprojection_errors = _tri.compute_reprojection_errors

# ---------------------------------------------------------------------------
# Paths + Anipose-matched thresholds
# ---------------------------------------------------------------------------

SESSION = Path(
    "/stelmo/cbroz/deeplabcut/anipose_dryad/extracted/"
    "mouse-testing/020820_preDTreaches_day1/020820_JiDT13"
).expanduser()
CALIB_TOML = SESSION / "calibration" / "calibration.toml"
POSE_2D_DIR = SESSION / "pose-2d"
POSE_3D_DIR = SESSION / "pose-3d"

# Match the Anipose project config (mouse-testing/config.toml).
MIN_CONFIDENCE = 0.3  # [triangulation] score_threshold
MAX_REPROJ_ERROR = 5.0  # [triangulation] reproj_error_threshold

BODYPARTS = ["l-base", "l-edge", "l-middle", "r-base", "r-edge", "r-middle"]


# ---------------------------------------------------------------------------
# Calibration: Anipose calibration.toml -> V2 cam_calibrations dict
# ---------------------------------------------------------------------------


def load_anipose_calibration(toml_path: Path) -> dict:
    """Parse an Anipose ``calibration.toml`` into V2 calibration dicts.

    Anipose stores the world->camera pose (Rodrigues ``rotation`` + linear
    ``translation``); V2 stores the camera->rig pose, so we invert.

    Parameters
    ----------
    toml_path : Path
        Path to an Anipose ``calibration.toml``.

    Returns
    -------
    dict
        Mapping ``camera_index -> {"intrinsics": ..., "extrinsics": ...,
        "name": ..., "size": ...}`` sorted by camera index.
    """
    import cv2

    with open(toml_path, "rb") as fh:
        raw = toml_lib.load(fh)

    cams = {}
    for key, cam in raw.items():
        if not key.startswith("cam_"):
            continue
        idx = int(key.split("_")[1])
        K = np.asarray(cam["matrix"], dtype=float)
        dist = list(cam["distortions"])  # [k1, k2, p1, p2, k3]

        rvec = np.asarray(cam["rotation"], dtype=float).reshape(3, 1)
        tvec = np.asarray(cam["translation"], dtype=float)
        R_wc, _ = cv2.Rodrigues(rvec)  # world(rig) -> camera
        R_cam_to_rig = R_wc.T
        t_cam_to_rig = -R_wc.T @ tvec

        cams[idx] = {
            "name": cam.get("name", key),
            "size": cam.get("size"),
            "intrinsics": {
                "fx": float(K[0, 0]),
                "fy": float(K[1, 1]),
                "cx": float(K[0, 2]),
                "cy": float(K[1, 2]),
                "dist_coeffs": dist,
            },
            "extrinsics": {
                "R": R_cam_to_rig.tolist(),
                "t": t_cam_to_rig.tolist(),
            },
        }
    return dict(sorted(cams.items()))


# ---------------------------------------------------------------------------
# 2D pose: Anipose per-camera DLC h5 -> (bodypart, coord) DataFrame
# ---------------------------------------------------------------------------


def load_primary_detections(h5_path: Path) -> pd.DataFrame:
    """Load the top DLC detection per body part from an Anipose pose-2d h5.

    Anipose's DLC output stores up to 20 candidate detections per body part
    (``x``/``y``/``likelihood``, ``x2``/``y2``/``likelihood2``, ...).  With
    ``ransac = false`` Anipose triangulates the single best (first) detection,
    so we keep only the ``x``/``y``/``likelihood`` columns.

    Parameters
    ----------
    h5_path : Path
        Path to one camera's pose-2d ``.h5``.

    Returns
    -------
    pd.DataFrame
        2-level ``(bodypart, coord)`` columns with ``coord`` in
        ``{x, y, likelihood}``; one row per frame.
    """
    raw = pd.read_hdf(h5_path)
    scorer = raw.columns.get_level_values("scorer")[0]
    raw = raw[scorer]  # drop scorer level -> (bodypart, coord)

    records = {}
    for bp in BODYPARTS:
        records[(bp, "x")] = raw[(bp, "x")].values
        records[(bp, "y")] = raw[(bp, "y")].values
        records[(bp, "likelihood")] = raw[(bp, "likelihood")].values
    out = pd.DataFrame(records, index=raw.index)
    out.columns = pd.MultiIndex.from_tuples(out.columns)
    return out


def list_reaches() -> list:
    """Return sorted reach stems that have both cameras + a 3D reference."""
    stems = set()
    for h5 in POSE_2D_DIR.glob("*_cam1.h5"):
        stem = h5.name[: -len("_cam1.h5")]
        cam2 = POSE_2D_DIR / f"{stem}_cam2.h5"
        ref = POSE_3D_DIR / f"{stem}.csv"
        if cam2.exists() and ref.exists():
            stems.add(stem)
    return sorted(stems)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def triangulate_reach(stem: str, cam_calib: dict):
    """Triangulate one reach and load the Anipose 3D reference.

    Returns
    -------
    tuple
        ``(result_df, reference_df, cam_dfs)``.
    """
    cam_dfs = {
        0: load_primary_detections(POSE_2D_DIR / f"{stem}_cam1.h5"),
        1: load_primary_detections(POSE_2D_DIR / f"{stem}_cam2.h5"),
    }
    cam_calibrations = {
        ci: {
            "intrinsics": cam_calib[ci]["intrinsics"],
            "extrinsics": cam_calib[ci]["extrinsics"],
        }
        for ci in cam_calib
    }
    result = triangulate_pose_df(
        cam_dfs,
        cam_calibrations,
        BODYPARTS,
        min_confidence=MIN_CONFIDENCE,
        max_reproj_error=MAX_REPROJ_ERROR,
    )
    reference = pd.read_csv(POSE_3D_DIR / f"{stem}.csv")
    return result, reference, cam_dfs


def main():
    print("\n=== Anipose multi-camera triangulation stress test ===")
    if not CALIB_TOML.exists():
        raise FileNotFoundError(
            f"Calibration not found: {CALIB_TOML}\n"
            "See the module docstring for download + extract instructions."
        )

    cam_calib = load_anipose_calibration(CALIB_TOML)
    print(f"\nCalibration: {CALIB_TOML}")
    for ci, cam in cam_calib.items():
        intr = cam["intrinsics"]
        t = np.asarray(cam["extrinsics"]["t"])
        print(
            f"  cam_{ci} ({cam['name']}): size={cam['size']}  "
            f"fx={intr['fx']:.1f}  k1={intr['dist_coeffs'][0]:+.4f}  "
            f"|t|={np.linalg.norm(t):.2f} mm"
        )
    # Baseline = distance between the two camera centres in the rig frame.
    t0 = np.asarray(cam_calib[0]["extrinsics"]["t"])
    t1 = np.asarray(cam_calib[1]["extrinsics"]["t"])
    print(f"  stereo baseline: {np.linalg.norm(t1 - t0):.2f} mm")

    reaches = list_reaches()
    print(f"\nReaches with both cams + 3D reference: {len(reaches)}")

    # Accumulators (per body part) across all reaches.
    bp_diffs = {bp: [] for bp in BODYPARTS}  # |V2 - Anipose| per point (mm)
    bp_reproj = {bp: [] for bp in BODYPARTS}  # V2 reprojection error (px)
    n_frames_total = 0
    t_start = time.perf_counter()

    for stem in reaches:
        result, reference, cam_dfs = triangulate_reach(stem, cam_calib)
        n_frames_total += len(result)

        proj = {
            ci: build_projection_matrix(
                cam_calib[ci]["intrinsics"], cam_calib[ci]["extrinsics"]
            )
            for ci in cam_calib
        }

        for bp in BODYPARTS:
            v2 = np.column_stack(
                [
                    result[("triangulated", bp, "x")].values,
                    result[("triangulated", bp, "y")].values,
                    result[("triangulated", bp, "z")].values,
                ]
            )
            ref = reference[[f"{bp}_x", f"{bp}_y", f"{bp}_z"]].values

            valid = ~np.isnan(v2[:, 0]) & ~np.isnan(ref[:, 0])
            if np.any(valid):
                d = np.linalg.norm(v2[valid] - ref[valid], axis=1)
                bp_diffs[bp].append(d)

            # V2 reprojection error against the same input 2D detections.
            pts_list, proj_list = [], []
            for ci in cam_calib:
                pts_list.append(
                    np.column_stack(
                        [
                            cam_dfs[ci][(bp, "x")].values,
                            cam_dfs[ci][(bp, "y")].values,
                        ]
                    )
                )
                proj_list.append(proj[ci])
            reproj = compute_reprojection_errors(v2, pts_list, proj_list)
            bp_reproj[bp].append(reproj[~np.isnan(reproj)])

    elapsed = time.perf_counter() - t_start

    # -----------------------------------------------------------------------
    # Report: V2 vs Anipose 3D agreement
    # -----------------------------------------------------------------------
    print(
        "\n--- V2 vs Anipose 3D agreement (mm) ---\n"
        f"  thresholds: min_confidence={MIN_CONFIDENCE}, "
        f"max_reproj_error={MAX_REPROJ_ERROR} px (match Anipose config)\n"
    )
    hdr = f"{'Bodypart':<10}  {'points':>8}  {'median':>8}  {'mean':>8}  {'p95':>8}  {'max':>8}"
    print(hdr)
    print("-" * len(hdr))

    all_diffs = []
    for bp in BODYPARTS:
        if not bp_diffs[bp]:
            print(f"{bp:<10}  {'0':>8}  {'—':>8}  {'—':>8}  {'—':>8}  {'—':>8}")
            continue
        d = np.concatenate(bp_diffs[bp])
        all_diffs.append(d)
        print(
            f"{bp:<10}  {d.size:>8}  {np.median(d):>8.4f}  {np.mean(d):>8.4f}  "
            f"{np.percentile(d, 95):>8.4f}  {d.max():>8.4f}"
        )
    print("-" * len(hdr))
    all_d = np.concatenate(all_diffs) if all_diffs else np.array([])
    if all_d.size:
        print(
            f"{'Overall':<10}  {all_d.size:>8}  {np.median(all_d):>8.4f}  "
            f"{np.mean(all_d):>8.4f}  {np.percentile(all_d, 95):>8.4f}  "
            f"{all_d.max():>8.4f}"
        )

    # -----------------------------------------------------------------------
    # Report: V2 reprojection error distribution
    # -----------------------------------------------------------------------
    print("\n--- V2 reprojection error (px) ---\n")
    print(f"{'Bodypart':<10}  {'points':>8}  {'median':>8}  {'p95':>8}")
    print("-" * 40)
    for bp in BODYPARTS:
        r = np.concatenate(bp_reproj[bp]) if bp_reproj[bp] else np.array([])
        if r.size:
            print(
                f"{bp:<10}  {r.size:>8}  {np.median(r):>8.3f}  "
                f"{np.percentile(r, 95):>8.3f}"
            )

    # -----------------------------------------------------------------------
    # Verdict
    # -----------------------------------------------------------------------
    print("\n--- Throughput ---")
    n_kp = n_frames_total * len(BODYPARTS)
    print(
        f"  {len(reaches)} reaches  {n_frames_total:,} frames  "
        f"{n_kp:,} keypoints in {elapsed:.2f}s  ({n_kp/elapsed:.0f} kp/s)"
    )

    if all_d.size:
        median_um = np.median(all_d) * 1000.0
        print(
            f"\nMedian V2-vs-Anipose 3D disagreement: "
            f"{np.median(all_d):.4f} mm ({median_um:.1f} µm)"
        )
        # Linear triangulation is deterministic; agreement should be ~rounding.
        assert np.median(all_d) < 0.5, (
            f"V2 median diverges from Anipose by {np.median(all_d):.4f} mm "
            "(expected < 0.5 mm for identical linear triangulation)"
        )
        print("V2 reproduces Anipose's linear triangulation: PASS")

    print("\nDone.")


if __name__ == "__main__":
    main()
