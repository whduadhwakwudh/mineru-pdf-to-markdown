from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "normalize_text.py"
SPEC = importlib.util.spec_from_file_location("normalize_text", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules["normalize_text"] = MODULE
SPEC.loader.exec_module(MODULE)


class LigatureRepairTests(unittest.TestCase):
    def test_unicode_ligature_block_is_expanded(self):
        text = "e\ufb01cient \ufb02ow \ufb00ect"
        fixed, report = MODULE.repair_text(text)
        self.assertEqual(fixed, "efficient flow ffect")
        self.assertEqual(report.ligature_fixes["\ufb01"], 1)
        self.assertEqual(report.ligature_fixes["\ufb02"], 1)

    def test_pua_fi_inside_word(self):
        # MinerU wraps the glyph in <sup>..</sup>; the wrapper must be removed
        # so the word stays contiguous.
        text = "recent scienti<sup>\ue103</sup>c research"
        fixed, _ = MODULE.repair_text(text)
        self.assertIn("scientific", fixed)
        self.assertNotIn("<sup>", fixed)

    def test_pua_ft_inside_word(self):
        text = "A\ue09der graduation"
        fixed, _ = MODULE.repair_text(text)
        self.assertIn("After", fixed)

    def test_pua_fl_vs_fi_disambiguation(self):
        # U+E104 is "fl" in influencing/flows, U+E103 is "fi" in filling.
        text = "in\ue104uencing the ionic \ue104ows and \ue103lling"
        fixed, _ = MODULE.repair_text(text)
        self.assertIn("influencing", fixed)
        self.assertIn("flows", fixed)
        self.assertIn("filling", fixed)

    def test_pua_primary_reading_used_when_no_word_match(self):
        text = "xyz\ue103abc"
        fixed, report = MODULE.repair_text(text)
        self.assertEqual(fixed, "xyzfiabc")
        self.assertIn("\ue103", report.unresolved)

    def test_unknown_pua_is_preserved_and_reported(self):
        text = "keep \uf8ff this"
        fixed, report = MODULE.repair_text(text)
        self.assertIn("\uf8ff", fixed)
        self.assertIn("\uf8ff", report.unresolved)

    def test_nbsp_and_degree_normalized(self):
        text = "a\u00a0b 95.7\u25e6C"
        fixed, report = MODULE.repair_text(text)
        self.assertEqual(fixed, "a b 95.7°C")
        self.assertEqual(report.symbol_fixes["\u00a0"], 1)
        self.assertEqual(report.symbol_fixes["\u25e6"], 1)

    def test_dropped_letter_words_repaired(self):
        text = "suficient electrolyte and insuficient strength"
        fixed, report = MODULE.repair_text(text)
        self.assertIn("sufficient", fixed)
        self.assertIn("insufficient", fixed)
        self.assertEqual(report.word_fixes["suficient"], 1)

    def test_dropped_letter_preserves_capitalization(self):
        fixed, _ = MODULE.repair_text("Diferent methods")
        self.assertIn("Different", fixed)

    def test_no_artifacts_returns_input_unchanged(self):
        text = "A perfectly normal sentence."
        fixed, report = MODULE.repair_text(text)
        self.assertEqual(fixed, text)
        self.assertEqual(report.total_fixes, 0)
        self.assertFalse(MODULE.has_artifacts(text))

    def test_has_artifacts_detects_pua(self):
        self.assertTrue(MODULE.has_artifacts("x\ue103y"))
        self.assertTrue(MODULE.has_artifacts("x\ufb01y"))
        self.assertTrue(MODULE.has_artifacts("x\uf8ffy"))

    def test_report_is_json_serializable(self):
        import json

        _, report = MODULE.repair_text("scienti\ue103c")
        json.dumps(report.as_dict())

    def test_markdown_image_data_is_not_corrupted(self):
        # base64 payloads must pass through untouched
        payload = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQ"
        text = f"![img]({payload})"
        fixed, _ = MODULE.repair_text(text)
        self.assertEqual(fixed, text)


if __name__ == "__main__":
    unittest.main()
