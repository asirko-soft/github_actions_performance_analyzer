import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
import json
import os
import shutil

from github_api_client import GitHubApiClient
from data_models import WorkflowRun, Job, Step
from data_collector import DataCollector
from stats_calculator import StatsCalculator

class TestGitHubActionsPerformanceAnalyzer(unittest.TestCase):

    def setUp(self):
        self.token = "test_token"
        self.owner = "test_owner"
        self.repo = "test_repo"
        self.workflow_id = "test_workflow.yml"
        self.client = GitHubApiClient(self.token)
        self.cache_dir = "./test_cache"
        self.collector = DataCollector(self.client, cache_dir=self.cache_dir)
        self.calculator = StatsCalculator()
        os.makedirs(self.cache_dir, exist_ok=True)

    def tearDown(self):
        if os.path.exists(self.cache_dir):
            shutil.rmtree(self.cache_dir)

    @patch("requests.get")
    def test_get_workflow_runs(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "workflow_runs": [
                {"id": 1, "name": "CI", "status": "completed", "conclusion": "success", "created_at": "2024-01-01T10:00:00Z", "updated_at": "2024-01-01T10:05:00Z", "event": "push", "head_branch": "main", "run_number": 1},
                {"id": 2, "name": "CI", "status": "completed", "conclusion": "failure", "created_at": "2024-01-01T11:00:00Z", "updated_at": "2024-01-01T11:06:00Z", "event": "pull_request", "head_branch": "dev", "run_number": 2}
            ]
        }
        mock_response.links = {}
        mock_get.return_value = mock_response

        runs = self.client.get_workflow_runs(self.owner, self.repo, self.workflow_id)
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0]["id"], 1)
        self.assertEqual(runs[1]["conclusion"], "failure")

    @patch("requests.get")
    def test_get_jobs_for_run(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "jobs": [
                {"id": 101, "name": "build", "status": "completed", "conclusion": "success", "started_at": "2024-01-01T10:00:10Z", "completed_at": "2024-01-01T10:02:00Z", "workflow_run_id": 1, "steps": []},
                {"id": 102, "name": "test", "status": "completed", "conclusion": "success", "started_at": "2024-01-01T10:02:10Z", "completed_at": "2024-01-01T10:04:00Z", "workflow_run_id": 1, "steps": []}
            ]
        }
        mock_response.links = {}
        mock_get.return_value = mock_response

        jobs = self.client.get_jobs_for_run(self.owner, self.repo, 1)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0]["id"], 101)
        self.assertEqual(jobs[1]["name"], "test")

    @patch.object(GitHubApiClient, "get_workflow_runs")
    @patch.object(GitHubApiClient, "get_jobs_for_run")
    def test_collect_workflow_data(self, mock_get_jobs, mock_get_runs):
        mock_get_runs.return_value = [
            {"id": 1, "name": "CI", "status": "completed", "conclusion": "success", "created_at": "2024-01-01T10:00:00Z", "updated_at": "2024-01-01T10:05:00Z", "event": "push", "head_branch": "main", "run_number": 1}
        ]
        mock_get_jobs.return_value = [
            {"id": 101, "name": "build", "status": "completed", "conclusion": "success", "started_at": "2024-01-01T10:00:10Z", "completed_at": "2024-01-01T10:02:00Z", "workflow_run_id": 1,
             "steps": [
                 {"name": "Checkout", "status": "completed", "conclusion": "success", "number": 1, "started_at": "2024-01-01T10:00:15Z", "completed_at": "2024-01-01T10:00:30Z"},
                 {"name": "Run Tests", "status": "completed", "conclusion": "success", "number": 2, "started_at": "2024-01-01T10:00:35Z", "completed_at": "2024-01-01T10:01:50Z"}
             ],
             "labels": ["self-hosted", "linux", "node:16", "os:ubuntu-latest"] # Example matrix labels
            }
        ]

        start_date = datetime(2024, 1, 1)
        end_date = datetime(2024, 1, 2)
        collected_runs = self.collector.collect_workflow_data(
            self.owner, self.repo, self.workflow_id, start_date=start_date, end_date=end_date
        )

        self.assertEqual(len(collected_runs), 1)
        self.assertEqual(collected_runs[0].id, 1)
        self.assertEqual(len(collected_runs[0].jobs), 1)
        self.assertEqual(collected_runs[0].jobs[0].id, 101)
        self.assertEqual(len(collected_runs[0].jobs[0].steps), 2)
        self.assertEqual(collected_runs[0].jobs[0].steps[0].name, "Checkout")
        self.assertIsNotNone(collected_runs[0].jobs[0].matrix_config)
        self.assertEqual(collected_runs[0].jobs[0].matrix_config["node"], "16")

    def test_calculate_run_statistics(self):
        runs = [
            WorkflowRun(id=1, name="CI", status="completed", conclusion="success", created_at="2024-01-01T10:00:00Z", updated_at="2024-01-01T10:05:00Z", event="push", head_branch="main", run_number=1),
            WorkflowRun(id=2, name="CI", status="completed", conclusion="failure", created_at="2024-01-01T11:00:00Z", updated_at="2024-01-01T11:06:00Z", event="push", head_branch="main", run_number=2),
            WorkflowRun(id=3, name="CI", status="completed", conclusion="cancelled", created_at="2024-01-01T12:00:00Z", updated_at="2024-01-01T12:07:00Z", event="push", head_branch="main", run_number=3),
            WorkflowRun(id=4, name="CI", status="completed", conclusion="success", created_at="2024-01-01T13:00:00Z", updated_at="2024-01-01T13:04:00Z", event="push", head_branch="main", run_number=4),
        ]
        metrics = self.calculator.calculate_run_statistics(runs)
        self.assertEqual(metrics.total_runs, 4)
        self.assertEqual(metrics.successful_runs, 2)
        self.assertEqual(metrics.failed_runs, 1)
        self.assertEqual(metrics.cancelled_runs, 1)
        self.assertGreater(metrics.avg_duration_ms, 0)

    def test_calculate_job_statistics(self):
        jobs = [
            Job(id=101, name="build", status="completed", conclusion="success", started_at="2024-01-01T10:00:10Z", completed_at="2024-01-01T10:02:00Z", workflow_run_id=1),
            Job(id=102, name="build", status="completed", conclusion="failure", started_at="2024-01-01T11:00:10Z", completed_at="2024-01-01T11:03:00Z", workflow_run_id=2),
            Job(id=103, name="test", status="completed", conclusion="success", started_at="2024-01-01T12:00:10Z", completed_at="2024-01-01T12:04:00Z", workflow_run_id=3),
        ]
        metrics = self.calculator.calculate_job_statistics(jobs)
        self.assertIn("build", metrics)
        self.assertIn("test", metrics)
        self.assertEqual(metrics["build"]["total_runs"], 2)
        self.assertEqual(metrics["build"]["successful_runs"], 1)
        self.assertEqual(metrics["build"]["failed_runs"], 1)
        self.assertGreater(metrics["build"]["avg_all_duration_ms"], 0)

    def test_calculate_step_statistics(self):
        job_with_steps = Job(id=101, name="build", status="completed", conclusion="success", started_at="2024-01-01T10:00:10Z", completed_at="2024-01-01T10:02:00Z", workflow_run_id=1,
            steps=[
                Step(name="Checkout", status="completed", conclusion="success", number=1, started_at="2024-01-01T10:00:15Z", completed_at="2024-01-01T10:00:30Z"),
                Step(name="Run Tests", status="completed", conclusion="success", number=2, started_at="2024-01-01T10:00:35Z", completed_at="2024-01-01T10:01:50Z"),
            ]
        )
        jobs = [job_with_steps]
        metrics = self.calculator.calculate_step_statistics(jobs)
        self.assertIn("Checkout", metrics)
        self.assertIn("Run Tests", metrics)
        self.assertEqual(metrics["Checkout"]["total_runs"], 1)
        self.assertEqual(metrics["Checkout"]["successful_runs"], 1)
        self.assertGreater(metrics["Checkout"]["avg_all_duration_ms"], 0)

    def test_analyze_matrix_builds(self):
        jobs = [
            Job(id=1, name="build (linux, node-16)", status="completed", conclusion="success", started_at="2024-01-01T10:00:00Z", completed_at="2024-01-01T10:05:00Z", workflow_run_id=1, matrix_config={"os": "linux", "node": "16"}),
            Job(id=2, name="build (windows, node-14)", status="completed", conclusion="failure", started_at="2024-01-01T10:10:00Z", completed_at="2024-01-01T10:16:00Z", workflow_run_id=1, matrix_config={"os": "windows", "node": "14"}),
            Job(id=3, name="build (linux, node-16)", status="completed", conclusion="success", started_at="2024-01-01T10:20:00Z", completed_at="2024-01-01T10:24:00Z", workflow_run_id=2, matrix_config={"os": "linux", "node": "16"}),
        ]
        metrics = self.calculator.analyze_matrix_builds(jobs)
        
        # Generate keys using json.dumps with sort_keys=True to match the StatsCalculator output
        linux_node_16_key = json.dumps({"os": "linux", "node": "16"}, sort_keys=True)
        windows_node_14_key = json.dumps({"os": "windows", "node": "14"}, sort_keys=True)

        self.assertIn(linux_node_16_key, metrics)
        self.assertIn(windows_node_14_key, metrics)

        self.assertEqual(metrics[linux_node_16_key]["total_runs"], 2)
        self.assertEqual(metrics[linux_node_16_key]["successful_runs"], 2)
        self.assertEqual(metrics[windows_node_14_key]["total_runs"], 1)
        self.assertEqual(metrics[windows_node_14_key]["failed_runs"], 1)

    @patch.object(DataCollector, '_load_from_cache')
    @patch.object(DataCollector, '_save_to_cache')
    @patch.object(GitHubApiClient, "get_workflow_runs")
    @patch.object(GitHubApiClient, "get_jobs_for_run")
    def test_data_collector_caching(self, mock_get_jobs, mock_get_runs, mock_save_to_cache, mock_load_from_cache):
        # Test cache hit
        mock_load_from_cache.return_value = [MagicMock(spec=WorkflowRun)] # Return a dummy WorkflowRun object
        
        start_date = datetime(2024, 1, 1)
        end_date = datetime(2024, 1, 2)
        collected_runs = self.collector.collect_workflow_data(
            self.owner, self.repo, self.workflow_id, start_date=start_date, end_date=end_date
        )
        mock_load_from_cache.assert_called_once()
        mock_get_runs.assert_not_called() # Should not call API if cache hit
        mock_save_to_cache.assert_not_called()
        self.assertEqual(len(collected_runs), 1)

        # Test cache miss and subsequent save
        mock_load_from_cache.reset_mock()
        mock_load_from_cache.return_value = None # Simulate cache miss
        mock_get_runs.return_value = [
            {"id": 1, "name": "CI", "status": "completed", "conclusion": "success", "created_at": "2024-01-01T10:00:00Z", "updated_at": "2024-01-01T10:05:00Z", "event": "push", "head_branch": "main", "run_number": 1}
        ]
        mock_get_jobs.return_value = [
            {"id": 101, "name": "build", "status": "completed", "conclusion": "success", "started_at": "2024-01-01T10:00:10Z", "completed_at": "2024-01-01T10:02:00Z", "workflow_run_id": 1, "steps": []}
        ]

        collected_runs = self.collector.collect_workflow_data(
            self.owner, self.repo, self.workflow_id, start_date=start_date, end_date=end_date
        )
        mock_load_from_cache.assert_called_once()
        mock_get_runs.assert_called_once()
        mock_save_to_cache.assert_called_once()
        self.assertEqual(len(collected_runs), 1)

if __name__ == "__main__":
    unittest.main()


