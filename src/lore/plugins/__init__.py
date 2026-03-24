"""Built-in enrichment plugins for Lore.

Import and register these with the global ``PluginRegistry`` to enable
automatic enrichment when memories are ingested.
"""

from lore.plugins.language_detect import LanguageDetectPlugin
from lore.plugins.pii_redactor import PIIRedactorPlugin

__all__ = ["LanguageDetectPlugin", "PIIRedactorPlugin"]
