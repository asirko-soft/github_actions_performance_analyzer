from flask import Flask, jsonify, request, render_template, Response
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import numpy as np
import os
import threading

from database import GHADatabase
from report_exporter import ReportExporter
from utils import generate_github_job_url, analyze_repl_build_steps
from fetch_task_manager import FetchTaskManager, execute_fetch_task
from stats_calculator import StatsCalculator
from config_manager import ConfigManager

app = Flask(__name__)
print("DEBUG: app.py loaded! ----------------------------------------", flush=True)

# Configuration constants
DEFAULT_EXCLUDE_STATUSES = ['in_progress', 'queued']
ENABLE_FILTER_METADATA = True
MAX_JOB_EXECUTIONS_LIMIT = 1000

# In a real app, you might manage the DB connection differently (e.g., per-request context)
# For simplicity, we'll create a new instance per request or use a global one.
DB_PATH = os.environ.get('DB_PATH', '/app/data/gha_metrics.db')

# Initialize global task manager for fetch operations
task_manager = FetchTaskManager()

# Initialize configuration manager for token management
config_manager = ConfigManager("/app/data/config.json")

def get_db():
    """Opens a new database connection."""
    if 'db' not in g:
        db_path = os.getenv("DB_PATH", "/app/data/gha_metrics.db")
        g.db = GHADatabase(db_path)
        g.db.connect()
    return g.db

@app.teardown_appcontext
def close_db(error):
    """Closes the database connection at the end of the request."""
    db = g.pop('db', None)
    if db is not None:
        db.close()

def parse_date(date_str: Optional[str]) -> Optional[datetime]:
    if not date_str:
        return None
    try:
        # Handle ISO format from JS (e.g., 2023-10-27T00:00:00.000Z)
        return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    except ValueError:
        return None


def validate_fetch_params(data: Dict[str, Any]) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Validate parameters for fetch operations.
    
    Returns:
        tuple: (is_valid, error_message, error_type)
    """
    # Check required parameters
    required_fields = ['owner', 'repo', 'workflow_id', 'start_date', 'end_date']
    missing_fields = [field for field in required_fields if not data.get(field)]
    
    if missing_fields:
        return (False, 
                f"Missing required parameters: {', '.join(missing_fields)}", 
                "validation")
    
    # Validate date format
    try:
        start_date = datetime.fromisoformat(data['start_date'].replace('Z', '+00:00'))
        end_date = datetime.fromisoformat(data['end_date'].replace('Z', '+00:00'))
    except (ValueError, AttributeError) as e:
        return (False, 
                "Invalid date format. Dates must be in ISO 8601 format (e.g., '2024-01-01T00:00:00Z')", 
                "validation")
    
    # Validate date range
    if end_date <= start_date:
        return (False, 
                "Invalid date range. End date must be after start date", 
                "validation")
    
    # Check if start date is not too far in the future
    now = datetime.now(start_date.tzinfo)
    if start_date > now:
        return (False, 
                "Start date cannot be in the future", 
                "validation")
    
    # Validate string parameters are not empty
    if not data['owner'].strip():
        return (False, "Owner cannot be empty", "validation")
    if not data['repo'].strip():
        return (False, "Repository name cannot be empty", "validation")
    if not data['workflow_id'].strip():
        return (False, "Workflow ID cannot be empty", "validation")
    
    return (True, None, None)


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

@app.route('/api/config', methods=['GET'])
def get_config():
    """
    API endpoint to retrieve default configuration for workflow data fetching.
    
    Returns:
    - JSON object with default configuration:
      - owner: Repository owner (from GITHUB_OWNER env var or default)
      - repo: Repository name (from GITHUB_REPO env var or default)
      - workflow_id: Workflow file name (from GITHUB_WORKFLOW_ID env var or default)
      - token_configured: Whether a GitHub token is configured
      - token_source: Source of the token ('environment', 'stored', or 'none')
    """
    config = {
        "owner": os.getenv("GITHUB_OWNER", "project-chip"),
        "repo": os.getenv("GITHUB_REPO", "connectedhomeip"),
        "workflow_id": os.getenv("GITHUB_WORKFLOW_ID", "tests.yaml"),
        "token_configured": config_manager.is_token_configured(),
        "token_source": config_manager.get_token_source()
    }
    return jsonify(config)

@app.route('/api/config/token', methods=['POST'])
def set_token():
    """
    API endpoint to set GitHub token via web interface.
    
    Request Body (JSON):
    - token (str, required): GitHub personal access token
    
    Returns:
    - 200: Token configured successfully
    - 400: Invalid request or token format
    - 500: Internal error
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                "error": "Request body must be JSON",
                "error_type": "validation"
            }), 400
        
        token = data.get('token')
        if not token:
            return jsonify({
                "error": "Missing required parameter: token",
                "error_type": "validation"
            }), 400
        
        # Validate token format (basic check)
        token = token.strip()
        if not token:
            return jsonify({
                "error": "Token cannot be empty",
                "error_type": "validation"
            }), 400
        
        # Validate token format (GitHub tokens start with ghp_, github_pat_, or gho_)
        if not (token.startswith('ghp_') or token.startswith('github_pat_') or token.startswith('gho_')):
            return jsonify({
                "error": "Invalid token format. GitHub tokens should start with 'ghp_', 'github_pat_', or 'gho_'",
                "error_type": "validation"
            }), 400
        
        # Validate token with GitHub API
        from github_api_client import GitHubApiClient
        try:
            client = GitHubApiClient(token)
            is_valid, error_msg, error_type = client.validate_token()
            
            if not is_valid:
                status_code = 401 if error_type == "authentication" else 500
                return jsonify({
                    "error": error_msg,
                    "error_type": error_type
                }), status_code
        except Exception as e:
            return jsonify({
                "error": f"Failed to validate token: {str(e)}",
                "error_type": "validation"
            }), 400
        
        # Store token using ConfigManager
        success = config_manager.set_github_token(token)
        
        if success:
            return jsonify({
                "success": True,
                "message": "GitHub token configured successfully"
            }), 200
        else:
            return jsonify({
                "error": "Failed to store token",
                "error_type": "internal"
            }), 500
    
    except Exception as e:
        return jsonify({
            "error": f"Internal server error: {str(e)}",
            "error_type": "internal"
        }), 500

