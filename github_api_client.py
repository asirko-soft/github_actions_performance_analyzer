import requests
import os
import time
from typing import Optional

# Import rate limit tracker (lazy to avoid circular imports)
_rate_limit_tracker = None

def _get_tracker():
    """Lazily get the rate limit tracker."""
    global _rate_limit_tracker
    if _rate_limit_tracker is None:
        try:
            from rate_limit_tracker import get_rate_limit_tracker
            _rate_limit_tracker = get_rate_limit_tracker()
        except ImportError:
            pass
    return _rate_limit_tracker


class GitHubApiClient:
    def __init__(self, token, db_path: Optional[str] = None):
        self.base_url = "https://api.github.com"
        self.headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json"
        }
        self.max_retries = 5
        self.initial_backoff_seconds = 1
        self.db_path = db_path
        
        # Initialize tracker with db_path if provided
        if db_path:
            try:
                from rate_limit_tracker import get_rate_limit_tracker
                global _rate_limit_tracker
                _rate_limit_tracker = get_rate_limit_tracker(db_path)
            except ImportError:
                pass

    def _update_rate_limit_from_response(self, response):
        """Update rate limit tracker from response headers."""
        tracker = _get_tracker()
        if not tracker:
            return
        
        try:
            remaining = response.headers.get('X-RateLimit-Remaining')
            reset_time = response.headers.get('X-RateLimit-Reset')
            
            if remaining is not None and reset_time is not None:
                remaining = int(remaining)
                reset_time = int(reset_time)
                
                # Register the request and update GitHub's rate limit info
                tracker.register_request(
                    count=1,
                    remaining=remaining,
                    reset_timestamp=reset_time
                )
                
                # Handle rate limit response
                tracker.handle_rate_limit_response(remaining, reset_time)
        except (ValueError, TypeError):
            pass

    def _wait_for_throttle(self):
        """Wait if we're being throttled."""
        tracker = _get_tracker()
        if tracker:
            # Check if we should pre-emptively throttle
            tracker.check_and_throttle_if_needed()
            # Wait if currently throttled
            tracker.wait_if_throttled(timeout=1800)  # Max 30 min wait

    def _make_request(self, url, params=None):
        rate_limit_retries = 0
        max_rate_limit_retries = 3  # Extra retries specifically for rate limit edge cases
        
        for attempt in range(self.max_retries):
            try:
                # Wait if we're being throttled
                self._wait_for_throttle()
                
                response = requests.get(url, headers=self.headers, params=params)
                
                # Update rate limit tracker from response
                self._update_rate_limit_from_response(response)
                
                # Check for rate limit
                if response.status_code == 403 and 'X-RateLimit-Remaining' in response.headers:
                    remaining = int(response.headers['X-RateLimit-Remaining'])
                    
                    if remaining == 0:
                        reset_time = int(response.headers['X-RateLimit-Reset'])
                        current_time = time.time()
                        sleep_duration = max(0, reset_time - current_time) + 5  # Add 5 seconds buffer
                        
                        # Handle edge case: reset time has passed but rate limit not lifted
                        if reset_time <= current_time:
                            rate_limit_retries += 1
                            if rate_limit_retries <= max_rate_limit_retries:
                                # Incremental backoff: 15s, 30s, 60s
                                backoff = 15 * (2 ** (rate_limit_retries - 1))
                                print(f"Rate limit still active after reset time. Waiting {backoff}s before retry {rate_limit_retries}/{max_rate_limit_retries}...")
                                time.sleep(backoff)
                                continue
                            else:
                                print(f"Rate limit still active after {max_rate_limit_retries} retries. Waiting until next hour...")
                                # Calculate time until next hour
                                from datetime import datetime, timedelta
                                now = datetime.utcnow()
                                next_hour = (now + timedelta(hours=1)).replace(minute=0, second=5, microsecond=0)
                                sleep_duration = (next_hour - now).total_seconds()
                        
                        # Start coordinated throttle
                        tracker = _get_tracker()
                        if tracker:
                            tracker.start_throttle(sleep_duration)
                        
                        print(f"Rate limit exceeded. Throttling all workers for {sleep_duration:.2f} seconds until {time.ctime(reset_time)}.")
                        
                        # Wait for throttle to end
                        self._wait_for_throttle()
                        continue  # Retry after sleeping

                response.raise_for_status()  # Raise an exception for HTTP errors (e.g., 4xx, 5xx)
                return response

            except requests.exceptions.HTTPError as e:
                if e.response.status_code in [500, 502, 503, 504]:  # Server errors, often transient
                    if attempt < self.max_retries - 1:
                        sleep_time = self.initial_backoff_seconds * (2 ** attempt)
                        print(f"Server error ({e.response.status_code}). Retrying in {sleep_time:.2f} seconds... (Attempt {attempt + 1}/{self.max_retries})")
                        time.sleep(sleep_time)
                    else:
                        print(f"Server error ({e.response.status_code}). Max retries reached. Giving up.")
                        raise
                else:
                    # For other HTTP errors (e.g., 404 Not Found, 400 Bad Request), don't retry
                    raise
            except requests.exceptions.ConnectionError as e:
                if attempt < self.max_retries - 1:
                    sleep_time = self.initial_backoff_seconds * (2 ** attempt)
                    print(f"Connection error. Retrying in {sleep_time:.2f} seconds... (Attempt {attempt + 1}/{self.max_retries})")
                    time.sleep(sleep_time)
                else:
                    print(f"Connection error. Max retries reached. Giving up.")
                    raise
            except Exception as e:
                print(f"An unexpected error occurred: {e}")
                raise
        
        # This part should ideally not be reached if max_retries is handled correctly
        raise Exception("Failed to make request after multiple retries.")

    def get_workflow_runs(self, owner, repo, workflow_id, branch=None, created_after=None, created_before=None):
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs"
        params = {"per_page": 100}  # Max per_page for pagination
        if branch: 
            params["branch"] = branch
        # Build created filter supporting combined ranges when both are provided
        if created_after and created_before:
            params["created"] = f"{created_after}..{created_before}"
        elif created_after:
            params["created"] = f">={created_after}"
        elif created_before:
            params["created"] = f"<={created_before}"

        all_runs = []
        while url:
            response = self._make_request(url, params=params)
            data = response.json()
            all_runs.extend(data.get("workflow_runs", []))
            # Check for next page
            if 'next' in response.links:
                url = response.links['next']['url']
                params = None # params are already in the next url
            else:
                url = None
        return all_runs

    def get_jobs_for_run(self, owner, repo, run_id):
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/runs/{run_id}/jobs"
        params = {"per_page": 100, "filter": "all"}
        
        all_jobs = []
        while url:
            response = self._make_request(url, params=params)
            data = response.json()
            all_jobs.extend(data.get("jobs", []))
            # Check for next page
            if 'next' in response.links:
                url = response.links['next']['url']
                params = None
            else:
                url = None
        return all_jobs

    def get_job_details(self, owner, repo, job_id):
        # The job details are usually included when fetching jobs for a run.
        # This method might be redundant if we always fetch jobs via get_jobs_for_run.
        # However, if there's a need to fetch a single job's details, this is the endpoint.
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/jobs/{job_id}"
        response = self._make_request(url)
        return response.json()

    def validate_token(self):
        """
        Validate the GitHub token by making a lightweight API call.
        
        Returns:
            tuple: (is_valid, error_message, error_type)
                - is_valid: True if token is valid, False otherwise
                - error_message: Error message if invalid, None if valid
                - error_type: 'authentication' for auth errors, None if valid
        
        Raises:
            requests.exceptions.RequestException: For network errors
        """
        try:
            # Use the /user endpoint as a lightweight way to validate the token
            url = f"{self.base_url}/user"
            response = requests.get(url, headers=self.headers, timeout=10)
            
            if response.status_code == 200:
                return (True, None, None)
            elif response.status_code == 401:
                return (False, "GitHub token is invalid or expired", "authentication")
            elif response.status_code == 403:
                # Check if it's a rate limit or permission issue
                if 'X-RateLimit-Remaining' in response.headers and int(response.headers['X-RateLimit-Remaining']) == 0:
                    return (False, "GitHub API rate limit exceeded", "authentication")
                else:
                    return (False, "GitHub token lacks required permissions", "authentication")
            else:
                return (False, f"GitHub API returned unexpected status: {response.status_code}", "authentication")
                
        except requests.exceptions.Timeout:
            return (False, "GitHub API request timed out", "api")
        except requests.exceptions.ConnectionError:
            return (False, "Failed to connect to GitHub API", "api")
        except Exception as e:
            return (False, f"Token validation failed: {str(e)}", "internal")

