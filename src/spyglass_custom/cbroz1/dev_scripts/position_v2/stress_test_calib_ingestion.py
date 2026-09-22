"""Calibration ingestion stress test using real OpenCV stereo calibration.

## Dataset
Caltech stereo example images (14 left/right JPEG pairs, 9×6 inner corners,
30 mm squares) from the DLC GitHub mirror:
  https://raw.githubusercontent.com/DeepLabCut/DeepLabCut/main/examples/stereo_example.zip

Downloaded to: /stelmo/cbroz/deeplabcut/stereo_example/

## Real calibration (OpenCV stereoCalibrate, RMS ≈ 0.205 px)
Intrinsics and stereo extrinsics were computed via cv2.stereoCalibrate on
13/14 image pairs (pair 05 left not detected).  Results are stored in the DB:
  camera_rig_id  = 'caltech_stereo_example5'
  calibration_id = 'opencv_stereo_20260515'

  cam0 (left):
    fx=532.98  fy=533.11  cx=341.39  cy=234.17
    dist=[-0.2868, 0.0798, 0.0010, -0.0003]  (k1, k2, p1, p2)
    t = [0, 0, 0]  (reference camera)

  cam1 (right):
    fx=537.17  fy=536.82  cx=326.04  cy=249.07
    dist=[-0.3015, 0.1725, -0.0007, 0.0002]
    R ≈ I (tiny ~0.3° toe-in from calibration noise)
    t = [99.84, -0.77, 0.03] mm  (baseline)

Image size: 640×480 — matches the DLC openfield example H5.

## What this test exercises
1. Fetching real calibration from the DB and verifying its structure.
2. Verifying undistort_points shifts pixels in the expected direction (k1 < 0
   → barrel distortion → undistorted points move away from centre).
3. Triangulating DLC-style pose data with real distortion coefficients, and
   confirming that applying undistortion reduces 3D reconstruction error
   relative to the no-undistortion path.

## Run
    conda run -n pv2 python maintenance_scripts/stress_test_calib_ingestion.py
"""

import importlib.util
import time
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = "placeholder"
raise RuntimeError("Please replace _REPO_ROOT with your spyglass path")


# ---------------------------------------------------------------------------
# Direct imports (bypass DataJoint package __init__ for triangulation utils)
# ---------------------------------------------------------------------------

_TRI_PATH = _REPO_ROOT / "src/spyglass/position/v2/utils/triangulation.py"
_spec = importlib.util.spec_from_file_location("triangulation", _TRI_PATH)
_tri = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tri)

build_projection_matrix = _tri.build_projection_matrix
undistort_points = _tri.undistort_points
triangulate_pose_df = _tri.triangulate_pose_df

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DLC_H5 = Path(
    "~/deeplabcut/examples/openfield-Pranav-2018-10-30"
    "/videos/m3v1mp4DLC_HrnetW32_pretrainedMay5shuffle1"
    "_detector_0_snapshot_0.h5"
).expanduser()

# ---------------------------------------------------------------------------
# Real calibration constants (from OpenCV stereoCalibrate on Caltech Example 5)
# Stored in DB as caltech_stereo_example5 / opencv_stereo_20260515
# ---------------------------------------------------------------------------

# cam0 (left/reference camera, camera-to-rig = identity)
INTR0 = {
    "fx": 532.9846577999509,
    "fy": 533.1149732271831,
    "cx": 341.39164514355144,
    "cy": 234.17226465788497,
    "dist_coeffs": [
        -0.2868166148073079,
        0.07982072366726319,
        0.0010077222797781045,
        -0.00031757765895281103,
    ],
}
EXT0 = {"R": np.eye(3).tolist(), "t": [0.0, 0.0, 0.0]}

# cam1 (right camera, ~99.84 mm baseline; R is nearly identity)
INTR1 = {
    "fx": 537.169886231981,
    "fy": 536.8246741071331,
    "cx": 326.0432017493408,
    "cy": 249.06899934888605,
    "dist_coeffs": [
        -0.30146432389271266,
        0.1724619460908023,
        -0.0007404748193110254,
        0.00015567845793277077,
    ],
}
EXT1 = {
    "R": [
        [0.9999822154324113, -0.0037233887158658256, -0.004658883488265265],
        [0.0037544091804059547, 0.999970724582966, 0.006667412445921244],
        [0.004633921729243079, -0.006684785223812733, 0.999966919660805],
    ],
    "t": [0.09984088790811123, -0.0007664665752093587, 2.532014649067043e-05],
}

