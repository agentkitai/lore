"""Plugin scaffold generator — creates project template for new plugins."""

from __future__ import annotations

from pathlib import Path

PYPROJECT_TEMPLATE = '''[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "lore-plugin-{name}"
version = "0.1.0"
description = "Lore plugin: {name}"
requires-python = ">=3.10"
dependencies = ["lore-sdk>=1.0.0"]

[project.entry-points."lore.plugins"]
{name} = "{module}.plugin:{class_name}"
'''

PLUGIN_TEMPLATE = '''"""Lore plugin: {name}."""

from lore.plugin.base import LorePlugin, PluginMeta


class {class_name}(LorePlugin):
    """Custom Lore plugin — {name}."""

    meta = PluginMeta(
        name="{name}",
        version="0.1.0",
        description="{name} plugin for Lore",
        priority=100,
    )

    def on_remember(self, memory):
        """Called after a memory is saved."""
        return memory

    def on_recall(self, query, results):
        """Called after recall results are scored."""
        return results
'''

TEST_TEMPLATE = '''"""Tests for {name} plugin."""

from {module}.plugin import {class_name}


def test_plugin_meta():
    plugin = {class_name}()
    assert plugin.meta.name == "{name}"
    assert plugin.meta.version == "0.1.0"


def test_on_remember_passthrough():
    plugin = {class_name}()
    memory = {{"id": "test", "content": "hello"}}
    result = plugin.on_remember(memory)
    assert result == memory


def test_on_recall_passthrough():
    plugin = {class_name}()
    results = [{{"memory": "test", "score": 0.9}}]
    out = plugin.on_recall("query", results)
    assert out == results
'''


def scaffold_plugin(name: str, output_dir: str = ".") -> Path:
    """Generate a new plugin project from templates."""
    # Normalize name
    slug = name.lower().replace(" ", "-").replace("_", "-")
    module = slug.replace("-", "_")
    class_name = "".join(w.capitalize() for w in slug.split("-")) + "Plugin"

    project_dir = Path(output_dir) / f"lore-plugin-{slug}"
    src_dir = project_dir / module
    tests_dir = project_dir / "tests"

    # Create directories
    src_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)

    # Write files
    (project_dir / "pyproject.toml").write_text(
        PYPROJECT_TEMPLATE.format(name=slug, module=module, class_name=class_name)
    )
    (src_dir / "__init__.py").write_text("")
    (src_dir / "plugin.py").write_text(
        PLUGIN_TEMPLATE.format(name=slug, class_name=class_name)
    )
    (tests_dir / "__init__.py").write_text("")
    (tests_dir / f"test_{module}.py").write_text(
        TEST_TEMPLATE.format(name=slug, module=module, class_name=class_name)
    )

    return project_dir
