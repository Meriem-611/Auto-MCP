"""
Command-line interface for SpecFix.

Orchestrates the three-phase pipeline:
1. Documentation Extraction
2. Detection (Heuristics + LLM Validation)
3. Fixing (LLM Fix Generation + Patch Application)
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

import yaml

from specfix.detection.heuristic_detector import HeuristicDetector
from specfix.detection.llm_validator import create_llm_validator
from specfix.detection.issues import IssueReport
from specfix.extraction.doc_extractor import DocumentationExtractor
from specfix.extraction.structured_docs import StructuredDocumentation

# Try to import Playwright extractor (optional dependency)
try:
    from specfix.extraction.playwright_extractor import PlaywrightDocumentationExtractor
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    PlaywrightDocumentationExtractor = None
from specfix.fixing.llm_fixer import create_llm_fixer
from specfix.fixing.patcher import SpecPatcher
from specfix.loader.spec_loader import get_spec_format, load_spec
from specfix.utils.diff import generate_unified_diff, save_diff
from specfix.utils.logger import setup_logger

logger = setup_logger()


def generate_issue_summary(report_dict: dict, top_paths: int = 5) -> str:
    """
    Generate a concise human-readable summary of detected issues.
    
    Args:
        report_dict: Report dictionary (report.to_dict())
        top_paths: Number of top problematic paths to include
    
    Returns:
        Summary text
    """
    total = report_dict.get("total_count", 0)
    by_severity = report_dict.get("by_severity", {})
    issues = report_dict.get("issues", [])

    # Count by type
    by_type: dict = {}
    # Count by path (extract from location like 'paths./x.get...')
    by_path: dict = {}
    for it in issues:
        t = it.get("type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1
        loc = it.get("location", "")
        path_key = ""
        if loc.startswith("paths."):
            # paths./users.get.parameters[0] -> /users
            parts = loc.split(".")
            if len(parts) > 1:
                path_key = parts[1]
        elif loc.startswith("servers"):
            path_key = "servers"
        elif loc.startswith("components"):
            path_key = "components"
        if path_key:
            by_path[path_key] = by_path.get(path_key, 0) + 1

    # Sort helpers
    def sort_dict_desc(d: dict):
        return sorted(d.items(), key=lambda kv: kv[1], reverse=True)

    lines = []
    lines.append("=" * 80)
    lines.append("SpecFix - Issue Summary")
    lines.append("=" * 80)
    lines.append("")
    lines.append(f"Total Issues: {total}")
    lines.append(f"Issues by Severity: {by_severity}")
    lines.append("")

    # By type
    lines.append("Issues by Type:")
    for t, count in sort_dict_desc(by_type):
        lines.append(f"- {t}: {count}")
    if not by_type:
        lines.append("- None")
    lines.append("")

    # Top problematic paths
    lines.append(f"Top {top_paths} Problematic Paths/Sections:")
    for path, count in sort_dict_desc(by_path)[:top_paths]:
        lines.append(f"- {path}: {count}")
    if not by_path:
        lines.append("- None")
    lines.append("")

    return "\n".join(lines)


def _get_default_docs_path(spec_path: Path, use_playwright: bool = False, output_path: Optional[Path] = None) -> Path:
    """
    Get default path for saved documentation file.
    
    Args:
        spec_path: Path to the spec file
        use_playwright: Whether Playwright extractor is being used
        output_path: Optional output file path (if provided, docs will be saved in same directory)
    
    Returns:
        Path to the documentation file
    """
    spec_name = spec_path.stem
    suffix = "_docs_playwright.json" if use_playwright else "_docs.json"
    
    # If output path is provided (batch processing), save docs in the same directory
    if output_path:
        return output_path.parent / f"{spec_name}{suffix}"
    
    # Otherwise, save in the spec's directory (default behavior)
    return spec_path.parent / f"{spec_name}{suffix}"


def _get_extractor(args: argparse.Namespace):
    """
    Get the appropriate extractor based on CLI arguments.
    
    Returns:
        DocumentationExtractor or PlaywrightDocumentationExtractor instance
    """
    use_playwright = getattr(args, "use_playwright", False)
    
    if use_playwright:
        if not PLAYWRIGHT_AVAILABLE:
            logger.warning(
                "Playwright not available. Install with: pip install playwright && playwright install"
            )
            logger.info("Falling back to regular extractor...")
            return DocumentationExtractor(timeout=getattr(args, "timeout", 30))
        
        headless = getattr(args, "playwright_headless", True)
        browser_type = getattr(args, "playwright_browser", "chromium")
        
        logger.info(f"Using Playwright extractor (headless={headless}, browser={browser_type})")
        return PlaywrightDocumentationExtractor(
            timeout=getattr(args, "timeout", 30),
            headless=headless,
            browser_type=browser_type,
        )
    else:
        return DocumentationExtractor(timeout=getattr(args, "timeout", 30))


def _parse_url_patterns(patterns_str: Optional[str]) -> Optional[list]:
    """
    Parse URL patterns from comma-separated string.
    
    Args:
        patterns_str: Comma-separated list of regex patterns
        
    Returns:
        List of pattern strings, or None if patterns_str is None/empty
    """
    if not patterns_str:
        return None
    return [p.strip() for p in patterns_str.split(",") if p.strip()]


def _get_default_deny_patterns() -> list:
    """
    Get default deny patterns that work across different API documentation sites.
    
    These patterns exclude common non-API documentation pages.
    Note: URLs containing "api" will override these patterns.
    
    Returns:
        List of regex pattern strings
    """
    return [
        r"/blog",                   # Blog posts
        r"/changelog",              # Changelog/version history
        r"/examples",               # Code examples (usually separate from API reference)
        r"/guides/",                # Guides (often separate from API reference)
        r"/tutorials",              # Tutorials
        r"/getting-started",        # Getting started pages
        r"/quickstart",             # Quickstart pages
        r"#",                       # Anchor links (URL fragments)
        r"/download",               # Download pages
        r"/pricing",                # Pricing pages
        r"/status",                 # Status pages
        r"/support",                # Support pages
        r"/contact",                # Contact pages
        r"/login",                  # Login pages (unless part of API URL)
        r"/sign-up",                # Sign-up pages
        r"/dashboard",              # Dashboard pages
        r"/about",                  # About pages
        r"/privacy",                # Privacy policy
        r"/terms",                  # Terms of service
        r"/legals",                 # Legal pages
    ]


def _load_documentation_from_file(docs_path: Path) -> Optional[StructuredDocumentation]:
    """Load StructuredDocumentation from a JSON file."""
    if not docs_path.exists():
        return None
    
    try:
        docs_dict = json.loads(docs_path.read_text(encoding="utf-8"))
        from specfix.extraction.structured_docs import DocumentationPage, StructuredDocumentation
        
        pages = [
            DocumentationPage(
                url=page_dict.get("url", ""),
                title=page_dict.get("title", ""),
                content=page_dict.get("content", ""),
                structured_elements=page_dict.get("structured_elements", {}),
                metadata=page_dict.get("metadata", {}),
                links=page_dict.get("links", []),
            )
            for page_dict in docs_dict.get("pages", [])
        ]
        
        documentation = StructuredDocumentation(
            pages=pages,
            full_text=docs_dict.get("full_text", ""),
            endpoints=docs_dict.get("endpoints", []),
            parameters=docs_dict.get("parameters", {}),
            headers=docs_dict.get("headers", []),
            auth_info=docs_dict.get("auth_info"),
            examples=docs_dict.get("examples", []),
            global_headers=docs_dict.get("global_headers", []),
            global_header_contexts=docs_dict.get("global_header_contexts", {}),
            global_auth_mentioned=docs_dict.get("global_auth_mentioned", False),
            server_info=docs_dict.get("server_info", []),
        )
        
        logger.info(f"Loaded documentation from cache: {docs_path} ({len(pages)} pages)")
        return documentation
    except Exception as e:
        logger.warning(f"Failed to load documentation from {docs_path}: {e}")
        return None


def _save_documentation_to_file(documentation: StructuredDocumentation, docs_path: Path) -> None:
    """Save StructuredDocumentation to a JSON file."""
    try:
        docs_dict = {
            "pages": [
                {
                    "url": page.url,
                    "title": page.title,
                    "content": page.content,
                    "structured_elements": page.structured_elements,
                    "metadata": page.metadata,
                    "links": page.links,
                }
                for page in documentation.pages
            ],
            "full_text": documentation.full_text,
            "endpoints": documentation.endpoints,
            "parameters": documentation.parameters,
            "headers": documentation.headers,
            "auth_info": documentation.auth_info,
            "examples": documentation.examples,
            "global_headers": documentation.global_headers,
            "global_header_contexts": documentation.global_header_contexts,
            "global_auth_mentioned": documentation.global_auth_mentioned,
            "server_info": documentation.server_info,
        }
        docs_path.write_text(json.dumps(docs_dict, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"Saved extracted documentation to: {docs_path}")
    except Exception as e:
        logger.warning(f"Failed to save documentation to {docs_path}: {e}")


def analyze_command(args: argparse.Namespace) -> int:
    """
    Analyze an OpenAPI spec and detect issues (Phase 1 + 2).
    
    Phase 1: Extract documentation
    Phase 2: Detect issues (heuristics + optional LLM validation)
    
    Args:
        args: Parsed command-line arguments
    
    Returns:
        Exit code (0 for success, non-zero for error)
    """
    try:
        # Load spec
        spec_path = Path(args.spec)
        spec = load_spec(spec_path)
        logger.info(f"Loaded spec from: {spec_path}")

        # Determine docs file path (use provided path or default)
        if args.save_docs:
            docs_path = Path(args.save_docs)
        else:
            use_playwright = getattr(args, "use_playwright", False)
            # If output is provided, save docs in the same directory as output (for batch processing)
            output_path = Path(args.output) if args.output else None
            docs_path = _get_default_docs_path(spec_path, use_playwright=use_playwright, output_path=output_path)
        
        # Phase 1: Extract documentation (or load from cache)
        documentation = None
        was_empty_cache = False  # Track if we detected an empty cache file
        
        # First, try to load from cache if it exists
        # Check both the output directory and the original spec directory
        docs_paths_to_try = [docs_path]
        if output_path:
            # Also check in the spec's directory (original location)
            original_docs_path = spec_path.parent / f"{spec_path.stem}_docs_playwright.json"
            if original_docs_path != docs_path and original_docs_path.exists():
                docs_paths_to_try.append(original_docs_path)
            # Also check in results directory with spec name (case-insensitive fallback)
            results_dir = Path("results")
            if results_dir.exists():
                for results_subdir in results_dir.iterdir():
                    if results_subdir.is_dir() and results_subdir.name.lower() == spec_path.stem.lower():
                        fallback_docs_path = results_subdir / f"{results_subdir.name}_docs_playwright.json"
                        if fallback_docs_path.exists() and fallback_docs_path not in docs_paths_to_try:
                            docs_paths_to_try.append(fallback_docs_path)
                        break
        
        documentation = None
        for try_docs_path in docs_paths_to_try:
            if try_docs_path.exists() and not getattr(args, "force_detect", False):
                logger.info(f"Phase 1: Loading documentation from cache: {try_docs_path}")
                documentation = _load_documentation_from_file(try_docs_path)
                # If loaded documentation is empty (no pages, no text), treat as None to force re-extraction
                if documentation and len(documentation.pages) == 0 and not documentation.full_text:
                    logger.info("Cached documentation is empty, will re-extract")
                    documentation = None
                    was_empty_cache = True
                else:
                    # Successfully loaded, use this path for saving later
                    docs_path = try_docs_path
                    
                    # If --re-extract-auth flag is set, re-extract auth_info from cached full_text
                    if getattr(args, "re_extract_auth", False) and documentation:
                        logger.info("Re-extracting auth_info from cached documentation...")
                        extractor = _get_extractor(args)
                        # Re-extract auth_info using the extractor's method
                        if hasattr(extractor, '_extract_auth_context'):
                            # Combine full_text and code blocks for re-extraction (same as in _extract_auth_context)
                            combined_text = documentation.full_text or ""
                            for page in documentation.pages:
                                for code_block in page.structured_elements.get("code_blocks", []):
                                    combined_text += "\n\n" + code_block
                            
                            # Get spec security schemes to prioritize
                            spec_security_schemes = spec.get("components", {}).get("securitySchemes", {})
                            if not spec_security_schemes:
                                spec_security_schemes = spec.get("securityDefinitions", {})
                            
                            # Re-extract auth_info from combined text, passing spec security schemes
                            new_auth_info = extractor._extract_auth_context(combined_text, spec_security_schemes=spec_security_schemes)
                            
                            # Always update auth_info, even if empty (to clear old incorrect values)
                            # new_auth_info can be a dict (single) or list (multiple)
                            documentation.auth_info = new_auth_info
                            # Update global_auth_mentioned if auth_info was found
                            # Check for type, scheme, or header (header alone indicates auth was detected)
                            if isinstance(new_auth_info, list):
                                has_valid_auth = any(auth.get("type") or auth.get("scheme") or auth.get("header") for auth in new_auth_info)
                            else:
                                has_valid_auth = new_auth_info.get("type") or new_auth_info.get("scheme") or new_auth_info.get("header")
                            
                            if has_valid_auth:
                                documentation.global_auth_mentioned = True
                            else:
                                # If no valid auth found, check if auth keywords exist in text
                                # This handles cases where auth is mentioned but not extracted properly
                                combined_text = documentation.full_text or ""
                                for page in documentation.pages:
                                    for code_block in page.structured_elements.get("code_blocks", []):
                                        combined_text += "\n\n" + code_block
                                auth_pattern = r"(?:auth|authentication|bearer|api[_\s]?key|token|oauth)"
                                if re.search(auth_pattern, combined_text, re.IGNORECASE):
                                    documentation.global_auth_mentioned = True
                                else:
                                    documentation.global_auth_mentioned = False
                            
                            logger.info(f"Re-extracted auth_info: {new_auth_info}")
                            # Save updated documentation back to cache
                            _save_documentation_to_file(documentation, docs_path)
                            logger.info(f"Updated cached documentation file: {docs_path}")
                    
                    break
        
        # If not loaded from cache, extract from source
        if documentation is None:
            logger.info("Phase 1: Extracting documentation...")
            extractor = _get_extractor(args)
            
            # Get spec security schemes to pass to extractor
            spec_security_schemes = spec.get("components", {}).get("securitySchemes", {})
            if not spec_security_schemes:
                spec_security_schemes = spec.get("securityDefinitions", {})
            
            # Parse URL filtering patterns
            allow_patterns = _parse_url_patterns(getattr(args, "allow_url_patterns", None))
            deny_patterns = _parse_url_patterns(getattr(args, "deny_url_patterns", None))
            
            # Use default deny patterns if none specified and using Playwright
            if isinstance(extractor, PlaywrightDocumentationExtractor):
                if not getattr(args, "no_default_filters", False) and not deny_patterns:
                    deny_patterns = _get_default_deny_patterns()
                    logger.info("Using default deny patterns (URLs containing 'api' will override these)")
            
            # Handle Playwright context manager
            if isinstance(extractor, PlaywrightDocumentationExtractor):
                extractor.__enter__()
                try:
                    if args.docs:
                        is_url = args.docs.startswith(("http://", "https://"))
                        extract_kwargs = {
                            "source": args.docs,
                            "is_url": is_url,
                            "crawl": args.crawl and is_url,
                            "max_pages": args.max_pages or 10,
                            "same_domain": True,
                            "restrict_path_prefix": args.restrict_path,
                            "spec_security_schemes": spec_security_schemes,
                        }
                        
                        if allow_patterns:
                            extract_kwargs["allow_url_patterns"] = allow_patterns
                        if deny_patterns:
                            extract_kwargs["deny_url_patterns"] = deny_patterns
                        
                        documentation = extractor.extract_all(**extract_kwargs)
                    elif args.docs_text:
                        docs_text_path = Path(args.docs_text)
                        if docs_text_path.exists():
                            docs_text = docs_text_path.read_text(encoding="utf-8")
                        else:
                            docs_text = args.docs_text
                        documentation = extractor.extract_all(source=docs_text, is_url=False, spec_security_schemes=spec_security_schemes)
                finally:
                    extractor.__exit__(None, None, None)
            else:
                if args.docs:
                    is_url = args.docs.startswith(("http://", "https://"))
                    extract_kwargs = {
                        "source": args.docs,
                        "is_url": is_url,
                        "crawl": args.crawl and is_url,
                        "max_pages": args.max_pages or 10,
                        "same_domain": True,
                        "restrict_path_prefix": args.restrict_path,
                        "spec_security_schemes": spec_security_schemes,
                    }
                    documentation = extractor.extract_all(**extract_kwargs)
                elif args.docs_text:
                    # Check if it's a file path or raw text
                    docs_text_path = Path(args.docs_text)
                    if docs_text_path.exists():
                        # It's a file path, read the content
                        docs_text = docs_text_path.read_text(encoding="utf-8")
                    else:
                        # It's raw text
                        docs_text = args.docs_text
                    
                    documentation = extractor.extract_all(
                        source=docs_text,
                        is_url=False,
                        spec_security_schemes=spec_security_schemes,
                    )
            
            # Only reload from file if no docs source was provided (not attempting re-extraction)
            if docs_path.exists() and not documentation and not (args.docs or args.docs_text):
                # No docs source provided, but docs file exists - load it
                logger.info(f"Phase 1: No docs source provided, loading from existing file: {docs_path}")
                documentation = _load_documentation_from_file(docs_path)
            
            if documentation is None:
                # Create empty structured documentation
                from specfix.extraction.structured_docs import StructuredDocumentation
                documentation = StructuredDocumentation()
            else:
                logger.info(f"Extracted documentation: {len(documentation.pages)} pages, {len(documentation.full_text)} chars")
                
                # Save extracted documentation (if we extracted, not if we loaded)
                # But don't save if it's still empty and we were re-extracting from an empty cache
                is_empty_result = len(documentation.pages) == 0 and not documentation.full_text
                if args.docs or args.docs_text:
                    if not (was_empty_cache and is_empty_result):
                        _save_documentation_to_file(documentation, docs_path)
                    elif was_empty_cache and is_empty_result:
                        logger.warning(f"Re-extraction resulted in empty documentation, not overwriting cache file")

        # Phase 2: Heuristic detection (no LLM calls - fast and cheap)
        logger.info("Phase 2: Running heuristic detection...")
        detector = HeuristicDetector(spec, documentation)
        report = detector.detect_all()
        logger.info(f"Detected {report.total_count} potential issues (heuristic-based)")

        # Output report
        if args.output:
            output_path = Path(args.output)
            report.save_to_file(str(output_path))
            logger.info(f"Issue report saved to: {output_path}")

            # Generate and save summary
            summary_text = generate_issue_summary(report.to_dict())
            if getattr(args, "summary_out", None):
                summary_path = Path(args.summary_out)
            else:
                summary_path = output_path.with_name(f"{output_path.stem}_summary.txt")
            summary_path.write_text(summary_text, encoding="utf-8")
            logger.info(f"Issue summary saved to: {summary_path}")
        else:
            # Print to stdout
            print(f"\n=== Issue Report ===")
            print(f"Total Issues: {report.total_count}")
            print(f"By Severity: {report.by_severity}")
            report_dict = report.to_dict()
            summary_text = generate_issue_summary(report_dict, top_paths=5)
            print("\n=== Summary ===")
            print(summary_text)
            print(f"\nPotential Issues (heuristic-based):")
            for issue in report.issues:
                print(f"  [{issue.severity.upper()}] {issue.type.value}")
                print(f"    Location: {issue.location}")
                print(f"    Description: {issue.description}")
                print()

        return 0 if report.total_count == 0 else 1

    except Exception as e:
        logger.error(f"Analysis failed: {e}", exc_info=True)
        return 1


def fix_command(args: argparse.Namespace) -> int:
    """
    Fix an OpenAPI spec (all phases).
    
    Phase 1: Extract documentation
    Phase 2: Detect and validate issues
    Phase 3: Generate fixes and apply patches
    
    Args:
        args: Parsed command-line arguments
    
    Returns:
        Exit code (0 for success, non-zero for error)
    """
    try:
        # Load spec
        spec_path = Path(args.spec).resolve()
        spec = load_spec(spec_path)
        spec_format = get_spec_format(spec_path)
        logger.info(f"Loaded spec from: {spec_path}")

        # Check if issues.json exists (from previous analyze run)
        issues_file = args.issues_json or (spec_path.parent / "issues.json")
        
        if Path(issues_file).exists() and not args.force_detect:
            # Load issues from file
            logger.info(f"Loading issues from: {issues_file}")
            report = IssueReport.load_from_file(str(issues_file))
            logger.info(f"Loaded {report.total_count} issues from file")
        else:
            # Determine docs file path (use provided path or default)
            if args.save_docs:
                docs_path = Path(args.save_docs)
            else:
                use_playwright = getattr(args, "use_playwright", False)
                # If issues file is specified, save docs in the same directory (for batch processing)
                issues_file_path = Path(issues_file) if issues_file else None
                docs_path = _get_default_docs_path(spec_path, use_playwright=use_playwright, output_path=issues_file_path)
            
            # Phase 1: Extract documentation (or load from cache)
            documentation = None
            
            # First, try to load from cache if it exists
            if docs_path.exists() and not args.force_detect:
                logger.info(f"Phase 1: Loading documentation from cache: {docs_path}")
                documentation = _load_documentation_from_file(docs_path)
                # If loaded documentation is empty (no pages, no text), treat as None to force re-extraction
                if documentation and len(documentation.pages) == 0 and not documentation.full_text:
                    logger.info("Cached documentation is empty, will re-extract")
                    documentation = None
            
            # If not loaded from cache, extract from source
            if documentation is None:
                logger.info("Phase 1: Extracting documentation...")
                extractor = _get_extractor(args)
                
                # Get spec security schemes to pass to extractor
                spec_security_schemes = spec.get("components", {}).get("securitySchemes", {})
                if not spec_security_schemes:
                    spec_security_schemes = spec.get("securityDefinitions", {})
                
                # Parse URL filtering patterns
                allow_patterns = _parse_url_patterns(getattr(args, "allow_url_patterns", None))
                deny_patterns = _parse_url_patterns(getattr(args, "deny_url_patterns", None))
                
                # Use default deny patterns if none specified and using Playwright
                if isinstance(extractor, PlaywrightDocumentationExtractor):
                    if not getattr(args, "no_default_filters", False) and not deny_patterns:
                        deny_patterns = _get_default_deny_patterns()
                        logger.info("Using default deny patterns (URLs containing 'api' will override these)")
                
                # Handle Playwright context manager
                if isinstance(extractor, PlaywrightDocumentationExtractor):
                    extractor.__enter__()
                    try:
                        if args.docs:
                            is_url = args.docs.startswith(("http://", "https://"))
                            extract_kwargs = {
                                "source": args.docs,
                                "is_url": is_url,
                                "crawl": args.crawl and is_url,
                                "max_pages": args.max_pages or 10,
                                "same_domain": True,
                                "restrict_path_prefix": args.restrict_path,
                                "spec_security_schemes": spec_security_schemes,
                            }
                            
                            if allow_patterns:
                                extract_kwargs["allow_url_patterns"] = allow_patterns
                            if deny_patterns:
                                extract_kwargs["deny_url_patterns"] = deny_patterns
                            
                            documentation = extractor.extract_all(**extract_kwargs)
                        elif args.docs_text:
                            docs_text_path = Path(args.docs_text)
                            if docs_text_path.exists():
                                docs_text = docs_text_path.read_text(encoding="utf-8")
                            else:
                                docs_text = args.docs_text
                            documentation = extractor.extract_all(source=docs_text, is_url=False, spec_security_schemes=spec_security_schemes)
                    finally:
                        extractor.__exit__(None, None, None)
                else:
                    if args.docs:
                        is_url = args.docs.startswith(("http://", "https://"))
                        extract_kwargs = {
                            "source": args.docs,
                            "is_url": is_url,
                            "crawl": args.crawl and is_url,
                            "max_pages": args.max_pages or 10,
                            "same_domain": True,
                            "restrict_path_prefix": args.restrict_path,
                            "spec_security_schemes": spec_security_schemes,
                        }
                        documentation = extractor.extract_all(**extract_kwargs)
                    elif args.docs_text:
                        docs_text_path = Path(args.docs_text)
                        if docs_text_path.exists():
                            docs_text = docs_text_path.read_text(encoding="utf-8")
                        else:
                            docs_text = args.docs_text
                        documentation = extractor.extract_all(source=docs_text, is_url=False, spec_security_schemes=spec_security_schemes)
                
                if docs_path.exists() and not documentation:
                    # No docs source provided, but docs file exists - load it
                    logger.info(f"Phase 1: No docs source provided, loading from existing file: {docs_path}")
                    documentation = _load_documentation_from_file(docs_path)
                
                if documentation is None:
                    from specfix.extraction.structured_docs import StructuredDocumentation
                    documentation = StructuredDocumentation()
                else:
                    logger.info(f"Extracted documentation: {len(documentation.pages)} pages, {len(documentation.full_text)} chars")
                    
                    # Always save extracted documentation (if we extracted, not if we loaded)
                    if args.docs or args.docs_text:
                        _save_documentation_to_file(documentation, docs_path)

            # Phase 2: Heuristic detection (no LLM calls - fast and cheap)
            logger.info("Phase 2: Running heuristic detection...")
            detector = HeuristicDetector(spec, documentation)
            report = detector.detect_all()
            logger.info(f"Detected {report.total_count} potential issues (heuristic-based)")

            # Save issues.json
            report.save_to_file(str(issues_file))
            logger.info(f"Saved issues to: {issues_file}")

        if report.total_count == 0:
            logger.info("No issues to fix. Spec is already in good shape!")
            return 0

        # Extract API name from spec for output folder (do this early, before LLM calls)
        api_name = spec.get("info", {}).get("title", "api")
        # Sanitize API name for folder name (remove special chars, lowercase, replace spaces)
        api_name_sanitized = re.sub(r'[^\w\s-]', '', api_name.lower())
        api_name_sanitized = re.sub(r'[-\s]+', '_', api_name_sanitized).strip('_')
        if not api_name_sanitized:
            api_name_sanitized = "api"
        
        # Create output directory early (before LLM calls so we can save LLM I/O there)
        output_dir = spec_path.parent / f"{api_name_sanitized}_output"
        output_dir.mkdir(exist_ok=True)
        logger.info(f"Output directory: {output_dir}")
        
        # If save_llm_io is specified but not an absolute path, save it in the output directory
        save_llm_io_path = None
        if args.save_llm_io:
            save_llm_io_path = Path(args.save_llm_io)
            if not save_llm_io_path.is_absolute():
                # Relative path - save in output directory
                save_llm_io_path = output_dir / save_llm_io_path.name
            # Ensure parent directory exists
            save_llm_io_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Phase 3: Validate and fix (combined LLM call - efficient)
        logger.info("Phase 3: Validating and fixing issues with LLM...")
        logger.info("(Each issue is validated first; only real issues are fixed)")
        
        # Use MODEL / API_VERSION / AZURE_OPENAI_* env vars when set (Azure deployment name)
        model = os.environ.get("MODEL") or args.model
        api_version = os.environ.get("API_VERSION") or args.api_version
        azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT") or args.azure_endpoint
        api_key = args.api_key or os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if os.environ.get("MODEL"):
            logger.info(f"Using model from MODEL env: {model}")
        
        fixer = create_llm_fixer(
            api_key=api_key,
            model=model,
            azure_endpoint=azure_endpoint,
            api_version=api_version,
            base_url=args.base_url,
        )
        
        fixed_issues = fixer.generate_fixes(
            report.issues,
            spec_format=spec_format,
            max_fixes=args.max_fixes,
            save_llm_io=str(save_llm_io_path) if save_llm_io_path else None,
            spec=spec,  # Pass spec for fragment reconstruction
        )
        logger.info(f"Validated and fixed {len(fixed_issues)} out of {report.total_count} potential issues")

        # Apply fixes
        logger.info("Applying fixes...")
        patcher = SpecPatcher(spec)
        applied_count = patcher.apply_all_fixes(fixed_issues)
        patched_spec = patcher.get_patched_spec()

        # Determine fixed spec filename
        if args.output:
            # If user specified output, use that name but in output dir
            output_path = output_dir / Path(args.output).name
        else:
            output_path = output_dir / f"fixed_{spec_path.name}"

        # Save patched spec (with error handling)
        try:
            if spec_format == "yaml":
                output_path.write_text(
                    yaml.dump(patched_spec, default_flow_style=False, sort_keys=False),
                    encoding="utf-8",
                )
            else:
                # json is already imported at top of file
                output_path.write_text(
                    json.dumps(patched_spec, indent=2), encoding="utf-8"
                )
            logger.info(f"Patched spec saved to: {output_path}")
        except Exception as e:
            logger.error(f"Failed to save patched spec: {e}")
            # Try to save with a different name
            try:
                backup_path = output_dir / f"fixed_{spec_path.name}.backup"
                if spec_format == "yaml":
                    backup_path.write_text(
                        yaml.dump(patched_spec, default_flow_style=False, sort_keys=False),
                        encoding="utf-8",
                    )
                else:
                    backup_path.write_text(
                        json.dumps(patched_spec, indent=2), encoding="utf-8"
                    )
                logger.info(f"Saved patched spec to backup location: {backup_path}")
            except Exception as e2:
                logger.error(f"Failed to save backup spec: {e2}")

        # Generate and save diff (with error handling)
        diff_path = None
        try:
            original_content = spec_path.read_text(encoding="utf-8")
            patched_content = output_path.read_text(encoding="utf-8")
            diff = generate_unified_diff(
                original_content,
                patched_content,
                original_path=str(spec_path),
                patched_path=str(output_path),
            )
            diff_path = output_dir / f"{output_path.stem}.diff"
            save_diff(diff, diff_path)
            logger.info(f"Diff saved to: {diff_path}")
        except Exception as e:
            logger.warning(f"Failed to generate/save diff: {e}")

        # Build and save fixes summary (with error handling)
        fixes_summary = None
        fixes_summary_path = output_dir / "fixes_summary.json"
        try:
            fixes_summary = {
                "api_name": api_name,
                "api_version": spec.get("info", {}).get("version", "unknown"),
                "total_issues_detected": report.total_count,
                "issues_validated_and_fixed": len(fixed_issues),
                "fixes_applied": applied_count,
                "fixed_issues": [
                    {
                        "id": issue.id,
                        "type": issue.type.value,
                        "location": issue.location,
                        "description": issue.description,
                        "severity": issue.severity,
                        "confidence": issue.confidence,
                        "validation_reasoning": issue.validation_reasoning,
                        "suggested_fix_reasoning": issue.suggested_fix_reasoning,
                        "fix_applied": issue.suggested_fix,
                        "is_global": issue.is_global,
                        "affected_locations": issue.affected_locations if issue.is_global else [],
                    }
                    for issue in fixed_issues
                ],
                "summary_by_type": {},
                "summary_by_severity": {},
            }
            
            # Calculate summaries
            for issue in fixed_issues:
                issue_type = issue.type.value
                fixes_summary["summary_by_type"][issue_type] = fixes_summary["summary_by_type"].get(issue_type, 0) + 1
                fixes_summary["summary_by_severity"][issue.severity] = fixes_summary["summary_by_severity"].get(issue.severity, 0) + 1
            
            # Save fixes summary JSON
            fixes_summary_path.write_text(
                json.dumps(fixes_summary, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
            logger.info(f"Fixes summary saved to: {fixes_summary_path}")
        except Exception as e:
            logger.error(f"Failed to save fixes summary: {e}")
            # Try to save a minimal version
            try:
                minimal_summary = {
                    "api_name": api_name,
                    "api_version": spec.get("info", {}).get("version", "unknown"),
                    "total_issues_detected": report.total_count,
                    "issues_validated_and_fixed": len(fixed_issues),
                    "fixes_applied": applied_count,
                    "error": f"Partial save due to error: {str(e)}",
                }
                fixes_summary_path.write_text(
                    json.dumps(minimal_summary, indent=2, ensure_ascii=False),
                    encoding="utf-8"
                )
                logger.info(f"Saved minimal fixes summary to: {fixes_summary_path}")
            except Exception as e2:
                logger.error(f"Failed to save minimal summary: {e2}")

        # Generate and save human-readable summary report (with error handling)
        summary_report_path = output_dir / "fixes_summary.md"
        try:
            if fixes_summary:
                summary_report_lines = [
                    f"# Fixes Summary for {api_name}",
                    f"API Version: {fixes_summary['api_version']}",
                    "",
                    "## Statistics",
                    f"- Total issues detected: {fixes_summary['total_issues_detected']}",
                    f"- Issues validated and fixed: {fixes_summary['issues_validated_and_fixed']}",
                    f"- Fixes applied: {fixes_summary['fixes_applied']}",
                    "",
                    "## Summary by Issue Type",
                ]
                
                for issue_type, count in sorted(fixes_summary["summary_by_type"].items()):
                    summary_report_lines.append(f"- {issue_type}: {count}")
                
                summary_report_lines.extend([
                    "",
                    "## Summary by Severity",
                ])
                
                for severity, count in sorted(fixes_summary["summary_by_severity"].items()):
                    summary_report_lines.append(f"- {severity}: {count}")
                
                summary_report_lines.extend([
                    "",
                    "## Detailed Fixes",
                    "",
                ])
                
                # Group by global vs operation-specific
                global_fixes = [issue for issue in fixed_issues if issue.is_global]
                operation_fixes = [issue for issue in fixed_issues if not issue.is_global]
                
                if global_fixes:
                    summary_report_lines.append("### Global Issues (affect all/multiple operations)")
                    summary_report_lines.append("")
                    for issue in global_fixes:
                        summary_report_lines.extend([
                            f"**{issue.type.value}** (ID: {issue.id})",
                            f"- Description: {issue.description}",
                            f"- Confidence: {issue.confidence:.2f}",
                            f"- Affected locations: {len(issue.affected_locations)} operations",
                            f"- Reasoning: {issue.validation_reasoning[:200]}..." if len(issue.validation_reasoning) > 200 else f"- Reasoning: {issue.validation_reasoning}",
                            "",
                        ])
                
                if operation_fixes:
                    summary_report_lines.append("### Operation-Specific Issues")
                    summary_report_lines.append("")
                    
                    # Group by operation
                    operation_groups = {}
                    for issue in operation_fixes:
                        op_path = issue.location.split(".")[0] if "." in issue.location else issue.location
                        if op_path not in operation_groups:
                            operation_groups[op_path] = []
                        operation_groups[op_path].append(issue)
                    
                    for op_path, issues in sorted(operation_groups.items()):
                        summary_report_lines.append(f"#### {op_path}")
                        summary_report_lines.append("")
                        for issue in issues:
                            summary_report_lines.extend([
                                f"- **{issue.type.value}** (ID: {issue.id})",
                                f"  - Description: {issue.description}",
                                f"  - Confidence: {issue.confidence:.2f}",
                                f"  - Reasoning: {issue.validation_reasoning[:150]}..." if len(issue.validation_reasoning) > 150 else f"  - Reasoning: {issue.validation_reasoning}",
                                "",
                            ])
                
                # Save summary report
                summary_report_path.write_text("\n".join(summary_report_lines), encoding="utf-8")
                logger.info(f"Summary report saved to: {summary_report_path}")
            else:
                # Fallback if fixes_summary wasn't created
                fallback_report = f"""# Fixes Summary for {api_name}