@app.route('/api/config/token/status', methods=['GET'])
def get_token_status():
    """
    API endpoint to check if GitHub token is configured.
    
    Returns:
    - JSON object with:
      - configured: Whether a token is configured
      - source: Source of the token ('environment', 'stored', or 'none')
      - valid: Whether the token is valid (requires API call)
    """
    try:
        configured = config_manager.is_token_configured()
        source = config_manager.get_token_source()
        
        # Check if token is valid by attempting to use it
        valid = False
        if configured:
            token = config_manager.get_github_token()
            if token:
                from github_api_client import GitHubApiClient
                try:
                    client = GitHubApiClient(token)
                    is_valid, _, _ = client.validate_token()
                    valid = is_valid
                except Exception:
                    valid = False
        
        return jsonify({
            "configured": configured,
            "source": source,
            "valid": valid
        }), 200
    
    except Exception as e:
        return jsonify({
            "error": f"Internal server error: {str(e)}",
            "error_type": "internal"
        }), 500

@app.route('/api/config/token', methods=['DELETE'])
def remove_token():
    """
    API endpoint to remove stored GitHub token.
    
    Returns:
    - 200: Token removed successfully
    - 500: Internal error
    """
    try:
        success = config_manager.remove_github_token()
        
        if success:
            return jsonify({
                "success": True,
                "message": "GitHub token removed successfully"
            }), 200
        else:
            return jsonify({
                "error": "Failed to remove token",
                "error_type": "internal"
            }), 500
    
    except Exception as e:
        return jsonify({
            "error": f"Internal server error: {str(e)}",
            "error_type": "internal"
        }), 500

