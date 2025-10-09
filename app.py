from flask import Flask, jsonify, request, render_template, Response
from datetime import datetime
from typing import Optional, List, Dict, Any
import numpy as np

from database import GHADatabase
from report_exporter import ReportExporter
from utils import generate_github_job_url, analyze_repl_build_steps

app = Flask(__name__)

# Configuration constants
DEFAULT_EXCLUDE_STATUSES = ['in_progress', 'queued']
ENABLE_FILTER_METADATA = True
MAX_JOB_EXECUTIONS_LIMIT = 1000

# In a real app, you might manage the DB connection differently (e.g., per-request context)
# For simplicity, we'll create a new instance per request or use a global one.
DB_PATH = "gha_metrics.db"

def get_db():
    """Opens a new database connection."""
    return GHADatabase(db_path=DB_PATH)

def parse_date(date_str: Optional[str]) -> Optional[datetime]:
    """Helper to parse ISO 8601 date strings from query params."""
    if not date_str:
        return None
    try:
        # Handles 'Z' suffix for UTC
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_conclusions_param(conclusions_param: Optional[str]) -> Optional[List[str]]:
    """
    Helper to parse comma-separated conclusions query parameter.
    
    :param conclusions_param: Comma-separated string of conclusions (e.g., 'success,failure')
    :return: List of conclusion strings, or None if parameter is empty
    """
    if not conclusions_param:
        return None
    conclusions = [c.strip() for c in conclusions_param.split(',') if c.strip()]
    return conclusions if conclusions else None


def parse_exclude_statuses_param(exclude_statuses_param: Optional[str]) -> List[str]:
    """
    Helper to parse exclude_statuses parameter with default value.
    
    :param exclude_statuses_param: Comma-separated string of statuses to exclude
    :return: List of status strings to exclude (defaults to ['in_progress', 'queued'])
    """
    if exclude_statuses_param is None:
        return ['in_progress', 'queued']
    
    if exclude_statuses_param == '':
        return []
    
    statuses = [s.strip() for s in exclude_statuses_param.split(',') if s.strip()]
    return statuses


def build_filter_metadata(conclusions: Optional[List[str]], exclude_statuses: List[str], 
                         excluded_count: Optional[int] = None) -> Dict[str, Any]:
    """
    Helper to construct metadata dict with applied filters.
    
    :param conclusions: List of conclusion filters applied
    :param exclude_statuses: List of excluded statuses
    :param excluded_count: Number of records excluded by filters (optional)
    :return: Dictionary containing filter metadata
    """
    metadata = {
        "filters_applied": {
            "conclusions": conclusions if conclusions else None,
            "excluded_statuses": exclude_statuses if exclude_statuses else None
        }
    }
    
    if excluded_count is not None:
        metadata["filters_applied"]["excluded_count"] = excluded_count
    
    return metadata

@app.route('/')
def dashboard():
    return render_template('dashboard.html')

@app.route('/api/workflows', methods=['GET'])
def get_workflows():
    """
    API endpoint to get detailed workflow run data.
    Query Parameters:
    - owner (str, required): Repository owner.
    - repo (str, required): Repository name.
    - workflow_id (str, required): Workflow file name (e.g., 'ci.yml').
    - start_date (str, optional): ISO 8601 format (e.g., '2024-01-01T00:00:00Z').
    - end_date (str, optional): ISO 8601 format.
    - conclusions (str, optional): Comma-separated list of conclusion values to filter by (e.g., 'success,failure').
    - exclude_statuses (str, optional): Comma-separated list of statuses to exclude (default: 'in_progress,queued').
    """
    owner = request.args.get('owner')
    repo = request.args.get('repo')
    workflow_id = request.args.get('workflow_id')

    if not all([owner, repo, workflow_id]):
        return jsonify({"error": "Missing required parameters: owner, repo, workflow_id"}), 400

    start_date = parse_date(request.args.get('start_date'))
    end_date = parse_date(request.args.get('end_date'))
    conclusions = parse_conclusions_param(request.args.get('conclusions'))
    exclude_statuses = parse_exclude_statuses_param(request.args.get('exclude_statuses'))

    try:
        with get_db() as db:
            runs = db.get_workflow_runs(
                owner=owner,
                repo=repo,
                workflow_id=workflow_id,
                start_date=start_date,
                end_date=end_date,
                conclusions=conclusions,
                exclude_statuses=exclude_statuses
            )
        
        # Handle empty result sets with informative metadata
        if not runs:
            metadata = build_filter_metadata(conclusions, exclude_statuses)
            return jsonify({
                "data": [],
                "metadata": {
                    **metadata,
                    "message": "No workflow runs match the specified filters"
                }
            }), 200
        
        return jsonify(runs)
    except ValueError as e:
        # Handle invalid filter parameters
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"An internal error occurred: {e}"}), 500


