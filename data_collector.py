from datetime import datetime, timedelta, timezone
from typing import Optional, Dict
import re

from github_api_client import GitHubApiClient
from data_models import WorkflowRun, Job, Step
from database import GHADatabase


class DataCollector:
    def __init__(self, github_client: GitHubApiClient, db: GHADatabase):
        self.github_client = github_client
        self.db = db

    def _normalize_utc(self, dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _parse_raw_run_data(self, raw_run: Dict, owner: str, repo: str) -> WorkflowRun:
        """Parses raw API data for a single run, fetches its jobs/steps, and builds a WorkflowRun object."""
        workflow_run = WorkflowRun(
            id=raw_run["id"],
            name=raw_run["name"],
            status=raw_run["status"],
            conclusion=raw_run["conclusion"],
            created_at=raw_run["created_at"],
            updated_at=raw_run["updated_at"],
            event=raw_run["event"],
            head_branch=raw_run["head_branch"],
            run_number=raw_run["run_number"],
        )

        raw_jobs = self.github_client.get_jobs_for_run(owner, repo, workflow_run.id)

        for raw_job in raw_jobs:
            matrix_config = None
            if raw_job.get("labels"):
                parsed_labels = {}
                for label in raw_job["labels"]:
                    if ":" in label:
                        key, value = label.split(":", 1)
                        parsed_labels[key.strip()] = value.strip()
                if parsed_labels:
                    matrix_config = parsed_labels
            # Fallback: parse matrix from job name
            if not matrix_config:
                job_name_for_parse = raw_job.get("name", "")
                name_match = re.search(r"\((.*?)\)", job_name_for_parse)
                if name_match:
                    params_str = name_match.group(1)
                    params = [p.strip() for p in params_str.split(',') if p.strip()]
                    if params:
                        matrix_config = {f"matrix_param_{i}": val for i, val in enumerate(params)}

            job = Job(
                id=raw_job["id"],
                name=raw_job["name"],
                status=raw_job["status"],
                conclusion=raw_job["conclusion"],
                started_at=raw_job["started_at"],
                completed_at=raw_job["completed_at"],
                workflow_run_id=workflow_run.id,
                matrix_config=matrix_config
            )

            if raw_job.get("steps"):
                for raw_step in raw_job["steps"]:
                    step = Step(
                        name=raw_step["name"],
                        status=raw_step["status"],
                        conclusion=raw_step["conclusion"],
                        number=raw_step["number"],
                        started_at=raw_step.get("started_at"),
                        completed_at=raw_step.get("completed_at"),
                    )
                    job.steps.append(step)
            workflow_run.jobs.append(job)
        
        # compute duration from job times
        job_start_times = [j.started_at for j in workflow_run.jobs if j.started_at]
        job_end_times = [j.completed_at for j in workflow_run.jobs if j.completed_at]
        if job_start_times and job_end_times:
            min_start = min(job_start_times)
            max_end = max(job_end_times)
            workflow_run.duration_ms = int((max_end - min_start).total_seconds() * 1000)
        
        return workflow_run

    def collect_workflow_data(self, owner: str, repo: str, workflow_id: str, 
                                  branch: Optional[str] = None, 
                                  start_date: Optional[datetime] = None, 
                                  end_date: Optional[datetime] = None,
                                  skip_incomplete: bool = False) -> Dict:
        """
        Fetches workflow run data from GitHub API in time-based batches and stores it in the database.
        This implements a "fresh start" batch-fetching strategy.
        
        Skips workflow runs that already exist in the database to avoid unnecessary API calls.

        :param owner: The repository owner.
        :param repo: The repository name.
        :param workflow_id: The workflow file name (e.g., 'ci.yml').
        :param branch: Optional branch name to filter runs.
        :param start_date: The start of the date range to fetch. Required.
        :param end_date: The end of the date range to fetch. Required.
        :param skip_incomplete: If True, skip workflows with status 'in_progress' or 'queued'.
        :return: Dict with keys: runs_collected, runs_skipped, incomplete_runs_stored, incomplete_runs_skipped.
        """
        if not start_date or not end_date:
            raise ValueError("start_date and end_date are required for data ingestion.")

        total_runs_collected = 0
        total_runs_skipped = 0
        incomplete_runs_stored = 0
        incomplete_runs_skipped = 0
        
        # Get existing workflow run IDs from database to avoid re-fetching
        existing_run_ids = self.db.get_existing_workflow_run_ids(
            owner, repo, workflow_id, start_date, end_date
        )
        print(f"Found {len(existing_run_ids)} existing workflow runs in database for this date range.")
        
        # Batching logic to manage API rate limits and data volume
        batch_delta = timedelta(days=7)
        current_start = self._normalize_utc(start_date)
        final_end = self._normalize_utc(end_date)

        def to_github_iso(dt: Optional[datetime]) -> Optional[str]:
            if not dt:
                return None
            # Already normalized to UTC
            return dt.isoformat().replace("+00:00", "Z")

        while current_start < final_end:
            current_end = min(current_start + batch_delta, final_end)
            
            print(f"Fetching data for batch: {current_start.isoformat()} to {current_end.isoformat()}")

            created_after_str = to_github_iso(current_start)
            created_before_str = to_github_iso(current_end)

            raw_workflow_runs = self.github_client.get_workflow_runs(
                owner, repo, workflow_id, branch, created_after_str, created_before_str
            )
            print(f"  Found {len(raw_workflow_runs)} runs in this batch.")

            for raw_run in raw_workflow_runs:
                run_id = raw_run.get("id")
                run_status = raw_run.get("status")
                
                # Skip if this run already exists in the database
                if run_id in existing_run_ids:
                    total_runs_skipped += 1
                    print(f"    Skipping workflow run ID {run_id} (already in database)")
                    continue
                
                # Check if workflow is incomplete
                is_incomplete = run_status in ['in_progress', 'queued']
                
                # Skip incomplete workflows if requested
                if skip_incomplete and is_incomplete:
                    incomplete_runs_skipped += 1
                    print(f"    Skipping workflow run ID {run_id} (status: {run_status})")
                    continue
                
                try:
                    # Parse all data for the run, including jobs and steps
                    workflow_run = self._parse_raw_run_data(raw_run, owner, repo)
                    
                    # Store the complete run object in the database
                    self.db.save_workflow_run(workflow_run, owner, repo, workflow_id)
                    total_runs_collected += 1
                    
                    # Track incomplete workflows that were stored
                    if is_incomplete:
                        incomplete_runs_stored += 1
                        print(f"    Stored workflow run ID: {workflow_run.id} (status: {run_status})")
                    else:
                        print(f"    Stored workflow run ID: {workflow_run.id}")
                except Exception as e:
                    print(f"    Failed to process or store run ID {raw_run.get('id')}: {e}")

            current_start = current_end
        
        print(f"\nTotal workflow runs collected and stored: {total_runs_collected}")
        print(f"Total workflow runs skipped (already in database): {total_runs_skipped}")
        if incomplete_runs_stored > 0:
            print(f"Incomplete workflows stored (in_progress/queued): {incomplete_runs_stored}")
        if incomplete_runs_skipped > 0:
            print(f"Incomplete workflows skipped (in_progress/queued): {incomplete_runs_skipped}")
        
        return {
            'runs_collected': total_runs_collected,
            'runs_skipped': total_runs_skipped,
            'incomplete_runs_stored': incomplete_runs_stored,
            'incomplete_runs_skipped': incomplete_runs_skipped
        }


if __name__ == '__main__':
    # Example Usage
    import os
    from dotenv import load_dotenv
    from database import GHADatabase

    load_dotenv() # Load environment variables from .env file

    github_token = os.getenv("GITHUB_TOKEN")
    if not github_token:
        print("Please set the GITHUB_TOKEN environment variable in a .env file or directly.")
    else:
        client = GitHubApiClient(github_token)
        db_file = "gha_metrics.db"
        
        with GHADatabase(db_path=db_file) as db:
            db.initialize_schema() # Ensure schema exists
            
            collector = DataCollector(client, db)

            owner = "project-chip"
            repo = "connectedhomeip"
            workflow_id = "tests.yaml" # Replace with your workflow ID or filename

            # Define a date range for collection (e.g., last 90 days)
            end_date = datetime.utcnow()
            start_date = end_date - timedelta(days=90)

            print(f"Collecting data for workflow '{workflow_id}' from {start_date.isoformat()} to {end_date.isoformat()}")
            try:
                result = collector.collect_workflow_data(
                    owner, repo, workflow_id, start_date=start_date, end_date=end_date
                )
                print(f"Successfully collected and stored data for {result['runs_collected']} workflow runs.")
            except Exception as e:
                print(f"An error occurred during data collection: {e}")



