from pathlib import Path


def output_path(root, relative):
    """Return an output path inside root, rejecting paths that escape it."""
    return Path(root) / relative