@app.route('/api/trends', methods=['GET'])
def get_trends():
    """
    API endpoint to get time-series trend data for workflow runs.
    Query Parameters:
    - owner (str, required): Repository owner.
    - repo (str, required): Repository name.
    - workflow_id (str, required): Workflow file name (e.g., 'ci.yml').
    - start_date (str, optional): ISO 8601 format.
    - end_date (str, optional): ISO 8601 format.
    - period (str, optional): 'day' or 'week'. Defaults to 'day'.
    - exclude_outliers (bool, optional): If true, exclude outliers from percentile and average success duration calculations.
    - conclusions (str, optional): Comma-separated list of conclusion values to filter by (e.g., 'success,failure').
    - exclude_statuses (str, optional): Comma-separated list of statuses to exclude (default: 'in_progress,queued').
    """
    owner = request.args.get('owner')
    repo = request.args.get('repo')
    workflow_id = request.args.get('workflow_id')

    if not all([owner, repo, workflow_id]):
        return jsonify({"error": "Missing required parameters: owner, repo, workflow_id"}), 400

    start_date = parse_date(request.args.get('start_date'))
    end_date = parse_date(request.args.get('end_date'))
    period = request.args.get('period', 'day')
    exclude_outliers = request.args.get('exclude_outliers', 'false').lower() == 'true'
    conclusions = parse_conclusions_param(request.args.get('conclusions'))
    exclude_statuses = parse_exclude_statuses_param(request.args.get('exclude_statuses'))

    if period not in ['day', 'week']:
        return jsonify({"error": "Invalid 'period' parameter. Must be 'day' or 'week'."}), 400

    try:
        with get_db() as db:
            trends_raw = db.get_time_series_metrics(
                owner=owner, repo=repo, workflow_id=workflow_id,
                start_date=start_date, end_date=end_date, period=period,
                conclusions=conclusions,
                exclude_statuses=exclude_statuses
            )

        trends = []
        for period_data in trends_raw:
            data = dict(period_data)  # Make a mutable copy
            success_durations_str = data.pop('success_durations_ms_list', None)
            all_durations_str = data.pop('all_durations_ms_list', None)

            p50 = p95 = p99 = None
            outlier_count = 0

            # Recalculate overall average duration if outliers are excluded
            if exclude_outliers and all_durations_str:
                all_durations = np.array([int(d) for d in all_durations_str.split(',') if d])
                if len(all_durations) > 1:
                    p25, p75 = np.percentile(all_durations, [25, 75])
                    iqr = p75 - p25
                    lower_bound = p25 - (1.5 * iqr)
                    upper_bound = p75 + (1.5 * iqr)
                    outliers_mask = (all_durations < lower_bound) | (all_durations > upper_bound)

                    filtered_durations = all_durations[~outliers_mask]
                    if len(filtered_durations) > 0:
                        data['avg_duration_ms'] = np.mean(filtered_durations)
                    else:
                        data['avg_duration_ms'] = None

            # The rest of the logic is for successful runs (percentiles, success average, outlier count)
            if success_durations_str:
                durations = np.array([int(d) for d in success_durations_str.split(',') if d])

                if len(durations) > 1:
                    # Calculate outliers based on original data before filtering
                    p25, p75 = np.percentile(durations, [25, 75])
                    iqr = p75 - p25
                    lower_bound = p25 - (1.5 * iqr)
                    upper_bound = p75 + (1.5 * iqr)
                    outliers_mask = (durations < lower_bound) | (durations > upper_bound)
                    outlier_count = int(np.sum(outliers_mask))

                    if exclude_outliers:
                        durations = durations[~outliers_mask]
                        # Recalculate average success duration if outliers are excluded
                        if len(durations) > 0:
                            data['avg_success_duration_ms'] = np.mean(durations)
                        else:
                            data['avg_success_duration_ms'] = None

                if len(durations) > 0:
                    p50, p95, p99 = np.percentile(durations, [50, 95, 99])

            data['p50_duration_ms'] = int(p50) if p50 is not None else None
            data['p95_duration_ms'] = int(p95) if p95 is not None else None
            data['p99_duration_ms'] = int(p99) if p99 is not None else None
            data['outlier_count'] = outlier_count

            trends.append(data)

        # Include filter metadata in response
        metadata = build_filter_metadata(conclusions, exclude_statuses)
        
        # Handle empty result sets with informative metadata
        if not trends:
            metadata["message"] = "No data matches the specified filters for the given time period"
        
        return jsonify({
            "data": trends,
            "metadata": metadata
        })
    except ValueError as e:
        # Handle invalid filter parameters
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"An internal error occurred: {e}"}), 500


