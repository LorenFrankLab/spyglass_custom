"""Load the Anipose mouse dataset as a 3D project through the Position V2 tables.

Where ``stress_test_anipose_multicam.py`` exercises only the standalone
``triangulation.py`` utilities, this script drives the **full V2 table stack**
end-to-end on the same real two-camera Anipose data:

    Calibration / CameraRig   <- real Anipose calibration.toml
    Skeleton / Model / ...     <- dummy upstream entries (make_example_3d_project)
    VidFileGroup (camera_index + Calibration)
    PoseEstim.populate()       -> 3D triangulation via PoseEstim._make_3d
    PoseEstim.fetch1_dataframe -> 3D (x, y, z) per body part

It verifies that the calibration, model, and pose-estimation tables can hold,
fetch, and run 3D inference on multi-camera data, and that the triangulated
output round-tripped through NWB still matches Anipose's own ``pose-3d`` export.

## Real videos, paired with calibrations
The real per-camera reach videos (``videos-raw/{reach}_cam{1,2}.mp4``) are
extracted from the dataset zip and registered in ``VideoFile``.  Each video is
paired to a calibration **by camera name**: the calibration entry for camera
index ``ci`` carries a name token (``"cam1"``/``"cam2"``) that must appear in
the matching video's filename, so the video at a given ``camera_index`` is the
one the calibration at that index describes.

## How it avoids real DLC inference
The V2 3D path runs per-camera DLC, then triangulates.  Anipose already shipped
the per-camera 2D detections, so we register the task with ``task_mode='load'``
and place cleaned, single-detection DLC ``.h5`` files (named
``{video_stem}DLC_*.h5``) where the DLC output-discovery strategy finds them.
Anipose stores up to 20 candidate detections per body part; we keep the top
detection (``x``/``y``/``likelihood``), which is what Anipose triangulates with
``ransac = false``.

## DB rows
A run preserves its rows by default (for inspection / reuse).  Pass ``--clean``
to delete them and exit.

## Units
Anipose calibration translations are in millimetres; the V2 3D path scales
triangulated coordinates by 100 assuming **metres -> centimetres**.  We convert
the extrinsic translations to metres (mm / 1000) on ingest, so V2's stored
centimetres equal the Anipose millimetres / 10.

## Requirements
- A live Spyglass DB (uses ``dj_local_conf_pv2.json``).
- The extracted Anipose session (see ``stress_test_anipose_multicam.py`` for
  download + extract instructions).

## Run
    conda run -n pv2 python maintenance_scripts/load_anipose_3d_project.py
    conda run -n pv2 python maintenance_scripts/load_anipose_3d_project.py --clean
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import tomllib as toml_lib  # py3.11+
except ModuleNotFoundError:
    import tomli as toml_lib  # py3.10

SPYGLASS_DIR = "placeholder"
raise RuntimeError("Please replace SPYGLASS_DIR with your spyglass path")

# ---------------------------------------------------------------------------
# Paths + Anipose-matched thresholds
# ---------------------------------------------------------------------------

_ANIPOSE_ROOT = Path("/stelmo/cbroz/deeplabcut/anipose_dryad")
MOUSE_ZIP = _ANIPOSE_ROOT / "mouse-anipose.zip"
EXTRACT_ROOT = _ANIPOSE_ROOT / "extracted"
ZIP_SESS = "mouse-testing/020820_preDTreaches_day1/020820_JiDT13"

SESSION = EXTRACT_ROOT / ZIP_SESS
CALIB_TOML = SESSION / "calibration" / "calibration.toml"
POSE_2D_DIR = SESSION / "pose-2d"
POSE_3D_DIR = SESSION / "pose-3d"
VIDEOS_RAW_DIR = SESSION / "videos-raw"

REACH_STEM = "preDT1_JiDT13_reach1_hit"
BODYPARTS = ["l-base", "l-edge", "l-middle", "r-base", "r-edge", "r-middle"]
EDGES = [
    ("l-base", "l-edge"),
    ("l-edge", "l-middle"),
    ("l-middle", "l-base"),
    ("r-base", "r-edge"),
    ("r-edge", "r-middle"),
    ("r-middle", "r-base"),
]
MIN_CONFIDENCE = 0.3  # [triangulation] score_threshold
MAX_REPROJ_ERROR = 5.0  # [triangulation] reproj_error_threshold
PREFIX = "anipose3d"
WORK_DIR = Path(SPYGLASS_DIR) / "tests" / "_data" / "anipose_3d"


# ---------------------------------------------------------------------------
# Anipose calibration.toml -> V2 calibration dicts (extrinsics in metres)
# ---------------------------------------------------------------------------


def anipose_calibration_to_v2(toml_path: Path) -> dict:
    """Parse an Anipose ``calibration.toml`` into V2 calibration dicts.

    Anipose stores the world->camera pose (Rodrigues ``rotation`` +
    ``translation`` in millimetres).  V2 stores the camera->rig pose with the
    translation in **metres**, so we invert and rescale.

    Parameters
    ----------
    toml_path : Path
        Path to an Anipose ``calibration.toml``.

    Returns
    -------
    dict
        ``camera_index -> {"intrinsics", "extrinsics", "image_size", "name"}``.
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
        rvec = np.asarray(cam["rotation"], dtype=float).reshape(3, 1)
        tvec_mm = np.asarray(cam["translation"], dtype=float)
        R_wc, _ = cv2.Rodrigues(rvec)  # world(rig) -> camera
        R_cam_to_rig = R_wc.T
        t_cam_to_rig_m = (-R_wc.T @ tvec_mm) / 1000.0  # mm -> metres

        cams[idx] = {
            "name": cam.get("name", key),
            "image_size": cam.get("size", [1024, 768]),
            "intrinsics": {
                "fx": float(K[0, 0]),
                "fy": float(K[1, 1]),
                "cx": float(K[0, 2]),
                "cy": float(K[1, 2]),
                "dist_coeffs": list(cam["distortions"]),
            },
            "extrinsics": {
                "R": R_cam_to_rig.tolist(),
                "t": t_cam_to_rig_m.tolist(),
            },
        }
    return dict(sorted(cams.items()))


