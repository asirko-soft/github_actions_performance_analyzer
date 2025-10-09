import argparse
import os
from datetime import datetime, timezone
from typing import List, Tuple

import matplotlib
matplotlib.use('Agg')  # Non-interactive backend suitable for servers/CI
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

from dotenv import load_dotenv

from github_api_client import GitHubApiClient
from data_collector import DataCollector
from stats_calculator import StatsCalculator
from utils import format_duration_hms


def parse_iso8601(dt_str: str) -> datetime:
    dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def filter_runs(runs, conclusions=None, exclude_statuses=None):
    """
    Filter workflow runs by conclusions and statuses.
    
    :param runs: List of WorkflowRun objects
    :param conclusions: List of conclusions to include (e.g., ['success', 'failure'])
    :param exclude_statuses: List of statuses to exclude (e.g., ['in_progress', 'queued'])
    :return: Filtered list of runs
    """
    if exclude_statuses is None:
        exclude_statuses = ['in_progress', 'queued']
    
    filtered = []
    for run in runs:
        # Exclude runs with NULL conclusion
        if run.conclusion is None:
            continue
        
        # Exclude runs with specified statuses
        if exclude_statuses and run.status in exclude_statuses:
            continue
        
        # Filter by conclusions if specified
        if conclusions and run.conclusion not in conclusions:
            continue
        
        filtered.append(run)
    
    return filtered


def build_filter_description(conclusions=None, exclude_statuses=None):
    """
    Build a human-readable description of applied filters.
    
    :param conclusions: List of conclusions to include
    :param exclude_statuses: List of statuses to exclude
    :return: String description of filters
    """
    parts = []
    
    if conclusions:
        conclusions_str = ', '.join(conclusions)
        parts.append(f"Conclusions: {conclusions_str}")
    
    if exclude_statuses:
        statuses_str = ', '.join(exclude_statuses)
        parts.append(f"Excluded statuses: {statuses_str}")
    elif exclude_statuses is None:
        parts.append("Excluded statuses: in_progress, queued")
    
    return ' | '.join(parts) if parts else 'No filters applied'


def aggregate_weekly_metrics(runs, conclusions=None, exclude_statuses=None) -> dict:
    """
    Group by ISO week and compute metrics via StatsCalculator per week.
    
    :param runs: List of WorkflowRun objects
    :param conclusions: List of conclusions to include (e.g., ['success', 'failure'])
    :param exclude_statuses: List of statuses to exclude (default: ['in_progress', 'queued'])
    :return: Dictionary of weekly metrics
    """
    # Apply filters
    filtered_runs = filter_runs(runs, conclusions, exclude_statuses)
    
    # Group by ISO week and compute metrics via StatsCalculator per week
    weekly_runs = {}
    for run in filtered_runs:
        isoyear, isoweek, _ = run.created_at.isocalendar()
        key = (isoyear, isoweek)
        weekly_runs.setdefault(key, []).append(run)
    calc = StatsCalculator()
    out = {}
    for key, wruns in weekly_runs.items():
        m = calc.calculate_run_statistics(wruns)
        out[key] = m
    return out


def aggregate_weekly_metrics_by_outcome(runs, conclusions=None, exclude_statuses=None) -> dict:
    """
    Group runs by week and outcome, then compute metrics for each outcome separately.
    
    :param runs: List of WorkflowRun objects
    :param conclusions: List of conclusions to include (e.g., ['success', 'failure'])
    :param exclude_statuses: List of statuses to exclude (default: ['in_progress', 'queued'])
    :return: Dictionary of weekly metrics by outcome
    """
    # Apply filters
    filtered_runs = filter_runs(runs, conclusions, exclude_statuses)
    
    weekly_by_outcome = {}
    for run in filtered_runs:
        isoyear, isoweek, _ = run.created_at.isocalendar()
        week_key = (isoyear, isoweek)
        outcome = run.conclusion or "other"
        key = (week_key, outcome)
        weekly_by_outcome.setdefault(key, []).append(run)
    
    calc = StatsCalculator()
    out = {}
    for (week_key, outcome), wruns in weekly_by_outcome.items():
        if not out.get(week_key):
            out[week_key] = {}
        m = calc.calculate_run_statistics(wruns)
        out[week_key][outcome] = m
    return out


