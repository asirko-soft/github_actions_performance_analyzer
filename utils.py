from typing import Optional, Union
import math


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


def percentile(sorted_values, p: float) -> Optional[float]:
    """Compute percentile p (0-100) from a pre-sorted list using linear interpolation."""
    if not sorted_values:
        return None
    if p <= 0:
        return float(sorted_values[0])
    if p >= 100:
        return float(sorted_values[-1])
    k = (len(sorted_values) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(sorted_values[int(k)])
    d0 = sorted_values[f] * (c - k)
    d1 = sorted_values[c] * (k - f)
    return float(d0 + d1)


