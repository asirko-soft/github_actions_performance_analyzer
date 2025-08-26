import sqlite3
from typing import Optional, List, Dict, Any
import json
from datetime import datetime

from data_models import WorkflowRun

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
                          end_date: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """
        Retrieves workflow runs from the database with optional date filtering.

        :param owner: The repository owner.
        :param repo: The repository name.
        :param workflow_id: The workflow file name (e.g., 'ci.yml').
        :param start_date: Optional start date for filtering.
        :param end_date: Optional end date for filtering.
        :return: A list of workflow runs, where each run is a dictionary.
        """
        if not self.conn:
            raise ConnectionError("Database is not connected. Call connect() first.")

        query = "SELECT * FROM workflows WHERE owner = ? AND repo = ? AND workflow_id = ?"
        params = [owner, repo, workflow_id]

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
                                period: str = 'day') -> List[Dict[str, Any]]:
        """
        Retrieves time-series aggregated metrics for workflow runs.

        :param owner: The repository owner.
        :param repo: The repository name.
        :param workflow_id: The workflow file name (e.g., 'ci.yml').
        :param start_date: Optional start date for filtering.
        :param end_date: Optional end date for filtering.
        :param period: The time period to group by ('day' or 'week').
        :return: A list of aggregated metrics, where each item is a dictionary.
        """
        if not self.conn:
            raise ConnectionError("Database is not connected. Call connect() first.")

        if period == 'day':
            date_group = "strftime('%Y-%m-%d', created_at)"
        elif period == 'week':
            # Using %Y-%W for year and week number (Monday as first day)
            date_group = "strftime('%Y-%W', created_at)"
        else:
            raise ValueError("Invalid period specified. Must be 'day' or 'week'.")

        query = f"""
            SELECT
                {date_group} as period_start,
                COUNT(*) as total_runs,
                SUM(CASE WHEN conclusion = 'success' THEN 1 ELSE 0 END) as successful_runs,
                SUM(CASE WHEN conclusion = 'failure' THEN 1 ELSE 0 END) as failed_runs,
                SUM(CASE WHEN conclusion = 'cancelled' THEN 1 ELSE 0 END) as cancelled_runs,
                AVG(duration_ms) as avg_duration_ms,
                AVG(CASE WHEN conclusion = 'success' THEN duration_ms END) as avg_success_duration_ms,
                GROUP_CONCAT(CASE WHEN conclusion = 'success' THEN duration_ms END) as success_durations_ms_list,
                GROUP_CONCAT(duration_ms) as all_durations_ms_list
            FROM workflows
            WHERE owner = ? AND repo = ? AND workflow_id = ?
        """
        params = [owner, repo, workflow_id]

        if start_date:
            query += " AND created_at >= ?"
            params.append(start_date)
        if end_date:
            query += " AND created_at <= ?"
            params.append(end_date)

        query += f" GROUP BY period_start ORDER BY period_start ASC"

        cursor = self.conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_job_metrics(self, owner: str, repo: str, workflow_id: str,
                        start_date: datetime, end_date: datetime) -> List[Dict[str, Any]]:
        """
        Retrieves aggregated metrics for each job within a workflow.

        :param owner: The repository owner.
        :param repo: The repository name.
        :param workflow_id: The workflow file name (e.g., 'ci.yml').
        :param start_date: The start date for filtering.
        :param end_date: The end date for filtering.
        :return: A list of aggregated metrics per job name.
        """
        if not self.conn:
            raise ConnectionError("Database is not connected. Call connect() first.")

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
            GROUP BY j.name
            ORDER BY j.name ASC
        """
        params = [owner, repo, workflow_id, start_date, end_date]

        cursor = self.conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

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
