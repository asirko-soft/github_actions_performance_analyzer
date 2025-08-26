import json
import csv
from typing import Dict, Any, List
import io

class ReportExporter:
    def export_to_json(self, data: Dict[str, Any], filename: str):
        """Exports data to a JSON file."""
        try:
            with open(filename, 'w') as f:
                json.dump(data, f, indent=4)
            print(f"Data successfully exported to {filename}")
        except IOError as e:
            print(f"Error writing JSON file {filename}: {e}")

    def export_to_csv(self, data: List[Dict[str, Any]], filename: str):
        """Exports a list of dictionaries to a CSV file."""
        if not data:
            print("No data to export to CSV.")
            return

        try:
            # Get headers from the first dictionary
            headers = data[0].keys()
            with open(filename, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                writer.writeheader()
                writer.writerows(data)
            print(f"Data successfully exported to {filename}")
        except IOError as e:
            print(f"Error writing CSV file {filename}: {e}")
        except Exception as e:
            print(f"An error occurred during CSV export: {e}")

    def export_to_csv_string(self, data: List[Dict[str, Any]]) -> str:
        """Exports a list of dictionaries to a CSV formatted string."""
        if not data:
            return ""

        output = io.StringIO()
        try:
            headers = data[0].keys()
            writer = csv.DictWriter(output, fieldnames=headers)
            writer.writeheader()
            writer.writerows(data)
            return output.getvalue()
        except Exception as e:
            print(f"An error occurred during CSV string export: {e}")
            return ""

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

    csv_data_steps = [
        {"step_name": "Checkout", "total_runs": 40, "avg_duration_ms": 1000},
        {"step_name": "Install Deps", "total_runs": 40, "avg_duration_ms": 3000}
    ]
    exporter.export_to_csv(csv_data_steps, "example_step_metrics.csv")


