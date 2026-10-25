import json
from pathlib import Path


def load_items():
    return json.loads(Path("data/items.json").read_text(encoding="utf-8"))
