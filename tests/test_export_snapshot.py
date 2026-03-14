"""Tests for S9: Snapshot Manager and S10: Snapshot CLI + Restore."""

from __future__ import annotations

import os
import time

import pytest

from lore.export.snapshot import SnapshotManager
from lore.store.memory import MemoryStore


@pytest.fixture
def lore_instance(tmp_path):
    from lore import Lore
    db = str(tmp_path / "lore.db")
    return Lore(store=MemoryStore())


@pytest.fixture
def snapshots_dir(tmp_path):
    return str(tmp_path / "snapshots")


class TestSnapshotCreate:
    def test_creates_file(self, lore_instance, snapshots_dir):
        lore_instance.remember("snapshot test memory")
        mgr = SnapshotManager(lore_instance, snapshots_dir=snapshots_dir)
        info = mgr.create()

        assert os.path.exists(info["path"])
        assert info["memories"] == 1
        assert "size_human" in info
        assert info["name"]  # not empty
        lore_instance.close()

    def test_directory_auto_created(self, lore_instance, snapshots_dir):
        assert not os.path.exists(snapshots_dir)
        mgr = SnapshotManager(lore_instance, snapshots_dir=snapshots_dir)
        mgr.create()
        assert os.path.isdir(snapshots_dir)
        lore_instance.close()


class TestSnapshotList:
    def test_list_snapshots(self, lore_instance, snapshots_dir):
        lore_instance.remember("memory 1")
        mgr = SnapshotManager(lore_instance, snapshots_dir=snapshots_dir)
        mgr.create()
        time.sleep(1.1)  # ensure different timestamp
        mgr.create()
        snapshots = mgr.list()
        assert len(snapshots) == 2
        # Newest first
        assert snapshots[0]["name"] >= snapshots[1]["name"]
        lore_instance.close()

    def test_list_empty(self, lore_instance, snapshots_dir):
        mgr = SnapshotManager(lore_instance, snapshots_dir=snapshots_dir)
        assert mgr.list() == []
        lore_instance.close()


class TestSnapshotDelete:
    def test_delete_existing(self, lore_instance, snapshots_dir):
        mgr = SnapshotManager(lore_instance, snapshots_dir=snapshots_dir)
        info = mgr.create()
        assert mgr.delete(info["name"]) is True
        assert not os.path.exists(info["path"])
        lore_instance.close()

    def test_delete_nonexistent(self, lore_instance, snapshots_dir):
        mgr = SnapshotManager(lore_instance, snapshots_dir=snapshots_dir)
        mgr._ensure_dir()
        assert mgr.delete("nonexistent") is False
        lore_instance.close()


class TestSnapshotCleanup:
    def test_cleanup_older_than(self, lore_instance, snapshots_dir, tmp_path):
        mgr = SnapshotManager(lore_instance, snapshots_dir=snapshots_dir)
        mgr._ensure_dir()

        # Create a "fake old" snapshot
        old_path = os.path.join(snapshots_dir, "2020-01-01-000000.json")
        with open(old_path, "w") as f:
            f.write("{}")

        # Create a current one
        mgr.create()

        count = mgr.cleanup("30d")
        assert count == 1
        assert not os.path.exists(old_path)
        # Current snapshot still exists
        assert len(mgr.list()) == 1
        lore_instance.close()


class TestSnapshotAutoPrune:
    def test_auto_prune(self, lore_instance, snapshots_dir):
        mgr = SnapshotManager(lore_instance, snapshots_dir=snapshots_dir, max_snapshots=2)
        mgr._ensure_dir()

        # Create 3 fake snapshots
        for i in range(3):
            path = os.path.join(snapshots_dir, f"2026-01-0{i+1}-000000.json")
            with open(path, "w") as f:
                f.write("{}")

        # Create one more which triggers prune
        mgr.create()

        # Should have at most max_snapshots
        files = list(mgr._dir.glob("*.json"))
        assert len(files) <= 2
        lore_instance.close()


class TestSnapshotRestore:
    def test_restore(self, tmp_path):
        from lore import Lore

        snapshots_dir = str(tmp_path / "snapshots")

        # Create and snapshot
        lore1 = Lore(store=MemoryStore())
        lore1.remember("restore test")
        mgr1 = SnapshotManager(lore1, snapshots_dir=snapshots_dir)
        info = mgr1.create()
        lore1.close()

        # Restore into fresh DB
        lore2 = Lore(store=MemoryStore())
        mgr2 = SnapshotManager(lore2, snapshots_dir=snapshots_dir)
        result = mgr2.restore(info["name"])
        assert result.imported == 1
        lore2.close()

    def test_restore_latest(self, tmp_path):
        from lore import Lore

        snapshots_dir = str(tmp_path / "snapshots")

        lore1 = Lore(store=MemoryStore())
        lore1.remember("latest test")
        mgr = SnapshotManager(lore1, snapshots_dir=snapshots_dir)
        mgr.create()
        lore1.close()

        lore2 = Lore(store=MemoryStore())
        mgr2 = SnapshotManager(lore2, snapshots_dir=snapshots_dir)
        result = mgr2.restore("__latest__")
        assert result.imported == 1
        lore2.close()

    def test_restore_nonexistent(self, lore_instance, snapshots_dir):
        mgr = SnapshotManager(lore_instance, snapshots_dir=snapshots_dir)
        mgr._ensure_dir()
        with pytest.raises(FileNotFoundError):
            mgr.restore("does-not-exist")
        lore_instance.close()

    def test_restore_latest_empty(self, lore_instance, snapshots_dir):
        mgr = SnapshotManager(lore_instance, snapshots_dir=snapshots_dir)
        with pytest.raises(FileNotFoundError, match="No snapshots"):
            mgr.restore("__latest__")
        lore_instance.close()