@app.route('/api/jobs', methods=['GET'])
def get_job_metrics():
    """
    API endpoint to get aggregated metrics per job name for a given workflow.
    Query Parameters:
    - owner (str, required): Repository owner.
    - repo (str, required): Repository name.
    - workflow_id (str, required): Workflow file name (e.g., 'ci.yml').
    - start_date (str, required): ISO 8601 format.
    - end_date (str, required): ISO 8601 format.
    - conclusions (str, optional): Comma-separated list of conclusion values to filter by (e.g., 'success,failure').
    - exclude_statuses (str, optional): Comma-separated list of statuses to exclude (default: 'in_progress,queued').
    """
    owner = request.args.get('owner')
    repo = request.args.get('repo')
    workflow_id = request.args.get('workflow_id')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')

    if not all([owner, repo, workflow_id, start_date_str, end_date_str]):
        return jsonify({"error": "Missing required parameters: owner, repo, workflow_id, start_date, end_date"}), 400

    start_date = parse_date(start_date_str)
    end_date = parse_date(end_date_str)

    if not start_date or not end_date:
        return jsonify({"error": "Invalid date format. Please use ISO 8601 format."}), 400
    
    conclusions = parse_conclusions_param(request.args.get('conclusions'))
    exclude_statuses = parse_exclude_statuses_param(request.args.get('exclude_statuses'))

    try:
        with get_db() as db:
            raw_metrics = db.get_job_metrics(
                owner=owner, repo=repo, workflow_id=workflow_id,
                start_date=start_date, end_date=end_date,
                conclusions=conclusions,
                exclude_statuses=exclude_statuses
            )

        results = []
        for job_data in raw_metrics:
            total_runs = job_data['total_runs']
            successful_runs = job_data['successful_runs'] or 0

            success_rate = (successful_runs / total_runs * 100.0) if total_runs > 0 else 0.0

            p95_duration_ms = None
            durations_str = job_data.get('success_durations_ms_list')
            if durations_str:
                durations = np.array([int(d) for d in durations_str.split(',') if d])
                if len(durations) > 0:
                    p95 = np.percentile(durations, 95)
                    p95_duration_ms = int(p95)

            results.append({
                "job_name": job_data['job_name'],
                "total_runs": total_runs,
                "success_rate": round(success_rate, 1),
                "p95_duration_ms": p95_duration_ms
            })

        # Handle empty result sets with informative metadata
        if not results:
            metadata = build_filter_metadata(conclusions, exclude_statuses)
            return jsonify({
                "data": [],
                "metadata": {
                    **metadata,
                    "message": "No job metrics match the specified filters"
                }
            }), 200

        return jsonify(results)
    except ValueError as e:
        # Handle invalid filter parameters
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"An internal error occurred: {e}"}), 500


