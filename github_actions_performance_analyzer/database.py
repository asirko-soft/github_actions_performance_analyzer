import sqlite3
from typing import Optional

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