def aggregate_weekly_step_metrics(runs, conclusions=None, exclude_statuses=None) -> dict:
    """
    Group steps by week and step name, compute duration trends.
    
    :param runs: List of WorkflowRun objects
    :param conclusions: List of conclusions to include (e.g., ['success', 'failure'])
    :param exclude_statuses: List of statuses to exclude (default: ['in_progress', 'queued'])
    :return: Dictionary of weekly step metrics
    """
    # Apply filters
    filtered_runs = filter_runs(runs, conclusions, exclude_statuses)
    
    weekly_steps = {}
    for run in filtered_runs:
        isoyear, isoweek, _ = run.created_at.isocalendar()
        week_key = (isoyear, isoweek)
        for job in run.jobs:
            for step in job.steps:
                if step.duration_ms is not None:
                    step_key = (week_key, step.name, step.conclusion or "other")
                    weekly_steps.setdefault(step_key, []).append(step.duration_ms)
    
    # Compute averages
    out = {}
    for (week_key, step_name, outcome), durations in weekly_steps.items():
        if not out.get(week_key):
            out[week_key] = {}
        if not out[week_key].get(step_name):
            out[week_key][step_name] = {}
        out[week_key][step_name][outcome] = {
            'avg_duration_ms': sum(durations) / len(durations),
            'count': len(durations),
            'min_duration_ms': min(durations),
            'max_duration_ms': max(durations)
        }
    return out


