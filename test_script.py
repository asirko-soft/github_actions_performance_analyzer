import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta, timezone
import json
import os

from app import app
from database import GHADatabase
from github_api_client import GitHubApiClient
from data_models import WorkflowRun, Job, Step
from data_collector import DataCollector
from stats_calculator import StatsCalculator

class TestGitHubActionsPerformanceAnalyzer(unittest.TestCase):

    def setUp(self):
        self.db_path = "test_gha_metrics.db"
        self.db_patcher = patch('app.DB_PATH', self.db_path)
        self.db_patcher.start()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

        self.db = GHADatabase(db_path=self.db_path)
        self.db.connect()
        self.db.initialize_schema()

        self.test_app = app.test_client()
        app.config['TESTING'] = True

        self.token = "test_token"
        self.owner = "test_owner"
        self.repo = "test_repo"
        self.workflow_id = "test_workflow.yml"
        self.api_client = GitHubApiClient(self.token)
        self.calculator = StatsCalculator()

    def tearDown(self):
        self.db.close()
        self.db_patcher.stop()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _create_test_run(self, run_id, created_at, conclusion, duration_ms, jobs=None):
        if jobs:
            for job in jobs:
                job.workflow_run_id = run_id
        run = WorkflowRun(
            id=run_id, name="CI", status="completed", conclusion=conclusion,
            created_at=created_at, updated_at=created_at + timedelta(minutes=5),
            event="push", head_branch="main", run_number=run_id,
            duration_ms=duration_ms, jobs=jobs or []
        )
        self.db.save_workflow_run(run, self.owner, self.repo, self.workflow_id)
        return run

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

        runs = self.api_client.get_workflow_runs(self.owner, self.repo, self.workflow_id)
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

        jobs = self.api_client.get_jobs_for_run(self.owner, self.repo, 1)
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
        
        collector = DataCollector(self.api_client, self.db)
        
        start_date = datetime(2024, 1, 1)
        end_date = datetime(2024, 1, 2)
        runs_collected_count = collector.collect_workflow_data(
            self.owner, self.repo, self.workflow_id, start_date=start_date, end_date=end_date
        )

        self.assertEqual(runs_collected_count, 1)

        # Verify data was written to the database
        runs_from_db = self.db.get_workflow_runs(self.owner, self.repo, self.workflow_id)
        self.assertEqual(len(runs_from_db), 1)
        self.assertEqual(runs_from_db[0]['id'], 1)
        self.assertEqual(runs_from_db[0]['conclusion'], 'success')

    def test_calculate_run_statistics(self):
        runs = [
            WorkflowRun(id=1, name="CI", status="completed", conclusion="success", created_at="2024-01-01T10:00:00Z", updated_at="2024-01-01T10:05:00Z", event="push", head_branch="main", run_number=1, duration_ms=300000),
            WorkflowRun(id=2, name="CI", status="completed", conclusion="failure", created_at="2024-01-01T11:00:00Z", updated_at="2024-01-01T11:06:00Z", event="push", head_branch="main", run_number=2, duration_ms=360000),
            WorkflowRun(id=3, name="CI", status="completed", conclusion="cancelled", created_at="2024-01-01T12:00:00Z", updated_at="2024-01-01T12:07:00Z", event="push", head_branch="main", run_number=3, duration_ms=420000),
            WorkflowRun(id=4, name="CI", status="completed", conclusion="success", created_at="2024-01-01T13:00:00Z", updated_at="2024-01-01T13:04:00Z", event="push", head_branch="main", run_number=4, duration_ms=240000),
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

    def test_api_trends(self):
        # Day 1 data
        self._create_test_run(1, datetime(2024, 1, 1, 10, 0), "success", 10000) # 10s
        self._create_test_run(2, datetime(2024, 1, 1, 11, 0), "success", 20000) # 20s
        self._create_test_run(3, datetime(2024, 1, 1, 12, 0), "failure", 5000)   # 5s
        # Day 2 data
        self._create_test_run(4, datetime(2024, 1, 2, 10, 0), "success", 30000) # 30s

        response = self.test_app.get(
            f'/api/trends?owner={self.owner}&repo={self.repo}&workflow_id={self.workflow_id}&period=day'
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(len(data), 2)

        day1_data = data[0]
        self.assertEqual(day1_data['period_start'], '2024-01-01')
        self.assertEqual(day1_data['total_runs'], 3)
        self.assertEqual(day1_data['successful_runs'], 2)
        # success durations: [10000, 20000]. p50=15000, p95=19500, p99=19900
        self.assertEqual(day1_data['p50_duration_ms'], 15000)
        self.assertEqual(day1_data['p95_duration_ms'], 19500)
        self.assertEqual(day1_data['p99_duration_ms'], 19900)

        day2_data = data[1]
        self.assertEqual(day2_data['period_start'], '2024-01-02')
        self.assertEqual(day2_data['total_runs'], 1)
        # with one data point, percentiles are just the value itself
        self.assertEqual(day2_data['p50_duration_ms'], 30000)
        self.assertEqual(day2_data['p95_duration_ms'], 30000)

    def test_api_jobs(self):
        start_date = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        end_date = datetime(2024, 1, 3, 0, 0, tzinfo=timezone.utc)

        job1 = Job(id=101, name="build", status="completed", conclusion="success", started_at=start_date, completed_at=start_date + timedelta(seconds=10), workflow_run_id=1)
        job2 = Job(id=102, name="test", status="completed", conclusion="success", started_at=start_date, completed_at=start_date + timedelta(seconds=20), workflow_run_id=1)
        self._create_test_run(1, start_date, "success", 30000, jobs=[job1, job2])

        job3 = Job(id=201, name="build", status="completed", conclusion="success", started_at=start_date, completed_at=start_date + timedelta(seconds=12), workflow_run_id=2)
        job4 = Job(id=202, name="test", status="completed", conclusion="failure", started_at=start_date, completed_at=start_date + timedelta(seconds=5), workflow_run_id=2)
        self._create_test_run(2, start_date + timedelta(hours=1), "failure", 20000, jobs=[job3, job4])

        start_date_str = start_date.isoformat().replace('+00:00', 'Z')
        end_date_str = end_date.isoformat().replace('+00:00', 'Z')
        url = f'/api/jobs?owner={self.owner}&repo={self.repo}&workflow_id={self.workflow_id}&start_date={start_date_str}&end_date={end_date_str}'
        response = self.test_app.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(len(data), 2) # build and test jobs

        build_job_data = next(item for item in data if item["job_name"] == "build")
        test_job_data = next(item for item in data if item["job_name"] == "test")

        self.assertEqual(build_job_data['total_runs'], 2)
        self.assertEqual(build_job_data['success_rate'], 100.0)
        self.assertEqual(build_job_data['p95_duration_ms'], 11900) # np.percentile([10000, 12000], 95)

        self.assertEqual(test_job_data['total_runs'], 2)
        self.assertEqual(test_job_data['success_rate'], 50.0)
        self.assertEqual(test_job_data['p95_duration_ms'], 20000) # np.percentile([20000], 95)

if __name__ == "__main__":
    unittest.main()