@app.route('/api/trends.csv', methods=['GET'])
def get_trends_csv():
    """
    API endpoint to get time-series trend data as a CSV file.
    Query Parameters are the same as /api/trends.
    """
    owner = request.args.get('owner')
    repo = request.args.get('repo')
    workflow_id = request.args.get('workflow_id')

    if not all([owner, repo, workflow_id]):
        return jsonify({"error": "Missing required parameters: owner, repo, workflow_id"}), 400

    start_date = parse_date(request.args.get('start_date'))
    end_date = parse_date(request.args.get('end_date'))
    period = request.args.get('period', 'day')
    exclude_outliers = request.args.get('exclude_outliers', 'false').lower() == 'true'
    conclusions = parse_conclusions_param(request.args.get('conclusions'))
    exclude_statuses = parse_exclude_statuses_param(request.args.get('exclude_statuses'))

    if period not in ['day', 'week']:
        return jsonify({"error": "Invalid 'period' parameter. Must be 'day' or 'week'."}), 400

    try:
        with get_db() as db:
            trends_raw = db.get_time_series_metrics(
                owner=owner, repo=repo, workflow_id=workflow_id,
                start_date=start_date, end_date=end_date, period=period,
                conclusions=conclusions,
                exclude_statuses=exclude_statuses
            )

        trends = []
        for period_data in trends_raw:
            data = dict(period_data)  # Make a mutable copy
            success_durations_str = data.pop('success_durations_ms_list', None)
            all_durations_str = data.pop('all_durations_ms_list', None)

            p50 = p95 = p99 = None
            outlier_count = 0

            # Recalculate overall average duration if outliers are excluded
            if exclude_outliers and all_durations_str:
                all_durations = np.array([int(d) for d in all_durations_str.split(',') if d])
                if len(all_durations) > 1:
                    p25, p75 = np.percentile(all_durations, [25, 75])
                    iqr = p75 - p25
                    lower_bound = p25 - (1.5 * iqr)
                    upper_bound = p75 + (1.5 * iqr)
                    outliers_mask = (all_durations < lower_bound) | (all_durations > upper_bound)

                    filtered_durations = all_durations[~outliers_mask]
                    if len(filtered_durations) > 0:
                        data['avg_duration_ms'] = np.mean(filtered_durations)
                    else:
                        data['avg_duration_ms'] = None

            # The rest of the logic is for successful runs (percentiles, success average, outlier count)
            if success_durations_str:
                durations = np.array([int(d) for d in success_durations_str.split(',') if d])

                if len(durations) > 1:
                    # Calculate outliers based on original data before filtering
                    p25, p75 = np.percentile(durations, [25, 75])
                    iqr = p75 - p25
                    lower_bound = p25 - (1.5 * iqr)
                    upper_bound = p75 + (1.5 * iqr)
                    outliers_mask = (durations < lower_bound) | (durations > upper_bound)
                    outlier_count = int(np.sum(outliers_mask))

                    if exclude_outliers:
                        durations = durations[~outliers_mask]
                        if len(durations) > 0:
                            data['avg_success_duration_ms'] = np.mean(durations)
                        else:
                            data['avg_success_duration_ms'] = None

                if len(durations) > 0:
                    p50, p95, p99 = np.percentile(durations, [50, 95, 99])

            data['p50_duration_ms'] = int(p50) if p50 is not None else None
            data['p95_duration_ms'] = int(p95) if p95 is not None else None
            data['p99_duration_ms'] = int(p99) if p99 is not None else None
            data['outlier_count'] = outlier_count

            trends.append(data)

        # Handle empty result sets
        if not trends:
            return jsonify({"error": "No data matches the specified filters for the given time period"}), 200

        # Build filter metadata for CSV header
        filter_metadata = {
            "owner": owner,
            "repo": repo,
            "workflow_id": workflow_id,
            "filters_applied": {
                "conclusions": conclusions if conclusions else [],
                "excluded_statuses": exclude_statuses if exclude_statuses else [],
            },
            "time_range": {
                "start_date": start_date.isoformat() if start_date else None,
                "end_date": end_date.isoformat() if end_date else None
            }
        }

        exporter = ReportExporter()
        csv_string = exporter.export_to_csv_string(trends, filter_metadata)

        return Response(
            csv_string,
            mimetype="text/csv",
            headers={"Content-disposition": "attachment; filename=trends.csv"}
        )
    except ValueError as e:
        # Handle invalid filter parameters
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"An internal error occurred: {e}"}), 500


