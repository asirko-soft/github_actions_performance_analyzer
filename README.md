# GitHub Actions Performance Analyzer

Analyze GitHub Actions workflow performance with detailed metrics and trends.

## Setup

```bash
pip install -r requirements.txt
export GITHUB_TOKEN="your_github_token"
```

## Run Tests

```bash
python test_script.py
```

## Ingest Data

Collect workflow data from GitHub API and store in local database:

```bash
python ingest.py --owner your_org --repo your_repo --workflow-id workflow.yml --weeks 4
```

Optional flags:
- `--weeks N`: Number of past weeks to collect (default: 4)
- `--force-refresh`: Clear existing data before ingesting

## Run App Server

Start the Flask web server:

```bash
python app.py
```

Navigate to http://localhost:5000 to view the dashboard.

## Features

- 📊 Interactive dashboard with performance metrics
- ⏱️ P50, P95, P99 duration percentiles
- 📈 Time-series trends and success rates
- 🔍 Job-level performance breakdown
- 📥 CSV data export


