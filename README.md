# GitHub Actions Performance Analyzer

This Python script analyzes GitHub Actions workflow performance week-over-week, providing detailed statistics for different run statuses, job steps, and matrix builds. The output is structured for easy visualization, making it a valuable tool for identifying performance trends and bottlenecks in your CI/CD pipelines.

## Features

-   **Week-over-week Analysis**: Track performance changes over time.
-   **Detailed Run Status Statistics**: Differentiate between successful, failed, cancelled, and other workflow run conclusions.
-   **Job-level Statistics**: Get insights into the performance of individual jobs within your workflows.
-   **Step-level Statistics**: Pinpoint performance issues down to specific steps within your jobs.
-   **Matrix Build Support**: Correctly aggregate and analyze data for workflows utilizing matrix strategies.
-   **Local Data Caching**: Saves retrieved workflow data locally to avoid redundant API calls and speed up subsequent analyses.
-   **Robust API Handling**: Includes retry logic with exponential backoff for transient network issues and handles GitHub API rate limiting.
-   **Flexible Data Export**: Output results in JSON and CSV formats, suitable for various visualization tools.

## Installation

1.  **Clone the repository (or download the script files)**:

    ```bash
    git clone https://github.com/your-repo/github-actions-performance-analyzer.git
    cd github-actions-performance-analyzer
    ```

2.  **Install dependencies**:

    ```bash
    pip install -r requirements.txt
    ```

## Usage

### Prerequisites

-   **GitHub Personal Access Token (PAT)**: The script requires a GitHub PAT with `repo` scope (for private repositories) or `public_repo` scope (for public repositories) to access workflow run data. You can generate one from your [GitHub Developer Settings](https://github.com/settings/tokens).

    It's recommended to set your PAT as an environment variable named `GITHUB_TOKEN`:

    ```bash
    export GITHUB_TOKEN="YOUR_GITHUB_PAT"
    ```

    Alternatively, you can pass it directly using the `--token` argument.

### Running the Script

```bash
python3 main.py \
    --owner <repository-owner> \
    --repo <repository-name> \
    --workflow_id <workflow-file-name-or-id> \
    [--branch <branch-name>] \
    [--weeks <number-of-weeks-to-analyze>] \
    [--output_dir <output-directory>]
```

**Arguments**:

-   `--owner` (Required): The owner of the GitHub repository (e.g., `octocat`).
-   `--repo` (Required): The name of the GitHub repository (e.g., `Spoon-Knife`).
-   `--workflow_id` (Required): The ID of the workflow or the workflow file name (e.g., `ci.yml` or `123456`).
-   `--branch` (Optional): Filter workflow runs by a specific branch (e.g., `main`).
-   `--weeks` (Optional): The number of past weeks to analyze. Defaults to `4`.
-   `--output_dir` (Optional): The directory where the generated reports (JSON and CSV) will be saved. Defaults to `./reports`.

### Example

To analyze the `ci.yml` workflow in the `my-org/my-repo` repository for the last 8 weeks:

```bash
python3 main.py \
    --owner my-org \
    --repo my-repo \
    --workflow_id ci.yml \
    --weeks 8 \
    --output_dir ./my_reports
```

## Output

The script generates two types of files in the specified `output_dir`:

1.  **JSON Report (`<workflow_id>_performance_report.json`)**: A comprehensive JSON file containing all calculated metrics:
    -   `overall_workflow_metrics`: Aggregated statistics for all workflow runs.
    -   `job_metrics`: Statistics for each unique job name.
    -   `step_metrics`: Statistics for each unique step name.
    -   `matrix_metrics`: Statistics for each unique matrix configuration (if applicable).

2.  **CSV Reports (`<workflow_id>_job_metrics.csv`, `<workflow_id>_step_metrics.csv`)**: CSV files providing a flat structure for job and step metrics, suitable for direct import into spreadsheet software or data visualization tools.

## Data Interpretation

-   **Durations**: All durations are reported in milliseconds (`_ms`).
-   **Conclusions**: `success`, `failure`, `cancelled`, `skipped`, `neutral`, `action_required`, `stale`, `timed_out`.
-   **Matrix Configuration**: For matrix jobs, the `matrix_metrics` section will show statistics grouped by the unique combinations of matrix variables. The keys for `matrix_metrics` are string representations of sorted dictionaries (e.g., `{"os": "linux", "node": "16"}`).

## Contributing

Feel free to fork the repository, open issues, or submit pull requests to improve this tool.

## License

This project is licensed under the MIT License - see the `LICENSE` file for details.