@app.route('/api/jobs/slowest', methods=['GET'])
def get_slowest_jobs():
    """
    API endpoint to get the slowest jobs by P95 duration.
    Query Parameters:
    - owner (str, required): Repository owner.
    - repo (str, required): Repository name.
    - workflow_id (str, required): Workflow file name (e.g., 'ci.yml').
    - start_date (str, required): ISO 8601 format.
    - end_date (str, required): ISO 8601 format.
    - limit (int, optional): Number of jobs to return (default 10).
    - conclusions (str, optional): Comma-separated list of conclusion values.
    - exclude_statuses (str, optional): Comma-separated list of statuses to exclude (default: 'in_progress,queued').
    """
    owner = request.args.get('owner')
    repo = request.args.get('repo')
    workflow_id = request.args.get('workflow_id')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    limit = int(request.args.get('limit', 10))

    if not all([owner, repo, workflow_id, start_date_str, end_date_str]):
        return jsonify({"error": "Missing required parameters: owner, repo, workflow_id, start_date, end_date"}), 400

    start_date = parse_date(start_date_str)
    end_date = parse_date(end_date_str)

    if not start_date or not end_date:
        return jsonify({"error": "Invalid date format. Please use ISO 8601 format."}), 400
    
    conclusions = parse_conclusions_param(request.args.get('conclusions'))
    exclude_statuses = parse_exclude_statuses_param(request.args.get('exclude_statuses'))

    try:
        with get_db() as db:
            raw_metrics = db.get_slowest_jobs(
                owner=owner, repo=repo, workflow_id=workflow_id,
                start_date=start_date, end_date=end_date,
                limit=limit, conclusions=conclusions,
                exclude_statuses=exclude_statuses
            )

        results = []
        for job_data in raw_metrics:
            total_runs = job_data['total_runs']
            successful_runs = job_data['successful_runs'] or 0

            success_rate = (successful_runs / total_runs * 100.0) if total_runs > 0 else 0.0

            avg_duration_ms = job_data.get('avg_success_duration_ms')
            p95_duration_ms = None
            durations_str = job_data.get('success_durations_ms_list')
            if durations_str:
                durations = np.array([int(d) for d in durations_str.split(',') if d])
                if len(durations) > 0:
                    p95 = np.percentile(durations, 95)
                    p95_duration_ms = int(p95)

            results.append({
                "job_name": job_data['job_name'],
                "total_runs": total_runs,
                "success_rate": round(success_rate, 1),
                "avg_duration_ms": int(avg_duration_ms) if avg_duration_ms else None,
                "p95_duration_ms": p95_duration_ms
            })

        # Handle empty result sets with informative metadata
        if not results:
            metadata = build_filter_metadata(conclusions, exclude_statuses)
            return jsonify({
                "data": [],
                "metadata": {
                    **metadata,
                    "message": "No slowest jobs match the specified filters"
                }
            }), 200

        return jsonify(results)
    except ValueError as e:
        # Handle invalid filter parameters
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"An internal error occurred: {e}"}), 500


@app.route('/api/steps', methods=['GET'])
def get_steps():
    """
    API endpoint to get step metrics, optionally filtered by job.
    Query Parameters:
    - owner (str, required): Repository owner.
    - repo (str, required): Repository name.
    - workflow_id (str, required): Workflow file name (e.g., 'ci.yml').
    - start_date (str, required): ISO 8601 format.
    - end_date (str, required): ISO 8601 format.
    - job_name (str, optional): Filter by job name.
    - limit (int, optional): Limit results (default: no limit).
    - conclusions (str, optional): Comma-separated list of conclusion values.
    - exclude_statuses (str, optional): Comma-separated list of statuses to exclude (default: 'in_progress,queued').
    """
    owner = request.args.get('owner')
    repo = request.args.get('repo')
    workflow_id = request.args.get('workflow_id')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    job_name = request.args.get('job_name')
    limit = request.args.get('limit')

    if not all([owner, repo, workflow_id, start_date_str, end_date_str]):
        return jsonify({"error": "Missing required parameters: owner, repo, workflow_id, start_date, end_date"}), 400

    start_date = parse_date(start_date_str)
    end_date = parse_date(end_date_str)

    if not start_date or not end_date:
        return jsonify({"error": "Invalid date format. Please use ISO 8601 format."}), 400
    
    conclusions = parse_conclusions_param(request.args.get('conclusions'))
    exclude_statuses = parse_exclude_statuses_param(request.args.get('exclude_statuses'))

    try:
        with get_db() as db:
            if limit:
                raw_metrics = db.get_slowest_steps(
                    owner=owner, repo=repo, workflow_id=workflow_id,
                    start_date=start_date, end_date=end_date,
                    job_name=job_name, limit=int(limit),
                    conclusions=conclusions,
                    exclude_statuses=exclude_statuses
                )
            else:
                raw_metrics = db.get_step_metrics(
                    owner=owner, repo=repo, workflow_id=workflow_id,
                    start_date=start_date, end_date=end_date,
                    job_name=job_name, conclusions=conclusions,
                    exclude_statuses=exclude_statuses
                )

        results = []
        for step_data in raw_metrics:
            total_runs = step_data['total_runs']
            successful_runs = step_data['successful_runs'] or 0

            success_rate = (successful_runs / total_runs * 100.0) if total_runs > 0 else 0.0

            avg_duration_ms = step_data.get('avg_duration_ms')
            avg_success_duration_ms = step_data.get('avg_success_duration_ms')
            p95_duration_ms = None
            durations_str = step_data.get('success_durations_ms_list')
            if durations_str:
                durations = np.array([int(d) for d in durations_str.split(',') if d])
                if len(durations) > 0:
                    p95 = np.percentile(durations, 95)
                    p95_duration_ms = int(p95)

            results.append({
                "step_name": step_data['step_name'],
                "job_name": step_data['job_name'],
                "total_runs": total_runs,
                "success_rate": round(success_rate, 1),
                "avg_duration_ms": int(avg_duration_ms) if avg_duration_ms else None,
                "avg_success_duration_ms": int(avg_success_duration_ms) if avg_success_duration_ms else None,
                "p95_duration_ms": p95_duration_ms
            })

        # Handle empty result sets with informative metadata
        if not results:
            metadata = build_filter_metadata(conclusions, exclude_statuses)
            return jsonify({
                "data": [],
                "metadata": {
                    **metadata,
                    "message": "No step metrics match the specified filters"
                }
            }), 200

        return jsonify(results)
    except ValueError as e:
        # Handle invalid filter parameters
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"An internal error occurred: {e}"}), 500


