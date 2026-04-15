"""
Diff generation utilities for SpecFix.

Generates unified diffs between original and patched OpenAPI specifications.
"""

import difflib
from pathlib import Path
from typing import Optional


def generate_unified_diff(
    original_content: str,
    patched_content: str,
    original_path: str = "original",
    patched_path: str = "patched",
    context_lines: int = 3,
) -> str:
    """
    Generate a unified diff between original and patched content.
    
    Args:
        original_content: Original file content
        patched_content: Patched file content
        original_path: Label for original file in diff
        patched_path: Label for patched file in diff
        context_lines: Number of context lines to include
    
    Returns:
        Unified diff string
    """
    # Split into lines without keeping newlines - difflib will handle newlines
    original_lines = original_content.splitlines(keepends=False)
    patched_lines = patched_content.splitlines(keepends=False)
    
    # Use relative paths for cleaner diff output
    # Extract just the filename if it's a full path
    from pathlib import Path
    try:
        original_path_clean = Path(original_path).name
        patched_path_clean = Path(patched_path).name
    except:
        original_path_clean = original_path
        patched_path_clean = patched_path
    
    diff = difflib.unified_diff(
        original_lines,
        patched_lines,
        fromfile=original_path_clean,
        tofile=patched_path_clean,
        n=context_lines,
        lineterm="\n",  # Use newline as line terminator
    )
    
    return "".join(diff)


def save_diff(
    diff_content: str,
    output_path: Path,
) -> None:
    """
    Save diff content to a file.
    
    Args:
        diff_content: Diff content to save
        output_path: Path to save diff file
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(diff_content, encoding="utf-8")

