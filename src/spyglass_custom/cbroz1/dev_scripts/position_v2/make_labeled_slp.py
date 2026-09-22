"""Regenerate ``small_robot_labeled.slp`` — the SLEAP continued-training fixture.

Loads the 2-frame, single-instance (skeleton A/B) ``small_robot_minimal.slp``
from the SLEAP test suite and re-saves it with the labeled frames' images
**embedded**, so the ``.slp`` is self-contained (no external video needed) when
``sleap-train`` uses it as ground truth in
``tests/position/v2/test_sleap_continue_live.py``.

Must be run in an environment that has SLEAP installed (e.g. the ``spyglass-sleap``
/ ``sl2`` env: sleap-io 0.7.1).

By default the fixture is written to ``tests/_data/sleap/`` (that dir is
git-ignored, which is why this reproducibility script lives here in tracked
``maintenance_scripts/`` instead).

Usage
-----
    conda run -n sl2 python maintenance_scripts/make_labeled_slp.py \
        --sleap-repo ~/wrk/sleap
"""

import argparse
from pathlib import Path

# Repo root: this script lives in <repo>/maintenance_scripts/.
_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "_data" / "sleap"


def main() -> None:
    """Embed images into the minimal labeled ``.slp`` and write the fixture.

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
        default=_FIXTURE_DIR / "small_robot_labeled.slp",
        help="Destination .slp path.",
    )
    args = parser.parse_args()

    import sleap_io  # imported here so --help works without SLEAP installed

    data = args.sleap_repo / "tests" / "data"
    src = data / "slp_hdf5" / "small_robot_minimal.slp"
    video = data / "videos" / "small_robot.mp4"
    for path in (src, video):
        if not path.exists():
            raise FileNotFoundError(f"Missing SLEAP test asset: {path}")

    labels = sleap_io.load_file(str(src))
    # The source .slp stores the video path relative to the SLEAP repo root;
    # repoint it at the absolute path so embedding can read the frames from
    # any working directory.
    labels.videos[0].replace_filename(str(video), open=True)
    # embed="user" bakes the user-labeled frames' images into the .slp
    sleap_io.save_file(labels, str(args.out), embed="user")

    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