def plot_comparison(series_a: dict, series_b: dict, label_a: str, label_b: str, out_file: str, filter_desc: str = None):
    """
    Plot comparison of two time series with optional filter description.
    
    :param series_a: First time series data
    :param series_b: Second time series data
    :param label_a: Label for first series
    :param label_b: Label for second series
    :param out_file: Output file path
    :param filter_desc: Description of applied filters
    """
    # Build sorted union of week keys
    all_keys = sorted(set(series_a.keys()) | set(series_b.keys()))
    x_labels = [f"{y}-W{w:02d}" for (y, w) in all_keys]

    def extract(series, key):
        vals = []
        for k in all_keys:
            m = series.get(k)
            # Use None for missing data points instead of 0.0
            vals.append(getattr(m, key) if m else None)
        return vals

    # Metrics to plot: avg_duration_ms, avg_success_duration_ms, avg_failure_duration_ms, failure_rate_percent
    # Also success percentiles: p50, p90, p95
    fig, axes = plt.subplots(3, 2, figsize=(16, 12))
    title = 'Week-over-Week Workflow Metrics Comparison'
    if filter_desc:
        title += f'\n({filter_desc})'
    fig.suptitle(title)

    plots = [
        ("avg_duration_ms", "Avg Run Duration (h:m)"),
        ("avg_success_duration_ms", "Avg Success Duration (h:m)"),
        ("avg_failure_duration_ms", "Avg Failure Duration (h:m)"),
        ("failure_rate_percent", "Failure Rate (%)"),
        ("success_p50_duration_ms", "Success p50 (h:m)"),
        ("success_p95_duration_ms", "Success p95 (h:m)"),
    ]

    def minutes_to_hm(x, _pos=None):
        try:
            total_minutes = float(x)
        except Exception:
            return "0h 0m"
        hours = int(total_minutes // 60)
        minutes = int(round(total_minutes % 60))
        return f"{hours}h {minutes}m"

    for ax, (attr, ylabel) in zip(axes.flatten(), plots):
        a_vals = extract(series_a, attr)
        b_vals = extract(series_b, attr)
        if attr.endswith("_ms"):
            # convert ms -> minutes for better readability; axis will be formatted as Hh Mm
            # Handle None values (missing data points)
            a_plot = [v / 60000.0 if v is not None else None for v in a_vals]
            b_plot = [v / 60000.0 if v is not None else None for v in b_vals]
        else:
            a_plot = a_vals
            b_plot = b_vals
        ax.plot(x_labels, a_plot, marker='o', label=label_a)
        ax.plot(x_labels, b_plot, marker='o', label=label_b)
        ax.set_ylabel(ylabel)
        if attr.endswith("_ms"):
            ax.yaxis.set_major_formatter(FuncFormatter(minutes_to_hm))
        ax.grid(True, linestyle='--', alpha=0.3)
        for tick in ax.get_xticklabels():
            tick.set_rotation(45)
            tick.set_ha('right')
    axes[0][0].legend()
    plt.tight_layout(rect=[0, 0.03, 1, 0.95])
    plt.savefig(out_file)
    plt.close()


def plot_single_series(series: dict, out_file: str, filter_desc: str = None):
    """
    Plot a single time series with optional filter description.
    
    :param series: Time series data
    :param out_file: Output file path
    :param filter_desc: Description of applied filters
    """
    all_keys = sorted(series.keys())
    x_labels = [f"{y}-W{w:02d}" for (y, w) in all_keys]

    def extract(attr: str):
        vals = []
        for k in all_keys:
            m = series.get(k)
            # Use None for missing data points instead of 0.0
            vals.append(getattr(m, attr) if m else None)
        return vals

    fig, axes = plt.subplots(3, 2, figsize=(16, 12))
    title = 'Weekly Workflow Metrics'
    if filter_desc:
        title += f'\n({filter_desc})'
    fig.suptitle(title)

    plots = [
        ("avg_duration_ms", "Avg Run Duration (h:m)"),
        ("avg_success_duration_ms", "Avg Success Duration (h:m)"),
        ("avg_failure_duration_ms", "Avg Failure Duration (h:m)"),
        ("failure_rate_percent", "Failure Rate (%)"),
        ("success_p50_duration_ms", "Success p50 (h:m)"),
        ("success_p95_duration_ms", "Success p95 (h:m)"),
    ]

    def minutes_to_hm(x, _pos=None):
        try:
            total_minutes = float(x)
        except Exception:
            return "0h 0m"
        hours = int(total_minutes // 60)
        minutes = int(round(total_minutes % 60))
        return f"{hours}h {minutes}m"

    for ax, (attr, ylabel) in zip(axes.flatten(), plots):
        vals = extract(attr)
        if attr.endswith("_ms"):
            # Handle None values (missing data points)
            vals = [v / 60000.0 if v is not None else None for v in vals]
        ax.plot(x_labels, vals, marker='o')
        ax.set_ylabel(ylabel)
        if attr.endswith("_ms"):
            ax.yaxis.set_major_formatter(FuncFormatter(minutes_to_hm))
        ax.grid(True, linestyle='--', alpha=0.3)
        for tick in ax.get_xticklabels():
            tick.set_rotation(45)
            tick.set_ha('right')
    plt.tight_layout(rect=[0, 0.03, 1, 0.95])
    plt.savefig(out_file)
    plt.close()


def plot_trends_by_outcome(weekly_data: dict, out_file: str, filter_desc: str = None):
    """
    Plot trends separated by outcome type (success/failure/cancelled).
    
    :param weekly_data: Weekly data grouped by outcome
    :param out_file: Output file path
    :param filter_desc: Description of applied filters
    """
    all_weeks = sorted(weekly_data.keys())
    x_labels = [f"{y}-W{w:02d}" for (y, w) in all_weeks]
    
    outcomes = ['success', 'failure', 'cancelled']
    outcome_colors = {'success': 'green', 'failure': 'red', 'cancelled': 'orange'}
    
    def minutes_to_hm(x, _pos=None):
        try:
            total_minutes = float(x)
        except Exception:
            return "0h 0m"
        hours = int(total_minutes // 60)
        minutes = int(round(total_minutes % 60))
        return f"{hours}h {minutes}m"
    
    fig, axes = plt.subplots(2, 2, figsize=(16, 10))
    title = 'Weekly Performance Trends by Outcome Type'
    if filter_desc:
        title += f'\n({filter_desc})'
    fig.suptitle(title)
    
    # Plot 1: Average Duration by Outcome
    ax = axes[0, 0]
    for outcome in outcomes:
        durations = []
        for week in all_weeks:
            week_data = weekly_data.get(week, {})
            outcome_data = week_data.get(outcome)
            if outcome_data and outcome_data.avg_duration_ms > 0:
                durations.append(outcome_data.avg_duration_ms / 60000.0)  # convert to minutes
            else:
                durations.append(None)
        # Filter out None values for plotting
        valid_indices = [i for i, v in enumerate(durations) if v is not None]
        valid_labels = [x_labels[i] for i in valid_indices]
        valid_durations = [durations[i] for i in valid_indices]
        if valid_durations:
            ax.plot(valid_labels, valid_durations, marker='o', label=f'{outcome.title()} Runs', 
                   color=outcome_colors[outcome], linewidth=2)
    ax.set_ylabel('Avg Duration (h:m)')
    ax.set_title('Average Duration Trends by Outcome')
    ax.yaxis.set_major_formatter(FuncFormatter(minutes_to_hm))
    ax.grid(True, linestyle='--', alpha=0.3)
    ax.legend()
    for tick in ax.get_xticklabels():
        tick.set_rotation(45)
        tick.set_ha('right')
    
    # Plot 2: Run Count by Outcome  
    ax = axes[0, 1]
    for outcome in outcomes:
        counts = []
        for week in all_weeks:
            week_data = weekly_data.get(week, {})
            outcome_data = week_data.get(outcome)
            if outcome_data:
                counts.append(outcome_data.total_runs)
            else:
                counts.append(0)
        ax.plot(x_labels, counts, marker='o', label=f'{outcome.title()} Runs', 
               color=outcome_colors[outcome], linewidth=2)
    ax.set_ylabel('Number of Runs')
    ax.set_title('Run Count Trends by Outcome')
    ax.grid(True, linestyle='--', alpha=0.3)
    ax.legend()
    for tick in ax.get_xticklabels():
        tick.set_rotation(45)
        tick.set_ha('right')
    
    # Plot 3: Success Rate Over Time
    ax = axes[1, 0]
    success_rates = []
    for week in all_weeks:
        week_data = weekly_data.get(week, {})
        total_runs = sum(outcome_data.total_runs for outcome_data in week_data.values())
        success_runs = week_data.get('success', type('obj', (), {'total_runs': 0})).total_runs
        success_rate = (success_runs / total_runs * 100) if total_runs > 0 else 0
        success_rates.append(success_rate)
    ax.plot(x_labels, success_rates, marker='o', color='blue', linewidth=2)
    ax.set_ylabel('Success Rate (%)')
    ax.set_title('Weekly Success Rate Trend')
    ax.grid(True, linestyle='--', alpha=0.3)
    ax.set_ylim(0, 100)
    for tick in ax.get_xticklabels():
        tick.set_rotation(45)
        tick.set_ha('right')
    
    # Plot 4: P95 Duration for Successful Runs Only
    ax = axes[1, 1]
    p95_durations = []
    for week in all_weeks:
        week_data = weekly_data.get(week, {})
        success_data = week_data.get('success')
        if success_data and hasattr(success_data, 'success_p95_duration_ms') and success_data.success_p95_duration_ms > 0:
            p95_durations.append(success_data.success_p95_duration_ms / 60000.0)
        else:
            p95_durations.append(None)
    # Filter out None values
    valid_indices = [i for i, v in enumerate(p95_durations) if v is not None]
    valid_labels = [x_labels[i] for i in valid_indices]
    valid_p95 = [p95_durations[i] for i in valid_indices]
    if valid_p95:
        ax.plot(valid_labels, valid_p95, marker='o', color='darkgreen', linewidth=2)
    ax.set_ylabel('P95 Duration (h:m)')
    ax.set_title('P95 Duration Trend (Successful Runs Only)')
    ax.yaxis.set_major_formatter(FuncFormatter(minutes_to_hm))
    ax.grid(True, linestyle='--', alpha=0.3)
    for tick in ax.get_xticklabels():
        tick.set_rotation(45)
        tick.set_ha('right')
    
    plt.tight_layout(rect=[0, 0.03, 1, 0.95])
    plt.savefig(out_file)
    plt.close()


def plot_step_trends(weekly_step_data: dict, top_n: int, out_file: str, filter_desc: str = None):
    """
    Plot trends for top N slowest steps, separated by outcome.
    
    :param weekly_step_data: Weekly step data grouped by outcome
    :param top_n: Number of slowest steps to plot
    :param out_file: Output file path
    :param filter_desc: Description of applied filters
    """
    # Find top N slowest steps by average duration across all weeks
    step_avg_durations = {}
    for week_data in weekly_step_data.values():
        for step_name, outcomes in week_data.items():
            for outcome, metrics in outcomes.items():
                if outcome == 'success':  # Focus on successful steps
                    if step_name not in step_avg_durations:
                        step_avg_durations[step_name] = []
                    step_avg_durations[step_name].append(metrics['avg_duration_ms'])
    
    # Compute overall averages and get top N
    step_overall_avg = {step: sum(durations) / len(durations) 
                       for step, durations in step_avg_durations.items() if durations}
    top_steps = sorted(step_overall_avg.items(), key=lambda x: x[1], reverse=True)[:top_n]
    
    all_weeks = sorted(weekly_step_data.keys())
    x_labels = [f"{y}-W{w:02d}" for (y, w) in all_weeks]
    
    def minutes_to_hm(x, _pos=None):
        try:
            total_minutes = float(x)
        except Exception:
            return "0h 0m"
        hours = int(total_minutes // 60)
        minutes = int(round(total_minutes % 60))
        return f"{hours}h {minutes}m"
    
    # Create subplots - 2 columns for top N/2 rows
    rows = (top_n + 1) // 2
    fig, axes = plt.subplots(rows, 2, figsize=(20, 4 * rows))
    title = f'Top {top_n} Slowest Steps - Performance Trends by Outcome'
    if filter_desc:
        title += f'\n({filter_desc})'
    fig.suptitle(title)
    
    if rows == 1:
        axes = [axes]  # Handle single row case
    
    outcome_colors = {'success': 'green', 'failure': 'red', 'cancelled': 'orange'}
    
    for idx, (step_name, _) in enumerate(top_steps):
        row = idx // 2
        col = idx % 2
        ax = axes[row][col]
        
        for outcome in ['success', 'failure', 'cancelled']:
            durations = []
            for week in all_weeks:
                week_data = weekly_step_data.get(week, {})
                step_data = week_data.get(step_name, {})
                outcome_data = step_data.get(outcome)
                if outcome_data:
                    durations.append(outcome_data['avg_duration_ms'] / 60000.0)  # convert to minutes
                else:
                    durations.append(None)
            
            # Filter out None values for plotting
            valid_indices = [i for i, v in enumerate(durations) if v is not None]
            valid_labels = [x_labels[i] for i in valid_indices]
            valid_durations = [durations[i] for i in valid_indices]
            
            if valid_durations:
                ax.plot(valid_labels, valid_durations, marker='o', label=f'{outcome.title()}', 
                       color=outcome_colors[outcome], linewidth=2, markersize=4)
        
        ax.set_title(step_name[:40] + ('...' if len(step_name) > 40 else ''), fontsize=10)
        ax.set_ylabel('Avg Duration (h:m)')
        ax.yaxis.set_major_formatter(FuncFormatter(minutes_to_hm))
        ax.grid(True, linestyle='--', alpha=0.3)
        ax.legend(fontsize=8)
        for tick in ax.get_xticklabels():
            tick.set_rotation(45)
            tick.set_ha('right')
            tick.set_fontsize(8)
    
    # Hide any unused subplots
    for idx in range(len(top_steps), rows * 2):
        row = idx // 2
        col = idx % 2
        axes[row][col].set_visible(False)
    
    plt.tight_layout(rect=[0, 0.03, 1, 0.95])
    plt.savefig(out_file, dpi=150, bbox_inches='tight')
    plt.close()


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Visualize week-over-week performance for two date ranges")
    parser.add_argument("--token", type=str, default=os.getenv("GITHUB_TOKEN"))
    parser.add_argument("--owner", type=str, required=True)
    parser.add_argument("--repo", type=str, required=True)
    parser.add_argument("--workflow_id", type=str, required=True)
    parser.add_argument("--branch", type=str, default=None)
    parser.add_argument("--from_a", type=str, required=True, help="Start of first range, ISO8601 (e.g. 2025-07-01T00:00:00Z)")
    parser.add_argument("--to_a", type=str, required=True, help="End of first range, ISO8601")
    parser.add_argument("--from_b", type=str, required=True, help="Start of second range, ISO8601")
    parser.add_argument("--to_b", type=str, required=True, help="End of second range, ISO8601")
    parser.add_argument("--output", type=str, default="./reports/woverweek_comparison.png")
    parser.add_argument("--mode", type=str, choices=["combined", "overlay", "trends", "steps"], default="trends",
                        help="combined: one value per week; overlay: two series; trends: by outcome type; steps: step-level analysis")
    parser.add_argument("--top_steps", type=int, default=10, help="Number of slowest steps to analyze (for steps mode)")
    parser.add_argument("--conclusions", type=str, default=None, 
                        help="Comma-separated list of conclusions to include (e.g., 'success,failure')")
    parser.add_argument("--exclude_statuses", type=str, default=None,
                        help="Comma-separated list of statuses to exclude (default: 'in_progress,queued')")

    args = parser.parse_args()

    if not args.token:
        print("Error: GITHUB_TOKEN missing. Provide --token or set env var.")
        return

    os.makedirs(os.path.dirname(args.output), exist_ok=True)

    client = GitHubApiClient(args.token)
    collector = DataCollector(client)

    start_a = parse_iso8601(args.from_a)
    end_a = parse_iso8601(args.to_a)
    start_b = parse_iso8601(args.from_b)
    end_b = parse_iso8601(args.to_b)

    # Parse filter parameters
    conclusions = None
    if args.conclusions:
        conclusions = [c.strip() for c in args.conclusions.split(',')]
    
    exclude_statuses = None
    if args.exclude_statuses:
        exclude_statuses = [s.strip() for s in args.exclude_statuses.split(',')]
    
    # Build filter description for chart titles
    filter_desc = build_filter_description(conclusions, exclude_statuses)

    # Collect both ranges
    runs_a = collector.collect_workflow_data(args.owner, args.repo, args.workflow_id, args.branch, start_a, end_a)
    runs_b = collector.collect_workflow_data(args.owner, args.repo, args.workflow_id, args.branch, start_b, end_b)

    # Combine all runs for analysis
    runs_by_id = {}
    for r in runs_a + runs_b:
        runs_by_id[r.id] = r
    all_runs = list(runs_by_id.values())

    if args.mode == "overlay":
        series_a = aggregate_weekly_metrics(runs_a, conclusions, exclude_statuses)
        series_b = aggregate_weekly_metrics(runs_b, conclusions, exclude_statuses)
        plot_comparison(series_a, series_b, label_a=f"{args.from_a}..{args.to_a}", 
                       label_b=f"{args.from_b}..{args.to_b}", out_file=args.output, 
                       filter_desc=filter_desc)
    elif args.mode == "combined":
        combined = aggregate_weekly_metrics(all_runs, conclusions, exclude_statuses)
        plot_single_series(combined, out_file=args.output, filter_desc=filter_desc)
    elif args.mode == "trends":
        # Analyze trends by outcome type (green vs green, red vs red)
        weekly_by_outcome = aggregate_weekly_metrics_by_outcome(all_runs, conclusions, exclude_statuses)
        plot_trends_by_outcome(weekly_by_outcome, args.output, filter_desc=filter_desc)
    elif args.mode == "steps":
        # Step-level performance analysis
        weekly_step_data = aggregate_weekly_step_metrics(all_runs, conclusions, exclude_statuses)
        plot_step_trends(weekly_step_data, args.top_steps, args.output, filter_desc=filter_desc)

    print(f"Saved visualization to {args.output}")
    print(f"Applied filters: {filter_desc}")


if __name__ == "__main__":
    main()


