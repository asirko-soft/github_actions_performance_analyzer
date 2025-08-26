from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple, Dict
from github_api_client import GitHubApiClient
from data_models import WorkflowRun, Job, Step
import json
import os
import re

class DataCollector:
    def __init__(self, github_client: GitHubApiClient, cache_dir: str = None):
        self.github_client = github_client
        # Default to module-relative cache directory for stability regardless of CWD
        default_cache = os.path.join(os.path.dirname(__file__), "cache")
        self.cache_dir = cache_dir or default_cache
        os.makedirs(self.cache_dir, exist_ok=True)

    def _get_cache_file_path(self, owner: str, repo: str, workflow_id: str, start_date: datetime, end_date: datetime) -> str:
        # Create a unique filename for the cache based on parameters
        start_str = start_date.strftime("%Y%m%d%H%M%S")
        end_str = end_date.strftime("%Y%m%d%H%M%S")
        filename = f"{owner}_{repo}_{workflow_id}_{start_str}_{end_str}.json"
        return os.path.join(self.cache_dir, filename)

    def _load_cache_file_by_path(self, file_path: str) -> List[WorkflowRun]:
        with open(file_path, "r") as f:
            raw_data = json.load(f)
            workflow_runs: List[WorkflowRun] = []
            for run_data in raw_data:
                jobs: List[Job] = []
                for job_data in run_data.pop("jobs", []):
                    steps: List[Step] = []
                    for step_data in job_data.pop("steps", []):
                        steps.append(Step(**step_data))
                    jobs.append(Job(steps=steps, **job_data))
                workflow_runs.append(WorkflowRun(jobs=jobs, **run_data))
            return workflow_runs

    def _load_from_cache(self, owner: str, repo: str, workflow_id: str, start_date: datetime, end_date: datetime) -> Optional[List[WorkflowRun]]:
        cache_file = self._get_cache_file_path(owner, repo, workflow_id, start_date, end_date)
        if os.path.exists(cache_file):
            print(f"Loading data from cache: {cache_file}")
            return self._load_cache_file_by_path(cache_file)
        return None

    def _discover_cached_segments(self, owner: str, repo: str, workflow_id: str) -> List[Tuple[datetime, datetime, str]]:
        """Scan the cache directory for files related to this workflow and return segments.

        Supports both current naming (owner_repo_workflow_start_end.json) and legacy naming
        (workflow_start_end.json) to reuse previously cached data.
        """
        segments: List[Tuple[datetime, datetime, str]] = []
        if not os.path.isdir(self.cache_dir):
            return segments

        # Build regexes for new and legacy naming
        new_prefix = re.escape(f"{owner}_{repo}_{workflow_id}")
        legacy_prefix = re.escape(f"{workflow_id}")
        new_re = re.compile(rf"^{new_prefix}_(\d{14})_(\d{14})\.json$")
        legacy_re = re.compile(rf"^{legacy_prefix}_(\d{14})_(\d{14})\.json$")

        for fname in os.listdir(self.cache_dir):
            if not fname.endswith(".json"):
                continue
            match = new_re.match(fname) or legacy_re.match(fname)
            if not match:
                continue
            start_str, end_str = match.group(1), match.group(2)
            try:
                start_dt = datetime.strptime(start_str, "%Y%m%d%H%M%S")
                end_dt = datetime.strptime(end_str, "%Y%m%d%H%M%S")
                segments.append((start_dt, end_dt, os.path.join(self.cache_dir, fname)))
            except Exception:
                continue
        if segments:
            print(f"Discovered {len(segments)} cache segment(s) for {owner}/{repo}/{workflow_id} in {self.cache_dir}")
        return segments

    def _normalize_utc(self, dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _merge_and_clip_cached_runs(self, cached_runs: List[WorkflowRun], start_date: datetime, end_date: datetime) -> List[WorkflowRun]:
        start_utc = self._normalize_utc(start_date)
        end_utc = self._normalize_utc(end_date)
        unique: Dict[int, WorkflowRun] = {}
        for run in cached_runs:
            run_created_utc = self._normalize_utc(run.created_at) if run.created_at else None
            if run_created_utc and start_utc <= run_created_utc <= end_utc:
                unique[run.id] = run
        # Return sorted by created_at for consistency
        return sorted(unique.values(), key=lambda r: r.created_at)

    def _load_cached_range(self, owner: str, repo: str, workflow_id: str, start_date: datetime, end_date: datetime) -> Tuple[List[WorkflowRun], List[Tuple[datetime, datetime]]]:
        """Load any cached runs overlapping the target range and return coverage intervals.

        Returns a tuple of (runs, covered_intervals_within_target).
        """
        segments = self._discover_cached_segments(owner, repo, workflow_id)
        if not segments:
            return [], []

        target_start = self._normalize_utc(start_date)
        target_end = self._normalize_utc(end_date)
        collected_runs: List[WorkflowRun] = []
        covered: List[Tuple[datetime, datetime]] = []

        for seg_start, seg_end, path in segments:
            # Compute overlap with target range (inclusive)
            seg_start_utc = self._normalize_utc(seg_start)
            seg_end_utc = self._normalize_utc(seg_end)
            overlap_start = max(seg_start_utc, target_start)
            overlap_end = min(seg_end_utc, target_end)
            if overlap_start <= overlap_end:
                try:
                    runs = self._load_cache_file_by_path(path)
                    collected_runs.extend(runs)
                    covered.append((overlap_start, overlap_end))
                except Exception as e:
                    print(f"Warning: failed to read cache file {path}: {e}")

        collected_runs = self._merge_and_clip_cached_runs(collected_runs, start_date, end_date)
        # Merge overlapping covered intervals
        covered = self._merge_intervals(sorted(covered, key=lambda x: x[0]))
        if covered:
            cov_str = ", ".join([f"[{s.isoformat()} .. {e.isoformat()}]" for s, e in covered])
            print(f"Cache coverage for requested range: {cov_str}")
        return collected_runs, covered

    def _merge_intervals(self, intervals: List[Tuple[datetime, datetime]]) -> List[Tuple[datetime, datetime]]:
        if not intervals:
            return []
        merged: List[Tuple[datetime, datetime]] = []
        cur_start, cur_end = intervals[0]
        for s, e in intervals[1:]:
            if s <= cur_end:
                cur_end = max(cur_end, e)
            else:
                merged.append((cur_start, cur_end))
                cur_start, cur_end = s, e
        merged.append((cur_start, cur_end))
        return merged

    def _invert_intervals(self, target_start: datetime, target_end: datetime, covered: List[Tuple[datetime, datetime]]) -> List[Tuple[datetime, datetime]]:
        """Given covered intervals within [target_start, target_end], compute missing intervals."""
        start_utc = self._normalize_utc(target_start)
        end_utc = self._normalize_utc(target_end)
        if not covered:
            return [(start_utc, end_utc)]
        covered_utc: List[Tuple[datetime, datetime]] = [
            (self._normalize_utc(s), self._normalize_utc(e)) for s, e in covered
        ]
        covered_utc.sort(key=lambda x: x[0])
        missing: List[Tuple[datetime, datetime]] = []
        cursor = start_utc
        for s, e in covered_utc:
            if cursor < s:
                missing.append((cursor, s))
            cursor = max(cursor, e)
        if cursor < end_utc:
            missing.append((cursor, end_utc))
        return missing

    def _save_to_cache(self, workflow_runs: List[WorkflowRun], owner: str, repo: str, workflow_id: str, start_date: datetime, end_date: datetime):
        cache_file = self._get_cache_file_path(owner, repo, workflow_id, start_date, end_date)
        print(f"Saving data to cache: {cache_file}")
        # Convert WorkflowRun objects to dictionary for JSON serialization
        serializable_runs = []
        for run in workflow_runs:
            run_dict = run.__dict__.copy()
            run_dict["created_at"] = run_dict["created_at"].isoformat()
            run_dict["updated_at"] = run_dict["updated_at"].isoformat()
            serializable_jobs = []
            for job in run_dict["jobs"]:
                job_dict = job.__dict__.copy()
                job_dict["started_at"] = job_dict["started_at"].isoformat() if job_dict["started_at"] else None
                job_dict["completed_at"] = job_dict["completed_at"].isoformat() if job_dict["completed_at"] else None
                serializable_steps = []
                for step in job_dict["steps"]:
                    step_dict = step.__dict__.copy()
                    step_dict["started_at"] = step_dict["started_at"].isoformat() if step_dict["started_at"] else None
                    step_dict["completed_at"] = step_dict["completed_at"].isoformat() if step_dict["completed_at"] else None
                    serializable_steps.append(step_dict)
                job_dict["steps"] = serializable_steps
                serializable_jobs.append(job_dict)
            run_dict["jobs"] = serializable_jobs
            serializable_runs.append(run_dict)

        with open(cache_file, "w") as f:
            json.dump(serializable_runs, f, indent=4)

    def collect_workflow_data(self, owner: str, repo: str, workflow_id: str, 
                              branch: Optional[str] = None, 
                              start_date: Optional[datetime] = None, 
                              end_date: Optional[datetime] = None) -> List[WorkflowRun]:
        
        # Try loading from cache first (supports exact file match and partial range reuse)
        combined_results: List[WorkflowRun] = []
        covered_intervals: List[Tuple[datetime, datetime]] = []
        if start_date and end_date:
            # First, check exact cache hit
            cached_data = self._load_from_cache(owner, repo, workflow_id, start_date, end_date)
            if cached_data:
                return cached_data
            # Otherwise, attempt to reuse overlapping cache segments
            cached_runs, covered_intervals = self._load_cached_range(owner, repo, workflow_id, start_date, end_date)
            if cached_runs:
                combined_results.extend(cached_runs)
                print(f"Reused {len(cached_runs)} run(s) from cached segment(s).")

        # Format dates for API if provided (ensure UTC 'Z' format)
        def to_github_iso(dt: Optional[datetime]) -> Optional[str]:
            if not dt:
                return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

        created_after_str = to_github_iso(start_date)
        created_before_str = to_github_iso(end_date)

        # Determine which intervals need fetching
        intervals_to_fetch: List[Tuple[datetime, datetime]] = []
        if start_date and end_date:
            intervals_to_fetch = self._invert_intervals(start_date, end_date, covered_intervals)
        else:
            # No dates specified; fetch everything (no caching possible)
            intervals_to_fetch = [(None, None)]  # type: ignore

        workflow_runs: List[WorkflowRun] = []
        if intervals_to_fetch == [(None, None)]:  # type: ignore
            # No start/end provided: fetch without bounds
            print(f"Collecting workflow runs for {owner}/{repo}/{workflow_id} (no date bounds)...")
            raw_workflow_runs = self.github_client.get_workflow_runs(
                owner, repo, workflow_id, branch, None, None
            )
            print(f"Found {len(raw_workflow_runs)} raw workflow runs.")

            for raw_run in raw_workflow_runs:
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

                print(f"  Fetching jobs for workflow run ID: {workflow_run.id} ({workflow_run.name})...")
                raw_jobs = self.github_client.get_jobs_for_run(owner, repo, workflow_run.id)
                print(f"    Found {len(raw_jobs)} jobs for run {workflow_run.id}.")

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
                workflow_runs.append(workflow_run)
        elif intervals_to_fetch:  # missing intervals exist
            for interval_start, interval_end in intervals_to_fetch:
                print(f"Collecting workflow runs for {owner}/{repo}/{workflow_id} (interval {interval_start} to {interval_end})...")
                after_str = to_github_iso(interval_start)
                before_str = to_github_iso(interval_end)
                raw_workflow_runs = self.github_client.get_workflow_runs(
                    owner, repo, workflow_id, branch, after_str, before_str
                )
                print(f"Found {len(raw_workflow_runs)} raw workflow runs for interval.")

                interval_runs: List[WorkflowRun] = []
                for raw_run in raw_workflow_runs:
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

                    print(f"  Fetching jobs for workflow run ID: {workflow_run.id} ({workflow_run.name})...")
                    raw_jobs = self.github_client.get_jobs_for_run(owner, repo, workflow_run.id)
                    print(f"    Found {len(raw_jobs)} jobs for run {workflow_run.id}.")

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
                    # compute duration for interval run
                    job_start_times = [j.started_at for j in workflow_run.jobs if j.started_at]
                    job_end_times = [j.completed_at for j in workflow_run.jobs if j.completed_at]
                    if job_start_times and job_end_times:
                        min_start = min(job_start_times)
                        max_end = max(job_end_times)
                        workflow_run.duration_ms = int((max_end - min_start).total_seconds() * 1000)
                    interval_runs.append(workflow_run)

                # extend global list with this interval's runs
                workflow_runs.extend(interval_runs)

                # Save interval cache after each interval fetch
                if interval_start and interval_end and interval_runs:
                    self._save_to_cache(interval_runs, owner, repo, workflow_id, interval_start, interval_end)

        # Combine newly fetched with reused cached results, de-duplicated by run ID
        if combined_results:
            all_runs: Dict[int, WorkflowRun] = {run.id: run for run in combined_results}
            for run in workflow_runs:
                all_runs[run.id] = run
            return sorted(all_runs.values(), key=lambda r: r.created_at)
        else:
            return workflow_runs

if __name__ == '__main__':
    # Example Usage
    import os
    from dotenv import load_dotenv

    load_dotenv() # Load environment variables from .env file

    github_token = os.getenv("GITHUB_TOKEN")
    if not github_token:
        print("Please set the GITHUB_TOKEN environment variable in a .env file or directly.")
    else:
        client = GitHubApiClient(github_token)
        collector = DataCollector(client)

        owner = "octocat"
        repo = "Spoon-Knife"
        workflow_id = "blank.yml" # Replace with your workflow ID or filename

        # Define a date range for collection (e.g., last 7 days)
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=7)

        print(f"Collecting data for workflow '{workflow_id}' from {start_date.isoformat()} to {end_date.isoformat()}")
        try:
            collected_runs = collector.collect_workflow_data(
                owner, repo, workflow_id, start_date=start_date, end_date=end_date
            )
            print(f"Successfully collected data for {len(collected_runs)} workflow runs.")
            if collected_runs:
                print("First collected workflow run details:")
                print(f"  ID: {collected_runs[0].id}")
                print(f"  Name: {collected_runs[0].name}")
                print(f"  Status: {collected_runs[0].status}")
                print(f"  Conclusion: {collected_runs[0].conclusion}")
                print(f"  Jobs found: {len(collected_runs[0].jobs)}")
                if collected_runs[0].jobs:
                    print("  First job details:")
                    print(f"    Name: {collected_runs[0].jobs[0].name}")
                    print(f"    Status: {collected_runs[0].jobs[0].status}")
                    print(f"    Conclusion: {collected_runs[0].jobs[0].conclusion}")
                    print(f"    Steps found: {len(collected_runs[0].jobs[0].steps)}")
                    if collected_runs[0].jobs[0].matrix_config:
                        print(f"    Matrix Config: {collected_runs[0].jobs[0].matrix_config}")

        except Exception as e:
            print(f"An error occurred during data collection: {e}")



