import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from citywheels.dotenv import candidate_paths, merged_env, parse_env_file
from citywheels.settings import from_mapping


class EnvFormatTests(unittest.TestCase):
    def test_plain_lines_match_the_repository_env_style(self):
        parsed = parse_env_file(
            "\n".join(
                [
                    "# comment",
                    "OPENAI_API_KEY=test-openai",
                    "GEMINI_API_KEY=test-gemini",
                    "CITYWHEELS_PROVIDER=gemini",
                    "OPENAI_API_KEY=",
                ]
            )
        )
        self.assertEqual(parsed["OPENAI_API_KEY"], "")
        self.assertEqual(parsed["GEMINI_API_KEY"], "test-gemini")
        self.assertEqual(parsed["CITYWHEELS_PROVIDER"], "gemini")

    def test_quotes_and_export_are_accepted(self):
        parsed = parse_env_file('export OPENAI_API_KEY="test-openai"\n')
        self.assertEqual(parsed["OPENAI_API_KEY"], "test-openai")

    def test_closer_file_wins_and_empty_does_not_erase(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "help-desk-agent" / "help-desk-agent"
            package.mkdir(parents=True)
            (root / ".env").write_text(
                "OPENAI_API_KEY=from-root\nGEMINI_API_KEY=from-root\n",
                encoding="utf-8",
            )
            (package / ".env").write_text(
                "OPENAI_API_KEY=\nGEMINI_API_KEY=from-near\n",
                encoding="utf-8",
            )
            paths = candidate_paths(package, package)
            merged = merged_env(paths)
        self.assertEqual(merged["OPENAI_API_KEY"], "from-root")
        self.assertEqual(merged["GEMINI_API_KEY"], "from-near")

    def test_provider_defaults_from_which_key_is_set(self):
        both = from_mapping(
            {"OPENAI_API_KEY": "test-openai", "GEMINI_API_KEY": "test-gemini"}
        )
        self.assertEqual(both.provider, "openai")
        self.assertEqual(both.openai_model, "gpt-4o-mini")
        self.assertEqual(both.gemini_model, "gemini-3.8-flash")
        chosen = from_mapping(
            {"OPENAI_API_KEY": "test-openai", "GEMINI_API_KEY": "test-gemini"},
            "gemini",
        )
        self.assertEqual(chosen.provider, "gemini")
        self.assertEqual(chosen.gemini_key, "test-gemini")
        gemini_only = from_mapping({"GEMINI_API_KEY": "test-gemini"})
        self.assertEqual(gemini_only.provider, "gemini")
        offline = from_mapping({})
        self.assertEqual(offline.provider, "fake")


if __name__ == "__main__":
    unittest.main()
