import argparse
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

from github_api_client import GitHubApiClient
from data_collector import DataCollector
from database import GHADatabase

def main():
    load_dotenv() # Load environment variables from .env file

    parser = argparse.ArgumentParser(description="Ingest GitHub Actions workflow run data into the database.")
    parser.add_argument("--token", type=str, default=os.getenv("GITHUB_TOKEN"),
                        help="GitHub Personal Access Token. Can also be set via GITHUB_TOKEN environment variable.")
    parser.add_argument("--owner", type=str, required=True, help="Repository owner (user or organization).")
    parser.add_argument("--repo", type=str, required=True, help="Repository name.")
    parser.add_argument("--workflow-id", dest="workflow_id", type=str, required=True,
                        help="Workflow ID or workflow file name (e.g., 'ci.yml').")
    parser.add_argument("--weeks", type=int, default=4, help="Number of past weeks of data to ingest.")
    parser.add_argument("--force-refresh", action="store_true",
                        help="Clear all existing data from the database before ingesting.")
    parser.add_argument("--skip-incomplete", action="store_true",
                        help="Skip ingesting workflows with status 'in_progress' or 'queued'.")

    args = parser.parse_args()

    if not args.token:
        print("Error: GitHub Personal Access Token not provided. "
              "Please set GITHUB_TOKEN environment variable or use --token argument.")
        return

    db_file = "gha_metrics.db"
    client = GitHubApiClient(args.token)

    print(f"Starting data ingestion for workflow '{args.workflow_id}' in {args.owner}/{args.repo}...")
    print(f"Database file: {db_file}")

    try:
        with GHADatabase(db_path=db_file) as db:
            db.initialize_schema()

            if args.force_refresh:
                print("Force refresh requested. Clearing all existing data...")
                db.clear_all_data()
                print("Data cleared.")

            collector = DataCollector(client, db)

            end_date = datetime.utcnow()
            start_date = end_date - timedelta(weeks=args.weeks)

            print(f"Collecting data for the past {args.weeks} weeks (from {start_date.isoformat()} to {end_date.isoformat()})")
            if args.skip_incomplete:
                print("Skip incomplete workflows option enabled. Workflows with status 'in_progress' or 'queued' will be skipped.")

            result = collector.collect_workflow_data(
                owner=args.owner, repo=args.repo, workflow_id=args.workflow_id,
                start_date=start_date, end_date=end_date,
                skip_incomplete=args.skip_incomplete
            )

            print(f"\nIngestion complete. Successfully collected and stored data for {result['runs_collected']} workflow runs.")
            if result['incomplete_runs_stored'] > 0:
                print(f"Note: {result['incomplete_runs_stored']} incomplete workflow(s) (in_progress/queued) were stored.")
            if result['incomplete_runs_skipped'] > 0:
                print(f"Skipped {result['incomplete_runs_skipped']} incomplete workflow(s) (in_progress/queued).")

    except Exception as e:
        print(f"An error occurred during data ingestion: {e}")

if __name__ == "__main__":
    main()


