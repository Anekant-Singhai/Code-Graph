import numpy as np


def featurize(event: dict, feature_names: list[str]):
    values = [event.get(name, 0) for name in feature_names]
    return np.array(values)


def score_event(features, threshold: float) -> dict:
    score = float(features.sum()) / max(len(features), 1)
    if score >= threshold:
        return {"score": score, "alert": True}
    return {"score": score, "alert": False}
