# swe_agent/trace.py
import json
from datetime import datetime, timezone
from pathlib import Path


class TraceWriter:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path

    def log(self, event: dict):
        if "ts" not in event:
            event["ts"] = datetime.now(timezone.utc).isoformat()
        with open(self._path, "a") as f:
            f.write(json.dumps(event) + "\n")
