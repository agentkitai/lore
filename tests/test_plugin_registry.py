"""Tests for Plugin Registry (F8)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lore.plugin.base import LorePlugin, PluginMeta


class MockPlugin(LorePlugin):
    meta = PluginMeta(name="mock-plugin", version="1.0.0", description="Test plugin", priority=50)

    def on_remember(self, memory):
        return memory

    def on_recall(self, query, results):
        return results


class TestPluginRegistry:
    def test_register_and_list(self):
        from lore.plugin.registry import PluginRegistry
        registry = PluginRegistry()
        plugin = MockPlugin()
        registry._plugins["mock-plugin"] = plugin
        plugins = registry.list_plugins()
        assert len(plugins) == 1
        assert plugins[0]["name"] == "mock-plugin"
        assert plugins[0]["enabled"] is True

    def test_enable_disable(self):
        from lore.plugin.registry import PluginRegistry
        registry = PluginRegistry()
        registry._plugins["mock-plugin"] = MockPlugin()

        assert registry.disable("mock-plugin") is True
        plugins = registry.list_plugins()
        assert plugins[0]["enabled"] is False

        assert registry.enable("mock-plugin") is True
        plugins = registry.list_plugins()
        assert plugins[0]["enabled"] is True

    def test_disable_nonexistent(self):
        from lore.plugin.registry import PluginRegistry
        registry = PluginRegistry()
        assert registry.disable("nonexistent") is False

    def test_get_active(self):
        from lore.plugin.registry import PluginRegistry
        registry = PluginRegistry()
        registry._plugins["mock-plugin"] = MockPlugin()
        active = registry.get_active()
        assert len(active) == 1

        registry.disable("mock-plugin")
        active = registry.get_active()
        assert len(active) == 0

    def test_get_plugin(self):
        from lore.plugin.registry import PluginRegistry
        registry = PluginRegistry()
        registry._plugins["mock-plugin"] = MockPlugin()
        assert registry.get("mock-plugin") is not None
        assert registry.get("nonexistent") is None

    def test_cleanup_all(self):
        from lore.plugin.registry import PluginRegistry
        registry = PluginRegistry()
        plugin = MockPlugin()
        plugin.cleanup = MagicMock()
        registry._plugins["mock-plugin"] = plugin
        registry.cleanup_all()
        plugin.cleanup.assert_called_once()

    def test_priority_ordering(self):
        from lore.plugin.registry import PluginRegistry

        class HighPriorityPlugin(LorePlugin):
            meta = PluginMeta(name="high", version="1.0", priority=10)

        class LowPriorityPlugin(LorePlugin):
            meta = PluginMeta(name="low", version="1.0", priority=200)

        registry = PluginRegistry()
        registry._plugins["low"] = LowPriorityPlugin()
        registry._plugins["high"] = HighPriorityPlugin()

        active = registry.get_active()
        assert active[0].meta.name == "high"
        assert active[1].meta.name == "low"


class TestPluginHooks:
    def test_dispatch_on_remember(self):
        from lore.plugin.hooks import dispatch_on_remember
        plugin = MockPlugin()
        memory = {"id": "test", "content": "hello"}
        result = dispatch_on_remember([plugin], memory)
        assert result == memory

    def test_dispatch_on_recall(self):
        from lore.plugin.hooks import dispatch_on_recall
        plugin = MockPlugin()
        results = [{"memory": "test"}]
        out = dispatch_on_recall([plugin], "query", results)
        assert out == results

    def test_error_isolation(self):
        from lore.plugin.hooks import dispatch_on_remember

        class FailingPlugin(LorePlugin):
            meta = PluginMeta(name="failing", version="1.0")
            def on_remember(self, memory):
                raise RuntimeError("Plugin crashed!")

        class GoodPlugin(LorePlugin):
            meta = PluginMeta(name="good", version="1.0")
            def on_remember(self, memory):
                memory["processed"] = True
                return memory

        memory = {"id": "test"}
        # FailingPlugin should not prevent GoodPlugin from running
        result = dispatch_on_remember([FailingPlugin(), GoodPlugin()], memory)
        assert result.get("processed") is True

    def test_dispatch_on_score(self):
        from lore.plugin.hooks import dispatch_on_score

        class BoostPlugin(LorePlugin):
            meta = PluginMeta(name="boost", version="1.0")
            def on_score(self, memory, score):
                return score * 1.5

        result = dispatch_on_score([BoostPlugin()], {}, 0.8)
        assert result == pytest.approx(1.2)


class TestPluginScaffold:
    def test_scaffold_creates_files(self, tmp_path):
        from lore.plugin.scaffold import scaffold_plugin
        project_dir = scaffold_plugin("my-tagger", output_dir=str(tmp_path))
        assert project_dir.exists()
        assert (project_dir / "pyproject.toml").exists()
        assert (project_dir / "my_tagger" / "plugin.py").exists()
        assert (project_dir / "my_tagger" / "__init__.py").exists()
        assert (project_dir / "tests" / "test_my_tagger.py").exists()

    def test_scaffold_content(self, tmp_path):
        from lore.plugin.scaffold import scaffold_plugin
        project_dir = scaffold_plugin("my-tagger", output_dir=str(tmp_path))
        content = (project_dir / "my_tagger" / "plugin.py").read_text()
        assert "class MyTaggerPlugin" in content
        assert "LorePlugin" in content

    def test_scaffold_pyproject(self, tmp_path):
        from lore.plugin.scaffold import scaffold_plugin
        project_dir = scaffold_plugin("my-tagger", output_dir=str(tmp_path))
        content = (project_dir / "pyproject.toml").read_text()
        assert "lore-plugin-my-tagger" in content
        assert "lore.plugins" in content


class TestPluginHarness:
    def test_harness_add_memory(self):
        from lore.plugin.harness import PluginTestHarness
        harness = PluginTestHarness(MockPlugin())
        memory = harness.add_test_memory("test content")
        assert memory.content == "test content"
        assert len(harness.memories) == 1

    def test_harness_run_hooks(self):
        from lore.plugin.harness import PluginTestHarness
        harness = PluginTestHarness(MockPlugin())
        harness.add_test_memory("test content")
        results = harness.run_all_hooks()
        assert "on_remember" in results
        assert "on_recall" in results
        assert "on_score" in results
