import json
from pathlib import Path


def set_port(path, port):
    """Update server.port in an existing JSON config, preserving other fields."""
    Path(path).write_text(json.dumps({"server": {"port": port}}), encoding="utf-8")
