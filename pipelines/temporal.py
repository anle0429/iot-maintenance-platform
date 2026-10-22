"""Provide small independent oracles for label boundaries, availability and temporal splits."""

from datetime import datetime, timedelta


def label_snapshot(machine_id: str, at: datetime, status: str, failures: list[dict],
                   maintenance: list[dict], observation_end: datetime) -> tuple[int | None, str]:
    """Label only operating, fully observed one-hour horizons without maintenance intervention."""
    end = at + timedelta(hours=1)
    if status != "operating":
        return None, "stopped"
    if end >= observation_end:
        return None, "censored"
    if any(event["machine_id"] == machine_id and at < event["start_ts"] <= end for event in maintenance):
        return None, "maintenance"
    return int(any(event["machine_id"] == machine_id and at < event["failure_start_ts"] <= end for event in failures)), "eligible"


def available_window(readings: list[dict], at: datetime, seconds: int = 600) -> list[dict]:
    """Select measurements in (t-window,t] that were already received by t."""
    start = at - timedelta(seconds=seconds)
    return [row for row in readings if start < row["event_ts"] <= at and row["received_ts"] <= at]


def temporal_split(at: datetime, label_available: datetime, train_end: datetime, validation_end: datetime) -> str | None:
    """Purge labels not yet available before the next partition, rather than split randomly."""
    if at < train_end:
        return "train" if label_available < train_end else None
    if at < validation_end:
        return "validation" if label_available < validation_end else None
    return "test"
