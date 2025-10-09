import json
from typing import List, Dict, Any, Union
from collections import defaultdict
import numpy as np
from data_models import WorkflowRun, Job, Step, PerformanceMetrics

class StatsCalculator:
    def calculate_run_statistics(self, workflow_runs: List[WorkflowRun]) -> PerformanceMetrics:
        metrics = PerformanceMetrics()
        total_durations = defaultdict(list)
        success_durations: List[int] = []

        metrics.total_runs = len(workflow_runs)

        for run in workflow_runs:
            if run.conclusion == "success":
                metrics.successful_runs += 1
                if run.duration_ms is not None:
                    total_durations["success"].append(run.duration_ms)
                    success_durations.append(run.duration_ms)
            elif run.conclusion == "failure":
                metrics.failed_runs += 1
                if run.duration_ms is not None:
                    total_durations["failure"].append(run.duration_ms)
            elif run.conclusion == "cancelled":
                metrics.cancelled_runs += 1
                if run.duration_ms is not None:
                    total_durations["cancelled"].append(run.duration_ms)
            elif run.conclusion == "skipped":
                metrics.skipped_runs += 1
            else:
                metrics.other_runs += 1
                if run.duration_ms is not None:
                    total_durations["other"].append(run.duration_ms)
            
            if run.duration_ms is not None:
                total_durations["all"].append(run.duration_ms)

        if total_durations["all"]:
            metrics.avg_duration_ms = sum(total_durations["all"]) / len(total_durations["all"])
        if total_durations["success"]:
            metrics.avg_success_duration_ms = sum(total_durations["success"]) / len(total_durations["success"])
        if total_durations["failure"]:
            metrics.avg_failure_duration_ms = sum(total_durations["failure"]) / len(total_durations["failure"])
        if total_durations["cancelled"]:
            metrics.avg_cancelled_duration_ms = sum(total_durations["cancelled"]) / len(total_durations["cancelled"])

        # Calculate rate percentages
        if metrics.total_runs > 0:
            metrics.success_rate_percent = 100.0 * (metrics.successful_runs / metrics.total_runs)
            metrics.failure_rate_percent = 100.0 * (metrics.failed_runs / metrics.total_runs)
            metrics.skip_rate_percent = 100.0 * (metrics.skipped_runs / metrics.total_runs)
            metrics.cancellation_rate_percent = 100.0 * (metrics.cancelled_runs / metrics.total_runs)

        # Success min/max/percentiles
        if success_durations:
            metrics.success_min_duration_ms = float(min(success_durations))
            metrics.success_max_duration_ms = float(max(success_durations))
            p50, p90, p95 = np.percentile(success_durations, [50, 90, 95])
            metrics.success_p50_duration_ms = float(p50)
            metrics.success_p90_duration_ms = float(p90)
            metrics.success_p95_duration_ms = float(p95)

        # Outlier detection (2 standard deviations from mean)
        if len(success_durations) > 2:
            mean = np.mean(success_durations)
            std_dev = np.std(success_durations)
            metrics.outlier_threshold_lower = float(mean - 2 * std_dev)
            metrics.outlier_threshold_upper = float(mean + 2 * std_dev)
            metrics.outlier_count = sum(1 for d in success_durations 
                                       if d < metrics.outlier_threshold_lower or d > metrics.outlier_threshold_upper)

        return metrics

    def calculate_job_statistics(self, jobs: List[Job]) -> Dict[str, Any]:
        job_stats = defaultdict(lambda: {
            "total_runs": 0,
            "successful_runs": 0,
            "failed_runs": 0,
            "cancelled_runs": 0,
            "skipped_runs": 0,
            "other_runs": 0,
            "durations": defaultdict(list)
        })

        for job in jobs:
            job_stats[job.name]["total_runs"] += 1
            if job.conclusion == "success":
                job_stats[job.name]["successful_runs"] += 1
            elif job.conclusion == "failure":
                job_stats[job.name]["failed_runs"] += 1
            elif job.conclusion == "cancelled":
                job_stats[job.name]["cancelled_runs"] += 1
            elif job.conclusion == "skipped":
                job_stats[job.name]["skipped_runs"] += 1
            else:
                job_stats[job.name]["other_runs"] += 1
            
            if job.duration_ms is not None:
                job_stats[job.name]["durations"]["all"].append(job.duration_ms)
                if job.conclusion == "success":
                    job_stats[job.name]["durations"]["success"].append(job.duration_ms)
                elif job.conclusion == "failure":
                    job_stats[job.name]["durations"]["failure"].append(job.duration_ms)
                elif job.conclusion == "cancelled":
                    job_stats[job.name]["durations"]["cancelled"].append(job.duration_ms)
                else:
                    job_stats[job.name]["durations"]["other"].append(job.duration_ms)

        # Calculate averages, percentiles, and rate percentages
        for job_name, stats in job_stats.items():
            total_runs = stats["total_runs"]
            
            # Calculate rate percentages
            if total_runs > 0:
                stats["success_rate_percent"] = 100.0 * (stats["successful_runs"] / total_runs)
                stats["failure_rate_percent"] = 100.0 * (stats["failed_runs"] / total_runs)
                stats["skip_rate_percent"] = 100.0 * (stats["skipped_runs"] / total_runs)
                stats["cancellation_rate_percent"] = 100.0 * (stats["cancelled_runs"] / total_runs)
            else:
                stats["success_rate_percent"] = 0.0
                stats["failure_rate_percent"] = 0.0
                stats["skip_rate_percent"] = 0.0
                stats["cancellation_rate_percent"] = 0.0
            
            # Calculate averages and percentiles
            for status_type in ["all", "success", "failure", "cancelled", "other"]:
                if stats["durations"][status_type]:
                    durations = stats["durations"][status_type]
                    stats[f"avg_{status_type}_duration_ms"] = sum(durations) / len(durations)
                    
                    # Calculate percentiles for success durations
                    if status_type == "success" and len(durations) > 0:
                        p50, p95, p99 = np.percentile(durations, [50, 95, 99])
                        stats["p50_duration_ms"] = float(p50)
                        stats["p95_duration_ms"] = float(p95)
                        stats["p99_duration_ms"] = float(p99)
                else:
                    stats[f"avg_{status_type}_duration_ms"] = 0.0
            
            # Outlier detection for success durations (2 standard deviations from mean)
            success_durations = stats["durations"]["success"]
            if len(success_durations) > 2:
                mean = np.mean(success_durations)
                std_dev = np.std(success_durations)
                outlier_threshold_lower = float(mean - 2 * std_dev)
                outlier_threshold_upper = float(mean + 2 * std_dev)
                outlier_count = sum(1 for d in success_durations 
                                   if d < outlier_threshold_lower or d > outlier_threshold_upper)
                
                stats["outlier_count"] = outlier_count
                stats["outlier_threshold_lower"] = outlier_threshold_lower
                stats["outlier_threshold_upper"] = outlier_threshold_upper
            else:
                stats["outlier_count"] = 0
                stats["outlier_threshold_lower"] = None
                stats["outlier_threshold_upper"] = None
            
            del stats["durations"] # Remove raw durations list
        return dict(job_stats)

    def calculate_step_statistics(self, jobs: List[Job]) -> Dict[str, Any]:
        step_stats = defaultdict(lambda: {
            "total_runs": 0,
            "successful_runs": 0,
            "failed_runs": 0,
            "cancelled_runs": 0,
            "skipped_runs": 0,
            "other_runs": 0,
            "durations": defaultdict(list)
        })

        for job in jobs:
            for step in job.steps:
                step_stats[step.name]["total_runs"] += 1
                if step.conclusion == "success":
                    step_stats[step.name]["successful_runs"] += 1
                elif step.conclusion == "failure":
                    step_stats[step.name]["failed_runs"] += 1
                elif step.conclusion == "cancelled":
                    step_stats[step.name]["cancelled_runs"] += 1
                elif step.conclusion == "skipped":
                    step_stats[step.name]["skipped_runs"] += 1
                else:
                    step_stats[step.name]["other_runs"] += 1
                
                if step.duration_ms is not None:
                    step_stats[step.name]["durations"]["all"].append(step.duration_ms)
                    if step.conclusion == "success":
                        step_stats[step.name]["durations"]["success"].append(step.duration_ms)
                    elif step.conclusion == "failure":
                        step_stats[step.name]["durations"]["failure"].append(step.duration_ms)
                    elif step.conclusion == "cancelled":
                        step_stats[step.name]["durations"]["cancelled"].append(step.duration_ms)
                    else:
                        step_stats[step.name]["durations"]["other"].append(step.duration_ms)

        # Calculate averages, percentiles, and rate percentages
        for step_name, stats in step_stats.items():
            total_runs = stats["total_runs"]
            
            # Calculate rate percentages
            if total_runs > 0:
                stats["success_rate_percent"] = 100.0 * (stats["successful_runs"] / total_runs)
                stats["failure_rate_percent"] = 100.0 * (stats["failed_runs"] / total_runs)
                stats["skip_rate_percent"] = 100.0 * (stats["skipped_runs"] / total_runs)
                stats["cancellation_rate_percent"] = 100.0 * (stats["cancelled_runs"] / total_runs)
            else:
                stats["success_rate_percent"] = 0.0
                stats["failure_rate_percent"] = 0.0
                stats["skip_rate_percent"] = 0.0
                stats["cancellation_rate_percent"] = 0.0
            
            # Calculate averages and percentiles
            for status_type in ["all", "success", "failure", "cancelled", "other"]:
                if stats["durations"][status_type]:
                    durations = stats["durations"][status_type]
                    stats[f"avg_{status_type}_duration_ms"] = sum(durations) / len(durations)
                    
                    # Calculate percentiles for success durations
                    if status_type == "success" and len(durations) > 0:
                        p50, p95, p99 = np.percentile(durations, [50, 95, 99])
                        stats["p50_duration_ms"] = float(p50)
                        stats["p95_duration_ms"] = float(p95)
                        stats["p99_duration_ms"] = float(p99)
                else:
                    stats[f"avg_{status_type}_duration_ms"] = 0.0
            
            # Outlier detection for success durations (2 standard deviations from mean)
            success_durations = stats["durations"]["success"]
            if len(success_durations) > 2:
                mean = np.mean(success_durations)
                std_dev = np.std(success_durations)
                outlier_threshold_lower = float(mean - 2 * std_dev)
                outlier_threshold_upper = float(mean + 2 * std_dev)
                outlier_count = sum(1 for d in success_durations 
                                   if d < outlier_threshold_lower or d > outlier_threshold_upper)
                
                stats["outlier_count"] = outlier_count
                stats["outlier_threshold_lower"] = outlier_threshold_lower
                stats["outlier_threshold_upper"] = outlier_threshold_upper
            else:
                stats["outlier_count"] = 0
                stats["outlier_threshold_lower"] = None
                stats["outlier_threshold_upper"] = None
            
            del stats["durations"] # Remove raw durations list
        return dict(step_stats)

    def analyze_matrix_builds(self, jobs: List[Job]) -> Dict[str, Any]:
        matrix_stats = defaultdict(lambda: {
            "total_runs": 0,
            "successful_runs": 0,
            "failed_runs": 0,
            "cancelled_runs": 0,
            "other_runs": 0,
            "durations": defaultdict(list)
        })

        for job in jobs:
            if job.matrix_config:
                # Convert matrix config dict to a sorted tuple of items for consistent hashing
                # This ensures the key is the same regardless of dictionary insertion order
                matrix_key = tuple(sorted(job.matrix_config.items()))
                
                matrix_stats[matrix_key]["total_runs"] += 1
                if job.conclusion == "success":
                    matrix_stats[matrix_key]["successful_runs"] += 1
                elif job.conclusion == "failure":
                    matrix_stats[matrix_key]["failed_runs"] += 1
                elif job.conclusion == "cancelled":
                    matrix_stats[matrix_key]["cancelled_runs"] += 1
                else:
                    matrix_stats[matrix_key]["other_runs"] += 1
                
                if job.duration_ms is not None:
                    matrix_stats[matrix_key]["durations"]["all"].append(job.duration_ms)
                    if job.conclusion == "success":
                        matrix_stats[matrix_key]["durations"]["success"].append(job.duration_ms)
                    elif job.conclusion == "failure":
                        matrix_stats[matrix_key]["durations"]["failure"].append(job.duration_ms)
                    elif job.conclusion == "cancelled":
                        matrix_stats[matrix_key]["durations"]["cancelled"].append(job.duration_ms)
                    else:
                        matrix_stats[matrix_key]["durations"]["other"].append(job.duration_ms)

        # Calculate averages and convert tuple key back to dict for readability
        formatted_matrix_stats = {}
        for matrix_key, stats in matrix_stats.items():
            # Convert tuple back to dict for output
            matrix_config_dict = dict(matrix_key)
            # Ensure the string representation of the dictionary is consistently sorted
            sorted_matrix_config_str = json.dumps(matrix_config_dict, sort_keys=True)
            for status_type in ["all", "success", "failure", "cancelled", "other"]:
                if stats["durations"][status_type]:
                    stats[f"avg_{status_type}_duration_ms"] = sum(stats["durations"][status_type]) / len(stats["durations"][status_type])
                else:
                    stats[f"avg_{status_type}_duration_ms"] = 0.0
            del stats["durations"]
            formatted_matrix_stats[sorted_matrix_config_str] = stats # Use sorted string representation as key

        return formatted_matrix_stats

    def calculate_advanced_metrics(self, workflow_runs: List[WorkflowRun]) -> Dict[str, Any]:
        """
        Calculates advanced statistical metrics for a list of workflow runs.
        This includes success/failure rates, and detailed duration analysis for successful runs
        using numpy for calculations like standard deviation and percentiles.

        :param workflow_runs: A list of WorkflowRun data model objects.
        :return: A dictionary containing advanced metrics.
        """
        total_runs = len(workflow_runs)
        if total_runs == 0:
            return {"total_runs": 0}

        runs_by_conclusion = defaultdict(list)
        for run in workflow_runs:
            if run.conclusion:
                runs_by_conclusion[run.conclusion].append(run)

        successful_runs = len(runs_by_conclusion.get("success", []))
        failed_runs = len(runs_by_conclusion.get("failure", []))

        metrics: Dict[str, Any] = {
            "total_runs": total_runs,
            "successful_runs": successful_runs,
            "failed_runs": failed_runs,
            "cancelled_runs": len(runs_by_conclusion.get("cancelled", [])),
            "success_rate_percent": (successful_runs / total_runs) * 100 if total_runs > 0 else 0.0,
            "failure_rate_percent": (failed_runs / total_runs) * 100 if total_runs > 0 else 0.0,
            "duration_stats": {}
        }

        success_durations = [run.duration_ms for run in runs_by_conclusion.get("success", []) if run.duration_ms is not None]

        if success_durations:
            data = np.array(success_durations)
            p25, p50, p75, p90, p95, p99 = np.percentile(data, [25, 50, 75, 90, 95, 99])
            iqr = p75 - p25
            lower_bound = p25 - (1.5 * iqr)
            upper_bound = p75 + (1.5 * iqr)

            metrics["duration_stats"] = {
                "count": len(data),
                "mean": np.mean(data),
                "std_dev": np.std(data),
                "min": np.min(data),
                "p25": p25,
                "median": p50,
                "p75": p75,
                "p90": p90,
                "p95": p95,
                "p99": p99,
                "max": np.max(data),
                "iqr": iqr,
                "iqr_lower_bound": lower_bound,
                "iqr_upper_bound": upper_bound,
            }
        return metrics

