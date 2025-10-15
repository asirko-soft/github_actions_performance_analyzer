import sqlite3
from typing import Optional, List, Dict, Any
import json
from datetime import datetime

from data_models import WorkflowRun

def validate_conclusions(conclusions: Optional[List[str]]) -> Optional[List[str]]:
    """
    Validates conclusion filter values against the allowed set.
    
    :param conclusions: List of conclusion values to validate
    :return: The validated conclusions list, or None if input is None
    :raises ValueError: If any conclusion value is invalid
    """
    if not conclusions:
        return None
    
    valid_conclusions = {'success', 'failure', 'cancelled', 'skipped', 'action_required'}
    invalid = set(conclusions) - valid_conclusions
    
    if invalid:
        raise ValueError(f"Invalid conclusions: {invalid}. "
                        f"Valid values: {valid_conclusions}")
    
    return conclusions


class GHADatabase:
    """Manages all database interactions for the GHA Performance Analyzer."""

    def __init__(self, db_path: str = "gha_metrics.db"):
        """
        Initializes the database connection.

        :param db_path: The path to the SQLite database file.
        """
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def connect(self):
        """Establishes a connection to the SQLite database."""
        if self.conn is None:
            try:
                self.conn = sqlite3.connect(self.db_path)
                self.conn.row_factory = sqlite3.Row # Return rows as dict-like objects
                # Enable foreign key support
                self.conn.execute("PRAGMA foreign_keys = 1")
            except sqlite3.Error as e:
                print(f"Error connecting to database: {e}")
                raise

    def close(self):
        """Closes the database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def initialize_schema(self):
        """
        Initializes the database schema by creating tables and indexes if they don't exist.
        """
        if not self.conn:
            raise ConnectionError("Database is not connected. Call connect() first.")

        schema_script = """
        -- Workflows Table: Stores one record per workflow run.
        CREATE TABLE IF NOT EXISTS workflows (
            id INTEGER PRIMARY KEY,          -- GitHub's run ID
            owner TEXT NOT NULL,
            repo TEXT NOT NULL,
            workflow_id TEXT NOT NULL,       -- The workflow filename, e.g., 'ci.yml'
            name TEXT,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL,
            status TEXT,
            conclusion TEXT,
            duration_ms INTEGER,
            event TEXT,
            head_branch TEXT,
            run_number INTEGER,
            UNIQUE(owner, repo, id)          -- A run ID is unique per repo
        );

        -- Jobs Table: Stores one record per job within a workflow run.
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY,          -- GitHub's job ID
            workflow_run_id INTEGER NOT NULL, -- Foreign key to workflows.id
            name TEXT NOT NULL,
            status TEXT,
            conclusion TEXT,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            duration_ms INTEGER,
            matrix_config TEXT,              -- JSON string of matrix parameters
            FOREIGN KEY(workflow_run_id) REFERENCES workflows(id) ON DELETE CASCADE
        );

        -- Steps Table: Stores one record per step within a job.
        CREATE TABLE IF NOT EXISTS steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT, -- Internal auto-incrementing ID
            job_id INTEGER NOT NULL,         -- Foreign key to jobs.id
            name TEXT NOT NULL,
            status TEXT,
            conclusion TEXT,
            number INTEGER,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            duration_ms INTEGER,
            FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
        );

        -- Indexes for Performance
        CREATE INDEX IF NOT EXISTS idx_workflows_created ON workflows(created_at);
        CREATE INDEX IF NOT EXISTS idx_jobs_workflow ON jobs(workflow_run_id);
        CREATE INDEX IF NOT EXISTS idx_steps_job ON steps(job_id);
        
        -- Additional indexes for filtering support
        CREATE INDEX IF NOT EXISTS idx_workflows_status_conclusion 
            ON workflows(owner, repo, workflow_id, status, conclusion, created_at);
        CREATE INDEX IF NOT EXISTS idx_jobs_conclusion ON jobs(workflow_run_id, conclusion);
        CREATE INDEX IF NOT EXISTS idx_steps_name ON steps(job_id, name);
        """

        try:
            self.conn.executescript(schema_script)
            self.conn.commit()
            print("Database schema initialized successfully.")
        except sqlite3.Error as e:
            print(f"An error occurred during schema initialization: {e}")
            self.conn.rollback()
            raise

    def save_workflow_run(self, workflow_run: WorkflowRun, owner: str, repo: str, workflow_id: str):
        """
        Saves a complete WorkflowRun object, including its jobs and steps, to the database.
        Uses a transaction to ensure atomicity. If the run already exists, it will be replaced.

        :param workflow_run: A WorkflowRun data model object.
        :param owner: The repository owner.
        :param repo: The repository name.
        :param workflow_id: The workflow file name (e.g., 'ci.yml').
        """
        if not self.conn:
            raise ConnectionError("Database is not connected. Call connect() first.")

        cursor = self.conn.cursor()
        try:
            # Insert/replace workflow run. The REPLACE will cascade deletes to jobs and steps.
            workflow_data = (
                workflow_run.id, owner, repo, workflow_id, workflow_run.name,
                workflow_run.created_at, workflow_run.updated_at, workflow_run.status,
                workflow_run.conclusion, workflow_run.duration_ms, workflow_run.event,
                workflow_run.head_branch, workflow_run.run_number
            )
            cursor.execute("""
                INSERT OR REPLACE INTO workflows (id, owner, repo, workflow_id, name, created_at, updated_at, status, conclusion, duration_ms, event, head_branch, run_number)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, workflow_data)

            for job in workflow_run.jobs:
                job_data = (
                    job.id, job.workflow_run_id, job.name, job.status, job.conclusion,
                    job.started_at, job.completed_at, job.duration_ms,
                    json.dumps(job.matrix_config) if job.matrix_config else None
                )
                cursor.execute("""
                    INSERT OR REPLACE INTO jobs (id, workflow_run_id, name, status, conclusion, started_at, completed_at, duration_ms, matrix_config)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, job_data)

                # Old steps are deleted by cascade from job REPLACE. Insert new ones.
                for step in job.steps:
                    step_data = (
                        job.id, step.name, step.status, step.conclusion, step.number,
                        step.started_at, step.completed_at, step.duration_ms
                    )
                    cursor.execute("""
                        INSERT INTO steps (job_id, name, status, conclusion, number, started_at, completed_at, duration_ms)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, step_data)

            self.conn.commit()
        except sqlite3.Error as e:
            print(f"Database error during save_workflow_run: {e}")
            self.conn.rollback()
            raise

    def clear_all_data(self):
        """
        Deletes all data from workflows, jobs, and steps tables using a transaction.
        """
        if not self.conn:
            raise ConnectionError("Database is not connected. Call connect() first.")

        try:
            self.conn.executescript("DELETE FROM steps; DELETE FROM jobs; DELETE FROM workflows;")
            self.conn.commit()
        except sqlite3.Error as e:
            print(f"Database error during clear_all_data: {e}")
            self.conn.rollback()
            raise

    def get_workflow_runs(self, owner: str, repo: str, workflow_id: str,
                          start_date: Optional[datetime] = None,
                          end_date: Optional[datetime] = None,
                          conclusions: Optional[List[str]] = None,
                          exclude_statuses: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Retrieves workflow runs from the database with optional date filtering.

        :param owner: The repository owner.
        :param repo: The repository name.
        :param workflow_id: The workflow file name (e.g., 'ci.yml').
        :param start_date: Optional start date for filtering.
        :param end_date: Optional end date for filtering.
        :param conclusions: Optional list of conclusions to filter by (e.g., ['success', 'failure']).
        :param exclude_statuses: Optional list of statuses to exclude (default: ['in_progress', 'queued']).
        :return: A list of workflow runs, where each run is a dictionary.
        """
        if not self.conn:
            raise ConnectionError("Database is not connected. Call connect() first.")

        # Validate conclusions if provided
        conclusions = validate_conclusions(conclusions)

        query = "SELECT * FROM workflows WHERE owner = ? AND repo = ? AND workflow_id = ?"
        params = [owner, repo, workflow_id]

        # Exclude incomplete workflows by default
        if exclude_statuses is None:
            exclude_statuses = ['in_progress', 'queued']
        if exclude_statuses:
            query += " AND conclusion IS NOT NULL"
            placeholders = ','.join('?' * len(exclude_statuses))
            query += f" AND status NOT IN ({placeholders})"
            params.extend(exclude_statuses)

        # Filter by conclusions
        if conclusions:
            placeholders = ','.join('?' * len(conclusions))
            query += f" AND conclusion IN ({placeholders})"
            params.extend(conclusions)

        if start_date:
            query += " AND created_at >= ?"
            params.append(start_date)
        if end_date:
            query += " AND created_at <= ?"
            params.append(end_date)

        query += " ORDER BY created_at DESC"

        cursor = self.conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_time_series_metrics(self, owner: str, repo: str, workflow_id: str,
                                start_date: Optional[datetime] = None,
                                end_date: Optional[datetime] = None,
                                period: str = 'day',
                                conclusions: Optional[List[str]] = None,
                                exclude_statuses: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Retrieves time-series aggregated metrics for workflow runs.

        :param owner: The repository owner.
        :param repo: The repository name.
        :param workflow_id: The workflow file name (e.g., 'ci.yml').
        :param start_date: Optional start date for filtering.
        :param end_date: Optional end date for filtering.
        :param period: The time period to group by ('day' or 'week').
        :param conclusions: Optional list of conclusion values to filter by (e.g., ['success', 'failure']).
        :param exclude_statuses: Optional list of statuses to exclude (default: ['in_progress', 'queued']).
        :return: A list of aggregated metrics, where each item is a dictionary.
        """
        if not self.conn:
            raise ConnectionError("Database is not connected. Call connect() first.")

        # Validate conclusions if provided
        conclusions = validate_conclusions(conclusions)

        if period == 'day':
            date_group = "strftime('%Y-%m-%d', w.created_at)"
        elif period == 'week':
            # Return the date of the Monday of that week for easier processing on the frontend.
            # %w is day of week, 0=Sunday. For a Monday-starting week, this calculates the preceding Monday.
            date_group = "date(w.created_at, '-' || strftime('%w', w.created_at) || ' days', '+1 day')"
        else:
            raise ValueError("Invalid period specified. Must be 'day' or 'week'.")

        query = f"""
            SELECT
                {date_group} as period_start,
                COUNT(DISTINCT w.id) as total_runs,
                SUM(CASE WHEN w.conclusion = 'success' THEN 1 ELSE 0 END) as successful_runs,
                SUM(CASE WHEN w.conclusion = 'failure' THEN 1 ELSE 0 END) as failed_runs,
                SUM(CASE WHEN w.conclusion = 'cancelled' THEN 1 ELSE 0 END) as cancelled_runs,
                AVG(job_durations.max_job_duration) as avg_duration_ms,
                AVG(CASE WHEN w.conclusion = 'success' THEN job_durations.max_job_duration END) as avg_success_duration_ms,
                GROUP_CONCAT(CASE WHEN w.conclusion = 'success' THEN job_durations.max_job_duration END) as success_durations_ms_list,
                GROUP_CONCAT(job_durations.max_job_duration) as all_durations_ms_list
            FROM workflows w
            LEFT JOIN (
                SELECT workflow_run_id, MAX(duration_ms) as max_job_duration
                FROM jobs
                WHERE duration_ms IS NOT NULL
                GROUP BY workflow_run_id
            ) job_durations ON w.id = job_durations.workflow_run_id
            WHERE w.owner = ? AND w.repo = ? AND w.workflow_id = ?
        """
        params = [owner, repo, workflow_id]

        # Exclude incomplete workflows by default
        if exclude_statuses is None:
            exclude_statuses = ['in_progress', 'queued']
        if exclude_statuses:
            query += " AND w.conclusion IS NOT NULL"
            placeholders = ','.join('?' * len(exclude_statuses))
            query += f" AND w.status NOT IN ({placeholders})"
            params.extend(exclude_statuses)

        # Filter by conclusions
        if conclusions:
            placeholders = ','.join('?' * len(conclusions))
            query += f" AND w.conclusion IN ({placeholders})"
            params.extend(conclusions)

        if start_date:
            query += " AND w.created_at >= ?"
            params.append(start_date)
        if end_date:
            query += " AND w.created_at <= ?"
            params.append(end_date)

        query += f" GROUP BY period_start ORDER BY period_start ASC"

        cursor = self.conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_job_metrics(self, owner: str, repo: str, workflow_id: str,
                        start_date: datetime, end_date: datetime,
                        conclusions: Optional[List[str]] = None,
                        exclude_statuses: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Retrieves aggregated metrics for each job within a workflow.

        :param owner: The repository owner.
        :param repo: The repository name.
        :param workflow_id: The workflow file name (e.g., 'ci.yml').
        :param start_date: The start date for filtering.
        :param end_date: The end date for filtering.
        :param conclusions: Optional list of conclusion values to filter by (e.g., ['success', 'failure']).
        :param exclude_statuses: Optional list of statuses to exclude (default: ['in_progress', 'queued']).
        :return: A list of aggregated metrics per job name.
        """
        if not self.conn:
            raise ConnectionError("Database is not connected. Call connect() first.")

        # Validate conclusions if provided
        conclusions = validate_conclusions(conclusions)

        query = """
            SELECT
                j.name as job_name,
                COUNT(j.id) as total_runs,
                SUM(CASE WHEN j.conclusion = 'success' THEN 1 ELSE 0 END) as successful_runs,
                GROUP_CONCAT(CASE WHEN j.conclusion = 'success' THEN j.duration_ms END) as success_durations_ms_list
            FROM jobs j
            JOIN workflows w ON j.workflow_run_id = w.id
            WHERE w.owner = ? AND w.repo = ? AND w.workflow_id = ?
              AND w.created_at >= ? AND w.created_at <= ?
        """
        params = [owner, repo, workflow_id, start_date, end_date]
        
        # Exclude incomplete workflows by default
        if exclude_statuses is None:
            exclude_statuses = ['in_progress', 'queued']
        if exclude_statuses:
            query += " AND w.conclusion IS NOT NULL"
            placeholders = ','.join('?' * len(exclude_statuses))
            query += f" AND w.status NOT IN ({placeholders})"
            params.extend(exclude_statuses)

        # Filter by conclusions
        if conclusions:
            placeholders = ','.join('?' * len(conclusions))
            query += f" AND w.conclusion IN ({placeholders})"
            params.extend(conclusions)
        
        query += """
            GROUP BY j.name
            ORDER BY j.name ASC
        """

        cursor = self.conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_existing_workflow_run_ids(self, owner: str, repo: str, workflow_id: str,
                                      start_date: Optional[datetime] = None,
                                      end_date: Optional[datetime] = None) -> set:
        """
        Retrieves a set of existing workflow run IDs from the database for the given parameters.
        This is useful to avoid re-fetching data that already exists.

        :param owner: The repository owner.
        :param repo: The repository name.
        :param workflow_id: The workflow file name (e.g., 'ci.yml').
        :param start_date: Optional start date for filtering.
        :param end_date: Optional end date for filtering.
        :return: A set of workflow run IDs that already exist in the database.
        """
        if not self.conn:
            raise ConnectionError("Database is not connected. Call connect() first.")

        query = "SELECT id FROM workflows WHERE owner = ? AND repo = ? AND workflow_id = ?"
        params = [owner, repo, workflow_id]

        if start_date:
            query += " AND created_at >= ?"
            params.append(start_date)
        if end_date:
            query += " AND created_at <= ?"
            params.append(end_date)

        cursor = self.conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return {row[0] for row in rows}

    def get_slowest_jobs(self, owner: str, repo: str, workflow_id: str,
                        start_date: datetime, end_date: datetime,
                        limit: int = 10,
                        conclusions: Optional[List[str]] = None,
                        exclude_statuses: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Retrieves the slowest jobs by P95 duration within a workflow.

        :param owner: The repository owner.
        :param repo: The repository name.
        :param workflow_id: The workflow file name (e.g., 'ci.yml').
        :param start_date: The start date for filtering.
        :param end_date: The end date for filtering.
        :param limit: Number of slowest jobs to return.
        :param conclusions: Optional list of conclusion values to filter by.
        :param exclude_statuses: Optional list of statuses to exclude (default: ['in_progress', 'queued']).
        :return: A list of slowest jobs with aggregated metrics.
        """
        if not self.conn:
            raise ConnectionError("Database is not connected. Call connect() first.")

        # Validate conclusions if provided
        conclusions = validate_conclusions(conclusions)

        query = """
            SELECT
                j.name as job_name,
                COUNT(j.id) as total_runs,
                SUM(CASE WHEN j.conclusion = 'success' THEN 1 ELSE 0 END) as successful_runs,
                AVG(CASE WHEN j.conclusion = 'success' THEN j.duration_ms END) as avg_success_duration_ms,
                GROUP_CONCAT(CASE WHEN j.conclusion = 'success' THEN j.duration_ms END) as success_durations_ms_list
            FROM jobs j
            JOIN workflows w ON j.workflow_run_id = w.id
            WHERE w.owner = ? AND w.repo = ? AND w.workflow_id = ?
              AND w.created_at >= ? AND w.created_at <= ?
        """
        params = [owner, repo, workflow_id, start_date, end_date]
        
        # Exclude incomplete workflows by default
        if exclude_statuses is None:
            exclude_statuses = ['in_progress', 'queued']
        if exclude_statuses:
            query += " AND w.conclusion IS NOT NULL"
            placeholders = ','.join('?' * len(exclude_statuses))
            query += f" AND w.status NOT IN ({placeholders})"
            params.extend(exclude_statuses)

        # Filter by conclusions
        if conclusions:
            placeholders = ','.join('?' * len(conclusions))
            query += f" AND w.conclusion IN ({placeholders})"
            params.extend(conclusions)
        
        query += """
            GROUP BY j.name
            HAVING COUNT(CASE WHEN j.conclusion = 'success' THEN 1 END) > 0
            ORDER BY avg_success_duration_ms DESC
            LIMIT ?
        """
        params.append(limit)

        cursor = self.conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_step_metrics(self, owner: str, repo: str, workflow_id: str,
                        start_date: datetime, end_date: datetime,
                        job_name: Optional[str] = None,
                        conclusions: Optional[List[str]] = None,
                        exclude_statuses: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Retrieves aggregated metrics for steps, optionally filtered by job name.

        :param owner: The repository owner.
        :param repo: The repository name.
        :param workflow_id: The workflow file name (e.g., 'ci.yml').
        :param start_date: The start date for filtering.
        :param end_date: The end date for filtering.
        :param job_name: Optional job name to filter steps.
        :param conclusions: Optional list of conclusion values to filter by.
        :param exclude_statuses: Optional list of statuses to exclude (default: ['in_progress', 'queued']).
        :return: A list of aggregated metrics per step name.
        """
        if not self.conn:
            raise ConnectionError("Database is not connected. Call connect() first.")

        # Validate conclusions if provided
        conclusions = validate_conclusions(conclusions)

        query = """
            SELECT
                s.name as step_name,
                j.name as job_name,
                COUNT(s.id) as total_runs,
                SUM(CASE WHEN s.conclusion = 'success' THEN 1 ELSE 0 END) as successful_runs,
                AVG(s.duration_ms) as avg_duration_ms,
                AVG(CASE WHEN s.conclusion = 'success' THEN s.duration_ms END) as avg_success_duration_ms,
                GROUP_CONCAT(CASE WHEN s.conclusion = 'success' THEN s.duration_ms END) as success_durations_ms_list
            FROM steps s
            JOIN jobs j ON s.job_id = j.id
            JOIN workflows w ON j.workflow_run_id = w.id
            WHERE w.owner = ? AND w.repo = ? AND w.workflow_id = ?
              AND w.created_at >= ? AND w.created_at <= ?
        """
        params = [owner, repo, workflow_id, start_date, end_date]
        
        # Exclude incomplete workflows by default
        if exclude_statuses is None:
            exclude_statuses = ['in_progress', 'queued']
        if exclude_statuses:
            query += " AND w.conclusion IS NOT NULL"
            placeholders = ','.join('?' * len(exclude_statuses))
            query += f" AND w.status NOT IN ({placeholders})"
            params.extend(exclude_statuses)

        if job_name:
            query += " AND j.name = ?"
            params.append(job_name)
        
        # Filter by conclusions
        if conclusions:
            placeholders = ','.join('?' * len(conclusions))
            query += f" AND w.conclusion IN ({placeholders})"
            params.extend(conclusions)
        
        query += """
            GROUP BY s.name, j.name
            ORDER BY avg_duration_ms DESC
        """

        cursor = self.conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_slowest_steps(self, owner: str, repo: str, workflow_id: str,
                         start_date: datetime, end_date: datetime,
                         job_name: Optional[str] = None,
                         limit: int = 10,
                         conclusions: Optional[List[str]] = None,
                         exclude_statuses: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Retrieves the slowest steps by average duration, optionally filtered by job.

        :param owner: The repository owner.
        :param repo: The repository name.
        :param workflow_id: The workflow file name (e.g., 'ci.yml').
        :param start_date: The start date for filtering.
        :param end_date: The end date for filtering.
        :param job_name: Optional job name to filter steps.
        :param limit: Number of slowest steps to return.
        :param conclusions: Optional list of conclusion values to filter by.
        :param exclude_statuses: Optional list of statuses to exclude (default: ['in_progress', 'queued']).
        :return: A list of slowest steps with aggregated metrics.
        """
        if not self.conn:
            raise ConnectionError("Database is not connected. Call connect() first.")

        # Validate conclusions if provided
        conclusions = validate_conclusions(conclusions)

        query = """
            SELECT
                s.name as step_name,
                j.name as job_name,
                COUNT(s.id) as total_runs,
                SUM(CASE WHEN s.conclusion = 'success' THEN 1 ELSE 0 END) as successful_runs,
                AVG(CASE WHEN s.conclusion = 'success' THEN s.duration_ms END) as avg_success_duration_ms,
                GROUP_CONCAT(CASE WHEN s.conclusion = 'success' THEN s.duration_ms END) as success_durations_ms_list
            FROM steps s
            JOIN jobs j ON s.job_id = j.id
            JOIN workflows w ON j.workflow_run_id = w.id
            WHERE w.owner = ? AND w.repo = ? AND w.workflow_id = ?
              AND w.created_at >= ? AND w.created_at <= ?
        """
        params = [owner, repo, workflow_id, start_date, end_date]
        
        # Exclude incomplete workflows by default
        if exclude_statuses is None:
            exclude_statuses = ['in_progress', 'queued']
        if exclude_statuses:
            query += " AND w.conclusion IS NOT NULL"
            placeholders = ','.join('?' * len(exclude_statuses))
            query += f" AND w.status NOT IN ({placeholders})"
            params.extend(exclude_statuses)

        if job_name:
            query += " AND j.name = ?"
            params.append(job_name)
        
        # Filter by conclusions
        if conclusions:
            placeholders = ','.join('?' * len(conclusions))
            query += f" AND w.conclusion IN ({placeholders})"
            params.extend(conclusions)
        
        query += """
            GROUP BY s.name, j.name
            HAVING COUNT(CASE WHEN s.conclusion = 'success' THEN 1 END) > 0
            ORDER BY avg_success_duration_ms DESC
            LIMIT ?
        """
        params.append(limit)

        cursor = self.conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_job_time_series(self, owner: str, repo: str, workflow_id: str,
                           job_name: str,
                           start_date: datetime, end_date: datetime,
                           period: str = 'day',
                           conclusions: Optional[List[str]] = None,
                           exclude_statuses: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Retrieves time-series metrics for a specific job.

        :param owner: The repository owner.
        :param repo: The repository name.
        :param workflow_id: The workflow file name (e.g., 'ci.yml').
        :param job_name: The job name to analyze.
        :param start_date: The start date for filtering.
        :param end_date: The end date for filtering.
        :param period: The time period to group by ('day' or 'week').
        :param conclusions: Optional list of conclusion values to filter by.
        :param exclude_statuses: Optional list of statuses to exclude (default: ['in_progress', 'queued']).
        :return: A list of time-series metrics for the job.
        """
        if not self.conn:
            raise ConnectionError("Database is not connected. Call connect() first.")

        # Validate conclusions if provided
        conclusions = validate_conclusions(conclusions)

        if period == 'day':
            date_group = "strftime('%Y-%m-%d', w.created_at)"
        elif period == 'week':
            date_group = "date(w.created_at, '-' || strftime('%w', w.created_at) || ' days', '+1 day')"
        else:
            raise ValueError("Invalid period specified. Must be 'day' or 'week'.")

        query = f"""
            SELECT
                {date_group} as period_start,
                COUNT(j.id) as total_runs,
                SUM(CASE WHEN j.conclusion = 'success' THEN 1 ELSE 0 END) as successful_runs,
                AVG(j.duration_ms) as avg_duration_ms,
                AVG(CASE WHEN j.conclusion = 'success' THEN j.duration_ms END) as avg_success_duration_ms,
                GROUP_CONCAT(CASE WHEN j.conclusion = 'success' THEN j.duration_ms END) as success_durations_ms_list
            FROM jobs j
            JOIN workflows w ON j.workflow_run_id = w.id
            WHERE w.owner = ? AND w.repo = ? AND w.workflow_id = ?
              AND j.name = ?
              AND w.created_at >= ? AND w.created_at <= ?
        """
        params = [owner, repo, workflow_id, job_name, start_date, end_date]
        
        # Exclude incomplete workflows by default
        if exclude_statuses is None:
            exclude_statuses = ['in_progress', 'queued']
        if exclude_statuses:
            query += " AND w.conclusion IS NOT NULL"
            placeholders = ','.join('?' * len(exclude_statuses))
            query += f" AND w.status NOT IN ({placeholders})"
            params.extend(exclude_statuses)

        # Filter by conclusions
        if conclusions:
            placeholders = ','.join('?' * len(conclusions))
            query += f" AND w.conclusion IN ({placeholders})"
            params.extend(conclusions)
        
        query += f" GROUP BY period_start ORDER BY period_start ASC"

        cursor = self.conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_job_executions_with_details(self, owner: str, repo: str, workflow_id: str,
                                       job_name: str,
                                       start_date: datetime, end_date: datetime,
                                       conclusions: Optional[List[str]] = None,
                                       exclude_statuses: Optional[List[str]] = None,
                                       limit: Optional[int] = None,
                                       order_by: str = 'duration_desc') -> List[Dict[str, Any]]:
        """
        Retrieves individual job executions with workflow context for GitHub URL generation.
        
        :param owner: The repository owner.
        :param repo: The repository name.
        :param workflow_id: The workflow file name (e.g., 'ci.yml').
        :param job_name: The job name to filter by.
        :param start_date: The start date for filtering.
        :param end_date: The end date for filtering.
        :param conclusions: Optional list of workflow conclusions to filter by.
        :param exclude_statuses: Optional list of statuses to exclude (default: ['in_progress', 'queued']).
        :param limit: Optional limit on number of results.
        :param order_by: Sort order - 'duration_desc', 'duration_asc', 'created_desc', 'created_asc'.
        :return: List of dicts with keys: job_id, workflow_run_id, job_name, job_conclusion,
                 job_duration_ms, job_started_at, job_completed_at, workflow_conclusion, 
                 created_at, owner, repo
        """
        if not self.conn:
            raise ConnectionError("Database is not connected. Call connect() first.")

        # Validate conclusions if provided
        conclusions = validate_conclusions(conclusions)

        # Validate order_by parameter
        valid_order_by = {
            'duration_desc': 'j.duration_ms DESC',
            'duration_asc': 'j.duration_ms ASC',
            'created_desc': 'w.created_at DESC',
            'created_asc': 'w.created_at ASC'
        }
        if order_by not in valid_order_by:
            raise ValueError(f"Invalid order_by value: {order_by}. "
                           f"Valid values: {list(valid_order_by.keys())}")

        query = """
            SELECT
                j.id as job_id,
                j.workflow_run_id,
                j.name as job_name,
                j.conclusion as job_conclusion,
                j.duration_ms as job_duration_ms,
                j.started_at as job_started_at,
                j.completed_at as job_completed_at,
                w.conclusion as workflow_conclusion,
                w.created_at,
                w.owner,
                w.repo
            FROM jobs j
            JOIN workflows w ON j.workflow_run_id = w.id
            WHERE w.owner = ? AND w.repo = ? AND w.workflow_id = ?
              AND j.name = ?
              AND w.created_at >= ? AND w.created_at <= ?
        """
        params = [owner, repo, workflow_id, job_name, start_date, end_date]
        
        # Exclude incomplete workflows by default
        if exclude_statuses is None:
            exclude_statuses = ['in_progress', 'queued']
        if exclude_statuses:
            query += " AND w.conclusion IS NOT NULL"
            placeholders = ','.join('?' * len(exclude_statuses))
            query += f" AND w.status NOT IN ({placeholders})"
            params.extend(exclude_statuses)

        # Filter by conclusions
        if conclusions:
            placeholders = ','.join('?' * len(conclusions))
            query += f" AND w.conclusion IN ({placeholders})"
            params.extend(conclusions)
        
        # Add ordering
        query += f" ORDER BY {valid_order_by[order_by]}"
        
        # Add limit if specified
        if limit:
            query += " LIMIT ?"
            params.append(limit)

        cursor = self.conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_step_metrics_with_pattern(self, owner: str, repo: str, workflow_id: str,
                                     job_name: str,
                                     step_pattern: str,
                                     start_date: datetime, end_date: datetime,
                                     conclusions: Optional[List[str]] = None,
                                     exclude_statuses: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Retrieves step metrics matching a SQL LIKE pattern.
        
        :param owner: The repository owner.
        :param repo: The repository name.
        :param workflow_id: The workflow file name (e.g., 'ci.yml').
        :param job_name: The job name to filter by.
        :param step_pattern: SQL LIKE pattern (e.g., 'Build linux-x64-%').
        :param start_date: The start date for filtering.
        :param end_date: The end date for filtering.
        :param conclusions: Optional list of workflow conclusions to filter by.
        :param exclude_statuses: Optional list of statuses to exclude (default: ['in_progress', 'queued']).
        :return: List of step metrics grouped by workflow_run_id and step_name.
        """
        if not self.conn:
            raise ConnectionError("Database is not connected. Call connect() first.")

        # Validate conclusions if provided
        conclusions = validate_conclusions(conclusions)

        query = """
            SELECT
                w.id as workflow_run_id,
                w.created_at,
                s.name as step_name,
                s.duration_ms,
                s.conclusion as step_conclusion,
                s.started_at as step_started_at,
                s.completed_at as step_completed_at
            FROM steps s
            JOIN jobs j ON s.job_id = j.id
            JOIN workflows w ON j.workflow_run_id = w.id
            WHERE w.owner = ? AND w.repo = ? AND w.workflow_id = ?
              AND j.name = ?
              AND s.name LIKE ?
              AND w.created_at >= ? AND w.created_at <= ?
        """
        params = [owner, repo, workflow_id, job_name, step_pattern, start_date, end_date]
        
        # Exclude incomplete workflows by default
        if exclude_statuses is None:
            exclude_statuses = ['in_progress', 'queued']
        if exclude_statuses:
            query += " AND w.conclusion IS NOT NULL"
            placeholders = ','.join('?' * len(exclude_statuses))
            query += f" AND w.status NOT IN ({placeholders})"
            params.extend(exclude_statuses)

        # Filter by conclusions
        if conclusions:
            placeholders = ','.join('?' * len(conclusions))
            query += f" AND w.conclusion IN ({placeholders})"
            params.extend(conclusions)
        
        query += " ORDER BY w.created_at DESC, s.name ASC"

        cursor = self.conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_workflow_job_based_durations(self, owner: str, repo: str, workflow_id: str,
                                        start_date: Optional[datetime] = None,
                                        end_date: Optional[datetime] = None,
                                        conclusions: Optional[List[str]] = None,
                                        exclude_statuses: Optional[List[str]] = None) -> List[int]:
        """
        Returns list of job-based workflow durations (max job duration per workflow).
        
        This method calculates workflow duration by taking the maximum job duration
        within each workflow run, which provides accurate performance metrics that
        aren't distorted by re-runs or idle time between job executions.
        
        :param owner: The repository owner.
        :param repo: The repository name.
        :param workflow_id: The workflow file name (e.g., 'ci.yml').
        :param start_date: Optional start date for filtering.
        :param end_date: Optional end date for filtering.
        :param conclusions: Optional list of conclusion values to filter by (e.g., ['success', 'failure']).
        :param exclude_statuses: Optional list of statuses to exclude (default: ['in_progress', 'queued']).
        :return: A list of workflow durations in milliseconds (integers).
        """
        if not self.conn:
            raise ConnectionError("Database is not connected. Call connect() first.")

        # Validate conclusions if provided
        conclusions = validate_conclusions(conclusions)

        query = """
            SELECT MAX(j.duration_ms) as max_job_duration
            FROM workflows w
            LEFT JOIN jobs j ON w.id = j.workflow_run_id
            WHERE w.owner = ? AND w.repo = ? AND w.workflow_id = ?
              AND j.duration_ms IS NOT NULL
        """
        params = [owner, repo, workflow_id]

        # Exclude incomplete workflows by default
        if exclude_statuses is None:
            exclude_statuses = ['in_progress', 'queued']
        if exclude_statuses:
            query += " AND w.conclusion IS NOT NULL"
            placeholders = ','.join('?' * len(exclude_statuses))
            query += f" AND w.status NOT IN ({placeholders})"
            params.extend(exclude_statuses)

        # Filter by conclusions
        if conclusions:
            placeholders = ','.join('?' * len(conclusions))
            query += f" AND w.conclusion IN ({placeholders})"
            params.extend(conclusions)

        if start_date:
            query += " AND w.created_at >= ?"
            params.append(start_date)
        if end_date:
            query += " AND w.created_at <= ?"
            params.append(end_date)

        query += " GROUP BY w.id HAVING max_job_duration IS NOT NULL"

        cursor = self.conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        # Extract durations as a list of integers
        durations = [int(row['max_job_duration']) for row in rows if row['max_job_duration'] is not None]
        return durations

if __name__ == '__main__':
    # Example usage: create and initialize the database
    db_file = "gha_metrics.db"
    print(f"Creating and initializing database at '{db_file}'...")
    try:
        with GHADatabase(db_path=db_file) as db:
            db.initialize_schema()
        print("Database setup complete.")
    except Exception as e:
        print(f"Failed to set up database: {e}")
