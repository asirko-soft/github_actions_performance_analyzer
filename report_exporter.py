import json
import csv
from typing import Dict, Any, List, Optional
import io
from datetime import datetime, timezone

class ReportExporter:
    def export_to_json(self, data: Dict[str, Any], filename: str):
        """Exports data to a JSON file."""
        try:
            with open(filename, 'w') as f:
                json.dump(data, f, indent=4)
            print(f"Data successfully exported to {filename}")
        except IOError as e:
            print(f"Error writing JSON file {filename}: {e}")

    def export_to_csv(self, data: List[Dict[str, Any]], filename: str, filter_metadata: Optional[Dict[str, Any]] = None):
        """Exports a list of dictionaries to a CSV file with optional filter metadata as header comments.
        
        :param data: List of dictionaries to export
        :param filename: Output CSV filename
        :param filter_metadata: Optional dict with filter information to include as header comments
        """
        if not data:
            print("No data to export to CSV.")
            return

        try:
            with open(filename, 'w', newline='') as f:
                # Write filter metadata as comments if provided
                if filter_metadata:
                    self._write_filter_metadata_comments(f, filter_metadata)
                
                # Get headers from the first dictionary
                headers = data[0].keys()
                writer = csv.DictWriter(f, fieldnames=headers)
                writer.writeheader()
                writer.writerows(data)
            print(f"Data successfully exported to {filename}")
        except IOError as e:
            print(f"Error writing CSV file {filename}: {e}")
        except Exception as e:
            print(f"An error occurred during CSV export: {e}")

    def export_to_csv_string(self, data: List[Dict[str, Any]], filter_metadata: Optional[Dict[str, Any]] = None) -> str:
        """Exports a list of dictionaries to a CSV formatted string with optional filter metadata as header comments.
        
        :param data: List of dictionaries to export
        :param filter_metadata: Optional dict with filter information to include as header comments
        :return: CSV formatted string
        """
        if not data:
            return ""

        output = io.StringIO()
        try:
            # Write filter metadata as comments if provided
            if filter_metadata:
                self._write_filter_metadata_comments(output, filter_metadata)
            
            headers = data[0].keys()
            writer = csv.DictWriter(output, fieldnames=headers)
            writer.writeheader()
            writer.writerows(data)
            return output.getvalue()
        except Exception as e:
            print(f"An error occurred during CSV string export: {e}")
            return ""
    
    def _write_filter_metadata_comments(self, file_obj, filter_metadata: Dict[str, Any]):
        """Writes filter metadata as CSV comment lines (lines starting with #).
        
        :param file_obj: File object or StringIO to write to
        :param filter_metadata: Dictionary containing filter information
        """
        file_obj.write(f"# GitHub Actions Performance Report\n")
        file_obj.write(f"# Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n")
        file_obj.write("#\n")
        
        # Write calculation method information
        if 'calculation_method' in filter_metadata:
            file_obj.write("# Calculation Method:\n")
            file_obj.write(f"#   Method: {filter_metadata['calculation_method']}\n")
            if 'calculation_description' in filter_metadata:
                file_obj.write(f"#   Description: {filter_metadata['calculation_description']}\n")
            file_obj.write("#\n")
        
        # Write filter information
        if 'filters_applied' in filter_metadata:
            filters = filter_metadata['filters_applied']
            file_obj.write("# Filters Applied:\n")
            
            if 'conclusions' in filters and filters['conclusions']:
                file_obj.write(f"#   Conclusions: {', '.join(filters['conclusions'])}\n")
            
            if 'excluded_statuses' in filters and filters['excluded_statuses']:
                file_obj.write(f"#   Excluded Statuses: {', '.join(filters['excluded_statuses'])}\n")
            
            if 'excluded_count' in filters:
                file_obj.write(f"#   Excluded Workflows: {filters['excluded_count']}\n")
        
        # Write time range information
        if 'time_range' in filter_metadata:
            time_range = filter_metadata['time_range']
            file_obj.write("#\n")
            file_obj.write("# Time Range:\n")
            if 'start_date' in time_range:
                file_obj.write(f"#   Start: {time_range['start_date']}\n")
            if 'end_date' in time_range:
                file_obj.write(f"#   End: {time_range['end_date']}\n")
        
        # Write additional metadata
        if 'owner' in filter_metadata:
            file_obj.write("#\n")
            file_obj.write(f"# Repository: {filter_metadata['owner']}/{filter_metadata['repo']}\n")
        
        if 'workflow_id' in filter_metadata:
            file_obj.write(f"# Workflow: {filter_metadata['workflow_id']}\n")
        
        file_obj.write("#\n")

if __name__ == '__main__':
    exporter = ReportExporter()

    # Example JSON data
    json_data = {
        "workflow_metrics": {
            "total_runs": 10,
            "successful_runs": 8,
            "avg_duration_ms": 12345.67
        },
        "job_metrics": {
            "Build": {"total_runs": 20, "avg_duration_ms": 5000},
            "Test": {"total_runs": 20, "avg_duration_ms": 7000}
        }
    }
    exporter.export_to_json(json_data, "example_report.json")

    # Example CSV data (list of dictionaries)
    csv_data = [
        {"name": "Build", "total_runs": 20, "avg_duration_ms": 5000},
        {"name": "Test", "total_runs": 20, "avg_duration_ms": 7000}
    ]
    exporter.export_to_csv(csv_data, "example_job_metrics.csv")

    # Example CSV data with filter metadata
    csv_data_with_metadata = [
        {"name": "Build", "total_runs": 20, "avg_duration_ms": 5000, "github_url": "https://github.com/owner/repo/actions/runs/123/job/456"},
        {"name": "Test", "total_runs": 20, "avg_duration_ms": 7000, "github_url": "https://github.com/owner/repo/actions/runs/123/job/789"}
    ]
    filter_metadata = {
        "owner": "owner",
        "repo": "repo",
        "workflow_id": "tests.yaml",
        "filters_applied": {
            "conclusions": ["success"],
            "excluded_statuses": ["in_progress", "queued"],
            "excluded_count": 5
        },
        "time_range": {
            "start_date": "2024-01-01T00:00:00Z",
            "end_date": "2024-01-31T23:59:59Z"
        }
    }
    exporter.export_to_csv(csv_data_with_metadata, "example_job_metrics_with_filters.csv", filter_metadata)

    csv_data_steps = [
        {"step_name": "Checkout", "total_runs": 40, "avg_duration_ms": 1000},
        {"step_name": "Install Deps", "total_runs": 40, "avg_duration_ms": 3000}
    ]
    exporter.export_to_csv(csv_data_steps, "example_step_metrics.csv")


