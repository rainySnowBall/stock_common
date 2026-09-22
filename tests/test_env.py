import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_common.env import default_dotenv_paths, load_dotenv, parse_dotenv


class EnvLoaderTests(unittest.TestCase):
    def test_parse_dotenv_unquotes_values(self) -> None:
        values = parse_dotenv('A=1\nB="two"\n# comment\nC=三\n')

        self.assertEqual(values["A"], "1")
        self.assertEqual(values["B"], "two")
        self.assertEqual(values["C"], "三")

    def test_default_paths_include_explicit_stock_common_env_file_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("TUSHARE_TOKEN=token-from-explicit-file\n", encoding="utf-8")

            with patch.dict(os.environ, {"STOCK_COMMON_ENV_FILE": str(env_path)}, clear=True):
                paths = default_dotenv_paths()

            self.assertEqual(paths[0], env_path)

    def test_load_dotenv_uses_explicit_file_without_overriding_existing_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("TUSHARE_TOKEN=token-from-file\nOTHER=value\n", encoding="utf-8")

            with patch.dict(
                os.environ,
                {
                    "STOCK_COMMON_ENV_FILE": str(env_path),
                    "TUSHARE_TOKEN": "already-set",
                },
                clear=True,
            ):
                loaded = load_dotenv()
                self.assertEqual(loaded["TUSHARE_TOKEN"], "token-from-file")
                self.assertEqual(os.environ["TUSHARE_TOKEN"], "already-set")
                self.assertEqual(os.environ["OTHER"], "value")


if __name__ == "__main__":
    unittest.main()
