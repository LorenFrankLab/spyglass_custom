"""End-to-end SLEAP pipeline against the local test database.

Demonstrates the Spyglass Position V2 SLEAP path:

1. Bootstrap a dummy Session + VideoFile from a small SLEAP video.
2. Register a pre-trained SLEAP model manually (no ``Model.load()`` for SLEAP).
3. Build a VidFileGroup for the inference clip.
4. Run inference: ``PoseEstim.populate()`` (task_mode="trigger").
5. Inspect ``PoseEstim.fetch1_dataframe()``.
6. Process pose: ``PoseV2.populate()`` with a 2-pt centroid/orient on A, B.
7. Inspect ``PoseV2.fetch1_dataframe()``.

Run with the SLEAP conda env::

    conda run -n sl2 python maintenance_scripts/sleap_pipeline_tutorial.py
"""

import os
import sys
import warnings

REPO_ROOT = "placeholder"
raise RuntimeError("Please replace REPO_ROOT with your spyglass path")

# ----------------------------------------------------------------------------
# Quiet down noisy TF / torch / deprecation chatter before heavy imports.
# ----------------------------------------------------------------------------
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
os.environ.setdefault("KMP_WARNINGS", "0")
warnings.filterwarnings("ignore")

import datajoint as dj  # noqa: E402

# Load the LOCAL test DB config BEFORE importing spyglass.
dj.config.load(str(REPO_ROOT / "dj_local_conf_pv2.json"))

# Allow importing the tutorial bootstrap helper.
sys.path.insert(0, str(REPO_ROOT / "tests" / "position" / "v2"))

import numpy as np  # noqa: E402

# ----------------------------------------------------------------------------
# Test data bundled in the repo.
# ----------------------------------------------------------------------------
DATA_DIR = REPO_ROOT / "tests" / "_data" / "sleap"
MODEL_DIR = (
    DATA_DIR / "model"
)  # legacy UNet: best_model.h5 + training_config.json
VIDEO_3FR = DATA_DIR / "small_robot_3_frame.mp4"  # 3 frames, 320x560
BODYPARTS = ["A", "B"]
NWB_STEM = "sleap_tutorial_sess"


def banner(msg):
    print("\n" + "=" * 70 + f"\n{msg}\n" + "=" * 70)