W, H = 640, 480  # image size (matches DLC openfield H5)
FLOOR_DEPTH_M = 1.0  # assumed camera-to-floor height
NOISE_PX = 1.5  # Gaussian noise σ added to cam1 projections (pixels)
RNG = np.random.default_rng(42)

DB_RIG_ID = "caltech_stereo_example5"
DB_CAL_ID = "opencv_stereo_20260515"

# cam0 distortion is zeroed in the triangulation calibration dict because the
# DLC pixels come from an unrelated openfield camera (not the Caltech stereo
# rig), so applying Caltech's distortion model to them is wrong.  Treating
# them as pinhole-undistorted matches the back-projection used for GT.
# cam1 pixels are synthesised via cv2.projectPoints with the real distortion
# model, so undistort_points is a genuine round-trip for cam1.
INTR0_NO_DIST = {**INTR0, "dist_coeffs": [0.0, 0.0, 0.0, 0.0]}

CAM_CALIBRATIONS = {
    0: {"intrinsics": INTR0_NO_DIST, "extrinsics": EXT0},
    1: {"intrinsics": INTR1, "extrinsics": EXT1},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def backproject(px, py, intr, depth_m):
    """Back-project a pixel to 3D at a fixed depth (cam0 = rig frame)."""
    X = (px - intr["cx"]) / intr["fx"] * depth_m
    Y = (py - intr["cy"]) / intr["fy"] * depth_m
    return np.array([X, Y, depth_m])


def project_with_distortion(pts3d, intr, ext):
    """Project (N,3) rig points to distorted 2-D pixels via cv2.projectPoints.

    Extrinsics use the camera-to-rig convention: R, t define the camera
    position in rig coordinates.  cv2.projectPoints expects the rig-to-camera
    (world-to-cam) transform, so we invert before calling.
    """
    import cv2

    R = np.array(ext["R"])
    t = np.array(ext["t"])
    R_cw = R.T
    t_cw = (-R_cw @ t).reshape(3, 1)
    rvec, _ = cv2.Rodrigues(R_cw)
    K = np.array(
        [
            [intr["fx"], 0.0, intr["cx"]],
            [0.0, intr["fy"], intr["cy"]],
            [0.0, 0.0, 1.0],
        ]
    )
    dist = np.array(intr["dist_coeffs"])
    proj, _ = cv2.projectPoints(pts3d.astype(np.float64), rvec, t_cw, K, dist)
    return proj[:, 0, 0], proj[:, 0, 1]


# ---------------------------------------------------------------------------
# Part 1 — DB round-trip
# ---------------------------------------------------------------------------


def test_db_roundtrip():
    """Insert calibration via full FK chain and fetch back to verify.

    Insert order enforced by the new schema:
      CameraDevice → CameraRig → CameraRig.Camera → Calibration → Calibration.Camera
    """
    try:
        import datajoint as dj

        dj.config.load(str(_REPO_ROOT / "dj_local_conf_pv2.json"))
    except Exception as e:
        print(f"  [SKIP] DataJoint not configured: {e}")
        return None

    try:
        from spyglass.common.common_device import CameraDevice
        from spyglass.position.v2.video import Calibration, CameraRig
    except Exception as e:
        print(f"  [SKIP] Cannot import tables: {e}")
        return None

    # Camera names registered in CameraDevice (source of truth)
    cam_names = {
        0: "caltech_stereo5_left",
        1: "caltech_stereo5_right",
    }
    cam_intrinsics = {0: INTR0, 1: INTR1}
    cam_extrinsics = {0: EXT0, 1: EXT1}

    # Step 1: CameraDevice — source of truth for physical cameras
    for ci, cam_name in cam_names.items():
        CameraDevice.insert1(
            {"camera_name": cam_name, "meters_per_pixel": 0.0},
            skip_duplicates=True,
        )

    # Step 2: CameraRig — physical grouping
    CameraRig.insert1(
        {
            "camera_rig_id": DB_RIG_ID,
            "description": "Bouguet stereo Example 5 (left+right, ~100 mm baseline)",
            "n_cameras": 2,
        },
        skip_duplicates=True,
    )

    # Step 3: CameraRig.Camera — map slot index → CameraDevice
    for ci, cam_name in cam_names.items():
        CameraRig.Camera.insert1(
            {
                "camera_rig_id": DB_RIG_ID,
                "camera_index": ci,
                "camera_name": cam_name,
            },
            skip_duplicates=True,
        )

    # Step 4: Calibration — header row
    Calibration.insert1(
        {
            "camera_rig_id": DB_RIG_ID,
            "calibration_id": DB_CAL_ID,
            "calibration_date": "2026-05-15",
            "notes": "Stereo RMS: 0.187/0.192 px mono, 0.205 px stereo; baseline 99.84 mm",
        },
        skip_duplicates=True,
    )

    # Step 5: Calibration.Camera — per-camera intrinsics/extrinsics
    for ci in cam_names:
        Calibration.Camera.insert1(
            {
                "camera_rig_id": DB_RIG_ID,
                "calibration_id": DB_CAL_ID,
                "camera_index": ci,
                "intrinsics": cam_intrinsics[ci],
                "extrinsics": cam_extrinsics[ci],
                "image_size": [W, H],
            },
            skip_duplicates=True,
        )

    # Fetch back and verify
    cam_rows = (
        Calibration.Camera
        & {"camera_rig_id": DB_RIG_ID, "calibration_id": DB_CAL_ID}
    ).fetch(
        "camera_index", "intrinsics", "extrinsics", "image_size", as_dict=True
    )

    assert len(cam_rows) == 2, f"Expected 2 camera rows, got {len(cam_rows)}"
    for row in sorted(cam_rows, key=lambda r: r["camera_index"]):
        ci = row["camera_index"]
        intr = row["intrinsics"]
        ext = row["extrinsics"]
        t = ext["t"]
        print(
            f"  cam{ci} ({cam_names[ci]}): "
            f"fx={intr['fx']:.2f}  k1={intr['dist_coeffs'][0]:.4f}  "
            f"t=[{t[0]*1000:.2f}, {t[1]*1000:.2f}, {t[2]*1000:.3f}] mm"
        )
        assert intr["fx"] > 0 and intr["fy"] > 0
        assert len(intr["dist_coeffs"]) == 4
        assert row["image_size"] == [640, 480]

    baseline_mm = np.linalg.norm(cam_rows[1]["extrinsics"]["t"]) * 1000
    print(f"  Baseline: {baseline_mm:.2f} mm")
    print("  DB round-trip: PASS")
    return cam_rows


# ---------------------------------------------------------------------------
# Part 2 — Undistortion direction check
# ---------------------------------------------------------------------------


def test_undistortion_direction():
    """Verify undistort_points shifts pixels outward for k1 < 0 (barrel)."""
    # Sample a grid of points across the image
    xs = np.linspace(50, W - 50, 10)
    ys = np.linspace(50, H - 50, 10)
    grid_x, grid_y = np.meshgrid(xs, ys)
    pts = np.column_stack([grid_x.ravel(), grid_y.ravel()])

    undist = undistort_points(pts, INTR0)

    cx = INTR0["cx"]
    failures = 0
    for p, u in zip(pts, undist):
        dx_orig = p[0] - cx
        dx_undist = u[0] - cx
        # k1 < 0 → barrel distortion → undistorted coords have LARGER magnitude
        if abs(dx_orig) > 30:
            if abs(dx_undist) < abs(dx_orig) * 0.9:
                failures += 1

    shift = np.linalg.norm(undist - pts, axis=1)
    print(
        f"  Max undistortion shift: {shift.max():.2f} px  "
        f"(mean: {shift.mean():.2f} px)"
    )
    assert (
        failures == 0
    ), f"{failures} points shifted inward (expected outward for k1<0)"
    print("  Undistortion direction: PASS")


# ---------------------------------------------------------------------------
# Part 3 — Triangulation accuracy with real distortion
# ---------------------------------------------------------------------------


def run_triangulation_test():
    """Triangulate real DLC data via synthetic stereo; compare undist vs. none."""
    if not DLC_H5.exists():
        print(f"  [SKIP] DLC H5 not found: {DLC_H5}")
        return

    print(f"  Loading DLC H5: {DLC_H5.name}")
    raw = pd.read_hdf(DLC_H5)
    scorer = raw.columns.get_level_values("scorer")[0]
    bodyparts = raw.columns.get_level_values("bodyparts").unique().tolist()
    n_frames = len(raw)
    fps = 30.0
    raw.index = np.arange(n_frames) / fps
    cam0_df = raw[scorer].copy()

    print(
        f"  {n_frames} frames × {len(bodyparts)} bodyparts | "
        f"baseline {EXT1['t'][0]*1000:.1f} mm | depth {FLOOR_DEPTH_M*100:.0f} cm"
    )
    print(
        f"  Synthesising cam1 (noise σ={NOISE_PX} px, forward distortion applied)…"
    )

    cam1_records = {}
    gt_3d = {}

    for bp in bodyparts:
        x0 = cam0_df[(bp, "x")].values
        y0 = cam0_df[(bp, "y")].values
        like0 = cam0_df[(bp, "likelihood")].values

        pts3d = np.full((n_frames, 3), np.nan)
        for i in range(n_frames):
            if not np.isnan(x0[i]):
                pts3d[i] = backproject(x0[i], y0[i], INTR0, FLOOR_DEPTH_M)
        gt_3d[bp] = pts3d

        valid = ~np.isnan(pts3d[:, 0])
        x1 = np.full(n_frames, np.nan)
        y1 = np.full(n_frames, np.nan)
        if np.any(valid):
            px, py = project_with_distortion(pts3d[valid], INTR1, EXT1)
            noise = RNG.normal(0, NOISE_PX, (valid.sum(), 2))
            x1[valid] = px + noise[:, 0]
            y1[valid] = py + noise[:, 1]

        cam1_records[(bp, "x")] = x1
        cam1_records[(bp, "y")] = y1
        cam1_records[(bp, "likelihood")] = like0

    cam1_df = pd.DataFrame(cam1_records, index=raw.index)
    cam1_df.columns = pd.MultiIndex.from_tuples(cam1_df.columns)
    cam_dfs = {0: cam0_df, 1: cam1_df}

    # WITH undistortion (real k1/k2)
    t0 = time.perf_counter()
    result_with = triangulate_pose_df(
        cam_dfs,
        CAM_CALIBRATIONS,
        bodyparts,
        min_confidence=0.6,
        max_reproj_error=15.0,
    )
    t_with = time.perf_counter() - t0

    # WITHOUT undistortion (zeroed dist_coeffs)
    no_dist = {
        k: {**v, "intrinsics": {**v["intrinsics"], "dist_coeffs": [0, 0, 0, 0]}}
        for k, v in CAM_CALIBRATIONS.items()
    }
    t0 = time.perf_counter()
    result_without = triangulate_pose_df(
        cam_dfs,
        no_dist,
        bodyparts,
        min_confidence=0.6,
        max_reproj_error=15.0,
    )
    t_without = time.perf_counter() - t0

    def mean_error_cm(result_df):
        errs = []
        for bp in bodyparts:
            x3d = result_df[("triangulated", bp, "x")].values
            y3d = result_df[("triangulated", bp, "y")].values
            z3d = result_df[("triangulated", bp, "z")].values
            gt = gt_3d[bp]
            valid = ~np.isnan(x3d) & ~np.isnan(gt[:, 0])
            if np.any(valid):
                rec = np.column_stack([x3d[valid], y3d[valid], z3d[valid]])
                errs.append(np.linalg.norm(rec - gt[valid], axis=1))
        return np.concatenate(errs).mean() * 100 if errs else np.nan

    err_with = mean_error_cm(result_with)
    err_without = mean_error_cm(result_without)
    n_kp = n_frames * len(bodyparts)

    # Theoretical depth uncertainty: σ_Z ≈ Z² / (fx * baseline) * σ_px
    sigma_z_cm = (
        FLOOR_DEPTH_M**2 / (INTR0["fx"] * EXT1["t"][0]) * NOISE_PX * 100
    )

    print(f"\n  {'':40} {'with undist':>12} {'without undist':>15}")
    print(f"  {'Mean 3D error (cm)':40} {err_with:>12.3f} {err_without:>15.3f}")
    print(f"  {'Time (s)':40} {t_with:>12.3f} {t_without:>15.3f}")
    print(
        f"  {'Throughput (kp/s)':40} {n_kp/t_with:>12.0f} {n_kp/t_without:>15.0f}"
    )
    print(f"\n  Theoretical depth σ ≈ {sigma_z_cm:.2f} cm  (per px of noise)")
    improved = err_with <= err_without
    print(
        f"  Undistortion {'REDUCED' if improved else 'did NOT reduce'} error "
        f"by {abs(err_without - err_with):.3f} cm"
    )
    assert improved, (
        f"Expected undistortion to reduce error; got {err_with:.3f} cm vs "
        f"{err_without:.3f} cm without"
    )
    print("  Triangulation with undistortion: PASS")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("\n=== Calibration ingestion stress test ===")
    print(f"DB entry: {DB_RIG_ID} / {DB_CAL_ID}")
    print(
        f"cam0: fx={INTR0['fx']:.2f}  k1={INTR0['dist_coeffs'][0]:.4f}  "
        f"baseline {EXT1['t'][0]*1000:.2f} mm"
    )

    print("\n--- Part 1: DB round-trip (requires live test DB) ---")
    test_db_roundtrip()

    print("\n--- Part 2: Undistortion direction check ---")
    test_undistortion_direction()

    print("\n--- Part 3: Triangulation with real distortion coefficients ---")
    run_triangulation_test()

    print("\nDone.")
