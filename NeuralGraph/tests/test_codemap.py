"""CODEMAP.md must match what tools/codemap.py generates from the tree."""

from __future__ import annotations

import unittest

from tools import codemap


class CodeMapTests(unittest.TestCase):
    def test_codemap_is_current(self):
        self.assertTrue(codemap.OUTPUT.exists(), "CODEMAP.md missing; run python -m tools.codemap")
        self.assertEqual(
            codemap.OUTPUT.read_text(encoding="utf-8"),
            codemap.render(),
            "CODEMAP.md is stale; run: python -m tools.codemap",
        )

    def test_map_points_at_real_lines(self):
        # Spot-check: every "L<n> class X" line names a line that starts a class.
        text = codemap.render()
        checked = 0
        current = None
        for line in text.splitlines():
            if line.startswith("### `") and line.endswith(")") and ".py`" in line:
                current = codemap.ROOT / line.split("`")[1]
                source = current.read_text(encoding="utf-8").splitlines()
            elif current is not None and line.startswith("  L") and " class " in line:
                number = int(line[3:9].strip())
                self.assertTrue(source[number - 1].lstrip().startswith("class "), (current, number))
                checked += 1
        self.assertGreater(checked, 20)


if __name__ == "__main__":
    unittest.main()
