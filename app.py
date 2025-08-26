from flask import Flask, jsonify, request, render_template
from datetime import datetime
from typing import Optional

from database import GHADatabase

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
    """
    owner = request.args.get('owner')
    repo = request.args.get('repo')
    workflow_id = request.args.get('workflow_id')

    if not all([owner, repo, workflow_id]):
        return jsonify({"error": "Missing required parameters: owner, repo, workflow_id"}), 400

    start_date = parse_date(request.args.get('start_date'))
    end_date = parse_date(request.args.get('end_date'))
    period = request.args.get('period', 'day')

    if period not in ['day', 'week']:
        return jsonify({"error": "Invalid 'period' parameter. Must be 'day' or 'week'."}), 400

    try:
        with get_db() as db:
            trends = db.get_time_series_metrics(
                owner=owner, repo=repo, workflow_id=workflow_id,
                start_date=start_date, end_date=end_date, period=period
            )
        return jsonify(trends)
    except Exception as e:
        return jsonify({"error": f"An internal error occurred: {e}"}), 500


if __name__ == '__main__':
    app.run(debug=True, port=5000)
