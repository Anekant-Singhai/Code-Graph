from src.io import load_events, load_settings, write_alerts
from src.model import featurize, score_event


def run_pipeline() -> None:
    settings = load_settings("config/settings.json")
    events = load_events(settings["input"]["path"])
    alerts = []

    for event in events:
        features = featurize(event, settings["model"]["features"])
        result = score_event(features, settings["model"]["threshold"])
        if result["alert"]:
            alerts.append({**event, **result})

    write_alerts(settings["output"]["path"], alerts)


if __name__ == "__main__":
    run_pipeline()
