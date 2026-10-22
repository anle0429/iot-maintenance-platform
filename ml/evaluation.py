"""Calculate classification metrics and choose a threshold using validation labels only."""

import math


def classification_metrics(labels: list[int], scores: list[float], threshold: float) -> dict:
    """Count true/false alarms and precision/recall without treating empty denominators as errors."""
    if len(labels) != len(scores) or not labels:
        raise ValueError("labels and scores must be nonempty and have equal length")
    if any(label not in (0, 1) for label in labels) or any(not math.isfinite(score) or not 0 <= score <= 1 for score in scores):
        raise ValueError("labels must be binary and scores finite probabilities")
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("threshold must be between 0 and 1")
    tp = sum(label == 1 and score >= threshold for label, score in zip(labels, scores))
    fp = sum(label == 0 and score >= threshold for label, score in zip(labels, scores))
    fn = sum(label == 1 and score < threshold for label, score in zip(labels, scores))
    tn = len(labels) - tp - fp - fn
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {"threshold": threshold, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": precision, "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0}


def choose_threshold(labels: list[int], scores: list[float]) -> dict:
    """Choose highest validation F1, then precision and threshold as deterministic tie breakers."""
    candidates = [classification_metrics(labels, scores, step / 10) for step in range(1, 10)]
    return max(candidates, key=lambda metrics: (metrics["f1"], metrics["precision"], metrics["threshold"]))
