# Docker Build Workflows

This directory contains GitHub Actions workflows for building and publishing multi-architecture Docker images.

## Available Workflows

### docker-build.yml (Docker Hub)

Builds and pushes images to Docker Hub with support for both AMD64 and ARM64 architectures.

**Prerequisites:**
1. Docker Hub account
2. Repository secrets configured:
   - `DOCKER_USERNAME`: Your Docker Hub username
   - `DOCKER_PASSWORD`: Your Docker Hub access token (not password)

**To create a Docker Hub access token:**
1. Log in to Docker Hub
2. Go to Account Settings → Security
3. Click "New Access Token"
4. Give it a descriptive name (e.g., "GitHub Actions")
5. Copy the token and add it to your GitHub repository secrets

### docker-build-ghcr.yml.example (GitHub Container Registry)

Alternative workflow for publishing to GitHub Container Registry (ghcr.io).

**Advantages:**
- No additional secrets needed (uses `GITHUB_TOKEN`)
- Integrated with GitHub repository
- Free for public repositories

**To use GHCR instead of Docker Hub:**
1. Rename `docker-build-ghcr.yml.example` to `docker-build.yml`
2. Delete or rename the existing Docker Hub workflow
3. Ensure repository has package write permissions (enabled by default)

## Triggering a Build

Builds are triggered automatically when you push a version tag:

```bash
# Create and push a version tag
git tag v1.0.0
git push origin v1.0.0

# Or create an annotated tag with a message
git tag -a v1.0.0 -m "Release version 1.0.0"
git push origin v1.0.0
```

## Image Tagging Strategy

The workflow automatically generates multiple tags for each release:

- `v1.0.0` - Full semantic version
- `v1.0` - Major.minor version
- `v1` - Major version only
- `latest` - Latest stable release (only on default branch)

**Example:**
```bash
# All of these will work after pushing tag v1.2.3:
docker pull username/repo:v1.2.3
docker pull username/repo:v1.2
docker pull username/repo:v1
docker pull username/repo:latest
```

## Multi-Architecture Support

Images are built for the following platforms:
- `linux/amd64` - x86_64 Linux systems
- `linux/arm64` - ARM64 systems (Apple Silicon, ARM servers)

Docker automatically pulls the correct architecture for your system:

```bash
# On Apple Silicon Mac - pulls ARM64 image
docker pull username/repo:latest

# On x86_64 Linux - pulls AMD64 image
docker pull username/repo:latest
```

## Build Cache

The workflow uses GitHub Actions cache to speed up builds:
- Dependencies are cached between builds
- Only changed layers are rebuilt
- Significantly faster build times for subsequent releases

## Monitoring Builds

1. Go to your repository on GitHub
2. Click the "Actions" tab
3. Select the workflow run to see detailed logs
4. Check the build summary for pull commands and tags

## Testing Locally

You can test multi-architecture builds locally before pushing:

```bash
# Set up buildx (one-time setup)
docker buildx create --name multiarch --use
docker buildx inspect --bootstrap

# Build for multiple platforms (without pushing)
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t username/repo:test \
  .

# Build and load for your current platform
docker buildx build \
  --platform linux/amd64 \
  -t username/repo:test \
  --load \
  .
```

## Troubleshooting

### Build fails with "permission denied"
- Check that repository secrets are correctly configured
- Verify Docker Hub token has write permissions
- For GHCR, ensure package write permissions are enabled

### Wrong architecture pulled
- Docker should automatically select the correct architecture
- To verify: `docker image inspect username/repo:latest | grep Architecture`
- To force a specific platform: `docker pull --platform linux/amd64 username/repo:latest`

### Build is slow
- First build will be slower (no cache)
- Subsequent builds use GitHub Actions cache
- Check that cache is being used in workflow logs

### Tag not triggering build
- Ensure tag matches pattern `v*` (e.g., v1.0.0, not 1.0.0)
- Check that workflow file is on the default branch
- Verify workflow is enabled in repository settings

## Manual Workflow Dispatch

To enable manual triggering (useful for testing):

Add this to the workflow's `on:` section:
```yaml
on:
  push:
    tags:
      - 'v*'
  workflow_dispatch:  # Enables manual trigger
```

Then you can trigger builds manually from the Actions tab.