@app.route('/api/jobs/<job_name>/trends', methods=['GET'])
def get_job_trends(job_name):
    """
    API endpoint to get time-series trend data for a specific job.
    Query Parameters:
    - owner (str, required): Repository owner.
    - repo (str, required): Repository name.
    - workflow_id (str, required): Workflow file name (e.g., 'ci.yml').
    - start_date (str, required): ISO 8601 format.
    - end_date (str, required): ISO 8601 format.
    - period (str, optional): 'day' or 'week' (default 'day').
    - conclusions (str, optional): Comma-separated list of conclusion values.
    - exclude_statuses (str, optional): Comma-separated list of statuses to exclude (default: 'in_progress,queued').
    """
    owner = request.args.get('owner')
    repo = request.args.get('repo')
    workflow_id = request.args.get('workflow_id')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    period = request.args.get('period', 'day')

    if not all([owner, repo, workflow_id, start_date_str, end_date_str]):
        return jsonify({"error": "Missing required parameters: owner, repo, workflow_id, start_date, end_date"}), 400

    start_date = parse_date(start_date_str)
    end_date = parse_date(end_date_str)

    if not start_date or not end_date:
        return jsonify({"error": "Invalid date format. Please use ISO 8601 format."}), 400
    
    if period not in ['day', 'week']:
        return jsonify({"error": "Invalid 'period' parameter. Must be 'day' or 'week'."}), 400
    
    conclusions = parse_conclusions_param(request.args.get('conclusions'))
    exclude_statuses = parse_exclude_statuses_param(request.args.get('exclude_statuses'))

    try:
        with get_db() as db:
            trends_raw = db.get_job_time_series(
                owner=owner, repo=repo, workflow_id=workflow_id,
                job_name=job_name,
                start_date=start_date, end_date=end_date,
                period=period, conclusions=conclusions,
                exclude_statuses=exclude_statuses
            )

        trends = []
        for period_data in trends_raw:
            data = dict(period_data)
            success_durations_str = data.pop('success_durations_ms_list', None)

            p50 = p95 = p99 = None
            if success_durations_str:
                durations = np.array([int(d) for d in success_durations_str.split(',') if d])
                if len(durations) > 0:
                    p50, p95, p99 = np.percentile(durations, [50, 95, 99])

            data['p50_duration_ms'] = int(p50) if p50 is not None else None
            data['p95_duration_ms'] = int(p95) if p95 is not None else None
            data['p99_duration_ms'] = int(p99) if p99 is not None else None

            trends.append(data)

        # Handle empty result sets with informative metadata
        if not trends:
            metadata = build_filter_metadata(conclusions, exclude_statuses)
            return jsonify({
                "data": [],
                "metadata": {
                    **metadata,
                    "message": "No job trend data matches the specified filters"
                }
            }), 200

        return jsonify(trends)
    except ValueError as e:
        # Handle invalid filter parameters
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"An internal error occurred: {e}"}), 500