@app.route('/api/fetch/preview', methods=['POST'])
def fetch_preview():
    """
    API endpoint to preview workflow data fetch by counting workflows in date range.
    
    Request Body (JSON):
    - owner (str, required): Repository owner
    - repo (str, required): Repository name
    - workflow_id (str, required): Workflow file name
    - start_date (str, required): ISO 8601 start date
    - end_date (str, required): ISO 8601 end date
    
    Returns:
    - JSON object with:
      - workflow_count: Number of workflows in the date range
      - date_range: Validated start and end dates
    
    Error Responses:
    - 400: Missing or invalid parameters
    - 401/403: GitHub authentication errors
    - 500: API or internal errors
    """
    try:
        # Parse request body
        data = request.get_json()
        if not data:
            return jsonify({
                "error": "Request body must be JSON",
                "error_type": "validation",
                "details": "Content-Type must be application/json"
            }), 400
        
        # Validate parameters using helper function
        is_valid, error_msg, error_type = validate_fetch_params(data)
        if not is_valid:
            return jsonify({
                "error": error_msg,
                "error_type": error_type,
                "details": error_msg
            }), 400
        
        # Extract validated parameters
        owner = data['owner'].strip()
        repo = data['repo'].strip()
        workflow_id = data['workflow_id'].strip()
        start_date = datetime.fromisoformat(data['start_date'].replace('Z', '+00:00'))
        end_date = datetime.fromisoformat(data['end_date'].replace('Z', '+00:00'))
        
        # Check for GitHub token using ConfigManager
        token = config_manager.get_github_token()
        if not token:
            return jsonify({
                "error": "GitHub token not configured",
                "error_type": "authentication",
                "details": "Configure GitHub token via web interface or set GITHUB_TOKEN environment variable"
            }), 401
        
        # Query GitHub API for a quick preview estimate
        from github_api_client import GitHubApiClient
        
        try:
            client = GitHubApiClient(token)
            
            # Validate token before proceeding
            is_valid, error_msg, error_type = client.validate_token()
            if not is_valid:
                status_code = 401 if error_type == "authentication" else 500
                return jsonify({
                    "error": error_msg,
                    "error_type": error_type,
                    "details": error_msg
                }), status_code
            
            # For preview, do a quick sample to estimate total count
            # Sample the first week and extrapolate
            sample_end = min(start_date + timedelta(days=7), end_date)
            total_days = (end_date - start_date).days
            sample_days = (sample_end - start_date).days
            
            if sample_days == 0:
                sample_days = 1
            
            # Fetch sample week
            created_after = start_date.strftime('%Y-%m-%dT%H:%M:%SZ')
            created_before = sample_end.strftime('%Y-%m-%dT%H:%M:%SZ')
            
            sample_runs = client.get_workflow_runs(
                owner=owner,
                repo=repo,
                workflow_id=workflow_id,
                created_after=created_after,
                created_before=created_before
            )
            
            sample_count = len(sample_runs)
            
            # If sample is less than 1000, we got all runs for that period
            # Extrapolate to estimate total
            if sample_count < 1000:
                # Simple extrapolation based on sample
                estimated_count = int((sample_count / sample_days) * total_days)
                is_estimate = total_days > sample_days
            else:
                # Sample hit the limit, so we know there are at least this many
                # Extrapolate but mark as minimum estimate
                estimated_count = int((1000 / sample_days) * total_days)
                is_estimate = True
            
            response = {
                "workflow_count": estimated_count,
                "date_range": {
                    "start": start_date.isoformat(),
                    "end": end_date.isoformat()
                },
                "is_estimate": is_estimate
            }
            
            if is_estimate:
                response["note"] = f"Estimated based on {sample_days}-day sample. Actual fetch will count all workflows accurately."
            
            return jsonify(response)
            
        except Exception as e:
            error_str = str(e)
            
            # Check for authentication errors
            if '401' in error_str or 'Unauthorized' in error_str:
                return jsonify({
                    "error": "GitHub authentication failed",
                    "error_type": "authentication",
                    "details": "GitHub token is invalid or expired. Please check your GITHUB_TOKEN"
                }), 401
            
            if '403' in error_str or 'Forbidden' in error_str:
                return jsonify({
                    "error": "Insufficient permissions",
                    "error_type": "authentication",
                    "details": "GitHub token lacks required permissions. Ensure it has 'repo' or 'actions:read' scope"
                }), 403
            
            # Generic API error
            return jsonify({
                "error": "GitHub API error",
                "error_type": "api",
                "details": error_str
            }), 500
    
    except Exception as e:
        return jsonify({
            "error": "Internal server error",
            "error_type": "internal",
            "details": str(e)
        }), 500

