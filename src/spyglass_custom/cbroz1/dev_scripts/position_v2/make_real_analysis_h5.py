"""Regenerate ``real_robot.analysis.h5`` — the SLEAP real-data test fixture.

Runs SLEAP inference on the 166-frame ``small_robot.mp4`` clip from the SLEAP
test suite using the bundled ``minimal_robot.UNet.single_instance`` model, then
exports the predictions to a SLEAP ``.analysis.h5`` file.

Must be run in an environment that has SLEAP installed (e.g. the ``spyglass-sleap``
/ ``sl2`` env: sleap 1.6.3, sleap-nn 0.2.0, sleap-io 0.7.1). The pinned
coordinate guards in ``tests/position/v2/test_sleap_realdata.py`` (frame-0
A = (319.797, 47.925), 166 frames, nodes A/B, track ``track_0``) are tied to
these versions.

By default the fixture is written to ``tests/_data/sleap/`` (that dir is
git-ignored, which is why this reproducibility script lives here in tracked
``maintenance_scripts/`` instead).

Usage
-----
    conda run -n sl2 python maintenance_scripts/make_real_analysis_h5.py \
        --sleap-repo ~/wrk/sleap

The source video and model live in the SLEAP repo test data:
    <sleap-repo>/tests/data/videos/small_robot.mp4
    <sleap-repo>/tests/data/models/minimal_robot.UNet.single_instance
"""

import argparse
import subprocess
import tempfile
from pathlib import Path

SPYGLASS_DIR = "placeholder"
raise RuntimeError("Please replace SPYGLASS_DIR with your spyglass path")

# Repo root: this script lives in <repo>/maintenance_scripts/.
_FIXTURE_DIR = Path(SPYGLASS_DIR) / "tests" / "_data" / "sleap"


def main() -> None:
    """Run inference and export the ``.analysis.h5`` fixture.

    Parameters are read from the command line; see ``--help``.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sleap-repo",
        type=Path,
        default=Path("~/wrk/sleap").expanduser(),
        help="Path to a checkout of the SLEAP repo (for its test data).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=_FIXTURE_DIR / "real_robot.analysis.h5",
        help="Destination .analysis.h5 path.",
    )
    args = parser.parse_args()

    import sleap_io  # imported here so --help works without SLEAP installed

    data = args.sleap_repo / "tests" / "data"
    video = data / "videos" / "small_robot.mp4"
    model = data / "models" / "minimal_robot.UNet.single_instance"
    for path in (video, model):
        if not path.exists():
            raise FileNotFoundError(f"Missing SLEAP test asset: {path}")

    with tempfile.TemporaryDirectory() as tmp:
        slp_path = Path(tmp) / "robot.predictions.slp"
        subprocess.run(
            [
                "sleap-track",
                str(video),
                "-m",
                str(model),
                "--cpu",
                "-o",
                str(slp_path),
            ],
            check=True,
        )
        labels = sleap_io.load_file(str(slp_path))
        sleap_io.save_analysis_h5(labels, str(args.out))

    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
