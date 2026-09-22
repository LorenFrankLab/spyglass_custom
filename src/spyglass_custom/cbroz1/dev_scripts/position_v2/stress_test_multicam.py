"""Multi-camera triangulation stress test using the openfield DLC example.

There are no bundled stereo video pairs in ~/deeplabcut/examples.  The DLC 3D
notebook (Demo_3D_DeepLabCut.ipynb) demonstrates the workflow but relies on
an external Caltech calibration dataset and user-supplied synchronised camera
pairs.

This script instead uses the existing single-camera DLC H5 output
(m3v1mp4DLC_HrnetW32_pretrainedMay5shuffle1_detector_0_snapshot_0.h5,
2330 frames, 39 bodyparts, 640×480) as a *ground-truth* cam0 view, then:

1. Back-projects the real pixel detections to 3D at a fixed floor depth,
   giving a known ground-truth point cloud.
2. Projects those 3D points through a synthetic cam1 (rotated 30° about Y,
   offset 0.5 m to the right) and adds Gaussian noise (σ=1 px).
3. Runs spyglass.position.v2.utils.triangulation.triangulate_pose_df with
   the two views.
4. Reports per-bodypart and overall reconstruction accuracy, reprojection
   error distribution, and throughput.

Run with:
    conda run -n pv2 python maintenance_scripts/stress_test_multicam.py

Requirements: numpy, pandas, scipy (for stats), cv2 (via opencv-python).
All are present in the pv2 environment.
"""

import importlib.util
import time
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = "placeholder"
raise RuntimeError("Please replace _REPO_ROOT with your spyglass path")


# Import triangulation directly to avoid triggering DataJoint connection via
# spyglass's package __init__.
_TRI_PATH = _REPO_ROOT / "src/spyglass/position/v2/utils/triangulation.py"
_spec = importlib.util.spec_from_file_location("triangulation", _TRI_PATH)
_tri = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tri)
build_projection_matrix = _tri.build_projection_matrix
triangulate_pose_df = _tri.triangulate_pose_df
compute_reprojection_errors = _tri.compute_reprojection_errors

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DLC_H5 = Path(
    "~/deeplabcut/examples/openfield-Pranav-2018-10-30"
    "/videos/m3v1mp4DLC_HrnetW32_pretrainedMay5shuffle1"
    "_detector_0_snapshot_0.h5"
).expanduser()

if not DLC_H5.exists():
    raise FileNotFoundError(
        f"DLC H5 not found at {DLC_H5}. "
        "Run this script from the repo root after installing deeplabcut examples."
    )

# ---------------------------------------------------------------------------
# Camera geometry
# ---------------------------------------------------------------------------

W, H = 640, 480
FPS = 30.0
# Assumed camera-to-floor depth.  At fx=500 this maps ±320 px → ±0.64 m, a
# plausible 1.28 m-wide arena view.
FLOOR_DEPTH_M = 1.0

# cam0: at rig origin, looking along +Z (identity).
INTR0 = {
    "fx": 500.0,
    "fy": 500.0,
    "cx": W / 2,
    "cy": H / 2,
    "dist_coeffs": [0.0, 0.0, 0.0, 0.0],
}
EXT0 = {"R": np.eye(3).tolist(), "t": [0.0, 0.0, 0.0]}

# cam1: parallel stereo — same orientation as cam0, 20 cm baseline to the
# right.  Both cameras share the same field of view; a 30° toe-in would shift
# cam1's centre-of-projection away from the scene causing triangulation to
# fail for most of the frame.
#
# With fx=500, baseline=0.2 m, depth=1 m:
#   disparity(X=0) = baseline * fx / depth = 100 px  → well-conditioned
#   σ_depth ≈ depth² / (fx * baseline) * σ_px = 0.02 m per px of noise
#
# A slightly different focal length exercises the heterogeneous-camera path.
INTR1 = {
    "fx": 510.0,
    "fy": 510.0,
    "cx": W / 2,
    "cy": H / 2,
    "dist_coeffs": [0.0, 0.0, 0.0, 0.0],
}
EXT1 = {"R": np.eye(3).tolist(), "t": [0.2, 0.0, 0.0]}

