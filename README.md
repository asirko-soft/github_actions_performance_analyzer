# GitHub Actions Performance Analyzer

Analyze GitHub Actions workflow performance with detailed metrics and trends.

## 📖 User Manual: Step-by-Step Guide

Follow these steps to get the application running quickly using Docker.

### Step 1: Build the Docker Image (Recommended)

Since the public image might be outdated or missing dependencies, it is recommended to build the image locally.

```bash
docker build -t gha-performance-analyzer:local .
```

Alternatively, you can pull from GHCR (may be outdated):
```bash
docker pull ghcr.io/asirko-soft/github_actions_performance_analyzer:latest
```

### Step 2: Prepare Data Directories (Persistence)

To ensure your data (database, cache, reports) is saved even if you restart the container, create a local directory to store it.

```bash
# Create a directory for the application data
mkdir -p gha-data
```

**Where is the database?**
The application uses a SQLite database named `gha_metrics.db`. By mounting a volume (as shown in the next step), this file will be stored in your local `gha-data` directory. This allows you to back it up or inspect it if needed.

### Step 3: Start the Application

Run the following command to start the application. This command mounts your local `gha-data` directory to the container so your data persists.

```bash
docker run -d \
  -p 5002:5000 \
  -v $(pwd)/gha-data:/app/data \
  --name gha-analyzer \
  gha-performance-analyzer:local
```

If you pulled the image from GHCR, replace `gha-performance-analyzer:local` with `ghcr.io/asirko-soft/github_actions_performance_analyzer:latest`.

*   `-d`: Runs the container in the background (detached mode).
*   `-p 5002:5000`: Maps port 5002 on your machine to the container.
*   `-v $(pwd)/gha-data:/app/data`: Saves your database and config to your local folder.

### Step 4: Access the Dashboard

Open your web browser and navigate to:

**[http://localhost:5002](http://localhost:5002)**

You should see the GitHub Actions Performance Dashboard.

### Step 5: Configuration & Data Ingestion

1.  **Configure GitHub Token:**
    *   Click the **Settings (⚙️)** button in the top right corner of the dashboard.
    *   Enter your **GitHub Personal Access Token**. This is required to fetch data from the GitHub API.
    *   Click **Save Token**.
    
    **Required Permissions:**
    The token needs the following scopes:
    *   `repo` (Full control of private repositories) - Required for private repos.
    *   `public_repo` (Access public repositories) - Sufficient for public repos.
    *   `workflow` (Update GitHub Action workflows) - Often included with `repo`, but good to verify.
    *   `read:user` (Read all user profile data) - Used for token validation.

2.  **Fetch Data:**
    *   In the main control panel, enter the **Repository Owner**, **Repository Name**, and **Workflow ID** (e.g., `tests.yaml`) you want to analyze.
    *   Select a **Start Date** and **End Date**.
    *   Click **Fetch Workflow Data**.
    *   The application will download the workflow run data and populate the dashboard.

---

## 🛠️ Developer Guide

If you want to run the application locally for development or testing, follow these instructions.

### Setup (Local Development)

```bash
# Install dependencies
pip install -r requirements.txt

# Set your GitHub token (optional, can also be set in UI)
export GITHUB_TOKEN="your_github_token"
```

### Run App Server

Start the Flask web server:

```bash
python app.py
```

Navigate to http://localhost:5002 to view the dashboard.

### Run Tests

```bash
python test_script.py
```

### Manual Data Ingestion (CLI)

You can also collect data using the command line:

```bash
python ingest.py --owner your_org --repo your_repo --workflow-id workflow.yml --weeks 4
```

---

## Features

- 📊 Interactive dashboard with performance metrics
- ⏱️ P50, P95, P99 duration percentiles
- 📈 Time-series trends and success rates
- 🔍 Job-level performance breakdown
- 📥 CSV data export
- 🔄 Flakiness detection and reporting

---

## 🐳 Advanced Docker Deployment

For more complex deployments, you can use named volumes or Docker Compose.

### Using Named Volumes

```bash
docker run -d -p 5002:5000 \
  -v gha-data:/app/data \
  -v gha-cache:/app/cache \
  -v gha-reports:/app/reports \
  --name gha-analyzer \
  gha-performance-analyzer:local
```

### Docker Compose

Create a `docker-compose.yml` file:

```yaml
version: '3.8'

services:
  gha-analyzer:
    image: gha-performance-analyzer:local
    build: .
    container_name: gha-analyzer
    ports:
      - "5002:5000"
    volumes:
      - gha-data:/app/data
    restart: unless-stopped

volumes:
  gha-data:
```

Run with:
```bash
docker-compose up -d
```
