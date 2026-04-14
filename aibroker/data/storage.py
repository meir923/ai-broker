from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
CACHE_DIR = DATA_DIR / "cache"


def ensure_data_dirs() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