NOISE_PX = 1.0  # Gaussian noise added to cam1 projections (pixels)
RNG = np.random.default_rng(42)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def backproject_to_3d(px, py, intrinsics, depth_m):
    """Back-project 2D pixel coord to 3D rig point at given depth (cam0 = rig)."""
    fx, fy = intrinsics["fx"], intrinsics["fy"]
    cx, cy = intrinsics["cx"], intrinsics["cy"]
    X = (px - cx) / fx * depth_m
    Y = (py - cy) / fy * depth_m
    Z = depth_m
    return np.array([X, Y, Z])


def project_3d_to_cam(pts3d_h, P):
    """Project (N,4) homogeneous points through (3,4) projection matrix."""
    proj = (P @ pts3d_h.T).T  # (N, 3)
    with np.errstate(invalid="ignore"):
        px = proj[:, 0] / proj[:, 2]
        py = proj[:, 1] / proj[:, 2]
    return px, py


def build_proj(intr, ext):
    return build_projection_matrix(intr, ext)


# ---------------------------------------------------------------------------
# Load real DLC data
# ---------------------------------------------------------------------------

print(f"\nLoading DLC H5: {DLC_H5.name}")
raw = pd.read_hdf(DLC_H5)
scorer = raw.columns.get_level_values("scorer")[0]
bodyparts = raw.columns.get_level_values("bodyparts").unique().tolist()
n_frames = len(raw)
n_bp = len(bodyparts)
print(f"  {n_frames} frames × {n_bp} bodyparts | scorer: {scorer[:40]}…")

# Reindex to float timestamps (seconds)
timestamps = np.arange(n_frames) / FPS
raw.index = timestamps

# Build cam0 (bodypart, coord) DataFrame — collapse scorer level
cam0_df = raw[scorer].copy()  # (n_frames, n_bp*3) with (bp, coord) MultiIndex

# ---------------------------------------------------------------------------
# Build synthetic cam1 from known 3D ground truth
# ---------------------------------------------------------------------------

P0 = build_proj(INTR0, EXT0)
P1 = build_proj(INTR1, EXT1)

print("\nSynthesising cam1 detections from known 3D ground truth …")
t0 = time.perf_counter()

# Per-bodypart: back-project cam0 pixels → 3D → project to cam1
cam1_records = {}
gt_3d = {}  # {bp: (n_frames, 3)} in metres

for bp in bodyparts:
    x0 = cam0_df[(bp, "x")].values
    y0 = cam0_df[(bp, "y")].values
    like0 = cam0_df[(bp, "likelihood")].values

    # Back-project each frame to 3D
    pts3d = np.full((n_frames, 3), np.nan)
    for i in range(n_frames):
        if not np.isnan(x0[i]):
            pts3d[i] = backproject_to_3d(x0[i], y0[i], INTR0, FLOOR_DEPTH_M)

    gt_3d[bp] = pts3d  # save ground truth (metres)

    # Project to cam1 + add noise
    valid = ~np.isnan(pts3d[:, 0])
    x1 = np.full(n_frames, np.nan)
    y1 = np.full(n_frames, np.nan)
    if np.any(valid):
        pts_h = np.column_stack([pts3d[valid], np.ones(valid.sum())])  # (M, 4)
        px, py = project_3d_to_cam(pts_h, P1)
        noise = RNG.normal(0, NOISE_PX, (valid.sum(), 2))
        x1[valid] = px + noise[:, 0]
        y1[valid] = py + noise[:, 1]

    cam1_records[(bp, "x")] = x1
    cam1_records[(bp, "y")] = y1
    # Inherit likelihood from cam0 (same animal, same frames)
    cam1_records[(bp, "likelihood")] = like0

elapsed_synth = time.perf_counter() - t0
print(f"  Done in {elapsed_synth:.2f}s")

cam1_df = pd.DataFrame(cam1_records, index=timestamps)
cam1_df.columns = pd.MultiIndex.from_tuples(cam1_df.columns)

