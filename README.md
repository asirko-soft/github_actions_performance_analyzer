# GitHub Actions Performance Analyzer

Analyze GitHub Actions workflow performance with detailed metrics and trends.

## Quick Start with Docker (Recommended)

The easiest way to run the application is using Docker:

```bash
docker run -d -p 5000:5000 \
  -e GITHUB_TOKEN=your_github_token \
  -v gha-data:/app/data \
  -v gha-cache:/app/cache \
  -v gha-reports:/app/reports \
  --name gha-analyzer \
  username/gha-performance-analyzer:latest
```

Access the dashboard at http://localhost:5000

## Setup (Local Development)

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



---

## Docker Deployment

### Running with Docker

#### Option 1: Environment Variable Configuration

Provide your GitHub token as an environment variable at startup:

```bash
docker run -d \
  -p 5000:5000 \
  -e GITHUB_TOKEN=ghp_your_github_token_here \
  -v gha-data:/app/data \
  -v gha-cache:/app/cache \
  -v gha-reports:/app/reports \
  --name gha-analyzer \
  username/gha-performance-analyzer:latest
```

#### Option 2: Web-Based Token Configuration

Start the container without a token and configure it through the web interface:

```bash
docker run -d \
  -p 5000:5000 \
  -v gha-data:/app/data \
  -v gha-cache:/app/cache \
  -v gha-reports:/app/reports \
  --name gha-analyzer \
  username/gha-performance-analyzer:latest
```

Then:
1. Navigate to http://localhost:5000
2. Click the settings icon in the dashboard header
3. Enter your GitHub token in the configuration modal
4. The token will be encrypted and persisted across container restarts

### Volume Mounts

The application uses three volume mounts for data persistence:

| Volume Mount | Purpose | Contents |
|--------------|---------|----------|
| `/app/data` | Database and configuration | `gha_metrics.db` (SQLite database)<br>`config.json` (encrypted token storage) |
| `/app/cache` | API response cache | `*.json` (cached GitHub API responses) |
| `/app/reports` | Generated reports | `*.csv` (exported metrics)<br>`*.png` (generated charts) |

**Using named volumes** (recommended):
```bash
docker run -d -p 5000:5000 \
  -v gha-data:/app/data \
  -v gha-cache:/app/cache \
  -v gha-reports:/app/reports \
  username/gha-performance-analyzer:latest
```

**Using bind mounts** (for direct file access):
```bash
docker run -d -p 5000:5000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/cache:/app/cache \
  -v $(pwd)/reports:/app/reports \
  username/gha-performance-analyzer:latest
```

### Docker Compose

Create a `docker-compose.yml` file:

```yaml
version: '3.8'

services:
  gha-analyzer:
    image: username/gha-performance-analyzer:latest
    container_name: gha-analyzer
    ports:
      - "5000:5000"
    environment:
      # Optional: Provide token via environment variable
      - GITHUB_TOKEN=${GITHUB_TOKEN}
    volumes:
      - gha-data:/app/data
      - gha-cache:/app/cache
      - gha-reports:/app/reports
    restart: unless-stopped

volumes:
  gha-data:
  gha-cache:
  gha-reports:
```

Run with:
```bash
# Using environment variable from .env file
echo "GITHUB_TOKEN=your_token" > .env
docker-compose up -d

# Or configure token via web interface after startup
docker-compose up -d
```

Manage the service:
```bash
docker-compose down          # Stop and remove container
docker-compose logs -f       # View logs
docker-compose restart       # Restart service
```

### Migrating Existing Data to Docker

If you have existing data from a local installation, you can migrate it to Docker:

#### Step 1: Prepare Your Data

Identify your existing data files:
- Database: `gha_metrics.db`
- Cache files: `cache/*.json`
- Reports: `reports/*.csv`, `reports/*.png`

#### Step 2: Create Volume Mounts

**Option A: Using bind mounts** (easiest for migration)

Copy your existing data to a local directory:
```bash
mkdir -p ./docker-data/data ./docker-data/cache ./docker-data/reports

# Copy database
cp gha_metrics.db ./docker-data/data/

# Copy cache files
cp -r cache/* ./docker-data/cache/

# Copy reports
cp -r reports/* ./docker-data/reports/
```

Start the container with bind mounts:
```bash
docker run -d -p 5000:5000 \
  -v $(pwd)/docker-data/data:/app/data \
  -v $(pwd)/docker-data/cache:/app/cache \
  -v $(pwd)/docker-data/reports:/app/reports \
  username/gha-performance-analyzer:latest
```

**Option B: Using named volumes** (better for production)

