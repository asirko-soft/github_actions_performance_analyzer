from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Callable
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
    
    def _to_github_iso(self, dt: Optional[datetime]) -> Optional[str]:
        """Convert datetime to GitHub API ISO format."""
        if not dt:
            return None
        # Normalize to UTC first
        dt_utc = self._normalize_utc(dt)
        return dt_utc.isoformat().replace("+00:00", "Z")
    
    def count_workflow_runs(self, owner: str, repo: str, workflow_id: str,
                           start_date: datetime, end_date: datetime,
                           branch: Optional[str] = None) -> int:
        """
        Count total workflow runs in a date range using adaptive batching.
        
        This method handles the GitHub API's 1000-run limit by:
        1. Starting with 7-day batches
        2. If a batch returns 1000 runs, subdivide it into smaller windows
        3. Continue until all batches return < 1000 runs
        
        :param owner: Repository owner
        :param repo: Repository name
        :param workflow_id: Workflow file name
        :param start_date: Start of date range
        :param end_date: End of date range
        :param branch: Optional branch filter
        :return: Total count of workflow runs
        """
        current_start = self._normalize_utc(start_date)
        final_end = self._normalize_utc(end_date)
        total_count = 0
        
        # Use a queue to handle batch subdivision
        batches_to_process = [(current_start, final_end)]
        
        while batches_to_process:
            batch_start, batch_end = batches_to_process.pop(0)
            
            # Fetch runs for this batch
            batch_runs = self.github_client.get_workflow_runs(
                owner, repo, workflow_id, branch,
                self._to_github_iso(batch_start),
                self._to_github_iso(batch_end)
            )
            
            batch_count = len(batch_runs)
            
            # If we hit the 1000 limit, subdivide this batch
            if batch_count >= 1000:
                # Calculate midpoint
                time_diff = batch_end - batch_start
                if time_diff.total_seconds() < 3600:  # Less than 1 hour - can't subdivide further
                    print(f"Warning: Batch {batch_start.date()} to {batch_end.date()} has 1000+ runs in < 1 hour window. Some runs may be missing.")
                    total_count += batch_count
                else:
                    # Split into two halves
                    midpoint = batch_start + time_diff / 2
                    print(f"Batch {batch_start.date()} to {batch_end.date()} hit 1000-run limit. Subdividing...")
                    batches_to_process.insert(0, (batch_start, midpoint))
                    batches_to_process.insert(1, (midpoint, batch_end))
            else:
                total_count += batch_count
        
        return total_count

    def _parse_raw_run_data(self, raw_run: Dict, owner: str, repo: str) -> WorkflowRun:
        """Parses raw API data for a single run, fetches its jobs/steps, and builds a WorkflowRun object."""
        # Extract head_sha from workflow run payload
        head_sha = raw_run.get("head_sha")
        
        # Extract pull_request_number from pull_requests array when event is "pull_request"
        pull_request_number = None
        if raw_run.get("event") == "pull_request":
            pull_requests = raw_run.get("pull_requests", [])
            if pull_requests and len(pull_requests) > 0:
                pull_request_number = pull_requests[0].get("number")
        
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
            head_sha=head_sha,
            pull_request_number=pull_request_number,
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

            # Extract run_attempt from job payload
            run_attempt = raw_job.get("run_attempt")
            
            job = Job(
                id=raw_job["id"],
                name=raw_job["name"],
                status=raw_job["status"],
                conclusion=raw_job["conclusion"],
                started_at=raw_job["started_at"],
                completed_at=raw_job["completed_at"],
                workflow_run_id=workflow_run.id,
                matrix_config=matrix_config,
                run_attempt=run_attempt
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
                                  skip_incomplete: bool = False,
                                  progress_callback: Optional[Callable[[int, int, str], None]] = None) -> Dict:
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
        :param progress_callback: Optional callback function(current, total, message) for progress updates.
        :return: Dict with keys: runs_collected, runs_updated, runs_skipped, incomplete_runs_stored, incomplete_runs_skipped.
        """
        if not start_date or not end_date:
            raise ValueError("start_date and end_date are required for data ingestion.")

        total_runs_collected = 0
        total_runs_skipped = 0
        total_runs_updated = 0
        incomplete_runs_stored = 0
        incomplete_runs_skipped = 0
        
        # Get existing workflow run IDs and their statuses from database
        existing_runs = self.db.get_existing_workflow_runs_with_status(
            owner, repo, workflow_id, start_date, end_date
        )
        existing_run_ids = {run['id'] for run in existing_runs}
        incomplete_run_ids = {run['id'] for run in existing_runs if run['status'] in ['in_progress', 'queued']}
        print(f"Found {len(existing_run_ids)} existing workflow runs in database for this date range.")
        print(f"Found {len(incomplete_run_ids)} incomplete workflow runs that may need updating.")
        
        # Adaptive batching logic to handle GitHub's 1000-run API limit
        current_start = self._normalize_utc(start_date)
        final_end = self._normalize_utc(end_date)

        # First pass: collect all workflow runs using adaptive batching
        all_raw_runs = []
        batch_count = 0
        
        if progress_callback:
            progress_callback(0, 0, "Starting data collection...")
        
        # Use a queue to handle batch subdivision
        # Start with 7-day batches
        batch_delta = timedelta(days=7)
        batches_to_process = []
        temp_start = current_start
        while temp_start < final_end:
            temp_end = min(temp_start + batch_delta, final_end)
            batches_to_process.append((temp_start, temp_end))
            temp_start = temp_end
        
        # Process batches with adaptive subdivision
        while batches_to_process:
            batch_start, batch_end = batches_to_process.pop(0)
            batch_count += 1
            
            if progress_callback:
                progress_callback(0, 0, f"Fetching batch {batch_count}: {batch_start.date()} to {batch_end.date()}")
            
            batch_runs = self.github_client.get_workflow_runs(
                owner, repo, workflow_id, branch,
                self._to_github_iso(batch_start),
                self._to_github_iso(batch_end)
            )
            
            batch_run_count = len(batch_runs)
            
            # If we hit the 1000 limit, subdivide this batch
            if batch_run_count >= 1000:
                time_diff = batch_end - batch_start
                if time_diff.total_seconds() < 3600:  # Less than 1 hour
                    print(f"Warning: Batch {batch_start.date()} to {batch_end.date()} has 1000+ runs in < 1 hour. Some runs may be missing.")
                    all_raw_runs.extend(batch_runs)
                else:
                    # Split into two halves and reprocess
                    midpoint = batch_start + time_diff / 2
                    print(f"Batch {batch_start.date()} to {batch_end.date()} hit 1000-run limit. Subdividing into smaller windows...")
                    batches_to_process.insert(0, (batch_start, midpoint))
                    batches_to_process.insert(1, (midpoint, batch_end))
                    batch_count -= 1  # Don't count this as a completed batch
            else:
                all_raw_runs.extend(batch_runs)
        
        total_workflows = len(all_raw_runs)
        print(f"Found {total_workflows} total workflow runs across {batch_count} batches.")
        
        if progress_callback:
            progress_callback(0, total_workflows, f"Found {total_workflows} workflows to process")
        
        # Process workflows with progress tracking using parallel execution for data fetching
        import concurrent.futures
        
        # Helper function to process a single run (fetch data only)
        def process_run_data(raw_run):
            try:
                # Parse all data for the run, including jobs and steps (network intensive)
                return self._parse_raw_run_data(raw_run, owner, repo)
            except Exception as e:
                return e

        # Filter runs to process
        runs_to_process = []
        for raw_run in all_raw_runs:
            run_id = raw_run.get("id")
            run_status = raw_run.get("status")
            
            # Skip if this run already exists in the database AND is not incomplete
            if run_id in existing_run_ids and run_id not in incomplete_run_ids:
                total_runs_skipped += 1
                continue
                
            # Check if workflow is incomplete
            is_incomplete = run_status in ['in_progress', 'queued']
            
            # Skip incomplete workflows if requested
            if skip_incomplete and is_incomplete:
                incomplete_runs_skipped += 1
                continue
                
            runs_to_process.append(raw_run)

        print(f"    Skipped {total_runs_skipped} existing runs and {incomplete_runs_skipped} incomplete runs.")
        print(f"    Processing {len(runs_to_process)} runs in parallel...")

        # Execute in parallel
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            # Create a map of future -> raw_run for error handling context
            future_to_run = {executor.submit(process_run_data, run): run for run in runs_to_process}
            
            completed_count = 0
            total_to_process = len(runs_to_process)
            
            for future in concurrent.futures.as_completed(future_to_run):
                raw_run = future_to_run[future]
                run_id = raw_run.get("id")
                run_status = raw_run.get("status")
                completed_count += 1
                
                # Invoke progress callback
                if progress_callback:
                    progress_callback(completed_count, total_to_process, f"Processing workflow {completed_count} of {total_to_process}")
                
                try:
                    result = future.result()
                    
                    if isinstance(result, Exception):
                        print(f"    Failed to process run ID {run_id}: {result}")
                        continue
                        
                    workflow_run = result
                    
                    # Store the complete run object in the database (sequential write)
                    self.db.save_workflow_run(workflow_run, owner, repo, workflow_id)
                    
                    is_update = run_id in incomplete_run_ids
                    is_incomplete = run_status in ['in_progress', 'queued']
                    
                    if is_update:
                        total_runs_updated += 1
                        print(f"    Updated workflow run ID: {workflow_run.id} (status: {run_status})")
                    else:
                        total_runs_collected += 1
                        if is_incomplete:
                            incomplete_runs_stored += 1
                            print(f"    Stored workflow run ID: {workflow_run.id} (status: {run_status})")
                        else:
                            print(f"    Stored workflow run ID: {workflow_run.id}")
                            
                except Exception as e:
                    print(f"    Error saving run ID {run_id}: {e}")

        print(f"\nTotal workflow runs collected and stored: {total_runs_collected}")
        print(f"Total workflow runs updated: {total_runs_updated}")
        print(f"Total workflow runs skipped (already in database): {total_runs_skipped}")
        if incomplete_runs_stored > 0:
            print(f"Incomplete workflows stored (in_progress/queued): {incomplete_runs_stored}")
        if incomplete_runs_skipped > 0:
            print(f"Incomplete workflows skipped (in_progress/queued): {incomplete_runs_skipped}")
        
        if progress_callback:
            progress_callback(total_workflows, total_workflows, "Data collection complete")
        
        return {
            'runs_collected': total_runs_collected,
            'runs_updated': total_runs_updated,
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



