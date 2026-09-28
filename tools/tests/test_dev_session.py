import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("dev_session", Path(__file__).parents[1] / "dev_session.py")
workflow = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workflow)


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "device"
        self.remote = self.root / "origin.git"
        self.git(self.root, "init", "--bare", str(self.remote))
        self.git(self.root, "init", "-b", "main", str(self.repo))
        self.git(self.repo, "config", "user.name", "Workflow Test")
        self.git(self.repo, "config", "user.email", "workflow@example.invalid")
        self.write(self.repo, "pi-server/app.py", "pi\n")
        self.write(self.repo, "jetson/app.py", "jetson\n")
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-m", "base")
        self.git(self.repo, "remote", "add", "origin", str(self.remote))
        self.git(self.repo, "push", "-u", "origin", "main")

    def git(self, path, *args):
        return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True,
                              text=True, encoding="utf-8").stdout.strip()

    @staticmethod
    def write(root, path, content):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def start(self, name="task"):
        with patch.dict(os.environ, {"AI_AGENT_RUNNER": "0"}):
            return workflow.prepare(self.repo, "pi", name)

    def test_latest_main_without_touching_active_checkout(self):
        other = self.root / "other"
        self.git(self.root, "clone", "-b", "main", str(self.remote), str(other))
        self.git(other, "config", "user.name", "Other Test")
        self.git(other, "config", "user.email", "other@example.invalid")
        self.write(other, "jetson/new.py", "latest\n")
        self.git(other, "add", ".")
        self.git(other, "commit", "-m", "remote work")
        self.git(other, "push", "origin", "main")
        self.git(self.repo, "switch", "-c", "pi-first-cook-trial")
        before = self.git(self.repo, "rev-parse", "HEAD")
        self.write(self.repo, "pi-server/app.py", "uncommitted work\n")
        target = self.start()
        self.assertEqual(self.git(target, "rev-parse", "HEAD"), self.git(other, "rev-parse", "HEAD"))
        self.assertEqual(self.git(self.repo, "branch", "--show-current"), "pi-first-cook-trial")
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), before)
        self.assertEqual((self.repo / "pi-server/app.py").read_text(), "uncommitted work\n")
        self.assertEqual(workflow.check(target, "pi"), [])

    def test_scope_covers_commits_staging_and_untracked(self):
        target = self.start()
        self.write(target, "pi-server/new.py", "allowed\n")
        self.assertEqual(workflow.check(target, "pi"), [])
        self.write(target, "notes/dev-log.md", "shared\n")
        self.assertIn("notes/dev-log.md", workflow.check(target, "pi"))
        self.git(target, "add", "notes/dev-log.md")
        self.git(target, "commit", "-m", "out of scope")
        self.assertIn("notes/dev-log.md", workflow.check(target, "pi"))
        self.write(target, "jetson/app.py", "changed\n")
        self.git(target, "add", "jetson/app.py")
        self.write(target, "jetson/app.py", "jetson\n")
        self.assertIn("jetson/app.py", workflow.check(target, "pi"))

    def test_rename_checks_original_owner(self):
        target = self.start()
        self.git(target, "mv", "jetson/app.py", "pi-server/moved.py")
        self.assertIn("jetson/app.py", workflow.check(target, "pi"))

    def test_existing_task_is_never_reset(self):
        target = self.start()
        self.write(target, "pi-server/app.py", "keep\n")
        with self.assertRaises(RuntimeError):
            self.start()
        self.assertEqual((target / "pi-server/app.py").read_text(), "keep\n")

    def test_bad_task_and_runner_rejected(self):
        for task in ("../escape", "--force", "UPPER", "x/y", ""):
            with self.assertRaises(RuntimeError):
                self.start(task)
        with patch.dict(os.environ, {"AI_AGENT_RUNNER": "1"}):
            with self.assertRaises(RuntimeError):
                workflow.prepare(self.repo, "pi", "task")

    def test_failed_fetch_does_not_create_branch(self):
        self.git(self.repo, "remote", "set-url", "origin", str(self.root / "missing.git"))
        with self.assertRaises(RuntimeError):
            self.start()
        self.assertEqual(self.git(self.repo, "branch", "--list", "pi/task"), "")

    def test_wrong_role_branch_rejected(self):
        target = self.start()
        with self.assertRaises(RuntimeError):
            workflow.check(target, "jetson")


if __name__ == "__main__":
    unittest.main()