First, create and start the container with named volumes:
```bash
docker run -d -p 5000:5000 \
  -v gha-data:/app/data \
  -v gha-cache:/app/cache \
  -v gha-reports:/app/reports \
  --name gha-analyzer \
  username/gha-performance-analyzer:latest
```

Then copy your existing data into the volumes:
```bash
# Copy database
docker cp gha_metrics.db gha-analyzer:/app/data/

# Copy cache files
docker cp cache/. gha-analyzer:/app/cache/

# Copy reports
docker cp reports/. gha-analyzer:/app/reports/

# Restart container to ensure proper permissions
docker restart gha-analyzer
```

#### Step 3: Verify Migration

Check the logs to ensure the database was loaded successfully:
```bash
docker logs gha-analyzer
```

Access the dashboard at http://localhost:5000 and verify your historical data is visible.

#### Troubleshooting Migration Issues

**Database not found or empty:**
- Verify the file was copied: `docker exec gha-analyzer ls -la /app/data/`
- Check file permissions: `docker exec gha-analyzer ls -la /app/data/gha_metrics.db`
- Ensure the database file is not corrupted: `sqlite3 gha_metrics.db "PRAGMA integrity_check;"`

**Schema compatibility errors:**
- The application automatically checks schema compatibility on startup
- Check logs for specific error messages: `docker logs gha-analyzer`
- If schema is incompatible, you may need to re-ingest data

**Permission denied errors:**
- The container runs as a non-root user
- Fix permissions: `docker exec gha-analyzer chown -R app:app /app/data /app/cache /app/reports`

**Cache files not loading:**
- Verify cache directory structure: `docker exec gha-analyzer ls -la /app/cache/`
- Cache files are optional; missing cache will trigger fresh API calls

**Reports not visible:**
- Check reports directory: `docker exec gha-analyzer ls -la /app/reports/`
- Reports can be regenerated through the dashboard

### Building and Publishing Docker Images

#### Prerequisites

- Docker 20.10 or later
- Docker Buildx plugin (included in Docker Desktop)
- Docker Hub or GitHub Container Registry account

#### Building Multi-Architecture Images Locally

Enable Docker Buildx:
```bash
docker buildx create --name multiarch --use
docker buildx inspect --bootstrap
```

Build for multiple architectures:
```bash
# Build for AMD64 and ARM64
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t username/gha-performance-analyzer:latest \
  -t username/gha-performance-analyzer:v1.0.0 \
  --push \
  .
```

Build for a single architecture (testing):
```bash
# AMD64 only
docker buildx build --platform linux/amd64 -t gha-analyzer:test-amd64 --load .

# ARM64 only
docker buildx build --platform linux/arm64 -t gha-analyzer:test-arm64 --load .
```

#### Registry Authentication

**Docker Hub:**
```bash
docker login
# Enter username and password when prompted
```

**GitHub Container Registry:**
```bash
echo $GITHUB_TOKEN | docker login ghcr.io -u USERNAME --password-stdin
```

#### Pushing to Registry

**Docker Hub:**
```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t username/gha-performance-analyzer:latest \
  -t username/gha-performance-analyzer:v1.0.0 \
  --push \
  .
```

**GitHub Container Registry:**
```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t ghcr.io/username/gha-performance-analyzer:latest \
  -t ghcr.io/username/gha-performance-analyzer:v1.0.0 \
  --push \
  .
```

#### Tag Management Best Practices

Use semantic versioning for releases:
- `latest` - Always points to the most recent stable release
- `v1.0.0` - Specific version tag (immutable)
- `v1.0` - Minor version tag (updated with patches)
- `v1` - Major version tag (updated with minor releases)
- `dev` - Development/unstable builds

Example tagging strategy:
```bash
# Release v1.2.3
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t username/gha-performance-analyzer:latest \
  -t username/gha-performance-analyzer:v1.2.3 \
  -t username/gha-performance-analyzer:v1.2 \
  -t username/gha-performance-analyzer:v1 \
  --push \
  .
```

#### Automated Builds with GitHub Actions

The repository includes a GitHub Actions workflow (`.github/workflows/docker-build.yml`) that automatically builds and publishes multi-architecture images when you push a version tag:

```bash
# Create and push a version tag
git tag v1.0.0
git push origin v1.0.0

# GitHub Actions will automatically:
# 1. Build for linux/amd64 and linux/arm64
# 2. Push to Docker Hub or GHCR
# 3. Create appropriate version tags
# 4. Update the 'latest' tag
```

Configure the following secrets in your GitHub repository:
- `DOCKERHUB_USERNAME` - Your Docker Hub username
- `DOCKERHUB_TOKEN` - Docker Hub access token
- Or for GHCR: `GITHUB_TOKEN` (automatically available)
