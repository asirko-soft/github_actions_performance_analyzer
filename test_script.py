import unittest
import os
from datetime import datetime
from database import GHADatabase
from data_models import WorkflowRun, Job, Step

class TestGHADatabase(unittest.TestCase):
    def setUp(self):
        self.db_path = "test_gha_metrics.db"
        self.db = GHADatabase(self.db_path)
        self.db.connect()
        self.db.initialize_schema()

    def tearDown(self):
        self.db.close()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_save_and_retrieve_workflow(self):
        run = WorkflowRun(
            id=123,
            name="Test Workflow",
            status="completed",
            conclusion="success",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            event="push",
            head_branch="main",
            run_number=1,
            head_sha="abc1234",
            pull_request_number=None
        )
        
        self.db.save_workflow_run(run, "owner", "repo", "ci.yml")
        
        runs = self.db.get_workflow_runs("owner", "repo", "ci.yml")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]['id'], 123)

if __name__ == '__main__':
    unittest.main()
