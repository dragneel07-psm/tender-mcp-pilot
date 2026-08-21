"""Milestone 12: load_dotenv()'s quote-stripping hardening (audit §13). Writes a real temporary
.env file rather than mocking file reads, so this exercises the actual parsing logic end to end."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tender_monitor import config

TEST_KEYS = ("TEST_PLAIN", "TEST_DOUBLE_QUOTED", "TEST_SINGLE_QUOTED", "TEST_WITH_EQUALS", "TEST_UNMATCHED_QUOTE", "TEST_MIXED_QUOTES")


class LoadDotenvTests(unittest.TestCase):
    def setUp(self):
        self._orig_env = {k: os.environ.get(k) for k in TEST_KEYS}
        for k in TEST_KEYS: os.environ.pop(k, None)
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

    def tearDown(self):
        for k, v in self._orig_env.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v

    def _load(self, contents):
        env_file = Path(self.tmpdir.name) / ".env"
        env_file.write_text(contents)
        with mock.patch.object(config, "ROOT", Path(self.tmpdir.name)):
            config.load_dotenv()

    def test_plain_value_is_unchanged(self):
        self._load("TEST_PLAIN=hello\n")
        self.assertEqual(os.environ["TEST_PLAIN"], "hello")

    def test_double_quoted_value_has_quotes_stripped(self):
        self._load('TEST_DOUBLE_QUOTED="sk-abc123"\n')
        self.assertEqual(os.environ["TEST_DOUBLE_QUOTED"], "sk-abc123")

    def test_single_quoted_value_has_quotes_stripped(self):
        self._load("TEST_SINGLE_QUOTED='sk-abc123'\n")
        self.assertEqual(os.environ["TEST_SINGLE_QUOTED"], "sk-abc123")

    def test_value_containing_equals_is_preserved_whole(self):
        self._load("TEST_WITH_EQUALS=a=b=c\n")
        self.assertEqual(os.environ["TEST_WITH_EQUALS"], "a=b=c")

    def test_unmatched_quote_is_left_as_is(self):
        self._load('TEST_UNMATCHED_QUOTE="unterminated\n')
        self.assertEqual(os.environ["TEST_UNMATCHED_QUOTE"], '"unterminated')

    def test_mismatched_quote_pair_is_left_as_is(self):
        self._load("TEST_MIXED_QUOTES=\"wrong'\n")
        self.assertEqual(os.environ["TEST_MIXED_QUOTES"], "\"wrong'")

    def test_does_not_override_an_already_set_env_var(self):
        os.environ["TEST_PLAIN"] = "real-value"
        self._load("TEST_PLAIN=from-dotenv\n")
        self.assertEqual(os.environ["TEST_PLAIN"], "real-value")

    def test_missing_env_file_is_a_silent_no_op(self):
        with mock.patch.object(config, "ROOT", Path(self.tmpdir.name) / "does-not-exist"):
            config.load_dotenv()  # must not raise


if __name__ == "__main__":
    unittest.main()
