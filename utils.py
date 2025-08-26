from typing import Optional, Union


def format_duration_hms(duration_ms: Optional[Union[int, float]]) -> str:
    """Format a duration in milliseconds into a human-readable H:M:S string.

    Examples:
    - 65000 -> "1m 5s"
    - 3661000 -> "1h 1m 1s"
    - None -> "-"
    """
    if duration_ms is None:
        return "-"
    try:
        total_seconds = int(round(float(duration_ms) / 1000.0))
    except Exception:
        return "-"

    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60

    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    # Always show seconds to avoid empty string
    parts.append(f"{seconds}s")
    return " ".join(parts)


def add_humanized_duration_fields(stats: dict, keys: list) -> dict:
    """Given a dictionary of stats and list of duration_ms keys, add *_hms for each.

    Returns the same dict instance after mutation for convenience.
    """
    for k in keys:
        h_key = k.replace("_ms", "_hms") if k.endswith("_ms") else f"{k}_hms"
        stats[h_key] = format_duration_hms(stats.get(k))
    return stats


