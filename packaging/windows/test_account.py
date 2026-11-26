import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from launch import account


class FirstLaunch(unittest.TestCase):
    def test_account_survives_restart_without_storing_password(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "account.json"
            with patch("builtins.input", return_value="operator"), patch(
                "launch.read_password", side_effect=["wat-test-password", "wat-test-password"]
            ):
                first = account(path)
            self.assertEqual(first, account(path))
            self.assertEqual(first["username"], "operator")
            self.assertNotIn("wat-test-password", path.read_text())
            self.assertEqual(json.loads(path.read_text()), first)
