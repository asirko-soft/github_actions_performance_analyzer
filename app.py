from flask import Flask, jsonify, request, render_template, Response
from datetime import datetime
from typing import Optional
import numpy as np

from database import GHADatabase
from report_exporter import ReportExporter

app = Flask(__name__)

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
    """
    owner = request.args.get('owner')
    repo = request.args.get('repo')
    workflow_id = request.args.get('workflow_id')

    if not all([owner, repo, workflow_id]):
        return jsonify({"error": "Missing required parameters: owner, repo, workflow_id"}), 400

    start_date = parse_date(request.args.get('start_date'))
    end_date = parse_date(request.args.get('end_date'))

    try:
        with get_db() as db:
            runs = db.get_workflow_runs(
                owner=owner,
                repo=repo,
                workflow_id=workflow_id,
                start_date=start_date,
                end_date=end_date
            )
        return jsonify(runs)
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

    if period not in ['day', 'week']:
        return jsonify({"error": "Invalid 'period' parameter. Must be 'day' or 'week'."}), 400

    try:
        with get_db() as db:
            trends_raw = db.get_time_series_metrics(
                owner=owner, repo=repo, workflow_id=workflow_id,
                start_date=start_date, end_date=end_date, period=period
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

        return jsonify(trends)
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

    try:
        with get_db() as db:
            raw_metrics = db.get_job_metrics(
                owner=owner, repo=repo, workflow_id=workflow_id,
                start_date=start_date, end_date=end_date
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

        return jsonify(results)
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

    if period not in ['day', 'week']:
        return jsonify({"error": "Invalid 'period' parameter. Must be 'day' or 'week'."}), 400

    try:
        with get_db() as db:
            trends_raw = db.get_time_series_metrics(
                owner=owner, repo=repo, workflow_id=workflow_id,
                start_date=start_date, end_date=end_date, period=period
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

        exporter = ReportExporter()
        csv_string = exporter.export_to_csv_string(trends)

        return Response(
            csv_string,
            mimetype="text/csv",
            headers={"Content-disposition": "attachment; filename=trends.csv"}
        )
    except Exception as e:
        return jsonify({"error": f"An internal error occurred: {e}"}), 500


if __name__ == '__main__':
    app.run(debug=True, port=5000)