if __name__ == '__main__':
    # Example Usage (requires dummy data or actual collected data)
    from datetime import datetime, timedelta
    import json # Import json for consistent string representation in dummy data

    # Create some dummy data for demonstration
    dummy_steps_success = [
        Step(name="Checkout", status="completed", conclusion="success", number=1, started_at=datetime.now() - timedelta(seconds=10), completed_at=datetime.now() - timedelta(seconds=8)),
        Step(name="Build", status="completed", conclusion="success", number=2, started_at=datetime.now() - timedelta(seconds=8), completed_at=datetime.now() - timedelta(seconds=3)),
    ]
    dummy_steps_failure = [
        Step(name="Checkout", status="completed", conclusion="success", number=1, started_at=datetime.now() - timedelta(seconds=12), completed_at=datetime.now() - timedelta(seconds=10)),
        Step(name="Test", status="completed", conclusion="failure", number=2, started_at=datetime.now() - timedelta(seconds=10), completed_at=datetime.now() - timedelta(seconds=5)),
    ]
    dummy_steps_cancelled = [
        Step(name="Checkout", status="completed", conclusion="success", number=1, started_at=datetime.now() - timedelta(seconds=15), completed_at=datetime.now() - timedelta(seconds=13)),
        Step(name="Deploy", status="completed", conclusion="cancelled", number=2, started_at=datetime.now() - timedelta(seconds=13), completed_at=datetime.now() - timedelta(seconds=7)),
    ]

    dummy_jobs = [
        Job(id=1, name="Build Job", status="completed", conclusion="success", started_at=datetime.now() - timedelta(minutes=5), completed_at=datetime.now() - timedelta(minutes=1), workflow_run_id=101, steps=dummy_steps_success),
        Job(id=2, name="Test Job", status="completed", conclusion="failure", started_at=datetime.now() - timedelta(minutes=7), completed_at=datetime.now() - timedelta(minutes=2), workflow_run_id=102, steps=dummy_steps_failure),
        Job(id=3, name="Deploy Job", status="completed", conclusion="cancelled", started_at=datetime.now() - timedelta(minutes=6), completed_at=datetime.now() - timedelta(minutes=1), workflow_run_id=103, steps=dummy_steps_cancelled),
        Job(id=4, name="Build Job", status="completed", conclusion="success", started_at=datetime.now() - timedelta(minutes=4), completed_at=datetime.now() - timedelta(minutes=0), workflow_run_id=104, steps=dummy_steps_success, matrix_config={"os": "ubuntu", "node": "16"}),
        Job(id=5, name="Build Job", status="completed", conclusion="failure", started_at=datetime.now() - timedelta(minutes=8), completed_at=datetime.now() - timedelta(minutes=3), workflow_run_id=105, steps=dummy_steps_failure, matrix_config={"os": "windows", "node": "14"}),
    ]

    dummy_workflow_runs = [
        WorkflowRun(id=101, name="CI", status="completed", conclusion="success", created_at=datetime.now() - timedelta(minutes=10), updated_at=datetime.now() - timedelta(minutes=1), event="push", head_branch="main", run_number=1, jobs=[dummy_jobs[0]]),
        WorkflowRun(id=102, name="CI", status="completed", conclusion="failure", created_at=datetime.now() - timedelta(minutes=12), updated_at=datetime.now() - timedelta(minutes=2), event="push", head_branch="main", run_number=2, jobs=[dummy_jobs[1]]),
        WorkflowRun(id=103, name="CI", status="completed", conclusion="cancelled", created_at=datetime.now() - timedelta(minutes=11), updated_at=datetime.now() - timedelta(minutes=1), event="push", head_branch="main", run_number=3, jobs=[dummy_jobs[2]]),
        WorkflowRun(id=104, name="CI", status="completed", conclusion="success", created_at=datetime.now() - timedelta(minutes=9), updated_at=datetime.now() - timedelta(minutes=0), event="push", head_branch="main", run_number=4, jobs=[dummy_jobs[3]]),
        WorkflowRun(id=105, name="CI", status="completed", conclusion="failure", created_at=datetime.now() - timedelta(minutes=13), updated_at=datetime.now() - timedelta(minutes=3), event="push", head_branch="main", run_number=5, jobs=[dummy_jobs[4]]),
    ]

    calculator = StatsCalculator()

    print("\n--- Workflow Run Statistics ---")
    run_metrics = calculator.calculate_run_statistics(dummy_workflow_runs)
    print(run_metrics)

    print("\n--- Job Statistics ---")
    job_metrics = calculator.calculate_job_statistics(dummy_jobs)
    for job_name, stats in job_metrics.items():
        print(f"Job: {job_name}, Stats: {stats}")

    print("\n--- Step Statistics ---")
    step_metrics = calculator.calculate_step_statistics(dummy_jobs)
    for step_name, stats in step_metrics.items():
        print(f"Step: {step_name}, Stats: {stats}")

    print("\n--- Matrix Build Statistics ---")
    matrix_metrics = calculator.analyze_matrix_builds(dummy_jobs)
    for matrix_config, stats in matrix_metrics.items():
        print(f"Matrix Config: {matrix_config}, Stats: {stats}")

    print("\n--- Advanced Workflow Run Statistics ---")
    advanced_metrics = calculator.calculate_advanced_metrics(dummy_workflow_runs)
    print(advanced_metrics)