API Version: {spec.get("info", {}).get("version", "unknown")}

## Statistics
- Total issues detected: {report.total_count}
- Issues validated and fixed: {len(fixed_issues)}
- Fixes applied: {applied_count}

Note: Full summary generation failed. See fixes_summary.json for details.
"""
                summary_report_path.write_text(fallback_report, encoding="utf-8")
                logger.info(f"Saved fallback summary report to: {summary_report_path}")
        except Exception as e:
            logger.error(f"Failed to save summary report: {e}")

        # Final summary
        logger.info(f"\n✅ Outputs saved to: {output_dir}")
        if output_path.exists():
            logger.info(f"  - Fixed spec: {output_path.name}")
        if diff_path and diff_path.exists():
            logger.info(f"  - Diff: {diff_path.name}")
        if fixes_summary_path.exists():
            logger.info(f"  - Fixes summary (JSON): fixes_summary.json")
        if summary_report_path.exists():
            logger.info(f"  - Summary report (Markdown): fixes_summary.md")

        return 0

    except Exception as e:
        logger.error(f"Fix failed: {e}", exc_info=True)
        # Try to save what we have even on error
        try:
            # Get spec_path from args if not in locals
            if 'spec_path' not in locals():
                spec_path = Path(args.spec).resolve()
            
            # If we got far enough to have fixed_issues, try to save them
            if 'fixed_issues' in locals() and fixed_issues:
                # Get api_name_sanitized if available, otherwise use default
                if 'api_name_sanitized' in locals():
                    api_name = api_name_sanitized
                else:
                    # Try to extract from spec
                    try:
                        spec = load_spec(spec_path)
                        api_name = spec.get("info", {}).get("title", "api")
                        api_name = re.sub(r'[^\w\s-]', '', api_name.lower())
                        api_name = re.sub(r'[-\s]+', '_', api_name).strip('_')
                        if not api_name:
                            api_name = "api"
                    except:
                        api_name = "api"
                
                error_output_dir = spec_path.parent / f"{api_name}_output"
                error_output_dir.mkdir(exist_ok=True)
                error_summary = {
                    "error": str(e),
                    "partial_results": True,
                    "issues_validated_and_fixed": len(fixed_issues),
                    "fixed_issues": [
                        {
                            "id": issue.id,
                            "type": issue.type.value,
                            "location": issue.location,
                            "description": issue.description,
                        }
                        for issue in fixed_issues[:10]  # Save first 10 as sample
                    ],
                }
                error_summary_path = error_output_dir / "error_summary.json"
                error_summary_path.write_text(
                    json.dumps(error_summary, indent=2, ensure_ascii=False),
                    encoding="utf-8"
                )
                logger.info(f"Saved error summary to: {error_summary_path}")
        except Exception as e2:
            logger.error(f"Failed to save error summary: {e2}")
        return 1


def main() -> int:
    """Main entry point for the CLI."""
    parser = argparse.ArgumentParser(
        description="SpecFix - Automated OpenAPI Specification Repair Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Analyze command
    analyze_parser = subparsers.add_parser(
        "analyze", help="Analyze an OpenAPI spec and detect issues"
    )
    analyze_parser.add_argument(
        "--spec", required=True, help="Path to OpenAPI spec file (JSON or YAML)"
    )
    analyze_parser.add_argument("--docs", help="URL to API documentation")
    analyze_parser.add_argument("--docs-text", help="Raw documentation text or path to text file")
    analyze_parser.add_argument(
        "--output", "-o", help="Output file for issue report (JSON)"
    )
    analyze_parser.add_argument(
        "--summary-out",
        help="Optional output file for a human-readable summary",
    )
    analyze_parser.add_argument(
        "--crawl",
        action="store_true",
        help="Crawl documentation links starting from the provided URL",
    )
    analyze_parser.add_argument(
        "--max-pages",
        type=int,
        help="Max pages to crawl when --crawl is enabled (default: 10)",
    )
    analyze_parser.add_argument(
        "--restrict-path",
        help="Only follow links whose path starts with this prefix",
    )
    analyze_parser.add_argument(
        "--save-docs",
        help="Save extracted documentation to a JSON file (default: {spec_name}_docs.json). Documentation is always saved and reused if available.",
    )
    analyze_parser.add_argument(
        "--validate",
        action="store_true",
        help="[DEPRECATED] Validation now happens during fixing phase for efficiency",
    )
    analyze_parser.add_argument(
        "--api-key", help="API key (or set OPENAI_API_KEY or AZURE_OPENAI_API_KEY env var)"
    )
    analyze_parser.add_argument(
        "--model", default="gpt-4o-mini", help="LLM model to use"
    )
    analyze_parser.add_argument(
        "--azure-endpoint", help="Azure OpenAI endpoint URL (if provided, uses Azure; or set AZURE_OPENAI_ENDPOINT env var)"
    )
    analyze_parser.add_argument(
        "--api-version", help="Azure OpenAI API version (required for Azure, default: 2025-01-01-preview)"
    )
    analyze_parser.add_argument(
        "--base-url", help="Custom base URL for OpenAI-compatible APIs"
    )
    analyze_parser.add_argument(
        "--use-playwright",
        action="store_true",
        help="Use Playwright extractor for JavaScript-rendered sites (requires playwright installed)",
    )
    analyze_parser.add_argument(
        "--playwright-headless",
        action="store_true",
        default=True,
        help="Run Playwright browser in headless mode (default: True)",
    )
    analyze_parser.add_argument(
        "--playwright-no-headless",
        action="store_false",
        dest="playwright_headless",
        help="Run Playwright browser with visible window",
    )
    analyze_parser.add_argument(
        "--playwright-browser",
        choices=["chromium", "firefox", "webkit"],
        default="chromium",
        help="Browser to use with Playwright (default: chromium)",
    )
    analyze_parser.add_argument(
        "--allow-url-patterns",
        help="Comma-separated regex patterns for URLs to include (whitelist). Only works with --use-playwright.",
    )
    analyze_parser.add_argument(
        "--deny-url-patterns",
        help="Comma-separated regex patterns for URLs to exclude (blacklist). Only works with --use-playwright. Note: URLs containing 'api' will override deny patterns.",
    )
    analyze_parser.add_argument(
        "--no-default-filters",
        action="store_true",
        help="Disable default deny patterns. Only works with --use-playwright.",
    )
    analyze_parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Timeout in seconds for page loads (default: 30)",
    )
    analyze_parser.add_argument(
        "--re-extract-auth",
        action="store_true",
        dest="re_extract_auth",
        help="Re-extract auth_info from cached full_text without re-running Playwright",
    )

    # Fix command
    fix_parser = subparsers.add_parser(
        "fix", help="Fix an OpenAPI spec using LLM-guided repair"
    )
    fix_parser.add_argument(
        "--spec", required=True, help="Path to OpenAPI spec file (JSON or YAML)"
    )
    fix_parser.add_argument("--docs", help="URL to API documentation")
    fix_parser.add_argument("--docs-text", help="Raw documentation text or path to text file")
    fix_parser.add_argument(
        "--output", "-o",
        help="Output file for patched spec (default: patched_<original_name>)"
    )
    fix_parser.add_argument(
        "--issues-json",
        help="Path to issues.json file (from previous analyze run). If not provided, will detect issues first.",
    )
    fix_parser.add_argument(
        "--force-detect",
        action="store_true",
        help="Force re-detection even if issues.json exists",
    )
    fix_parser.add_argument(
        "--api-key", help="API key (or set OPENAI_API_KEY or AZURE_OPENAI_API_KEY env var)"
    )
    fix_parser.add_argument(
        "--model", default="gpt-4o-mini", help="LLM model to use"
    )
    fix_parser.add_argument(
        "--azure-endpoint", help="Azure OpenAI endpoint URL (if provided, uses Azure; or set AZURE_OPENAI_ENDPOINT env var)"
    )
    fix_parser.add_argument(
        "--api-version", help="Azure OpenAI API version (required for Azure, default: 2025-01-01-preview)"
    )
    fix_parser.add_argument(
        "--base-url", help="Custom base URL for OpenAI-compatible APIs"
    )
    fix_parser.add_argument(
        "--max-fixes", type=int, help="Maximum number of fixes to generate"
    )
    fix_parser.add_argument(
        "--save-llm-io",
        help="Save LLM inputs (prompts) and outputs (responses) to a JSON file for inspection",
    )
    fix_parser.add_argument(
        "--validate",
        action="store_true",
        help="[DEPRECATED] Validation is now automatic during fixing phase",
    )
    fix_parser.add_argument(
        "--crawl",
        action="store_true",
        help="Crawl documentation links starting from the provided URL",
    )
    fix_parser.add_argument(
        "--max-pages",
        type=int,
        help="Max pages to crawl when --crawl is enabled (default: 10)",
    )
    fix_parser.add_argument(
        "--restrict-path",
        help="Only follow links whose path starts with this prefix",
    )
    fix_parser.add_argument(
        "--save-docs",
        help="Save extracted documentation to a JSON file (default: {spec_name}_docs.json). Documentation is always saved and reused if available.",
    )
    fix_parser.add_argument(
        "--use-playwright",
        action="store_true",
        help="Use Playwright extractor for JavaScript-rendered sites (requires playwright installed)",
    )
    fix_parser.add_argument(
        "--playwright-headless",
        action="store_true",
        default=True,
        help="Run Playwright browser in headless mode (default: True)",
    )
    fix_parser.add_argument(
        "--playwright-no-headless",
        action="store_false",
        dest="playwright_headless",
        help="Run Playwright browser with visible window",
    )
    fix_parser.add_argument(
        "--playwright-browser",
        choices=["chromium", "firefox", "webkit"],
        default="chromium",
        help="Browser to use with Playwright (default: chromium)",
    )
    fix_parser.add_argument(
        "--allow-url-patterns",
        help="Comma-separated regex patterns for URLs to include (whitelist). Only works with --use-playwright.",
    )
    fix_parser.add_argument(
        "--deny-url-patterns",
        help="Comma-separated regex patterns for URLs to exclude (blacklist). Only works with --use-playwright. Note: URLs containing 'api' will override deny patterns.",
    )
    fix_parser.add_argument(
        "--no-default-filters",
        action="store_true",
        help="Disable default deny patterns. Only works with --use-playwright.",
    )
    fix_parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Timeout in seconds for page loads (default: 30)",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Validate Playwright arguments
    if getattr(args, "use_playwright", False) and not PLAYWRIGHT_AVAILABLE:
        logger.error(
            "Playwright is not installed. Install with: pip install playwright && playwright install"
        )
        logger.info("You can continue without --use-playwright to use the regular extractor.")
        return 1
    
    if args.command == "analyze":
        return analyze_command(args)
    elif args.command == "fix":
        return fix_command(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