# ---------------------------------------------------------------------------
# Per-camera 2D prep: clean DLC h5 + matching dummy video
# ---------------------------------------------------------------------------


def _read_primary_detection(h5_path: Path) -> tuple:
    """Return (clean 3-level DLC df, scorer) keeping only the top detection."""
    raw = pd.read_hdf(h5_path)
    scorer = raw.columns.get_level_values("scorer")[0]
    flat = raw[scorer]  # (bodypart, coord)
    cols, data = [], {}
    for bp in BODYPARTS:
        for coord in ("x", "y", "likelihood"):
            cols.append((scorer, bp, coord))
            data[(scorer, bp, coord)] = flat[(bp, coord)].values
    df = pd.DataFrame(data)
    df.columns = pd.MultiIndex.from_tuples(
        cols, names=["scorer", "bodyparts", "coords"]
    )
    return df, scorer


def extract_reach_videos(tokens: list) -> dict:
    """Extract the real per-camera reach videos from the Anipose zip.

    Pulls ``videos-raw/{REACH_STEM}_{token}.mp4`` for each camera token (e.g.
    ``"cam1"``) into the extracted session tree, reusing already-extracted
    files when present.

    Parameters
    ----------
    tokens : list of str
        Camera-name tokens (from the calibration) identifying each camera's
        video, e.g. ``["cam1", "cam2"]``.

    Returns
    -------
    dict
        Mapping ``token -> absolute video Path``.
    """
    import zipfile

    videos = {}
    for token in tokens:
        member = f"{ZIP_SESS}/videos-raw/{REACH_STEM}_{token}.mp4"
        dest = EXTRACT_ROOT / member
        if not dest.exists() or dest.stat().st_size == 0:
            if not MOUSE_ZIP.exists():
                raise FileNotFoundError(
                    f"Reach video not on disk and zip missing: {dest}\n"
                    f"Expected zip at {MOUSE_ZIP}."
                )
            with zipfile.ZipFile(MOUSE_ZIP) as zf:
                zf.extract(member, EXTRACT_ROOT)
        videos[token] = dest.resolve()
    return videos


