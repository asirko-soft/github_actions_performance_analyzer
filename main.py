import argparse
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

from github_api_client import GitHubApiClient
from data_collector import DataCollector
from stats_calculator import StatsCalculator
from report_exporter import ReportExporter
from utils import add_humanized_duration_fields

def main():
    load_dotenv() # Load environment variables from .env file

    parser = argparse.ArgumentParser(description="Analyze GitHub Actions workflow performance.")
    parser.add_argument("--token", type=str, default=os.getenv("GITHUB_TOKEN"),
                        help="GitHub Personal Access Token. Can also be set via GITHUB_TOKEN environment variable.")
    parser.add_argument("--owner", type=str, required=True, help="Repository owner (user or organization).")
    parser.add_argument("--repo", type=str, required=True, help="Repository name.")
    parser.add_argument("--workflow_id", type=str, required=True,
                        help="Workflow ID or workflow file name (e.g., main.yml).")
    parser.add_argument("--branch", type=str, default=None, help="Optional: Filter by branch name.")
    parser.add_argument("--weeks", type=int, default=4, help="Number of past weeks to analyze.")
    parser.add_argument("--output_dir", type=str, default="./reports",
                        help="Directory to save the reports.")

    args = parser.parse_args()

    if not args.token:
        print("Error: GitHub Personal Access Token not provided. "
              "Please set GITHUB_TOKEN environment variable or use --token argument.")
        return

    # Ensure output directory exists
    os.makedirs(args.output_dir, exist_ok=True)

    client = GitHubApiClient(args.token)
    collector = DataCollector(client)
    calculator = StatsCalculator()
    exporter = ReportExporter()

    print(f"Starting analysis for workflow \'{args.workflow_id}\' in {args.owner}/{args.repo}...")

    all_workflow_runs = []
    # Calculate the overall start and end dates for the analysis period
    overall_end_date = datetime.utcnow()
    overall_start_date = overall_end_date - timedelta(weeks=args.weeks)

    print(f"Collecting data for the period from {overall_start_date.strftime('%Y-%m-%d')} to {overall_end_date.strftime('%Y-%m-%d')}")

    # Collect all data within the overall date range first
    runs_in_period = collector.collect_workflow_data(
        args.owner, args.repo, args.workflow_id, args.branch,
        start_date=overall_start_date, end_date=overall_end_date
    )
    all_workflow_runs.extend(runs_in_period)
    print(f"Collected {len(runs_in_period)} runs within the specified {args.weeks} weeks.")

    if not all_workflow_runs:
        print("No workflow runs found for the specified criteria. Exiting.")
        return

    print(f"Total collected workflow runs: {len(all_workflow_runs)}")

    # Calculate overall statistics
    print("Calculating overall workflow run statistics...")
    overall_run_metrics = calculator.calculate_run_statistics(all_workflow_runs)
    print("Overall Run Metrics:", overall_run_metrics)

    # Extract all jobs from all collected workflow runs
    all_jobs = []
    for run in all_workflow_runs:
        all_jobs.extend(run.jobs)
    
    if not all_jobs:
        print("No jobs found in the collected workflow runs. Exiting.")
        return

    # Calculate job-level statistics
    print("Calculating job-level statistics...")
    job_metrics = calculator.calculate_job_statistics(all_jobs)
    print("Job Metrics:", job_metrics)

    # Calculate step-level statistics
    print("Calculating step-level statistics...")
    step_metrics = calculator.calculate_step_statistics(all_jobs)
    print("Step Metrics:", step_metrics)

    # Analyze matrix builds
    print("Analyzing matrix build statistics...")
    matrix_metrics = calculator.analyze_matrix_builds(all_jobs)
    print("Matrix Metrics:", matrix_metrics)

    # Prepare data for export (add human readable duration fields)
    overall_dict = overall_run_metrics.__dict__.copy()
    add_humanized_duration_fields(overall_dict, [
        "avg_duration_ms",
        "avg_success_duration_ms",
        "avg_failure_duration_ms",
        "avg_cancelled_duration_ms",
    ])
    # Add humanized for success distribution
    add_humanized_duration_fields(overall_dict, [
        "success_min_duration_ms",
        "success_max_duration_ms",
        "success_p50_duration_ms",
        "success_p90_duration_ms",
        "success_p95_duration_ms",
    ])

    # Enrich job metrics
    enriched_job_metrics = {}
    for job_name, stats in job_metrics.items():
        enriched = stats.copy()
        add_humanized_duration_fields(enriched, [
            "avg_all_duration_ms",
            "avg_success_duration_ms",
            "avg_failure_duration_ms",
            "avg_cancelled_duration_ms",
            "avg_other_duration_ms",
        ])
        enriched_job_metrics[job_name] = enriched

    # Enrich step metrics
    enriched_step_metrics = {}
    for step_name, stats in step_metrics.items():
        enriched = stats.copy()
        add_humanized_duration_fields(enriched, [
            "avg_all_duration_ms",
            "avg_success_duration_ms",
            "avg_failure_duration_ms",
            "avg_cancelled_duration_ms",
            "avg_other_duration_ms",
        ])
        enriched_step_metrics[step_name] = enriched

    # Enrich matrix metrics
    enriched_matrix_metrics = {}
    for matrix_key, stats in matrix_metrics.items():
        enriched = stats.copy()
        add_humanized_duration_fields(enriched, [
            "avg_all_duration_ms",
            "avg_success_duration_ms",
            "avg_failure_duration_ms",
            "avg_cancelled_duration_ms",
            "avg_other_duration_ms",
        ])
        enriched_matrix_metrics[matrix_key] = enriched

    report_data = {
        "overall_workflow_metrics": overall_dict,
        "job_metrics": enriched_job_metrics,
        "step_metrics": enriched_step_metrics,
        "matrix_metrics": enriched_matrix_metrics
    }

    # Export results
    json_output_path = os.path.join(args.output_dir, f"{args.workflow_id}_performance_report.json")
    exporter.export_to_json(report_data, json_output_path)

    # Optionally, export job and step metrics to CSV for easier visualization in spreadsheets
    if enriched_job_metrics:
        job_metrics_list = [{**{"job_name": k}, **v} for k, v in enriched_job_metrics.items()]
        csv_job_output_path = os.path.join(args.output_dir, f"{args.workflow_id}_job_metrics.csv")
        exporter.export_to_csv(job_metrics_list, csv_job_output_path)

    if enriched_step_metrics:
        step_metrics_list = [{**{"step_name": k}, **v} for k, v in enriched_step_metrics.items()]
        csv_step_output_path = os.path.join(args.output_dir, f"{args.workflow_id}_step_metrics.csv")
        exporter.export_to_csv(step_metrics_list, csv_step_output_path)

    print("Analysis complete. Reports saved to", args.output_dir)

if __name__ == "__main__":
    main()