@app.route('/api/fetch/start', methods=['POST'])
def fetch_start():
    """
    API endpoint to initiate asynchronous workflow data fetch operation.
    
    Request Body (JSON):
    - owner (str, required): Repository owner
    - repo (str, required): Repository name
    - workflow_id (str, required): Workflow file name
    - start_date (str, required): ISO 8601 start date
    - end_date (str, required): ISO 8601 end date
    - skip_incomplete (bool, optional): Whether to skip incomplete workflows (default: False)
    
    Returns:
    - JSON object with:
      - task_id: Unique identifier for the background task
      - status: Initial task status ('pending')
    
    Error Responses:
    - 400: Missing or invalid parameters
    """
    try:
        # Parse request body
        data = request.get_json()
        if not data:
            return jsonify({
                "error": "Request body must be JSON",
                "error_type": "validation",
                "details": "Content-Type must be application/json"
            }), 400
        
        # Validate parameters using helper function
        is_valid, error_msg, error_type = validate_fetch_params(data)
        if not is_valid:
            return jsonify({
                "error": error_msg,
                "error_type": error_type,
                "details": error_msg
            }), 400
        
        # Extract validated parameters
        owner = data['owner'].strip()
        repo = data['repo'].strip()
        workflow_id = data['workflow_id'].strip()
        start_date_str = data['start_date']
        end_date_str = data['end_date']
        skip_incomplete = data.get('skip_incomplete', False)
        
        # Parse dates (already validated)
        start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
        end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
        
        # Create task configuration
        config = {
            'owner': owner,
            'repo': repo,
            'workflow_id': workflow_id,
            'start_date': start_date_str,
            'end_date': end_date_str,
            'skip_incomplete': skip_incomplete
        }
        
        # Create task in task manager
        task_id = task_manager.create_task(config)
        
        # Start background thread to execute fetch
        thread = threading.Thread(
            target=execute_fetch_task,
            args=(task_manager, task_id, owner, repo, workflow_id, start_date, end_date, DB_PATH, skip_incomplete, config_manager),
            daemon=True
        )
        thread.start()
        
        return jsonify({
            "task_id": task_id,
            "status": "started"
        })
    
    except Exception as e:
        return jsonify({
            "error": "Internal server error",
            "error_type": "internal",
            "details": str(e)
        }), 500

@app.route('/api/fetch/status/<task_id>', methods=['GET'])
def fetch_status(task_id):
    """
    API endpoint to retrieve the status of a background fetch task.
    
    Path Parameters:
    - task_id (str): Unique task identifier returned by /api/fetch/start
    
    Returns:
    - JSON object with:
      - task_id: Task identifier
      - status: Current status ('pending', 'in_progress', 'completed', 'failed')
      - progress: Progress information (if in_progress)
        - current: Current workflow count
        - total: Total workflows to process
        - message: Descriptive progress message
      - result: Fetch results (if completed)
        - runs_collected: Number of workflow runs collected
        - runs_skipped: Number of runs skipped (already in database)
        - incomplete_runs_stored: Number of incomplete runs stored
        - incomplete_runs_skipped: Number of incomplete runs skipped
        - errors: List of errors encountered
      - error: Error message (if failed)
      - created_at: Task creation timestamp
      - updated_at: Last update timestamp
    
    Error Responses:
    - 404: Task ID not found
    """
    try:
        # Retrieve task status from task manager
        status = task_manager.get_task_status(task_id)
        
        if status is None:
            return jsonify({
                "error": "Task not found",
                "error_type": "not_found",
                "details": f"No task found with ID: {task_id}"
            }), 404
        
        return jsonify(status)
    
    except Exception as e:
        return jsonify({
            "error": "Internal server error",
            "error_type": "internal",
            "details": str(e)
        }), 500

