"""
Task manager for asynchronous workflow data fetch operations.
Provides thread-safe task state management with progress tracking.
"""

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, Optional


@dataclass
class FetchTask:
    """Represents a background fetch task with its current state."""
    task_id: str
    status: str  # 'pending', 'in_progress', 'completed', 'failed'
    config: Dict[str, Any]
    progress: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


class FetchTaskManager:
    """
    Thread-safe manager for background fetch tasks.
    Stores task state and provides methods for task lifecycle management.
    """
    
    def __init__(self):
        """Initialize the task manager with empty task storage."""
        self.tasks: Dict[str, FetchTask] = {}
        self.lock = threading.Lock()
    
    def create_task(self, config: Dict[str, Any]) -> str:
        """
        Initialize a new fetch task with pending status.
        
        Args:
            config: Dictionary containing fetch configuration
                   (owner, repo, workflow_id, start_date, end_date)
        
        Returns:
            str: Unique task ID (UUID)
        """
        task_id = str(uuid.uuid4())
        
        with self.lock:
            task = FetchTask(
                task_id=task_id,
                status='pending',
                config=config
            )
            self.tasks[task_id] = task
        
        return task_id
    
    def update_progress(self, task_id: str, current: int, total: int, message: str) -> None:
        """
        Update task progress information.
        
        Args:
            task_id: Unique task identifier
            current: Current progress count
            total: Total items to process
            message: Descriptive progress message
        """
        with self.lock:
            if task_id in self.tasks:
                task = self.tasks[task_id]
                task.status = 'in_progress'
                task.progress = {
                    'current': current,
                    'total': total,
                    'message': message
                }
                task.updated_at = datetime.utcnow()
    
    def complete_task(self, task_id: str, result: Dict[str, Any]) -> None:
        """
        Mark task as completed with results.
        
        Args:
            task_id: Unique task identifier
            result: Dictionary containing fetch results
                   (runs_collected, runs_updated, runs_skipped, incomplete_runs_stored, incomplete_runs_skipped)
        """
        with self.lock:
            if task_id in self.tasks:
                task = self.tasks[task_id]
                task.status = 'completed'
                task.result = result
                task.updated_at = datetime.utcnow()
    
    def fail_task(self, task_id: str, error: str) -> None:
        """
        Mark task as failed with error message.
        
        Args:
            task_id: Unique task identifier
            error: Error message or description
        """
        with self.lock:
            if task_id in self.tasks:
                task = self.tasks[task_id]
                task.status = 'failed'
                task.error = error
                task.updated_at = datetime.utcnow()
    
    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve current task status and metadata.
        
        Args:
            task_id: Unique task identifier
        
        Returns:
            Dictionary containing task status, progress, and results,
            or None if task not found
        """
        with self.lock:
            if task_id not in self.tasks:
                return None
            
            task = self.tasks[task_id]
            
            status_dict = {
                'task_id': task.task_id,
                'status': task.status,
                'created_at': task.created_at.isoformat(),
                'updated_at': task.updated_at.isoformat()
            }
            
            if task.progress:
                status_dict['progress'] = task.progress
            
            if task.result:
                status_dict['result'] = task.result
            
            if task.error:
                status_dict['error'] = task.error
            
            return status_dict


def execute_fetch_task(task_manager: FetchTaskManager, task_id: str, 
                       owner: str, repo: str, workflow_id: str, 
                       start_date: datetime, end_date: datetime,
                       db_path: str = "gha_metrics.db",
                       skip_incomplete: bool = False,
                       config_manager = None) -> None:
    """
    Background worker function that executes a fetch task in a separate thread.
    
    This function:
    1. Validates the GitHub token
    2. Initializes the GitHub API client and data collector
    3. Executes the data collection with progress callbacks
    4. Updates task state throughout the process
    5. Handles errors and updates task state accordingly
    
    Args:
        task_manager: FetchTaskManager instance to update task state
        task_id: Unique task identifier
        owner: Repository owner
        repo: Repository name
        workflow_id: Workflow file name (e.g., 'tests.yaml')
        start_date: Start date for data collection
        end_date: End date for data collection
        db_path: Path to SQLite database file
        skip_incomplete: Whether to skip incomplete workflows
        config_manager: ConfigManager instance for token retrieval (optional)
    """
    import os
    import traceback
    from github_api_client import GitHubApiClient
    from data_collector import DataCollector
    from database import GHADatabase
    
    # Wrap everything in try-except to ensure task state is always updated
    try:
        print(f"[Task {task_id}] Starting fetch task for {owner}/{repo}/{workflow_id}")
        print(f"[Task {task_id}] Date range: {start_date} to {end_date}")
        print(f"[Task {task_id}] Database path: {db_path}")
    except Exception as e:
        print(f"[Task {task_id}] Error in initial logging: {e}")
    
    try:
        # Validate GitHub token using ConfigManager if available, otherwise fall back to env var
        if config_manager:
            token = config_manager.get_github_token()
        else:
            token = os.getenv("GITHUB_TOKEN")
            
        if not token:
            task_manager.fail_task(task_id, "GitHub token not configured. Configure via web interface or set GITHUB_TOKEN environment variable")
            return
        
        # Initialize clients with error handling
        try:
            client = GitHubApiClient(token)
            
            # Validate token before proceeding with data collection
            is_valid, error_msg, error_type = client.validate_token()
            if not is_valid:
                if error_type == "authentication":
                    task_manager.fail_task(task_id, f"GitHub authentication failed: {error_msg}")
                else:
                    task_manager.fail_task(task_id, f"Token validation failed: {error_msg}")
                return
                
        except Exception as e:
            task_manager.fail_task(task_id, f"Failed to initialize GitHub API client: {str(e)}")
            return
        
        try:
            db = GHADatabase(db_path=db_path)
            db.connect()
        except Exception as e:
            task_manager.fail_task(task_id, f"Failed to connect to database: {str(e)}")
            return
        
        try:
            collector = DataCollector(client, db)
            
            # Define progress callback that updates task state
            def progress_callback(current: int, total: int, message: str) -> None:
                task_manager.update_progress(task_id, current, total, message)
            
            # Execute data collection with progress tracking
            result = collector.collect_workflow_data(
                owner=owner,
                repo=repo,
                workflow_id=workflow_id,
                start_date=start_date,
                end_date=end_date,
                skip_incomplete=skip_incomplete,
                progress_callback=progress_callback
            )
            
            # Mark task as complete with results
            task_manager.complete_task(task_id, result)
            
        finally:
            # Ensure database connection is closed
            db.close()
    
    except Exception as e:
        # Catch any unexpected errors and mark task as failed
        error_message = f"Unexpected error during data collection: {str(e)}"
        print(f"[Task {task_id}] FATAL ERROR: {error_message}")
        print(f"[Task {task_id}] Traceback: {traceback.format_exc()}")
        try:
            task_manager.fail_task(task_id, error_message)
        except Exception as fail_error:
            print(f"[Task {task_id}] Failed to update task status: {fail_error}")
