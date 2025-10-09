from typing import Optional, Union, List, Dict, Any
import re


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




def generate_github_job_url(owner: str, repo: str, workflow_run_id: int, job_id: int) -> str:
    """
    Generates a GitHub URL for a specific job execution.
    
    :param owner: Repository owner
    :param repo: Repository name
    :param workflow_run_id: Workflow run ID
    :param job_id: Job ID
    :return: Full GitHub URL, or empty string if any parameter is missing
    
    Example:
    >>> generate_github_job_url("owner", "repo", 67890, 12345)
    'https://github.com/owner/repo/actions/runs/67890/job/12345'
    """
    if not all([owner, repo, workflow_run_id, job_id]):
        return ""
    return f"https://github.com/{owner}/{repo}/actions/runs/{workflow_run_id}/job/{job_id}"


def match_step_pattern(step_name: str, pattern: str) -> bool:
    """
    Matches a step name against a pattern with wildcard support.
    
    Supports '*' as a wildcard that matches any sequence of characters.
    
    :param step_name: The step name to match
    :param pattern: Pattern with * wildcard (e.g., "Build linux-x64-*")
    :return: True if the step name matches the pattern
    
    Examples:
    >>> match_step_pattern("Build linux-x64-app1", "Build linux-x64-*")
    True
    >>> match_step_pattern("Build linux-x64-app2", "Build linux-x64-*")
    True
    >>> match_step_pattern("Build apps", "Build linux-x64-*")
    False
    >>> match_step_pattern("Build apps", "Build apps")
    True
    """
    regex_pattern = pattern.replace('*', '.*')
    return bool(re.match(f"^{regex_pattern}$", step_name))


def analyze_repl_build_steps(steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Analyzes build steps for REPL Tests jobs, handling both legacy and new patterns.
    
    Legacy pattern: Single "Build apps" step
    New pattern: Multiple "Build linux-x64-{app_name}" steps
    
    :param steps: List of step dicts with 'name' and 'duration_ms' keys
    :return: Dict with 'build_type', 'total_build_duration_ms', 'build_steps'
    
    Example return values:
    {
        'build_type': 'legacy',
        'total_build_duration_ms': 120000,
        'build_steps': [{'name': 'Build apps', 'duration_ms': 120000}]
    }
    
    {
        'build_type': 'multi_step',
        'total_build_duration_ms': 125000,
        'build_steps': [
            {'name': 'Build linux-x64-app1', 'duration_ms': 60000},
            {'name': 'Build linux-x64-app2', 'duration_ms': 65000}
        ]
    }
    """
    legacy_pattern = "Build apps"
    new_pattern_prefix = "Build linux-x64-"
    
    # Check for legacy pattern
    legacy_steps = [s for s in steps if s.get('name') == legacy_pattern]
    if legacy_steps:
        return {
            'build_type': 'legacy',
            'total_build_duration_ms': legacy_steps[0].get('duration_ms', 0),
            'build_steps': legacy_steps
        }
    
    # Check for new pattern
    new_build_steps = [s for s in steps if s.get('name', '').startswith(new_pattern_prefix)]
    if new_build_steps:
        total_duration = sum(s.get('duration_ms', 0) for s in new_build_steps if s.get('duration_ms'))
        return {
            'build_type': 'multi_step',
            'total_build_duration_ms': total_duration,
            'build_steps': new_build_steps
        }
    
    # No recognized build pattern found
    return {
        'build_type': 'unknown',
        'total_build_duration_ms': 0,
        'build_steps': []
    }