def main():
    from make_example_dlc_project import bootstrap_from_video_paths
    from spyglass.position.v2.estim import (
        PoseEstim,
        PoseEstimParams,
        PoseEstimSelection,
        PoseParams,
        PoseSelection,
        PoseV2,
    )
    from spyglass.position.v2.train import (
        Model,
        ModelParams,
        ModelSelection,
        Skeleton,
        default_pk_name,
    )
    from spyglass.position.v2.video import VidFileGroup

    # ------------------------------------------------------------------
    # Step 1: Bootstrap a Session + VideoFile from the SLEAP video.
    # ------------------------------------------------------------------
    banner("Step 1: Bootstrap session + video")
    nwb_file_name, inf_vid_path = bootstrap_from_video_paths(
        [VIDEO_3FR],
        nwb_stem=NWB_STEM,
        task_name="sleap_tutorial",
        camera_name="sleap_tutorial",
    )
    print(f"nwb_file_name = {nwb_file_name}")
    print(f"inference clip = {inf_vid_path}")

    # ------------------------------------------------------------------
    # Step 2: Register the pre-trained SLEAP model manually.
    # ------------------------------------------------------------------
    banner("Step 2: Register SLEAP model")

    # 2a. Skeleton with bodyparts A and B (match the model). The bodyparts
    # are not in the curated BodyPart table, so accept_new_bodyparts=True.
    skeleton_key = Skeleton().insert1(
        {
            "skeleton_id": "sleap_AB",
            "bodyparts": BODYPARTS,
            "edges": [("A", "B")],
        },
        check_duplicates=False,
        skip_duplicates=True,
        accept_new_bodyparts=True,
    )
    skeleton_id = skeleton_key["skeleton_id"]
    print(f"Skeleton: {skeleton_id} -> {Skeleton().get_bodyparts(skeleton_id)}")

    # 2b. ModelParams for the SLEAP tool, linked to the skeleton.
    model_params_key = ModelParams().insert1(
        {
            "tool": "SLEAP",
            "model_params_id": "sleap_tutorial",
            "params": {
                "model_type": "single_instance",
                "backbone": "unet",
            },
            "skeleton_id": skeleton_id,
        },
        skip_duplicates=True,
    )
    print(f"ModelParams: {model_params_key}")

    # 2c. VidFileGroup for the model's "training" video (reuse the clip).
    model_vid_group = VidFileGroup.create_from_files(
        video_files=[str(inf_vid_path)],
        description="sleap_tutorial_model_train_video",
        vid_group_id="sleap_train_grp",
    )
    print(f"Model VidFileGroup: {model_vid_group}")

    # 2d. ModelSelection pairing params + train video group.
    model_sel_key = {
        **model_params_key,
        **model_vid_group,
        "model_selection_id": default_pk_name(
            "ms-sleap", {"model_params_id": model_params_key["model_params_id"]}
        ),
    }
    ModelSelection().insert1(model_sel_key, skip_duplicates=True)
    print(f"ModelSelection: {model_sel_key['model_selection_id']}")

    # 2e. Insert the Model row directly (mirrors _import_dlc_model).
    model_id = "sleap_tutorial_model"
    stored_path = str(MODEL_DIR.resolve())
    if Model() & {"model_id": model_id}:
        model_key = (Model() & {"model_id": model_id}).fetch1("KEY")
    else:
        model_key = {
            **model_sel_key,
            "model_id": model_id,
            "model_path": stored_path,
        }
        Model().insert1(model_key, allow_direct_insert=True)
    print(f"Model: {model_id} -> {stored_path}")

    # ------------------------------------------------------------------
    # Step 3: Inference VidFileGroup (just the inference clip).
    # ------------------------------------------------------------------
    banner("Step 3: Inference VidFileGroup")
    inf_vid_group = VidFileGroup.create_from_files(
        video_files=[str(inf_vid_path)],
        description="sleap_tutorial_inference",
        vid_group_id="sleap_infer_grp",
    )
    vid_group_id = inf_vid_group["vid_group_id"]
    # Verify the group resolves to a registered NWB session.
    resolved = VidFileGroup().get_nwb_file(vid_group_id)
    print(f"Inference VidFileGroup: {vid_group_id} -> {resolved}")

    # ------------------------------------------------------------------
    # Step 4: PoseEstimParams + PoseEstimSelection + PoseEstim.populate.
    # ------------------------------------------------------------------
    banner("Step 4: PoseEstim (trigger inference)")
    estim_params_key = PoseEstimParams.insert_params(
        {"device": "cpu", "batch_size": 4},
        params_id="sleap_cpu",
        skip_duplicates=True,
    )
    print(f"PoseEstimParams: {estim_params_key}")

    estim_key = {
        "model_id": model_id,
        "vid_group_id": vid_group_id,
        **estim_params_key,
    }
    PoseEstimSelection().insert_estimation_task(
        estim_key,
        task_mode="trigger",
        skip_duplicates=True,
    )
    print(f"PoseEstimSelection: {estim_key}")

    PoseEstim().populate(estim_key, reserve_jobs=False)
    if not (PoseEstim() & estim_key):
        raise RuntimeError("PoseEstim.populate produced no entry")
    print("PoseEstim populated.")

    # ------------------------------------------------------------------
    # Step 5: PoseEstim.fetch1_dataframe.
    # ------------------------------------------------------------------
    banner("Step 5: PoseEstim.fetch1_dataframe")
    estim_df = (PoseEstim() & estim_key).fetch1_dataframe()
    print(f"shape = {estim_df.shape}")
    print(f"columns =\n{estim_df.columns.tolist()}")
    print(f"index (time) = {estim_df.index.values}")
    print(estim_df.head())

    # ------------------------------------------------------------------
    # Step 6: PoseParams + PoseSelection + PoseV2.populate.
    # ------------------------------------------------------------------
    banner("Step 6: PoseV2 (process pose)")
    PoseParams.insert_default(skip_duplicates=True)

    # Custom 2-pt params on bodyparts A, B (the default targets LED names).
    pose_params_id = "sleap_AB_2pt"
    PoseParams().insert_custom(
        params_name=pose_params_id,
        orient={
            "method": "two_pt",
            "bodypart1": "A",
            "bodypart2": "B",
            "interpolate": True,
            "smooth": False,
        },
        centroid={
            "method": "2pt",
            "points": {"point1": "A", "point2": "B"},
            # This tutorial has no camera calibration, so meters_per_pixel
            # falls back to 1.0 and coordinates are scaled pixels-times-100.
            # The A-B separation is ~100 px -> ~10,000 in these units, so the
            # threshold must be generous to avoid rejecting every frame.
            "max_LED_separation": 60000.0,
        },
        smoothing={
            "interpolate": True,
            "interp_params": {
                "max_pts_to_interp": 10,
                "max_cm_to_interp": 60000.0,
            },
            "smooth": True,
            "smoothing_params": {
                "method": "moving_avg",
                "smoothing_duration": 0.05,
            },
            "likelihood_thresh": 0.0,
            "velocity_smoothing_std_dev": 0.1,
        },
        skip_duplicates=True,
    )
    print(f"PoseParams: {pose_params_id}")

    pose_sel_key = {**estim_key, "pose_params_id": pose_params_id}
    PoseSelection().insert1(pose_sel_key, skip_duplicates=True)
    print(f"PoseSelection: {pose_sel_key}")

    PoseV2().populate(pose_sel_key, reserve_jobs=False)
    if not (PoseV2() & pose_sel_key):
        raise RuntimeError("PoseV2.populate produced no entry")
    print("PoseV2 populated.")

    # ------------------------------------------------------------------
    # Step 7: PoseV2.fetch1_dataframe.
    # ------------------------------------------------------------------
    banner("Step 7: PoseV2.fetch1_dataframe")
    pose_df = (PoseV2() & pose_sel_key).fetch1_dataframe()
    print(f"shape = {pose_df.shape}")
    print(f"columns = {pose_df.columns.tolist()}")
    print(pose_df)

    required = ["position_x", "position_y", "orientation", "speed"]
    missing = [c for c in required if c not in pose_df.columns]
    if missing:
        raise RuntimeError(f"PoseV2 dataframe missing columns: {missing}")
    for col in required:
        vals = pose_df[col].values
        if not np.all(np.isfinite(vals)):
            raise RuntimeError(f"PoseV2 column '{col}' has non-finite values")
    print("\nAll required columns present and finite:", required)

    banner("SUCCESS: SLEAP V2 pipeline ran end-to-end")


if __name__ == "__main__":
    main()
