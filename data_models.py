from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict, Any
from utils import generate_github_job_url

@dataclass
class Step:
    name: str
    status: str
    conclusion: Optional[str]
    number: int
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    duration_ms: Optional[int] = None

    def __post_init__(self):
        if self.started_at and isinstance(self.started_at, str):
            self.started_at = datetime.fromisoformat(self.started_at.replace("Z", "+00:00"))
        if self.completed_at and isinstance(self.completed_at, str):
            self.completed_at = datetime.fromisoformat(self.completed_at.replace("Z", "+00:00"))
        if self.started_at and self.completed_at:
            self.duration_ms = int((self.completed_at - self.started_at).total_seconds() * 1000)

@dataclass
class Job:
    id: int
    name: str
    status: str
    conclusion: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    workflow_run_id: int
    steps: List[Step] = field(default_factory=list)
    matrix_config: Optional[Dict[str, Any]] = None
    duration_ms: Optional[int] = None
    run_attempt: Optional[int] = None

    def __post_init__(self):
        if self.started_at and isinstance(self.started_at, str):
            self.started_at = datetime.fromisoformat(self.started_at.replace("Z", "+00:00"))
        if self.completed_at and isinstance(self.completed_at, str):
            self.completed_at = datetime.fromisoformat(self.completed_at.replace("Z", "+00:00"))
        if self.started_at and self.completed_at:
            self.duration_ms = int((self.completed_at - self.started_at).total_seconds() * 1000)

@dataclass
class WorkflowRun:
    id: int
    name: str
    status: str
    conclusion: Optional[str]
    created_at: datetime
    updated_at: datetime
    event: str
    head_branch: str
    run_number: int
    jobs: List[Job] = field(default_factory=list)
    duration_ms: Optional[int] = None
    head_sha: Optional[str] = None
    pull_request_number: Optional[int] = None

    def __post_init__(self):
        if isinstance(self.created_at, str):
            self.created_at = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        if isinstance(self.updated_at, str):
            self.updated_at = datetime.fromisoformat(self.updated_at.replace("Z", "+00:00"))
        # duration_ms for WorkflowRun is computed in DataCollector after jobs are populated

@dataclass
class JobExecutionDetail:
    """Represents a single job execution with workflow context and GitHub URL."""
    job_id: int
    workflow_run_id: int
    owner: str
    repo: str
    job_name: str
    job_conclusion: Optional[str]
    job_duration_ms: Optional[int]
    job_started_at: Optional[datetime]
    job_completed_at: Optional[datetime]
    workflow_conclusion: Optional[str]
    workflow_created_at: datetime
    github_url: str = field(init=False)
    
    def __post_init__(self):
        """Auto-generate GitHub URL after initialization."""
        # Handle datetime string parsing if needed
        if isinstance(self.workflow_created_at, str):
            self.workflow_created_at = datetime.fromisoformat(self.workflow_created_at.replace("Z", "+00:00"))
        if self.job_started_at and isinstance(self.job_started_at, str):
            self.job_started_at = datetime.fromisoformat(self.job_started_at.replace("Z", "+00:00"))
        if self.job_completed_at and isinstance(self.job_completed_at, str):
            self.job_completed_at = datetime.fromisoformat(self.job_completed_at.replace("Z", "+00:00"))
        
        # Generate GitHub URL
        self.github_url = generate_github_job_url(
            self.owner, self.repo, self.workflow_run_id, self.job_id
        )


@dataclass
class PerformanceMetrics:
    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    cancelled_runs: int = 0
    skipped_runs: int = 0
    other_runs: int = 0
    avg_duration_ms: float = 0.0
    avg_success_duration_ms: float = 0.0
    avg_failure_duration_ms: float = 0.0
    avg_cancelled_duration_ms: float = 0.0
    failure_rate_percent: float = 0.0
    success_rate_percent: float = 0.0
    skip_rate_percent: float = 0.0
    cancellation_rate_percent: float = 0.0
    # Success run duration distribution
    success_min_duration_ms: float = 0.0
    success_max_duration_ms: float = 0.0
    success_p50_duration_ms: float = 0.0
    success_p90_duration_ms: float = 0.0
    success_p95_duration_ms: float = 0.0
    # Outlier tracking
    outlier_count: int = 0
    outlier_threshold_lower: Optional[float] = None
    outlier_threshold_upper: Optional[float] = None
    job_metrics: Dict[str, Any] = field(default_factory=dict) # Keyed by job name
    step_metrics: Dict[str, Any] = field(default_factory=dict) # Keyed by step name
    matrix_metrics: Dict[str, Any] = field(default_factory=dict) # Keyed by matrix config string



@dataclass
class FlakyJobSummary:
    """Represents flakiness metrics for a single job."""
    job_name: str
    flakiness_score: float
    flake_rate: float
    flake_count: int
    total_runs: int
    wasted_ci_time_ms: int
    last_flaked_context: Dict[str, str]
