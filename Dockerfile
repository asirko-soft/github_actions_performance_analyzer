# Multi-architecture Dockerfile for GitHub Actions Performance Analyzer
# Supports linux/amd64 and linux/arm64 platforms

# Use Python 3.11 Alpine as base image for smaller size
FROM python:3.11-alpine

# Set working directory
WORKDIR /app

# Install system dependencies required for Python packages
# - gcc, musl-dev: Required for compiling Python packages with C extensions
# - libffi-dev: Required for cryptography package
# - g++: Required for numpy/scipy compilation
# - gfortran: Required for scipy
# - openblas-dev: Required for numpy/scipy linear algebra operations
RUN apk add --no-cache \
    gcc \
    musl-dev \
    libffi-dev \
    g++ \
    gfortran \
    openblas-dev \
    freetype-dev \
    libpng-dev

# Copy requirements file first for better layer caching
COPY requirements.txt .

# Install Python dependencies
# Add gunicorn for production WSGI server
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir gunicorn

# Copy application code to /app directory
COPY *.py ./
COPY templates/ ./templates/
COPY docker-entrypoint.sh ./

# Make entrypoint script executable
RUN chmod +x /app/docker-entrypoint.sh

# Create non-root user for running the application
# Use UID/GID 1000 for compatibility with host systems
RUN addgroup -g 1000 appgroup && \
    adduser -u 1000 -G appgroup -s /bin/sh -D appuser && \
    chown -R appuser:appgroup /app

# Define volume mount points for persistent data
VOLUME ["/app/data", "/app/cache", "/app/reports"]

# Create directories and set permissions
RUN mkdir -p /app/data /app/cache /app/reports && \
    chown -R appuser:appgroup /app/data /app/cache /app/reports

# Switch to non-root user
USER appuser

# Expose port 5000 for Flask web server
EXPOSE 5000

# Set entrypoint to initialization script
ENTRYPOINT ["/app/docker-entrypoint.sh"]