@app.route('/api/overall-metrics', methods=['GET'])
def get_overall_metrics():
    """
    API endpoint to get overall metrics including job-based P95 duration.
    
    Query Parameters:
    - owner (str, required): Repository owner.
    - repo (str, required): Repository name.
    - workflow_id (str, required): Workflow file name (e.g., 'ci.yml').
    - start_date (str, optional): ISO 8601 start date for filtering.
    - end_date (str, optional): ISO 8601 end date for filtering.
    - conclusions (str, optional): Comma-separated list of conclusions to include (e.g., 'success,failure').
    - exclude_statuses (str, optional): Comma-separated list of statuses to exclude (default: 'in_progress,queued').
    
    Returns:
    - JSON object with overall metrics:
      - total_runs: Total number of workflow runs
      - successful_runs: Number of successful runs
      - success_rate: Success rate as a percentage
      - p95_duration_ms: Overall P95 duration calculated from job-based durations
    """
    owner = request.args.get('owner')
    repo = request.args.get('repo')
    workflow_id = request.args.get('workflow_id')
    
    if not owner or not repo or not workflow_id:
        return jsonify({"error": "Missing required parameters: owner, repo, workflow_id"}), 400
    
    start_date = parse_date(request.args.get('start_date'))
    end_date = parse_date(request.args.get('end_date'))
    
    conclusions_str = request.args.get('conclusions')
    conclusions = conclusions_str.split(',') if conclusions_str else None
    
    exclude_statuses_str = request.args.get('exclude_statuses')
    exclude_statuses = exclude_statuses_str.split(',') if exclude_statuses_str else DEFAULT_EXCLUDE_STATUSES
    
    try:
        with get_db() as db:
            # Get total runs and successful runs
            workflows = db.get_workflow_runs(
                owner=owner,
                repo=repo,
                workflow_id=workflow_id,
                start_date=start_date,
                end_date=end_date,
                conclusions=conclusions,
                exclude_statuses=exclude_statuses
            )
            
            total_runs = len(workflows)
            successful_runs = sum(1 for w in workflows if w.get('conclusion') == 'success')
            success_rate = (successful_runs / total_runs * 100.0) if total_runs > 0 else 0.0
            
            # Get job-based durations for successful runs only
            job_based_durations = db.get_workflow_job_based_durations(
                owner=owner,
                repo=repo,
                workflow_id=workflow_id,
                start_date=start_date,
                end_date=end_date,
                conclusions=['success'],
                exclude_statuses=exclude_statuses
            )
            
            # Calculate P95 from job-based durations
            p95_duration_ms = None
            if len(job_based_durations) > 0:
                p95 = np.percentile(job_based_durations, 95)
                p95_duration_ms = int(p95)
            
            return jsonify({
                "total_runs": total_runs,
                "successful_runs": successful_runs,
                "success_rate": round(success_rate, 1),
                "p95_duration_ms": p95_duration_ms,
                "metadata": {
                    "calculation_method": "job_based",
                    "calculation_description": "P95 duration calculated from maximum job duration per workflow"
                }
            })
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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
        
        # Add calculation method metadata
        metadata["calculation_method"] = "job_based"
        metadata["calculation_description"] = "Duration metrics calculated from maximum job duration per workflow, excluding idle time between re-runs"
        
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
            "calculation_method": "job_based",
            "calculation_description": "Duration metrics calculated from maximum job duration per workflow, excluding idle time between re-runs",
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


