"""
Fixing module.

Handles LLM-based fix generation and patch application.
"""

from specfix.fixing.llm_fixer import LLMFixer, create_llm_fixer
from specfix.fixing.patcher import SpecPatcher

__all__ = ["LLMFixer", "create_llm_fixer", "SpecPatcher"]