if __name__ == '__main__':
    # Example Usage (replace with your actual token, owner, repo, workflow_id)
    # It's recommended to load the token from environment variables or a .env file
    # For this example, we'll assume it's set as an environment variable.
    github_token = os.getenv("GITHUB_TOKEN")
    if not github_token:
        print("Please set the GITHUB_TOKEN environment variable.")
    else:
        client = GitHubApiClient(github_token)
        owner = "project-chip"
        repo = "connectedhomeip"
        workflow_id = "tests.yaml" # Or the workflow ID number

        print(f"Fetching workflow runs for {owner}/{repo}/{workflow_id}...")
        try:
            runs = client.get_workflow_runs(owner, repo, workflow_id, created_after="2024-01-01T00:00:00Z")
            print(f"Found {len(runs)} workflow runs.")
            if runs:
                first_run_id = runs[0]["id"]
                print(f"Fetching jobs for run ID: {first_run_id}...")
                jobs = client.get_jobs_for_run(owner, repo, first_run_id)
                print(f"Found {len(jobs)} jobs for run {first_run_id}.")
                if jobs:
                    first_job_id = jobs[0]["id"]
                    print(f"Fetching details for job ID: {first_job_id}...")
                    job_details = client.get_job_details(owner, repo, first_job_id)
                    print(f"Job details for {first_job_id}: {job_details.get('name')}")
        except requests.exceptions.RequestException as e:
            print(f"An API error occurred: {e}")
        except Exception as e:
            print(f"An unexpected error occurred: {e}")


