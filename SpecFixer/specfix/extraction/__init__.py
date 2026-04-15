"""
Documentation extraction module.

Handles enhanced extraction of documentation with structured parsing.
"""

from specfix.extraction.doc_extractor import DocumentationExtractor
from specfix.extraction.structured_docs import StructuredDocumentation

try:
    from specfix.extraction.playwright_extractor import PlaywrightDocumentationExtractor
    __all__ = ["DocumentationExtractor", "StructuredDocumentation", "PlaywrightDocumentationExtractor"]
except ImportError:
    __all__ = ["DocumentationExtractor", "StructuredDocumentation"]