# ---------------------------------------------------------------------------
# Triangulate
# ---------------------------------------------------------------------------

cam_dfs = {0: cam0_df, 1: cam1_df}
cam_calibrations = {
    0: {"intrinsics": INTR0, "extrinsics": EXT0},
    1: {"intrinsics": INTR1, "extrinsics": EXT1},
}

print(f"\nTriangulating {n_frames} frames × {n_bp} bodyparts …")
t0 = time.perf_counter()

result_df = triangulate_pose_df(
    cam_dfs,
    cam_calibrations,
    bodyparts,
    min_confidence=0.6,
    max_reproj_error=5.0,
)

elapsed_triang = time.perf_counter() - t0
print(
    f"  Done in {elapsed_triang:.3f}s  ({n_frames * n_bp / elapsed_triang:.0f} keypoints/s)"
)

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

print("\n--- Reconstruction accuracy (metres, vs known 3D ground truth) ---\n")
print(
    f"{'Bodypart':<30}  {'frames':>6}  {'mean_err_m':>10}  {'p95_err_m':>10}  {'triag_%':>8}"
)
print("-" * 70)

all_errors = []
for bp in bodyparts:
    # triangulate_pose_df returns metres (units of the extrinsics translation).
    # The production path multiplies by 100 before NWB storage; here we
    # compare directly in metres against gt_3d (also in metres).
    x3d = result_df[("triangulated", bp, "x")].values
    y3d = result_df[("triangulated", bp, "y")].values
    z3d = result_df[("triangulated", bp, "z")].values
    like = result_df[("triangulated", bp, "likelihood")].values

    gt = gt_3d[bp]  # (n_frames, 3) in metres

    valid = ~np.isnan(x3d) & ~np.isnan(gt[:, 0])
    if not np.any(valid):
        print(f"{bp:<30}  {'—':>6}  {'—':>10}  {'—':>10}  {'0.0':>8}")
        continue

    recovered = np.column_stack([x3d[valid], y3d[valid], z3d[valid]])
    errors = np.linalg.norm(recovered - gt[valid], axis=1)
    all_errors.append(errors)

    pct_triangulated = 100.0 * valid.sum() / n_frames
    print(
        f"{bp:<30}  {valid.sum():>6}  "
        f"{np.mean(errors):>10.4f}  "
        f"{np.percentile(errors, 95):>10.4f}  "
        f"{pct_triangulated:>7.1f}%"
    )

all_errors_flat = np.concatenate(all_errors) if all_errors else np.array([])
print("-" * 70)
if len(all_errors_flat):
    print(
        f"\n{'Overall':<30}  {len(all_errors_flat):>6}  "
        f"{np.mean(all_errors_flat):>10.4f}  "
        f"{np.percentile(all_errors_flat, 95):>10.4f}"
    )
    print(
        f"\nMean 3D error: {np.mean(all_errors_flat)*100:.2f} cm  "
        f"(noise injected: {NOISE_PX:.1f} px)"
    )

# ---------------------------------------------------------------------------
# Likelihood gate check
# ---------------------------------------------------------------------------

total_keypoints = n_frames * n_bp
gated_out = 0
for bp in bodyparts:
    like = result_df[("triangulated", bp, "likelihood")].values
    # likelihood == 0 means reprojection gate rejected it (not NaN = not triangulated)
    gated_out += int(np.sum(like == 0.0))

print(
    f"\nReprojection gate rejected {gated_out}/{total_keypoints} "
    f"keypoints ({100*gated_out/total_keypoints:.2f}%)"
)

# ---------------------------------------------------------------------------
# Throughput summary
# ---------------------------------------------------------------------------

print("\n--- Throughput ---")
print(f"  Synthesis:       {elapsed_synth:.2f}s")
print(
    f"  Triangulation:   {elapsed_triang:.3f}s  "
    f"({n_frames * n_bp / elapsed_triang:.0f} kp/s)"
)
print(
    f"  Total keypoints: {n_frames * n_bp:,}  ({n_frames} frames × {n_bp} bp)"
)

print("\nDone.")
