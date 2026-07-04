import json
from pathlib import Path


def load_settings(path: str = "config/settings.json") -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_events(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_alerts(path: str, alerts: list[dict]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(alerts, handle, indent=2)
