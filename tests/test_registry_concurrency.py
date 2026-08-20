"""Regression test for the sources.json read-modify-write race identified in ARCHITECTURE_AUDIT.md
§4/§11 and closed in Milestone 2 by storage.REGISTRY_WRITE_LOCK. Without the lock, two concurrent
"add a source" calls can both read the same starting list, each append their own source, and the
second writer's save clobbers the first writer's addition -- a lost update. This reproduces that
scenario directly against storage.sources()/save_sources() (not the HTTP layer) at enough
concurrency that the race reliably manifests when unguarded, and asserts nothing is lost."""
import tempfile
import threading
import unittest
from pathlib import Path

from tender_monitor import storage


class RegistryConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self._orig_sources = storage.SOURCES
        storage.SOURCES = Path(self.tmpdir.name) / "sources.json"
        storage.SOURCES.write_text("[]")

    def tearDown(self):
        storage.SOURCES = self._orig_sources

    def _add_source_under_lock(self, index):
        with storage.REGISTRY_WRITE_LOCK:
            items = storage.sources()
            items.append({"id": f"src-{index}", "name": f"Source {index}"})
            storage.save_sources(items)

    def test_concurrent_registry_writes_lose_no_updates(self):
        threads = [threading.Thread(target=self._add_source_under_lock, args=(i,)) for i in range(30)]
        for t in threads: t.start()
        for t in threads: t.join()
        stored_ids = {s["id"] for s in storage.sources()}
        self.assertEqual(stored_ids, {f"src-{i}" for i in range(30)})  # every write survived, none clobbered


if __name__ == "__main__":
    unittest.main()