def prepare_camera_inputs(calib: dict, work_dir: Path) -> dict:
    """Pair each calibrated camera with its real reach video + cleaned DLC h5.

    Videos are paired to calibrations **by camera name**: the calibration entry
    for camera index ``ci`` carries a name token (``"cam1"``/``"cam2"``) that
    must appear in the matching video's filename.  This guarantees the video at
    a given ``camera_index`` is the one the calibration at that index describes.

    The per-camera DLC ``.h5`` (Anipose's top detection) is truncated to the
    timestamp camera's real frame count and written as ``{video_stem}DLC_*.h5``
    in ``output_dir`` so the DLC output-discovery strategy finds it.

    Parameters
    ----------
    calib : dict
        Output of :func:`anipose_calibration_to_v2`.
    work_dir : Path
        Scratch directory for the cleaned DLC outputs.

    Returns
    -------
    dict
        ``{"video_paths", "camera_indices", "output_dir", "scorer",
        "n_frames", "pairing"}`` where ``pairing`` is a list of
        ``(camera_index, token, video_path)`` tuples.
    """
    import cv2

    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir = work_dir / "dlc_outputs"
    output_dir.mkdir(exist_ok=True)

    # Camera order + name tokens come from the calibration, so every loaded
    # video is bound to the calibration that describes the same physical camera.
    cam_order = sorted(calib)
    tokens = [calib[ci]["name"] for ci in cam_order]
    videos = extract_reach_videos(tokens)

    video_paths, camera_indices, pairing = [], [], []
    for ci, token in zip(cam_order, tokens):
        vpath = videos[token]
        if token not in vpath.name:
            raise ValueError(
                f"Calibration camera {ci} is named '{token}' but its paired "
                f"video '{vpath.name}' does not contain that token."
            )
        video_paths.append(str(vpath))
        camera_indices.append(ci)
        pairing.append((ci, token, str(vpath)))

    # Timestamps for the 3D object come from the lowest-index camera's video;
    # truncate every camera's pose table to that exact frame count so pose rows
    # and per-frame timestamps stay aligned.
    cap = cv2.VideoCapture(video_paths[0])
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()

    scorer = None
    for ci, token in zip(cam_order, tokens):
        df, scorer = _read_primary_detection(
            POSE_2D_DIR / f"{REACH_STEM}_{token}.h5"
        )
        if len(df) < n_frames:
            raise ValueError(
                f"Camera '{token}' pose table has {len(df)} rows < timestamp "
                f"camera frame count {n_frames}; cannot align."
            )
        stem = Path(videos[token]).stem  # e.g. preDT1_JiDT13_reach1_hit_cam1
        out = output_dir / f"{stem}DLC_resnet50_mouse.h5"
        df.iloc[:n_frames].to_hdf(str(out), key="df_with_missing", mode="w")

    return {
        "video_paths": video_paths,
        "camera_indices": camera_indices,
        "output_dir": str(output_dir),
        "scorer": scorer,
        "n_frames": n_frames,
        "pairing": pairing,
    }


# ---------------------------------------------------------------------------
# Validation against Anipose's pose-3d reference
# ---------------------------------------------------------------------------


