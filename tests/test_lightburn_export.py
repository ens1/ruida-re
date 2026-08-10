"""Tests for offline LightBurn machine-file automation."""

from __future__ import annotations

from pathlib import Path
import unittest

from ruida_re.lightburn_export import _automation_script


class LightBurnExportTest(unittest.TestCase):
    def test_script_saves_rd_without_machine_actions(self) -> None:
        project = Path('/tmp/a "controlled".lbrn2')
        output = Path("/tmp/ruida fixtures/output.rd")

        script = _automation_script(project, output)

        self.assertIn('click menu item "Save RD file"', script)
        self.assertIn(
            'click button "Save" of splitter group 1',
            script,
        )
        self.assertIn('keystroke "output.rd"', script)
        self.assertIn('/tmp/ruida fixtures', script)
        for action in ("Start", "Send", "Run RD file"):
            self.assertNotIn(f'click button "{action}"', script)


if __name__ == "__main__":
    unittest.main()
