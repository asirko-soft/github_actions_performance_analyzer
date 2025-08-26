from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict, Any

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

    def __post_init__(self):
        if isinstance(self.created_at, str):
            self.created_at = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        if isinstance(self.updated_at, str):
            self.updated_at = datetime.fromisoformat(self.updated_at.replace("Z", "+00:00"))
        # duration_ms for WorkflowRun is computed in DataCollector after jobs are populated

@dataclass
class PerformanceMetrics:
    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    cancelled_runs: int = 0
    other_runs: int = 0
    avg_duration_ms: float = 0.0
    avg_success_duration_ms: float = 0.0
    avg_failure_duration_ms: float = 0.0
    avg_cancelled_duration_ms: float = 0.0
    failure_rate_percent: float = 0.0
    # Success run duration distribution
    success_min_duration_ms: float = 0.0
    success_max_duration_ms: float = 0.0
    success_p50_duration_ms: float = 0.0
    success_p90_duration_ms: float = 0.0
    success_p95_duration_ms: float = 0.0
    job_metrics: Dict[str, Any] = field(default_factory=dict) # Keyed by job name
    step_metrics: Dict[str, Any] = field(default_factory=dict) # Keyed by step name
    matrix_metrics: Dict[str, Any] = field(default_factory=dict) # Keyed by matrix config string


