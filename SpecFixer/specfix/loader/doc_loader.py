"""
Documentation loader and scraper.

Handles loading API documentation from URLs or raw text, extracting relevant
content for comparison with OpenAPI specifications.
"""

import re
from pathlib import Path
from typing import Optional, Set, Deque, Tuple
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup

from specfix.utils.logger import get_logger

logger = get_logger(__name__)


class DocLoadError(Exception):
    """Raised when documentation loading fails."""

    pass


def load_documentation(
    source: str, is_url: bool = True, timeout: int = 30
) -> str:
    """
    Load documentation from a URL or raw text.
    
    Args:
        source: URL or raw documentation text
        is_url: Whether source is a URL (True) or raw text (False)
        timeout: Request timeout in seconds (for URLs)
    
    Returns:
        Cleaned documentation text
    
    Raises:
        DocLoadError: If documentation cannot be loaded
    """
    if is_url:
        return load_documentation_from_url(source, timeout)
    else:
        return clean_documentation_text(source)


def load_documentation_from_url(url: str, timeout: int = 30) -> str:
    return _fetch_and_extract(url, timeout=timeout)


def _fetch_and_extract(url: str, timeout: int = 30) -> str:
    """
    Fetch and extract documentation from a URL.
    
    Args:
        url: URL to fetch documentation from
        timeout: Request timeout in seconds
    
    Returns:
        Cleaned documentation text
    
    Raises:
        DocLoadError: If URL cannot be fetched
    """
    logger.info(f"Fetching documentation from URL: {url}")
    
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as e:
        raise DocLoadError(f"Failed to fetch documentation URL: {e}") from e
    
    # Parse HTML
    try:
        soup = BeautifulSoup(response.content, "html.parser")
        
        # Remove script and style elements
        for script in soup(["script", "style", "nav", "footer", "header"]):
            script.decompose()
        
        # Extract text content
        text = soup.get_text()
        
        # Clean up the text
        cleaned = clean_documentation_text(text)
        
        logger.info(f"Successfully extracted {len(cleaned)} characters from documentation")
        return cleaned
        
    except Exception as e:
        raise DocLoadError(f"Failed to parse documentation HTML: {e}") from e


def crawl_documentation(
    start_url: str,
    max_pages: int = 10,
    same_domain: bool = True,
    restrict_path_prefix: Optional[str] = None,
    timeout: int = 30,
) -> str:
    """
    Crawl linked documentation pages and aggregate cleaned text.
    
    Heuristics:
    - BFS up to max_pages pages
    - Only follow links in the same domain if same_domain=True
    - Optionally restrict to links whose path starts with restrict_path_prefix
    
    Args:
        start_url: Starting documentation URL
        max_pages: Max number of pages to fetch
        same_domain: Restrict to same host
        restrict_path_prefix: Only follow links whose path starts with this prefix (e.g., '/reference')
        timeout: Request timeout
    
    Returns:
        Aggregated cleaned text from crawled pages
    """
    from collections import deque

    logger.info(
        f"Crawling documentation from: {start_url} (max_pages={max_pages}, same_domain={same_domain}, restrict_path_prefix={restrict_path_prefix})"
    )

    try:
        parsed_start = urlparse(start_url)
        host = parsed_start.netloc
        base = f"{parsed_start.scheme}://{parsed_start.netloc}"
        visited: Set[str] = set()
        queue: Deque[str] = deque([start_url])
        aggregated: list[str] = []
        pages_fetched = 0

        while queue and pages_fetched < max_pages:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)

            try:
                logger.info(f"Crawl fetching: {current}")
                resp = requests.get(current, timeout=timeout)
                resp.raise_for_status()
            except requests.RequestException:
                continue

            soup = BeautifulSoup(resp.content, "html.parser")

            # Extract text from current page
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text()
            aggregated.append(clean_documentation_text(text))
            pages_fetched += 1

            # Enqueue new links
            for a in soup.find_all("a", href=True):
                href = a["href"]
                # Skip anchors and mailto
                if href.startswith("#") or href.startswith("mailto:"):
                    continue
                absolute = urljoin(current, href)
                parsed = urlparse(absolute)
                if same_domain and parsed.netloc != host:
                    continue
                if restrict_path_prefix and not parsed.path.startswith(restrict_path_prefix):
                    continue
                if absolute not in visited:
                    queue.append(absolute)

        combined = "\n\n----\n\n".join(aggregated)
        logger.info(f"Crawl complete. Pages fetched: {pages_fetched}, combined length: {len(combined)}")
        return combined

    except Exception as e:
        raise DocLoadError(f"Failed during crawl: {e}") from e


def clean_documentation_text(text: str) -> str:
    """
    Clean and normalize documentation text.
    
    Removes excessive whitespace, normalizes line breaks, and extracts
    relevant content patterns.
    
    Args:
        text: Raw documentation text
    
    Returns:
        Cleaned documentation text
    """
    # Remove excessive whitespace
    text = re.sub(r"\s+", " ", text)
    
    # Normalize line breaks
    text = re.sub(r"\n\s*\n", "\n", text)
    
    # Remove leading/trailing whitespace from each line
    lines = [line.strip() for line in text.split("\n")]
    lines = [line for line in lines if line]  # Remove empty lines
    
    return "\n".join(lines)


def extract_api_elements(text: str) -> dict:
    """
    Extract API-related elements from documentation text.
    
    Uses heuristics to find:
    - Endpoints/paths
    - Parameters
    - Authentication requirements
    - Headers
    - Request/response examples
    
    Args:
        text: Documentation text
    
    Returns:
        Dictionary with extracted elements
    """
    elements = {
        "endpoints": [],
        "parameters": [],
        "headers": [],
        "auth_mentions": [],
        "examples": [],
    }
    
    # Extract potential endpoints (paths starting with /)
    endpoint_pattern = r"(?:GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)\s+([/\w\-{}]+)"
    endpoints = re.findall(endpoint_pattern, text, re.IGNORECASE)
    elements["endpoints"].extend(endpoints)
    
    # Extract parameter mentions
    param_pattern = r"(?:parameter|param|query|path|header)\s*[:=]\s*(\w+)"
    params = re.findall(param_pattern, text, re.IGNORECASE)
    elements["parameters"].extend(params)
    
    # Extract header mentions
    header_pattern = r"(?:header|Authorization|Content-Type|Accept)\s*[:=]\s*([\w\-]+)"
    headers = re.findall(header_pattern, text, re.IGNORECASE)
    elements["headers"].extend(headers)
    
    # Extract authentication mentions
    auth_pattern = r"(?:auth|authentication|bearer|api[_\s]?key|token|oauth)"
    auth_mentions = re.findall(auth_pattern, text, re.IGNORECASE)
    elements["auth_mentions"].extend(auth_mentions)
    
    # Extract JSON-like examples
    json_pattern = r"\{[^{}]*\"[^\"]+\"[^{}]*\}"
    examples = re.findall(json_pattern, text)
    elements["examples"].extend(examples[:10])  # Limit to 10
    
    return elements