@app.route('/api/jobs/<job_name>/executions', methods=['GET'])
def get_job_executions(job_name):
    """
    API endpoint to get individual job executions with GitHub links.
    
    Query Parameters:
    - owner (str, required): Repository owner.
    - repo (str, required): Repository name.
    - workflow_id (str, required): Workflow file name (e.g., 'ci.yml').
    - start_date (str, required): ISO 8601 format.
    - end_date (str, required): ISO 8601 format.
    - conclusions (str, optional): Comma-separated list of workflow conclusions to filter by.
    - exclude_statuses (str, optional): Comma-separated list of statuses to exclude (default: 'in_progress,queued').
    - limit (int, optional): Number of executions to return.
    - order_by (str, optional): Sort order - 'duration_desc', 'duration_asc', 'created_desc', 'created_asc' (default: 'duration_desc').
    
    Response:
    [
      {
        "job_id": 12345,
        "workflow_run_id": 67890,
        "job_name": "REPL Tests",
        "job_conclusion": "success",
        "job_duration_ms": 180000,
        "job_started_at": "2024-01-15T10:30:00Z",
        "job_completed_at": "2024-01-15T10:33:00Z",
        "workflow_conclusion": "success",
        "created_at": "2024-01-15T10:30:00Z",
        "github_url": "https://github.com/owner/repo/actions/runs/67890/job/12345"
      },
      ...
    ]
    """
    owner = request.args.get('owner')
    repo = request.args.get('repo')
    workflow_id = request.args.get('workflow_id')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    limit_str = request.args.get('limit')
    order_by = request.args.get('order_by', 'duration_desc')

    if not all([owner, repo, workflow_id, start_date_str, end_date_str]):
        return jsonify({"error": "Missing required parameters: owner, repo, workflow_id, start_date, end_date"}), 400

    start_date = parse_date(start_date_str)
    end_date = parse_date(end_date_str)

    if not start_date or not end_date:
        return jsonify({"error": "Invalid date format. Please use ISO 8601 format."}), 400
    
    # Parse limit parameter
    limit = None
    if limit_str:
        try:
            limit = int(limit_str)
            if limit <= 0:
                return jsonify({"error": "Limit must be a positive integer."}), 400
        except ValueError:
            return jsonify({"error": "Invalid limit parameter. Must be an integer."}), 400
    
    # Validate order_by parameter
    valid_order_by = ['duration_desc', 'duration_asc', 'created_desc', 'created_asc']
    if order_by not in valid_order_by:
        return jsonify({"error": f"Invalid order_by parameter. Must be one of: {', '.join(valid_order_by)}"}), 400
    
    conclusions = parse_conclusions_param(request.args.get('conclusions'))
    exclude_statuses = parse_exclude_statuses_param(request.args.get('exclude_statuses'))

    try:
        with get_db() as db:
            executions = db.get_job_executions_with_details(
                owner=owner,
                repo=repo,
                workflow_id=workflow_id,
                job_name=job_name,
                start_date=start_date,
                end_date=end_date,
                conclusions=conclusions,
                exclude_statuses=exclude_statuses,
                limit=limit,
                order_by=order_by
            )
        
        # Generate GitHub URLs for each execution
        results = []
        for execution in executions:
            result = dict(execution)
            # Generate GitHub URL
            result['github_url'] = generate_github_job_url(
                result['owner'],
                result['repo'],
                result['workflow_run_id'],
                result['job_id']
            )
            results.append(result)
        
        # Handle empty result sets with informative metadata
        if not results:
            metadata = build_filter_metadata(conclusions, exclude_statuses)
            return jsonify({
                "data": [],
                "metadata": {
                    **metadata,
                    "message": f"No job executions found for '{job_name}' matching the specified filters"
                }
            }), 200
        
        return jsonify(results)
    except ValueError as e:
        # Handle validation errors (e.g., invalid conclusions)
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"An internal error occurred: {e}"}), 500


