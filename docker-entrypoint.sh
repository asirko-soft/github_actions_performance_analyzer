#!/bin/sh
set -e

echo "=== GitHub Actions Performance Analyzer - Container Startup ==="

# Create data directories if they don't exist
echo "Creating data directories..."
mkdir -p /app/data /app/cache /app/reports

# Set proper permissions (ignore errors if already set by volume mount)
chmod 755 /app/data /app/cache /app/reports 2>/dev/null || true

# Check for GitHub token
if [ -z "$GITHUB_TOKEN" ]; then
    echo "WARNING: GITHUB_TOKEN environment variable is not set."
    echo "You can configure your GitHub token via the web interface at http://localhost:5000"
    echo "Or restart the container with -e GITHUB_TOKEN=your_token"
else
    echo "GitHub token detected from environment variable."
fi

# Initialize database schema if database doesn't exist or needs initialization
echo "Checking database schema..."
python3 << 'PYTHON_SCRIPT'
import os
import sys
from database import GHADatabase

db_path = '/app/data/gha_metrics.db'
db_exists = os.path.exists(db_path)

try:
    db = GHADatabase(db_path)
    db.connect()
    
    if not db_exists:
        print(f"Initializing new database at {db_path}...")
        db.initialize_schema()
        print("Database schema initialized successfully.")
    else:
        print(f"Database found at {db_path}. Verifying schema...")
        # Verify schema by checking if required tables exist
        cursor = db.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        required_tables = ['workflows', 'jobs', 'steps']
        
        if all(table in tables for table in required_tables):
            print("Database schema verified successfully.")
        else:
            print("WARNING: Database schema appears incomplete. Reinitializing...")
            db.initialize_schema()
            print("Database schema reinitialized.")
    
    db.close()
    sys.exit(0)
except Exception as e:
    print(f"ERROR: Failed to initialize database: {e}", file=sys.stderr)
    sys.exit(1)
PYTHON_SCRIPT

if [ $? -ne 0 ]; then
    echo "ERROR: Database initialization failed. Exiting."
    exit 1
fi

echo "Starting Flask application with Gunicorn..."
echo "Application will be available at http://0.0.0.0:5000"
echo "=========================================="

# Start the application with Gunicorn
# - bind to all interfaces on port 5000
# - use 1 worker process (required for in-memory task manager to work across requests)
# - use 4 threads per worker for concurrency
# - set timeout to 300 seconds for long-running API calls and data fetches
# - enable access logging
exec gunicorn --bind 0.0.0.0:5000 \
    --workers 1 \
    --threads 4 \
    --timeout 300 \
    --access-logfile - \
    --error-logfile - \
    --log-level info \
    app:app