@app.route('/api/jobs/flakiest', methods=['GET'])
def get_flakiest_jobs():
    with open("/app/data/debug.log", "a") as f:
        f.write("DEBUG: get_flakiest_jobs CALLED!\n")
    print("DEBUG: get_flakiest_jobs CALLED!", flush=True)
    """
    API endpoint to get the flakiest jobs ranked by time-weighted score.
    
    Query Parameters:
    - owner (str, required): Repository owner
    - repo (str, required): Repository name
    - workflow_id (str, required): Workflow file name (e.g., 'tests.yaml').
    - start_date (str, required): ISO 8601 format
    - end_date (str, required): ISO 8601 format
    - limit (int, optional): Number of jobs to return (default: 10)
    - conclusions (str, optional): Comma-separated list of conclusions to include
    - exclude_statuses (str, optional): Comma-separated list of statuses to exclude (default: 'in_progress,queued')
    
    Returns:
    - 200: JSON array of flaky jobs with metrics
    - 400: Invalid parameters
    - 500: Internal error
    
    Response Format:
    [
      {
        "job_name": "test-clusters-chip-tool (spec)",
        "flakiness_score": 15.7,
        "flake_rate": 75.0,
        "flake_count": 18,
        "total_runs": 24,
        "wasted_ci_time_ms": 12120000,
        "last_flaked_context": {
          "type": "pr",
          "display_text": "PR #12345",
          "url": "https://github.com/owner/repo/pull/12345",
          "job_url": "https://github.com/owner/repo/actions/runs/98765/job/54321"
        }
      }
    ]
    """
    # Parse and validate required parameters
    owner = request.args.get('owner')
    repo = request.args.get('repo')
    workflow_id = request.args.get('workflow_id')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    
    if not all([owner, repo, workflow_id, start_date_str, end_date_str]):
        return jsonify({
            "error": "Missing required parameters: owner, repo, workflow_id, start_date, end_date"
        }), 400
    
    # Parse dates
    start_date = parse_date(start_date_str)
    end_date = parse_date(end_date_str)
    
    if not start_date or not end_date:
        return jsonify({
            "error": "Invalid date format. Please use ISO 8601 format."
        }), 400
    
    # Parse optional parameters
    limit_str = request.args.get('limit', '10')
    try:
        limit = int(limit_str)
        if limit <= 0:
            return jsonify({
                "error": "Limit must be a positive integer."
            }), 400
    except ValueError:
        return jsonify({
            "error": "Invalid limit parameter. Must be an integer."
        }), 400
    
    conclusions = parse_conclusions_param(request.args.get('conclusions'))
    exclude_statuses = parse_exclude_statuses_param(request.args.get('exclude_statuses'))
    
    try:
        # Retrieve raw flaky job data from database
        print(f"DEBUG: Calling get_flaky_jobs_summary with: owner={owner}, repo={repo}, workflow={workflow_id}, start={start_date}, end={end_date}", flush=True)
        with get_db() as db:
            flaky_jobs_data = db.get_flaky_jobs_summary(
                owner=owner,
                repo=repo,
                workflow_id=workflow_id,
                start_date=start_date,
                end_date=end_date,
                conclusions=conclusions,
                exclude_statuses=exclude_statuses
            )
        print(f"DEBUG: get_flaky_jobs_summary returned {len(flaky_jobs_data)} records", flush=True)

        # Calculate flakiness metrics and format results
        calculator = StatsCalculator()
        flaky_summaries = calculator.calculate_flakiness_metrics(
            flaky_jobs_data=flaky_jobs_data,
            owner=owner,
            repo=repo,
            start_date=start_date,
            end_date=end_date,
            limit=limit
        )
        print(f"DEBUG: calculate_flakiness_metrics returned {len(flaky_summaries)} summaries", flush=True)
        
        # Convert FlakyJobSummary objects to JSON-serializable dicts
        results = []
        for summary in flaky_summaries:
            results.append({
                "job_name": summary.job_name,
                "flakiness_score": round(summary.flakiness_score, 1),
                "flake_rate": round(summary.flake_rate, 1),
                "flake_count": summary.flake_count,
                "total_runs": summary.total_runs,
                "wasted_ci_time_ms": summary.wasted_ci_time_ms,
                "last_flaked_context": summary.last_flaked_context
            })
        
        # Handle empty result sets with informative metadata
        if not results:
            metadata = build_filter_metadata(conclusions, exclude_statuses)
            return jsonify({
                "data": [],
                "metadata": {
                    **metadata,
                    "message": "No flaky jobs found matching the specified filters"
                }
            }), 200
        
        return jsonify(results)
    
    except ValueError as e:
        # Handle validation errors
        return jsonify({
            "error": str(e)
        }), 400
    except Exception as e:
        # Handle internal errors
        return jsonify({
            "error": f"An internal error occurred: {e}"
        }), 500


if __name__ == '__main__':
    app.run(debug=True, port=5002)