@app.route('/api/jobs/<job_name>/build-steps', methods=['GET'])
def get_job_build_steps(job_name):
    """
    API endpoint to get aggregated build step metrics for REPL Tests jobs.
    
    Handles both legacy "Build apps" and new "Build linux-x64-*" patterns.
    
    Query Parameters:
    - owner (str, required): Repository owner.
    - repo (str, required): Repository name.
    - workflow_id (str, required): Workflow file name (e.g., 'tests.yaml').
    - start_date (str, required): ISO 8601 format.
    - end_date (str, required): ISO 8601 format.
    - conclusions (str, optional): Comma-separated list of workflow conclusions to filter by.
    - exclude_statuses (str, optional): Comma-separated list of statuses to exclude (default: 'in_progress,queued').
    
    Response:
    [
      {
        "workflow_run_id": 67890,
        "created_at": "2024-01-15T10:30:00Z",
        "build_type": "legacy",  // or "multi_step" or "unknown"
        "total_build_duration_ms": 120000,
        "build_steps": [
          {"step_name": "Build apps", "duration_ms": 120000, "step_conclusion": "success"}
        ]
      },
      {
        "workflow_run_id": 67891,
        "created_at": "2024-01-16T11:00:00Z",
        "build_type": "multi_step",
        "total_build_duration_ms": 125000,
        "build_steps": [
          {"step_name": "Build linux-x64-app1", "duration_ms": 60000, "step_conclusion": "success"},
          {"step_name": "Build linux-x64-app2", "duration_ms": 65000, "step_conclusion": "success"}
        ]
      }
    ]
    """
    owner = request.args.get('owner')
    repo = request.args.get('repo')
    workflow_id = request.args.get('workflow_id')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')

    if not all([owner, repo, workflow_id, start_date_str, end_date_str]):
        return jsonify({"error": "Missing required parameters: owner, repo, workflow_id, start_date, end_date"}), 400

    start_date = parse_date(start_date_str)
    end_date = parse_date(end_date_str)

    if not start_date or not end_date:
        return jsonify({"error": "Invalid date format. Please use ISO 8601 format."}), 400
    
    conclusions = parse_conclusions_param(request.args.get('conclusions'))
    exclude_statuses = parse_exclude_statuses_param(request.args.get('exclude_statuses'))

    try:
        with get_db() as db:
            # Query for both legacy "Build apps" and new "Build linux-x64-%" patterns
            # We'll query all steps and then filter/analyze them
            legacy_steps = db.get_step_metrics_with_pattern(
                owner=owner,
                repo=repo,
                workflow_id=workflow_id,
                job_name=job_name,
                step_pattern='Build apps',
                start_date=start_date,
                end_date=end_date,
                conclusions=conclusions,
                exclude_statuses=exclude_statuses
            )
            
            new_pattern_steps = db.get_step_metrics_with_pattern(
                owner=owner,
                repo=repo,
                workflow_id=workflow_id,
                job_name=job_name,
                step_pattern='Build linux-x64-%',
                start_date=start_date,
                end_date=end_date,
                conclusions=conclusions,
                exclude_statuses=exclude_statuses
            )
            
            # Combine all steps
            all_steps = legacy_steps + new_pattern_steps
            
            # Group steps by workflow_run_id
            workflow_runs = {}
            for step in all_steps:
                run_id = step['workflow_run_id']
                if run_id not in workflow_runs:
                    workflow_runs[run_id] = {
                        'workflow_run_id': run_id,
                        'created_at': step['created_at'],
                        'steps': []
                    }
                workflow_runs[run_id]['steps'].append({
                    'name': step['step_name'],
                    'duration_ms': step['duration_ms'],
                    'step_conclusion': step['step_conclusion'],
                    'step_started_at': step['step_started_at'],
                    'step_completed_at': step['step_completed_at']
                })
            
            # Analyze each workflow run's build steps
            results = []
            for run_id, run_data in workflow_runs.items():
                analysis = analyze_repl_build_steps(run_data['steps'])
                
                # Format build_steps for response
                formatted_steps = []
                for step in analysis['build_steps']:
                    formatted_steps.append({
                        'step_name': step['name'],
                        'duration_ms': step['duration_ms'],
                        'step_conclusion': step.get('step_conclusion'),
                        'step_started_at': step.get('step_started_at'),
                        'step_completed_at': step.get('step_completed_at')
                    })
                
                results.append({
                    'workflow_run_id': run_data['workflow_run_id'],
                    'created_at': run_data['created_at'],
                    'build_type': analysis['build_type'],
                    'total_build_duration_ms': analysis['total_build_duration_ms'],
                    'build_steps': formatted_steps
                })
            
            # Sort by created_at descending (most recent first)
            results.sort(key=lambda x: x['created_at'], reverse=True)
            
            # Handle empty result sets with informative metadata
            if not results:
                metadata = build_filter_metadata(conclusions, exclude_statuses)
                return jsonify({
                    "data": [],
                    "metadata": {
                        **metadata,
                        "message": f"No build steps found for '{job_name}' matching the specified filters"
                    }
                }), 200
            
            return jsonify(results)
    except ValueError as e:
        # Handle validation errors (e.g., invalid conclusions)
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"An internal error occurred: {e}"}), 500


if __name__ == '__main__':
    app.run(debug=True, port=5001)