def validate_against_anipose(v2_df: pd.DataFrame, n_frames: int) -> dict:
    """Compare V2's fetched 3D dataframe to Anipose's pose-3d CSV (mm).

    V2 stores centimetres; Anipose stores millimetres.  We rescale V2 by 10 and
    report the per-point Euclidean disagreement.

    Returns
    -------
    dict
        ``{"median_mm", "p95_mm", "n_points", "per_bp"}``.
    """
    ref = pd.read_csv(POSE_3D_DIR / f"{REACH_STEM}.csv").iloc[:n_frames]
    diffs, per_bp = [], {}
    for bp in BODYPARTS:
        v2 = (
            np.column_stack(
                [
                    v2_df[("triangulated", bp, "x")].values,
                    v2_df[("triangulated", bp, "y")].values,
                    v2_df[("triangulated", bp, "z")].values,
                ]
            )
            * 10.0  # cm -> mm
        )
        r = ref[[f"{bp}_x", f"{bp}_y", f"{bp}_z"]].values
        valid = ~np.isnan(v2[:, 0]) & ~np.isnan(r[:, 0])
        if np.any(valid):
            d = np.linalg.norm(v2[valid] - r[valid], axis=1)
            diffs.append(d)
            per_bp[bp] = (int(valid.sum()), float(np.median(d)))
    alld = np.concatenate(diffs) if diffs else np.array([])
    return {
        "median_mm": float(np.median(alld)) if alld.size else float("nan"),
        "p95_mm": float(np.percentile(alld, 95)) if alld.size else float("nan"),
        "n_points": int(alld.size),
        "per_bp": per_bp,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete all DB rows created by a previous run and exit. By "
        "default a run preserves its rows for inspection.",
    )
    args = parser.parse_args()

    import datajoint as dj

    dj.config.load(str(SPYGLASS_DIR / "dj_local_conf_pv2.json"))

    sys.path.insert(0, str(SPYGLASS_DIR))
    from tests.position.v2.make_example_3d_project import (
        bootstrap_3d_project,
        cleanup_3d_project,
    )

    if args.clean:
        cleanup_3d_project(PREFIX)
        print(f"Cleaned up DB rows for prefix '{PREFIX}'.")
        return

    if not CALIB_TOML.exists():
        raise FileNotFoundError(
            f"Anipose session not found: {SESSION}\n"
            "See stress_test_anipose_multicam.py for download instructions."
        )

    print("\n=== Load Anipose data as a 3D Position V2 project ===")
    from spyglass.position.v2.estim import PoseEstim
    from spyglass.position.v2.video import VidFileGroup

    # Start from a clean slate so re-runs are deterministic.
    cleanup_3d_project(PREFIX)

    calib = anipose_calibration_to_v2(CALIB_TOML)

    print("\n[1/4] Pairing real reach videos with calibrations + DLC h5 ...")
    inputs = prepare_camera_inputs(calib, WORK_DIR)
    print(
        f"  reach={REACH_STEM}  frames={inputs['n_frames']}  "
        f"bodyparts={len(BODYPARTS)}"
    )
    for ci, token, vpath in inputs["pairing"]:
        cam = calib[ci]
        t = np.asarray(cam["extrinsics"]["t"])
        print(
            f"  cam_index {ci}: calib '{cam['name']}' (fx={cam['intrinsics']['fx']:.1f}, "
            f"|t|={np.linalg.norm(t) * 1000:.1f} mm)  <->  video {Path(vpath).name}"
        )

    print("\n[2/4] Inserting upstream DB entries ...")
    info = bootstrap_3d_project(
        video_paths=inputs["video_paths"],
        output_dir=inputs["output_dir"],
        calib_by_cam=calib,
        bodyparts=BODYPARTS,
        edges=EDGES,
        scorer=inputs["scorer"],
        camera_indices=inputs["camera_indices"],
        prefix=PREFIX,
        min_confidence=MIN_CONFIDENCE,
        max_reproj_error=MAX_REPROJ_ERROR,
    )
    print(f"  model_id={info['model_id']}  vid_group_id={info['vid_group_id']}")
    print(f"  calibration={info['camera_rig_id']}/{info['calibration_id']}")

    print("\n[3/4] PoseEstim.populate() (3D triangulation via tables) ...")
    t0 = time.perf_counter()
    PoseEstim.populate(info["selection_key"], display_progress=False)
    elapsed = time.perf_counter() - t0
    populated = PoseEstim & info["selection_key"]
    if not populated:
        raise RuntimeError(
            "PoseEstim.populate() produced no entry — 3D path did not run."
        )
    print(f"  populated 1 PoseEstim entry in {elapsed:.1f}s")

    print("\n[4/4] Fetch 3D dataframe + validate vs Anipose pose-3d ...")
    df = populated.fetch1_dataframe()
    # 3D fetch returns a (scorer, bodypart, coord) MultiIndex with x/y/z.
    is_3d = any(c[-1] == "z" for c in df.columns)
    print(f"  fetched df: {df.shape[0]} frames × {df.shape[1]} cols")
    print(f"  index='{df.index.name}'  3D columns present: {is_3d}")
    if not is_3d:
        raise RuntimeError(
            f"Fetched dataframe is not 3D (no z coord): {list(df.columns)}"
        )

    stats = validate_against_anipose(df, inputs["n_frames"])
    print("\n  --- V2-tables vs Anipose pose-3d agreement (mm) ---")
    print(f"  {'bodypart':<10}  {'points':>7}  {'median_mm':>10}")
    for bp, (n, med) in stats["per_bp"].items():
        print(f"  {bp:<10}  {n:>7}  {med:>10.4f}")
    print(
        f"\n  Overall: {stats['n_points']} points  "
        f"median={stats['median_mm']:.4f} mm  p95={stats['p95_mm']:.4f} mm"
    )

    assert stats["median_mm"] < 0.5, (
        f"V2-tables 3D output diverges from Anipose by "
        f"{stats['median_mm']:.4f} mm (expected < 0.5 mm)"
    )
    print(
        "\nPASS: V2 calibration/model/pose-estimation tables ran 3D inference "
        "and reproduced Anipose's triangulation."
    )

    # Confirm the registered VideoFile paths are the real reach videos paired
    # with the loaded per-camera calibrations.
    from spyglass.common import VideoFile

    vf_rows = (
        (VidFileGroup.File & {"vid_group_id": info["vid_group_id"]}) * VideoFile
    ).fetch("camera_index", "path", as_dict=True)
    print("\n  Registered VideoFile <-> calibration pairing:")
    for r in sorted(vf_rows, key=lambda x: x["camera_index"]):
        cam = calib[r["camera_index"]]
        print(
            f"    camera_index {r['camera_index']} (calib '{cam['name']}') "
            f"-> {Path(r['path']).name}"
        )

    print(
        f"\nDB rows preserved (prefix '{PREFIX}'). "
        "Run with --clean to remove them."
    )
    print("\nDone.")


if __name__ == "__main__":
    main()
