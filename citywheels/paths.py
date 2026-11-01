from pathlib import Path


def default_data_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data"
