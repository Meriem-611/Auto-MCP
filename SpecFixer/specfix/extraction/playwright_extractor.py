"""
Playwright-based documentation extractor.

Uses Playwright to extract documentation with full JavaScript execution support.
Handles dynamic content, SPAs, and JavaScript-rendered documentation sites.
"""

import re
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Union
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from specfix.extraction.structured_docs import DocumentationPage, StructuredDocumentation
from specfix.utils.logger import get_logger

logger = get_logger(__name__)

try:
    from playwright.sync_api import sync_playwright, Browser, Page, TimeoutError as PlaywrightTimeoutError
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logger.warning("Playwright not available. Install with: pip install playwright && playwright install")


class DocExtractionError(Exception):
    """Raised when documentation extraction fails."""
    pass


class PlaywrightDocumentationExtractor:
    """
    Playwright-based documentation extractor with full JavaScript support.
    
    Handles:
    - JavaScript-rendered content
    - Dynamic expandable/collapsible sections
    - Single Page Applications (SPAs)
    - Lazy-loaded content
    - Client-side routing
    - Interactive elements
    """
    
    def __init__(self, timeout: int = 30, headless: bool = True, browser_type: str = "chromium", auto_init: bool = True):
        """
        Initialize the Playwright extractor.
        
        Args:
            timeout: Page load timeout in seconds
            headless: Run browser in headless mode
            browser_type: Browser to use ('chromium', 'firefox', or 'webkit')
            auto_init: Automatically initialize browser on first use (if False, use context manager)
        """
        if not PLAYWRIGHT_AVAILABLE:
            raise ImportError(
                "Playwright is not installed. Install with: "
                "pip install playwright && playwright install"
            )
        
        self.timeout = timeout * 1000  # Convert to milliseconds for Playwright
        self.headless = headless
        self.browser_type = browser_type
        self.auto_init = auto_init
        self.playwright = None
        self.browser: Optional[Browser] = None
        self._initialized = False
    
    def __enter__(self):
        """Context manager entry."""
        if not self._initialized:
            self.playwright = sync_playwright().start()
            if self.browser_type == "chromium":
                self.browser = self.playwright.chromium.launch(headless=self.headless)
            elif self.browser_type == "firefox":
                self.browser = self.playwright.firefox.launch(headless=self.headless)
            elif self.browser_type == "webkit":
                self.browser = self.playwright.webkit.launch(headless=self.headless)
            else:
                raise ValueError(f"Unknown browser type: {self.browser_type}")
            self._initialized = True
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.cleanup()
    
    def _ensure_initialized(self):
        """Ensure browser is initialized."""
        if not self._initialized and self.auto_init:
            self.playwright = sync_playwright().start()
            if self.browser_type == "chromium":
                self.browser = self.playwright.chromium.launch(headless=self.headless)
            elif self.browser_type == "firefox":
                self.browser = self.playwright.firefox.launch(headless=self.headless)
            elif self.browser_type == "webkit":
                self.browser = self.playwright.webkit.launch(headless=self.headless)
            else:
                raise ValueError(f"Unknown browser type: {self.browser_type}")
            self._initialized = True
    
    def cleanup(self):
        """Clean up browser resources."""
        if self.browser:
            self.browser.close()
            self.browser = None
        if self.playwright:
            self.playwright.stop()
            self.playwright = None
        self._initialized = False
    
    def extract_all(
        self,
        source: str,
        is_url: bool = True,
        crawl: bool = False,
        max_pages: int = 10,
        same_domain: bool = True,
        restrict_path_prefix: Optional[str] = None,
        allow_url_patterns: Optional[List[Union[str, re.Pattern]]] = None,
        deny_url_patterns: Optional[List[Union[str, re.Pattern]]] = None,
        url_filter: Optional[Callable[[str], bool]] = None,
        spec_security_schemes: Optional[Dict[str, Any]] = None,
    ) -> StructuredDocumentation:
        """
        Extract all documentation content with structure.
        
        Args:
            source: URL or raw documentation text
            is_url: Whether source is a URL
            crawl: Whether to crawl linked pages
            max_pages: Maximum pages to crawl
            same_domain: Restrict crawling to same domain
            restrict_path_prefix: Only follow links with this path prefix
            allow_url_patterns: List of regex patterns or strings that URLs must match (whitelist)
            deny_url_patterns: List of regex patterns or strings that URLs must NOT match (blacklist)
            url_filter: Custom function(url: str) -> bool to filter URLs. Returns True to include URL.
        
        Returns:
            StructuredDocumentation object
        
        Examples:
            # Only scrape API documentation pages
            extractor.extract_all(
                source="https://docs.example.com",
                allow_url_patterns=[r"/api/", r"/docs/api/"]
            )
            
            # Exclude certain pages
            extractor.extract_all(
                source="https://docs.example.com",
                deny_url_patterns=[r"/changelog", r"/blog"]
            )
            
            # Custom filter function
            def my_filter(url: str) -> bool:
                return "/api/" in url and "v2" not in url
            extractor.extract_all(
                source="https://docs.example.com",
                url_filter=my_filter
            )
        """
        if is_url:
            if crawl:
                return self._extract_with_crawl(
                    source, max_pages, same_domain, restrict_path_prefix,
                    allow_url_patterns, deny_url_patterns, url_filter,
                    spec_security_schemes=spec_security_schemes
                )
            else:
                return self._extract_single_page(source, spec_security_schemes=spec_security_schemes)
        else:
            return self._extract_from_text(source, spec_security_schemes=spec_security_schemes)
    
    def _extract_single_page(self, url: str, spec_security_schemes: Optional[Dict[str, Any]] = None) -> StructuredDocumentation:
        """Extract from a single page."""
        page = self._fetch_and_parse_page(url)
        pages = [page]
        
        structured = StructuredDocumentation(
            pages=pages,
            full_text=page.content,
        )
        
        # Extract structured elements
        self._extract_structured_elements(structured, spec_security_schemes=spec_security_schemes)
        
        return structured
    
    def _extract_with_crawl(
        self,
        start_url: str,
        max_pages: int,
        same_domain: bool,
        restrict_path_prefix: Optional[str],
        allow_url_patterns: Optional[List[Union[str, re.Pattern]]] = None,
        deny_url_patterns: Optional[List[Union[str, re.Pattern]]] = None,
        url_filter: Optional[Callable[[str], bool]] = None,
        spec_security_schemes: Optional[Dict[str, Any]] = None,
    ) -> StructuredDocumentation:
        """Extract with crawling multiple pages."""
        self._ensure_initialized()
        
        parsed_start = urlparse(start_url)
        host = parsed_start.netloc
        visited: Set[str] = set()
        queue: Deque[str] = deque([start_url])
        pages: list[DocumentationPage] = []
        pages_fetched = 0
        
        # Create a browser context for crawling
        context = self.browser.new_context()
        
        try:
            while queue and pages_fetched < max_pages:
                current = queue.popleft()
                if current in visited:
                    continue
                visited.add(current)
                
                try:
                    logger.info(f"Crawling: {current}")
                    print(f"[PROGRESS] Page {pages_fetched + 1}/{max_pages}: Fetching {current}")
                    page = self._fetch_and_parse_page_with_context(current, context)
                    pages.append(page)
                    pages_fetched += 1
                    print(f"[PROGRESS] Page {pages_fetched}/{max_pages}: Extracted {len(page.content):,} chars from '{page.title[:60]}...'")
                    
                    # Small delay between requests to avoid rate limiting
                    if pages_fetched < max_pages:
                        print(f"[PROGRESS] Waiting 0.5s before next page...")
                        time.sleep(0.5)  # 500ms delay between requests
                    
                    # Enqueue new links
                    if pages_fetched < max_pages:
                        for link in page.links:
                            if not self._should_follow_link(
                                link, host, same_domain, restrict_path_prefix,
                                allow_url_patterns, deny_url_patterns, url_filter, visited
                            ):
                                continue
                            queue.append(link)
                
                except Exception as e:
                    logger.warning(f"Failed to fetch {current}: {e}")
                    continue
        finally:
            context.close()
        
        # Combine all pages
        full_text = "\n\n----\n\n".join([p.content for p in pages])
        
        structured = StructuredDocumentation(
            pages=pages,
            full_text=full_text,
        )
        
        # Extract structured elements from all pages
        self._extract_structured_elements(structured, spec_security_schemes=spec_security_schemes)
        
        logger.info(f"Crawl complete: {pages_fetched} pages, {len(full_text)} chars")
        return structured
    
    def _should_follow_link(
        self,
        link: str,
        host: str,
        same_domain: bool,
        restrict_path_prefix: Optional[str],
        allow_url_patterns: Optional[List[Union[str, re.Pattern]]],
        deny_url_patterns: Optional[List[Union[str, re.Pattern]]],
        url_filter: Optional[Callable[[str], bool]],
        visited: Set[str],
    ) -> bool:
        """
        Determine if a link should be followed based on filtering rules.
        
        Returns True if the link should be followed, False otherwise.
        
        Special rule: If URL contains "api" (case-insensitive), it's always included
        even if it matches deny patterns (API docs take priority).
        """
        # Skip if already visited
        if link in visited:
            return False
        
        parsed = urlparse(link)
        
        # Domain check
        if same_domain and parsed.netloc != host:
            return False
        
        # Path prefix check
        if restrict_path_prefix and not parsed.path.startswith(restrict_path_prefix):
            return False
        
        # Special rule: If URL contains "api" (case-insensitive), always include it
        # This overrides deny patterns to ensure API documentation is captured
        contains_api = "api" in link.lower()
        
        # Deny patterns (blacklist) - checked first, but skip if contains "api"
        if deny_url_patterns and not contains_api:
            for pattern in deny_url_patterns:
                if isinstance(pattern, re.Pattern):
                    if pattern.search(link):
                        print(f"  [FILTER] Denied (blacklist): {link}")
                        return False
                else:
                    if re.search(pattern, link, re.IGNORECASE):
                        print(f"  [FILTER] Denied (blacklist): {link}")
                        return False
        
        # Allow patterns (whitelist) - if specified, must match at least one
        if allow_url_patterns:
            matched = False
            for pattern in allow_url_patterns:
                if isinstance(pattern, re.Pattern):
                    if pattern.search(link):
                        matched = True
                        break
                else:
                    if re.search(pattern, link, re.IGNORECASE):
                        matched = True
                        break
            if not matched:
                # Exception: if contains "api", allow it anyway
                if not contains_api:
                    print(f"  [FILTER] Denied (not in whitelist): {link}")
                    return False
        
        # Custom filter function
        if url_filter:
            try:
                if not url_filter(link):
                    # Exception: if contains "api", allow it anyway
                    if not contains_api:
                        print(f"  [FILTER] Denied (custom filter): {link}")
                        return False
            except Exception as e:
                logger.warning(f"Error in custom URL filter for {link}: {e}")
                if not contains_api:
                    return False
        
        # Log if we're including because of "api" in URL
        if contains_api and deny_url_patterns:
            for pattern in deny_url_patterns:
                pattern_str = pattern.pattern if isinstance(pattern, re.Pattern) else pattern
                if re.search(pattern_str, link, re.IGNORECASE):
                    print(f"  [FILTER] Allowed (contains 'api'): {link}")
                    break
        
        return True
    
    def _extract_from_text(self, text: str, spec_security_schemes: Optional[Dict[str, Any]] = None) -> StructuredDocumentation:
        """
        Extract from raw text content (not from URL).
        This is a completely separate path from URL extraction.
        Used when source is a text file or raw text string.
        """
        # For text extraction, we don't parse HTML - just use the text as-is
        cleaned_text = self._clean_text(text)
        
        page = DocumentationPage(
            url="",
            title="",
            content=cleaned_text,
            structured_elements={"tables": [], "code_blocks": [], "lists": []},
            links=[],
        )
        
        structured = StructuredDocumentation(
            pages=[page],
            full_text=cleaned_text,
        )
        
        # Extract structured elements from the text (endpoints, parameters, etc.)
        self._extract_structured_elements(structured, spec_security_schemes=spec_security_schemes)
        return structured
    
    def _fetch_and_parse_page(self, url: str) -> DocumentationPage:
        """
        Fetch and parse a single HTML page from a URL using Playwright.
        Creates a new browser context for each page.
        """
        self._ensure_initialized()
        context = self.browser.new_context()
        try:
            return self._fetch_and_parse_page_with_context(url, context)
        finally:
            context.close()
    
    def _fetch_and_parse_page_with_context(self, url: str, context) -> DocumentationPage:
        """
        Fetch and parse a single HTML page using an existing browser context.
        """
        page = context.new_page()
        
        try:
            # Navigate to the page
            logger.debug(f"Navigating to: {url}")
            print(f"  -> Navigating to page...")
            page.goto(url, wait_until="networkidle", timeout=self.timeout)
            print(f"  -> Page loaded, waiting for DOM...")
            
            # Wait for page to be fully loaded
            page.wait_for_load_state("domcontentloaded")
            
            # Wait for main content to appear (common selectors for documentation sites)
            # Try multiple selectors to catch different site structures
            content_selectors = [
                "main", "article", "[role='main']", ".content", "#content",
                ".main-content", ".doc-content", ".page-content", "main article"
            ]
            content_found = False
            for selector in content_selectors:
                try:
                    page.wait_for_selector(selector, timeout=5000, state="attached")
                    # Wait a bit more for content to render
                    page.wait_for_timeout(1000)
                    content_found = True
                    break
                except Exception:
                    continue
            
            if not content_found:
                # If no specific content selector found, wait a bit longer for JS to render
                page.wait_for_timeout(2000)
            
            print(f"  -> Expanding collapsible sections...")
            
            # Expand collapsible sections
            self._expand_collapsible_sections_playwright(page)
            
            print(f"  -> Scrolling to trigger lazy loading...")
            # Scroll to trigger lazy loading
            self._scroll_page(page)
            print(f"  -> Extracting content...")
            
            # Get the HTML content after JavaScript execution
            html_content = page.content()
            
            # Parse with BeautifulSoup
            soup = BeautifulSoup(html_content, "html.parser")
            
            # Extract title
            title = ""
            if soup.title:
                title = soup.title.get_text().strip()
            elif soup.find("h1"):
                title = soup.find("h1").get_text().strip()
            else:
                # Try to get title from page
                try:
                    title = page.title()
                except Exception:
                    pass
            
            # Remove non-content elements (but be careful not to remove main content)
            # Only remove script/style, and navigation/footer/header that are clearly not main content
            for tag in soup(["script", "style"]):
                tag.decompose()
            
            # Remove nav, footer, header, aside but only if they're not the main content container
            # Check if main/article exists - if so, only remove nav/footer/header/aside outside of it
            main_content = soup.find("main") or soup.find("article") or soup.find(attrs={"role": "main"})
            if main_content:
                # Only remove nav/footer/header/aside outside the main content
                for tag in soup.find_all(["nav", "footer", "header", "aside"]):
                    # Check if this tag is inside the main content area
                    # Use find_parent to check if tag is a descendant of main_content
                    parent = tag.find_parent(["main", "article"])
                    if parent or (main_content and tag in main_content.descendants):
                        continue  # Keep it, it's part of the main content
                    tag.decompose()
            else:
                # If no main content wrapper, remove nav/footer/header/aside more carefully
                # Only remove if they're clearly navigation/footer elements
                for tag in soup.find_all(["nav", "footer"]):
                    tag.decompose()
                # For header, only remove if it's clearly a site header (not article header)
                for header in soup.find_all("header"):
                    # Keep header if it's inside article/main, otherwise remove
                    if not (header.find_parent("article") or header.find_parent("main")):
                        header.decompose()
                for aside in soup.find_all("aside"):
                    # Keep aside if it's inside article/main, otherwise remove
                    if not (aside.find_parent("article") or aside.find_parent("main")):
                        aside.decompose()
            
            # Extract structured elements before getting text
            structured_elements = self._parse_structured_elements(soup)
            
            # Extract links BEFORE we convert to text (so we can crawl them)
            links = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if href.startswith("#") or href.startswith("mailto:") or href.startswith("javascript:"):
                    continue
                absolute = urljoin(url, href)
                links.append(absolute)
            
            # Also get links from the page (in case they're dynamically added)
            try:
                page_links = page.evaluate("""
                    () => {
                        const links = [];
                        document.querySelectorAll('a[href]').forEach(a => {
                            const href = a.getAttribute('href');
                            if (href && !href.startsWith('#') && !href.startsWith('mailto:') && !href.startsWith('javascript:')) {
                                links.push(href);
                            }
                        });
                        return links;
                    }
                """)
                for link in page_links:
                    absolute = urljoin(url, link)
                    if absolute not in links:
                        links.append(absolute)
            except Exception as e:
                logger.debug(f"Could not extract links from page: {e}")
            
            # Extract text content
            text = soup.get_text()
            cleaned_text = self._clean_text(text)
            
            return DocumentationPage(
                url=url,
                title=title,
                content=cleaned_text,
                structured_elements=structured_elements,
                links=links,
            )
        
        except PlaywrightTimeoutError as e:
            raise DocExtractionError(f"Timeout while fetching {url}: {e}") from e
        except Exception as e:
            raise DocExtractionError(f"Failed to parse {url}: {e}") from e
        finally:
            page.close()
    
    def _expand_collapsible_sections_playwright(self, page: Page) -> None:
        """
        Expand collapsible/accordion sections using Playwright.
        Actually clicks buttons and waits for content to load.
        """
        try:
            # Expand <details> elements
            page.evaluate("""
                () => {
                    document.querySelectorAll('details').forEach(details => {
                        details.open = true;
                    });
                }
            """)
            
            # Click common expand/collapse buttons
            expand_selectors = [
                'button[aria-expanded="false"]',
                '.expand-button',
                '.collapse-button',
                '[class*="expand"]',
                '[class*="collapse"]',
                '[class*="accordion"] button',
                '.toggle',
                '[data-toggle="collapse"]',
            ]
            
            for selector in expand_selectors:
                try:
                    elements = page.query_selector_all(selector)
                    for element in elements:
                        try:
                            # Check if it's visible and clickable
                            if element.is_visible():
                                element.click(timeout=1000)
                                # Small delay to let content load
                                page.wait_for_timeout(200)
                        except Exception:
                            # Element might not be clickable, skip it
                            continue
                except Exception:
                    # Selector might not match anything, continue
                    continue
            
            # Wait a bit for any animations/transitions
            page.wait_for_timeout(500)
            
        except Exception as e:
            logger.debug(f"Error expanding collapsible sections: {e}")
    
    def _scroll_page(self, page: Page) -> None:
        """
        Scroll the page to trigger lazy loading of content.
        """
        try:
            # Get page dimensions
            viewport_height = page.viewport_size["height"]
            content_height = page.evaluate("document.body.scrollHeight")
            
            # Scroll down in increments
            scroll_position = 0
            scroll_increment = viewport_height * 0.8  # Scroll 80% of viewport at a time
            
            while scroll_position < content_height:
                page.evaluate(f"window.scrollTo(0, {scroll_position})")
                page.wait_for_timeout(300)  # Wait for lazy loading
                scroll_position += scroll_increment
                
                # Check if content height increased (new content loaded)
                new_height = page.evaluate("document.body.scrollHeight")
                if new_height > content_height:
                    content_height = new_height
            
            # Scroll back to top
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(200)
            
        except Exception as e:
            logger.debug(f"Error scrolling page: {e}")
    
    def _parse_structured_elements(self, soup: BeautifulSoup) -> dict:
        """Parse structured elements from HTML."""
        elements = {
            "tables": [],
            "code_blocks": [],
            "lists": [],
        }
        
        # Extract tables
        for table in soup.find_all("table"):
            table_data = []
            for row in table.find_all("tr"):
                cells = [cell.get_text(strip=True) for cell in row.find_all(["td", "th"])]
                if cells:
                    table_data.append(cells)
            if table_data:
                elements["tables"].append(table_data)
        
        # Extract code blocks
        for code in soup.find_all(["code", "pre"]):
            code_text = code.get_text()
            if code_text.strip():
                elements["code_blocks"].append(code_text)
        
        # Extract lists
        for ul in soup.find_all(["ul", "ol"]):
            items = [li.get_text(strip=True) for li in ul.find_all("li")]
            if items:
                elements["lists"].append(items)
        
        return elements
    
    def _extract_structured_elements(self, structured: StructuredDocumentation, spec_security_schemes: Optional[Dict[str, Any]] = None) -> None:
        """
        Extract API-related structured elements from documentation.
        Same logic as the original extractor.
        
        Args:
            structured: The StructuredDocumentation object to populate
            spec_security_schemes: Optional dict of security schemes from the spec
        """
        text = structured.full_text.lower()
        full_text_original = structured.full_text  # Keep original case
        
        # Extract endpoints
        endpoint_pattern = r"(?:GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)\s+([/\w\-{}]+)"
        endpoints = re.findall(endpoint_pattern, structured.full_text, re.IGNORECASE)
        structured.endpoints = [{"path": ep, "method": None} for ep in set(endpoints)]
        
        # Extract parameters (with context)
        param_pattern = r"(?:parameter|param|query|path|header)\s*[:=]\s*(\w+)"
        params = re.findall(param_pattern, structured.full_text, re.IGNORECASE)
        
        # Try to extract parameters from tables
        for page in structured.pages:
            for table in page.structured_elements.get("tables", []):
                # Look for parameter tables
                if len(table) > 0 and any("param" in str(cell).lower() for cell in table[0]):
                    for row in table[1:]:
                        if len(row) > 0:
                            param_name = row[0].strip()
                            if param_name:
                                params.append(param_name)
        
        # Group parameters by endpoint (simple heuristic)
        for endpoint in structured.endpoints:
            path = endpoint["path"]
            # Find parameters mentioned near this endpoint
            endpoint_params = []
            for param in set(params):
                # Check if param is mentioned in context of this endpoint
                escaped_path = re.escape(path)
                escaped_param = re.escape(param)
                pattern = rf"{escaped_path}(?:(?!{{|}}).)*{escaped_param}"
                try:
                    if re.search(pattern, structured.full_text, re.IGNORECASE):
                        endpoint_params.append({"name": param, "type": None})
                except re.error:
                    # If regex fails, fall back to simple string search
                    if param in structured.full_text:
                        endpoint_params.append({"name": param, "type": None})
            
            if endpoint_params:
                structured.parameters[path] = endpoint_params
        
        # Extract headers
        header_pattern1 = r"(?:header|Authorization|Content-Type|Accept|X-[\w\-]+)\s*[:=]\s*([\w\-]+)"
        header_pattern2 = r"([A-Z][\w\-]+)\s+header"
        header_pattern3 = r"([A-Z][\w\-]+)\s*:\s*(?!//)[^\n]+"
        
        non_header_words = {
            "https", "http", "get", "post", "put", "delete", "patch", "options", "head",
            "api", "url", "uri", "json", "xml", "html", "text", "plain", "form", "data",
            "bearer", "basic", "oauth", "token", "key", "secret", "id", "type", "name",
            "value", "string", "integer", "boolean", "array", "object", "null", "true", "false"
        }
        
        headers = re.findall(header_pattern1, structured.full_text, re.IGNORECASE)
        headers.extend(re.findall(header_pattern2, structured.full_text, re.IGNORECASE))
        # Extract from code blocks too
        for page in structured.pages:
            for code_block in page.structured_elements.get("code_blocks", []):
                code_headers = re.findall(header_pattern3, code_block, re.IGNORECASE)
                headers.extend([h for h in code_headers if len(h) > 3])
        
        # Filter out non-header words and validate headers
        valid_headers = []
        for h in headers:
            h_lower = h.lower()
            if h_lower in non_header_words:
                continue
            if h_lower in ["https", "http", "ftp", "ws", "wss"]:
                continue
            if len(h) < 3:
                continue
            if "-" in h or h[0].isupper():
                valid_headers.append(h)
        
        structured.headers = list(set(valid_headers))
        
        # Check for global headers (mentioned with "required" language)
        global_header_patterns = [
            r"([A-Z][\w\-]+)\s+header\s+(?:is\s+)?required",
            r"([A-Z][\w\-]+)\s+header\s+must\s+be\s+included",
            r"Setting\s+(?:this\s+)?([A-Z][\w\-]+)\s+header\s+is\s+required",
            r"([A-Z][\w\-]+)\s+header\s+must\s+be\s+included\s+in\s+all",
            r"all\s+(?:REST\s+)?API\s+requests.*?([A-Z][\w\-]+)\s+header",
        ]
        
        for pattern in global_header_patterns:
            matches = re.findall(pattern, structured.full_text, re.IGNORECASE | re.DOTALL)
            for match in matches:
                if isinstance(match, tuple):
                    match = match[0] if match else ""
                match_lower = match.lower()
                if match_lower in non_header_words or match_lower in ["https", "http"]:
                    continue
                if match and len(match) > 3 and ("-" in match or match[0].isupper()):
                    if match not in structured.global_headers:
                        structured.global_headers.append(match)
                    match_pos = structured.full_text.find(match)
                    if match_pos != -1:
                        context_start = max(0, match_pos - 200)
                        context_end = min(len(structured.full_text), match_pos + len(match) + 300)
                        context = structured.full_text[context_start:context_end]
                        if match not in structured.global_header_contexts or "required" in context.lower():
                            structured.global_header_contexts[match] = context.strip()
        
        # Also check for headers mentioned near "all requests" or "every request"
        global_header_keywords = ["all requests", "every request", "all rest api requests", "all api requests"]
        for keyword in global_header_keywords:
            if keyword in text:
                context_start = text.find(keyword)
                context = structured.full_text[max(0, context_start-300):context_start+600]
                
                required_keywords = ["required", "must", "mandatory", "needed"]
                context_lower = context.lower()
                has_required_language = any(req_word in context_lower for req_word in required_keywords)
                
                if not has_required_language:
                    continue
                
                context_headers1 = re.findall(header_pattern1, context, re.IGNORECASE)
                context_headers2 = re.findall(header_pattern2, context, re.IGNORECASE)
                context_headers3 = re.findall(r"([A-Z][\w\-]+)\s*:\s*(?!//)", context, re.IGNORECASE)
                all_context_headers = context_headers1 + context_headers2 + context_headers3
                
                valid_context_headers = []
                for h in all_context_headers:
                    h_lower = h.lower()
                    if h_lower in non_header_words or h_lower in ["https", "http"]:
                        continue
                    if len(h) > 3 and ("-" in h or h[0].isupper()):
                        if h not in structured.global_headers:
                            valid_context_headers.append(h)
                            structured.global_header_contexts[h] = context.strip()
                
                structured.global_headers.extend(valid_context_headers)
        
        # Special case: Detect Accept headers with versioned content types (e.g., application/vnd.*)
        # These are often required for API versioning even if not explicitly stated
        accept_pattern = r"Accept\s*:\s*application/vnd\.[\w\-\.]+\+json"
        accept_matches = re.findall(accept_pattern, structured.full_text, re.IGNORECASE)
        if accept_matches:
            # Check if Accept appears consistently in multiple examples (indicates it's required)
            # Count occurrences in code blocks/examples
            accept_count = 0
            for page in structured.pages:
                for code_block in page.structured_elements.get("code_blocks", []):
                    if re.search(accept_pattern, code_block, re.IGNORECASE):
                        accept_count += 1
            # If Accept appears in 2+ examples, consider it a global requirement
            if accept_count >= 2 and "Accept" not in structured.global_headers:
                structured.global_headers.append("Accept")
                # Extract context from one of the examples
                first_match = re.search(accept_pattern, structured.full_text, re.IGNORECASE)
                if first_match:
                    match_pos = first_match.start()
                    context_start = max(0, match_pos - 200)
                    context_end = min(len(structured.full_text), match_pos + 300)
                    context = structured.full_text[context_start:context_end]
                    structured.global_header_contexts["Accept"] = context.strip()
        
        # Final filtering: remove duplicates and validate
        final_headers = []
        for h in structured.global_headers:
            h_lower = h.lower()
            if h_lower in non_header_words or h_lower in ["https", "http"]:
                continue
            if len(h) > 3 and ("-" in h or h[0].isupper()):
                final_headers.append(h)
        
        structured.global_headers = list(set(final_headers))
        
        # Extract authentication info
        # Combine full_text and all code blocks for checking auth mentions (code blocks often contain auth examples)
        combined_text_for_check = structured.full_text or ""
        all_code_text = "\n".join([
            code_block 
            for page in structured.pages 
            for code_block in page.structured_elements.get("code_blocks", [])
        ])
        if all_code_text:
            combined_text_for_check += "\n\n" + all_code_text
        
        auth_pattern = r"(?:auth|authentication|bearer|api[_\s]?key|token|oauth)"
        structured.global_auth_mentioned = bool(
            re.search(auth_pattern, combined_text_for_check, re.IGNORECASE)
        )
        
        if structured.global_auth_mentioned:
            # Extract auth context from combined text, passing spec security schemes if available
            auth_context = self._extract_auth_context(combined_text_for_check, spec_security_schemes=spec_security_schemes)
            
            structured.auth_info = auth_context
        
        # Extract examples from code blocks
        for page in structured.pages:
            for code_block in page.structured_elements.get("code_blocks", []):
                if "{" in code_block and '"' in code_block:
                    structured.examples.append({
                        "type": "json",
                        "content": code_block,
                    })
    
    def _extract_auth_context(self, text: str, spec_security_schemes: Optional[Dict[str, Any]] = None) -> Union[dict, List[dict]]:
        """
        Extract authentication context from documentation.
        
        Strategy:
        1. If spec_security_schemes provided, first look for those auth methods in docs
        2. Collect all auth methods found in documentation
        3. Validate each with strict checklist (only add if passes)
        4. Return list of validated auth methods (or single dict for backward compatibility if only one)
        
        Args:
            text: Documentation text to search
            spec_security_schemes: Optional dict of security schemes from the spec
                                  Format: {scheme_name: {type: "...", scheme: "...", in: "...", name: "..."}}
        
        Returns:
            List of dicts with auth info (each passing strict checklist), or single dict if only one found (backward compat).
        """
        # Initialize auth_info to empty dict
        auth_info = {}
        
        # First, collect all possible auth matches from the documentation
        all_auth_matches = []
        
        # Helper function to extract auth from a match
        def extract_auth_from_match(match, scheme_name, token_value):
            """Extract auth info from a regex match."""
            scheme_lower = scheme_name.lower()
            
            # Determine auth type based on scheme
            if scheme_lower == "bearer":
                return {"type": "bearer", "scheme": "Bearer", "header": "Authorization"}
            elif scheme_lower in ["token", "apikey", "api_key", "api-key"]:
                return {"type": "api_key", "scheme": scheme_name, "header": "Authorization"}
            elif scheme_lower == "basic":
                return {"type": "http", "scheme": "basic", "header": "Authorization"}
            elif scheme_lower in ["oauth", "oauth2"]:
                return {"type": "oauth2", "header": "Authorization"}
            else:
                # Custom prefix - treat as api_key
                return {"type": "api_key", "scheme": scheme_name, "header": "Authorization"}
        
        # Normalize spec security schemes for matching
        spec_auth_to_match = []
        if spec_security_schemes:
            for scheme_name, scheme_def in spec_security_schemes.items():
                auth_type = scheme_def.get("type", "").lower()
                if auth_type == "http":
                    scheme = scheme_def.get("scheme", "").lower()
                    spec_auth_to_match.append({
                        "type": "http",
                        "scheme": scheme,
                        "spec_scheme_name": scheme_name,
                        "spec_def": scheme_def
                    })
                elif auth_type == "oauth2":
                    spec_auth_to_match.append({
                        "type": "oauth2",
                        "spec_scheme_name": scheme_name,
                        "spec_def": scheme_def
                    })
                elif auth_type == "apikey":
                    spec_auth_to_match.append({
                        "type": "apikey",
                        "spec_scheme_name": scheme_name,
                        "spec_def": scheme_def
                    })
        
        # Known authentication prefixes that appear after "Authorization:"
        # These are the standard and common auth scheme prefixes
        known_auth_prefixes = [
            "bearer", "token", "basic", "digest", "apikey", "api_key", "api-key",
            "oauth", "oauth2", "hawk", "aws", "aws4-hmac-sha256", "vapid"
        ]
        
        # Pattern to match "Authorization: [prefix] [token_or_placeholder]"
        # The value after prefix can be:
        # - Actual tokens: long alphanumeric/base64 strings (8+ chars)
        # - Placeholders: variable names like "YOUR_API_KEY", "api_token", "${TOKEN}", etc.
        # - Short tokens: at least 3 chars for placeholders
        # Handles both formats:
        #   - "Authorization: Token VALUE" (direct format)
        #   - "Authorization": "Token VALUE" (JSON/dictionary format with quotes)
        #   - "Authorization": "Token VALUE" (with escaped quotes in code blocks)
        general_auth_pattern = (
            r"['\"]?"  # Optional opening quote before Authorization (for JSON format)
            r"Authorization\s*[:=]\s*"
            r"['\"]?"  # Optional opening quote after colon (for JSON format)
            r"([A-Za-z][A-Za-z0-9_-]{1,})\s+"  # Prefix (any word starting with letter, at least 2 chars)
            r"([A-Za-z0-9_${}-]{3,})"  # Token/placeholder (alphanumeric, underscores, ${VAR}, $VAR, at least 3 chars)
            r"['\"]?"  # Optional closing quote (for JSON format)
        )
        
        # Also try a more flexible pattern that handles JSON strings better
        # This pattern allows the entire value to be in quotes: "Authorization": "Token VALUE"
        json_auth_pattern = (
            r"['\"]?Authorization['\"]?\s*:\s*['\"]"  # "Authorization": "
            r"([A-Za-z][A-Za-z0-9_-]{1,})\s+"  # Prefix
            r"([A-Za-z0-9_${}-]{3,})"  # Token/placeholder
            r"['\"]"  # Closing quote
        )
        
        # Python keywords and common false positives that should NOT be prefixes
        python_keywords = {"from", "import", "as", "def", "class", "return", "if", "else", "elif", 
                          "for", "while", "try", "except", "finally", "with", "pass", "break", 
                          "continue", "raise", "yield", "in", "is", "not", "and", "or"}
        common_false_positives = {"the", "and", "for", "use", "api", "key", "header", "value", 
                                 "authentication", "credential", "credentials", "accept", "content", "type",
                                 "of", "to", "in", "on", "at", "by", "with", "as", "if", "when", "is", "are",
                                 "was", "were", "be", "been", "being", "have", "has", "had", "do", "does", "did",
                                 "will", "would", "should", "could", "may", "might", "must", "can", "cannot"}
        
        # Step 1: If spec_security_schemes provided, first look for those specific auth methods
        print(f"[DEBUG] Step 1: spec_auth_to_match count: {len(spec_auth_to_match) if spec_auth_to_match else 0}", flush=True)
        if spec_auth_to_match:
            for spec_auth in spec_auth_to_match:
                # Search for this specific auth method in the documentation
                if spec_auth["type"] == "http" and spec_auth["scheme"] == "bearer":
                    # Look for Bearer token
                    bearer_pattern = (
                        r"['\"]?Authorization['\"]?\s*:\s*['\"]?"
                        r"Bearer\s+"
                        r"([A-Za-z0-9_${}-]{3,})"
                        r"['\"]?"
                    )
                    match = re.search(bearer_pattern, text, re.IGNORECASE)
                    if match:
                        auth_info = {"type": "bearer", "scheme": "Bearer", "header": "Authorization"}
                        return auth_info
                elif spec_auth["type"] == "http" and spec_auth["scheme"] == "basic":
                    # Look for Basic auth (but only if not in token request context for OAuth)
                    print(f"[DEBUG] Step 1: Checking for Basic auth (spec has http basic)", flush=True)
                    # Pattern 1: Authorization: Basic <token> or Authorization: Basic <placeholder>
                    basic_pattern = (
                        r"['\"]?Authorization['\"]?\s*:\s*['\"]?"
                        r"Basic\s+"
                        r"([A-Za-z0-9_${}/=<>-]{3,})"  # Allow < > - for placeholders like <base64_encoded_credentials>
                        r"['\"]?"
                    )
                    match = re.search(basic_pattern, text, re.IGNORECASE)
                    # Pattern 2: curl -u "username:password" (Basic Auth via curl)
                    if not match:
                        curl_basic_pattern = r"-u\s+['\"]([^'\"]+):([^'\"]+)['\"]"
                        curl_match = re.search(curl_basic_pattern, text, re.IGNORECASE)
                        if curl_match:
                            print(f"[DEBUG] Step 1: Found curl -u pattern for Basic auth: {curl_match.group(1)}", flush=True)
                            match = curl_match  # Use curl match as Basic auth indicator
                    
                    if match:
                        print(f"[DEBUG] Step 1: Basic auth pattern matched", flush=True)
                        # Check if Basic is in a token request context (OAuth token endpoint)
                        match_start = match.start()
                        context_around = text[max(0, match_start - 200):min(len(text), match_start + 500)].lower()
                        is_token_request = any(indicator in context_around for indicator in [
                            "grant_type", "client_credentials", "/token", "connect/token", 
                            "token endpoint", "get token", "obtain token"
                        ])
                        print(f"[DEBUG] Step 1: is_token_request={is_token_request}, oauth_mentioned={bool(re.search(r'oauth\s*2\.?0|oauth2', text, re.IGNORECASE))}", flush=True)
                        # If it's a token request and OAuth2 is mentioned, skip Basic
                        # BUT: Only skip if it's actually in a token request context, not just if OAuth is mentioned anywhere
                        if is_token_request and re.search(r"oauth\s*2\.?0|oauth2", context_around, re.IGNORECASE):
                            print(f"[DEBUG] Step 1: Skipping Basic auth - it's in OAuth token request context", flush=True)
                            continue  # Skip this Basic match, look for OAuth2 instead
                        auth_info = {"type": "http", "scheme": "basic", "header": "Authorization"}
                        print(f"[DEBUG] Step 1: Returning Basic auth: {auth_info}", flush=True)
                        return auth_info
                    else:
                        print(f"[DEBUG] Step 1: Basic auth pattern did NOT match", flush=True)
                elif spec_auth["type"] == "oauth2":
                    # Look for OAuth2 mentions
                    print(f"[DEBUG] Step 1: Checking for OAuth2 (spec has oauth2)", flush=True)
                    if re.search(r"oauth\s*2\.?0|oauth2", text, re.IGNORECASE):
                        print(f"[DEBUG] Step 1: OAuth2 mentioned in docs", flush=True)
                        auth_info = {"type": "oauth2", "header": "Authorization"}
                        # Extract flow if possible
                        if re.search(r"authorization\s+code|authorization_code|grant_type\s*=\s*authorization_code", text, re.IGNORECASE):
                            auth_info["flow"] = "authorizationCode"
                        elif re.search(r"grant_type\s*=\s*client_credential|client\s+credential\s+flow", text, re.IGNORECASE):
                            auth_info["flow"] = "clientCredentials"
                        print(f"[DEBUG] Step 1: Returning OAuth2: {auth_info}", flush=True)
                        return auth_info
                    else:
                        print(f"[DEBUG] Step 1: OAuth2 NOT mentioned in docs", flush=True)
                elif spec_auth["type"] == "apikey":
                    # First, check for token-only format (no prefix) - this MUST come before prefix pattern
                    token_only_pattern_step1 = (
                        r"['\"]?Authorization['\"]?\s*:\s*['\"]?"
                        r"([A-Za-z0-9_]{20,})"  # Long token (at least 20 chars)
                        r"['\"]?"
                        r"(?:\s|$|['\"]|,|\n|}|[,\]])"
                    )
                    token_only_match = re.search(token_only_pattern_step1, text, re.IGNORECASE)
                    if token_only_match:
                        token_value = token_only_match.group(1)
                        print(f"[DEBUG] Step 1: Token-only pattern matched for apikey: token_value='{token_value}', len={len(token_value)}", flush=True)
                        if len(token_value) >= 20:
                            print(f"[DEBUG] Step 1: Returning token-only format (no scheme) for apikey", flush=True)
                            return {"type": "api_key", "header": "Authorization"}
                    
                    # Look for API key patterns with prefix
                    # Pattern 1: "Authorization: Token token=<value>" format (e.g., PagerDuty)
                    api_key_pattern_token_format = (
                        r"['\"]?Authorization['\"]?\s*:\s*['\"]?"
                        r"Token\s+token\s*=\s*"  # "Token token=" (literal)
                        r"([A-Za-z0-9_${}:=-]{3,})"  # Token value (allow = for "token=${VAR}" format)
                        r"['\"]?"
                    )
                    match = re.search(api_key_pattern_token_format, text, re.IGNORECASE)
                    if match:
                        # Found "Token token=<value>" format - return with "Token" as scheme
                        print(f"[DEBUG] Step 1: Found 'Token token=' format for apikey", flush=True)
                        return {"type": "api_key", "scheme": "Token", "header": "Authorization"}
                    
                    # Pattern 2: Standard "Authorization: Prefix <value>" format
                    api_key_pattern = (
                        r"['\"]?Authorization['\"]?\s*:\s*['\"]?"
                        r"([A-Za-z][A-Za-z0-9_-]{1,})\s+"  # Prefix (Token, ApiKey, etc.)
                        r"([A-Za-z0-9_${}-]{3,})"
                        r"['\"]?"
                    )
                    match = re.search(api_key_pattern, text, re.IGNORECASE)
                    if match:
                        scheme_name = match.group(1)
                        scheme_lower = scheme_name.lower()
                        print(f"[DEBUG] Step 1: API key pattern matched: scheme_name='{scheme_name}', len={len(scheme_name)}", flush=True)
                        # CRITICAL: Check if the "prefix" is actually a long token (token-only format)
                        if len(scheme_name) >= 20:
                            print(f"[DEBUG] Step 1: Long token detected as prefix! Returning token-only format (no scheme)", flush=True)
                            return {"type": "api_key", "header": "Authorization"}
                        # Only accept if it's not Bearer or OAuth
                        if scheme_lower not in ["bearer", "oauth", "oauth2", "basic"]:
                            auth_info = {"type": "api_key", "scheme": scheme_name, "header": "Authorization"}
                            print(f"[DEBUG] Step 1: Returning with scheme: {scheme_name}", flush=True)
                            return auth_info
        
        # Step 2: If no spec auth found (or no spec provided), look for any auth method
        # CRITICAL: First, check for token-only format (no prefix): "Authorization: <token>"
        # This MUST run before the general pattern to avoid false matches
        # Pattern: Authorization: <long_token> (no prefix, just the token directly)
        # This pattern matches tokens that are long enough to be actual tokens (not prefixes)
        logger.debug(f"[DEBUG] Step 2: Checking for token-only format. Text length: {len(text)}, first 200 chars: {text[:200]}")
        print(f"[DEBUG] Step 2: Checking for token-only format. Text length: {len(text)}", flush=True)
        token_only_pattern = (
            r"['\"]?Authorization['\"]?\s*:\s*['\"]?"
            r"([A-Za-z0-9_]{20,})"  # Long token (at least 20 chars) - likely an actual token, not a prefix
            r"['\"]?"
            r"(?:\s|$|['\"]|,|\n|}|[,\]])"  # Must be followed by whitespace, end of string, quote, comma, newline, closing brace, or array bracket
        )
        # Try token-only pattern first - use findall to get all matches and pick the longest
        token_only_matches = re.finditer(token_only_pattern, text, re.IGNORECASE)
        for token_only_match in token_only_matches:
            token_value = token_only_match.group(1)
            print(f"[DEBUG] Token-only pattern matched: token_value='{token_value}', len={len(token_value)}", flush=True)
            # Validate it's a real token (not a common word, not too short)
            # Also check it doesn't look like a prefix (starts with known prefix words)
            known_prefixes = ["bearer", "token", "basic", "digest", "apikey", "api_key", "api-key", "oauth", "oauth2"]
            token_starts_with_prefix = any(token_value.lower().startswith(prefix) for prefix in known_prefixes)
            print(f"[DEBUG] Validation: len>=20={len(token_value) >= 20}, not in blacklist={token_value.lower() not in {'authorization', 'bearer', 'token', 'basic'}}, not starts_with_prefix={not token_starts_with_prefix}", flush=True)
            if (len(token_value) >= 20 and 
                token_value.lower() not in {"authorization", "bearer", "token", "basic"} and
                not token_starts_with_prefix):
                # This is a token-only format (no prefix) - set as api_key without scheme
                # Return immediately to prevent general pattern from matching
                print(f"[DEBUG] Returning token-only format (no scheme) for token: {token_value[:50]}...", flush=True)
                return {"type": "api_key", "header": "Authorization"}
        
        # Try to find Authorization header with any prefix
        # First try the JSON pattern (more specific for JSON/dict format like "Authorization": "Token VALUE")
        auth_header_match = re.search(json_auth_pattern, text, re.IGNORECASE)
        # If not found, try the general pattern
        if not auth_header_match:
            auth_header_match = re.search(general_auth_pattern, text, re.IGNORECASE)
        if auth_header_match:
            scheme_name = auth_header_match.group(1)
            token_value = auth_header_match.group(2) if auth_header_match.lastindex >= 2 else ""
            scheme_lower = scheme_name.lower()
            print(f"[DEBUG] General pattern matched: scheme_name='{scheme_name}', len={len(scheme_name)}, token_value='{token_value}'", flush=True)
            
            # CRITICAL: Check if the "prefix" is actually a long token (token-only format, no prefix)
            # This must be checked BEFORE any other processing, including validation
            # If scheme_name is very long (20+ chars), it's likely a token, not a prefix
            if len(scheme_name) >= 20:
                # This is likely a token-only format (Authorization: <token>), not a prefix
                # The general pattern incorrectly matched the token as a prefix
                # Don't treat it as a prefix - return token-only format without scheme
                print(f"[DEBUG] General pattern matched long token as prefix! Returning token-only format (no scheme) for: {scheme_name[:50]}...", flush=True)
                return {"type": "api_key", "header": "Authorization"}
            
            # Validate that this is a real auth header, not Python code or false positive
            match_start = auth_header_match.start()
            match_end = auth_header_match.end()
            
            # Check context before the match - reject if it looks like Python code
            # But allow if it's in a code example (like headers = { "Authorization": ... })
            context_before = text[max(0, match_start - 200):match_start].lower()
            text_before_match = text[max(0, match_start - 100):match_start]
            
            # Check if this is in a code example (dictionary/object literal)
            # Code examples typically have: headers = { "Authorization": ... } or { "Authorization": ... }
            is_in_code_example = (
                "headers" in context_before or
                "{" in text_before_match or
                "=" in text_before_match[-50:] or  # Assignment operator nearby
                "curl" in context_before or
                "request" in context_before
            )
            
            # Only reject if it has Python keywords AND is NOT in a code example
            has_code_keywords = any(keyword in context_before for keyword in ["import", "from", "def ", "class ", "return "])
            
            # If we're in a code example (headers dict, curl, etc.), always allow it
            # Also check if the match itself is inside quotes (JSON/dict format) - that's definitely a code example
            match_text = text[match_start:match_end]
            is_in_quotes = (match_text.startswith('"') or match_text.startswith("'") or 
                           '"' in text[max(0, match_start-5):match_start] or
                           "'" in text[max(0, match_start-5):match_start])
            
            if has_code_keywords and not is_in_code_example and not is_in_quotes:
                # Additional check: if the match is inside a dictionary/object literal, it's a code example
                # Look for opening brace or assignment before the match
                if "{" not in text_before_match and "=" not in text_before_match[-30:]:
                    auth_header_match = None
            
            # Check context after the match - reject if token is followed by Python keywords
            if auth_header_match:
                context_after = text[match_end:min(len(text), match_end + 50)].lower()
                # If token is followed by Python keywords like "import", "from", etc., it's likely code
                if re.search(r"^\s*(import|from|def |class |return |\n\s*(import|from|def|class))", context_after):
                    auth_header_match = None
            
            # Reject if prefix is a Python keyword or common false positive
            if auth_header_match and (scheme_lower in python_keywords or scheme_lower in common_false_positives):
                auth_header_match = None
            
            # Validate token/placeholder looks reasonable (not code, not random text)
            if auth_header_match:
                # Token/placeholder should be mostly alphanumeric/underscores/dollar signs
                # Accept placeholders like "YOUR_API_KEY", "api_token", "${TOKEN}", etc.
                # Reject if it contains spaces (likely not a token/placeholder)
                if " " in token_value:
                    auth_header_match = None
                # Reject common English words that are too short to be tokens
                elif token_value.lower() in {"adp", "api", "the", "and", "for", "use", "key", "url", "end", "get", "set", "put", "post", "has", "had", "was", "are", "is", "to", "of", "in", "on", "at", "by", "as", "if", "or", "be", "do", "it", "an"}:
                    auth_header_match = None
                # Reject if it looks like code (contains operators, parentheses, etc.)
                elif any(char in token_value for char in ["(", ")", "[", "]", "{", "}", "=", "+", "-", "*", "/", "%"]):
                    # But allow ${VAR} style placeholders and $VAR style placeholders
                    # Also allow "token=${VAR}" or "token=$VAR" format (e.g., "Token token=${input:key}")
                    if not (re.match(r'^\$\{[A-Za-z0-9_]+\}$', token_value) or 
                            re.match(r'^\$[A-Za-z0-9_]+$', token_value) or
                            re.match(r'^token\s*=\s*\$?\{?[A-Za-z0-9_:]+\}?$', token_value, re.IGNORECASE)):
                        auth_header_match = None
            
            if auth_header_match:
                # Initialize auth_info if not already set
                if "auth_info" not in locals():
                    auth_info = {}
                
                # Determine auth type based on scheme
                if scheme_lower == "bearer":
                    auth_info["type"] = "bearer"
                    auth_info["scheme"] = scheme_name  # Store "Bearer" as scheme
                elif scheme_lower in ["token", "apikey", "api_key", "api-key"]:
                    auth_info["type"] = "api_key"
                    auth_info["scheme"] = scheme_name  # Store "Token", "ApiKey", etc. as scheme
                elif scheme_lower == "basic":
                    auth_info["type"] = "http"
                    auth_info["scheme"] = "basic"
                elif scheme_lower in ["oauth", "oauth2"]:
                    auth_info["type"] = "oauth2"
                else:
                    # Custom prefix - treat as api_key and store the prefix
                    # BUT: If scheme_name is very long (20+ chars), it's likely a token, not a prefix
                    # This is a final safeguard in case the earlier check didn't catch it
                    print(f"[DEBUG] Custom prefix branch: scheme_name='{scheme_name}', len={len(scheme_name)}", flush=True)
                    if len(scheme_name) >= 20:
                        # This is a token-only format, not a custom prefix
                        print(f"[DEBUG] Final safeguard caught long token! Not setting scheme for: {scheme_name[:50]}...", flush=True)
                        auth_info["type"] = "api_key"
                        # Don't set scheme for token-only format
                    else:
                        auth_info["type"] = "api_key"
                        auth_info["scheme"] = scheme_name  # Store the actual scheme name
                        print(f"[DEBUG] Setting custom prefix scheme: {scheme_name}", flush=True)
                
                auth_info["header"] = "Authorization"
                print(f"[DEBUG] Final auth_info: {auth_info}", flush=True)
        
        # If no token prefix found, check for OAuth2 (more specific)
        if not auth_info.get("type"):
            if re.search(r"oauth\s*2\.?0|oauth2", text, re.IGNORECASE):
                auth_info["type"] = "oauth2"
                
                # Extract OAuth2 flow type
                # Priority: Look for actual flow descriptions, not just mentions in lists
                # Check for authorization code flow (highest priority - most common)
                # Look for: response_type=code, authorization code flow, exchange code for token
                if (re.search(r"response_type\s*=\s*code|exchange\s+code\s+for\s+token|authorization\s+code\s+flow", text, re.IGNORECASE) or
                    re.search(r"authorization\s+code|authorization_code|grant_type\s*=\s*authorization_code", text, re.IGNORECASE)):
                    auth_info["flow"] = "authorizationCode"
                # Check for client credentials flow
                # Only detect if it's described as a flow (not just mentioned in a list)
                # Look for: grant_type=client_credentials, client credentials flow, client credentials grant flow
                elif (re.search(r"grant_type\s*=\s*client_credential|client\s+credential\s+flow|client\s+credential\s+grant\s+flow", text, re.IGNORECASE) or
                      (re.search(r"client\s+credential|client_credential", text, re.IGNORECASE) and 
                       not re.search(r"authorization\s+code|response_type\s*=\s*code|exchange\s+code", text, re.IGNORECASE))):
                    # Only use client credentials if authorization code is NOT mentioned
                    auth_info["flow"] = "clientCredentials"
                elif re.search(r"\bimplicit\s+flow|\bimplicit\s+grant|response_type\s*=\s*token", text, re.IGNORECASE):
                    auth_info["flow"] = "implicit"
                elif re.search(r"password\s+flow|password\s+grant|grant_type\s*=\s*password", text, re.IGNORECASE):
                    auth_info["flow"] = "password"
            elif re.search(r"bearer\s+token", text, re.IGNORECASE):
                auth_info["type"] = "bearer"
                if "scheme" not in auth_info:
                    auth_info["scheme"] = "Bearer"
            elif re.search(r"api[_\s]?key", text, re.IGNORECASE):
                # Check context - only detect as auth if it's about using the key, not just getting/signing up
                # Phrases like "Get Your Free API Key" or "Sign up to get your API key" are about registration, not auth
                text_lower = text.lower()
                api_key_match = re.search(r"api[_\s]?key", text_lower, re.IGNORECASE)
                if api_key_match:
                    match_start = api_key_match.start()
                    # Get context around the match (200 chars before and after)
                    context_start = max(0, match_start - 200)
                    context_end = min(len(text), match_start + len(api_key_match.group()) + 200)
                    context = text_lower[context_start:context_end]
                    
                    # Check if it's in a signup/registration context (false positive)
                    signup_phrases = [
                        "get.*free.*api.*key",
                        "sign.*up.*to.*get.*api.*key",
                        "sign.*up.*for.*api.*key",
                        "register.*for.*api.*key",
                        "your.*api.*key.*will.*be.*active",
                        "api.*key.*will.*be.*enabled",
                        "get.*your.*api.*key",
                        "request.*api.*key",
                        "apply.*for.*api.*key"
                    ]
                    
                    is_signup_context = any(re.search(phrase, context, re.IGNORECASE) for phrase in signup_phrases)
                    
                    # Check if it's in an authentication/usage context (true positive)
                    auth_phrases = [
                        "include.*api.*key",
                        "send.*api.*key",
                        "use.*api.*key",
                        "api.*key.*in.*header",
                        "api.*key.*required",
                        "authentication.*api.*key",
                        "api.*key.*for.*authentication",
                        "api.*key.*to.*authenticate",
                        "x-api-key",
                        "api.*key.*parameter",
                        "api.*key.*query"
                    ]
                    
                    is_auth_context = any(re.search(phrase, context, re.IGNORECASE) for phrase in auth_phrases)
                    
                    # Only set as api_key if it's in auth context, not signup context
                    if is_auth_context and not is_signup_context:
                        auth_info["type"] = "api_key"
            elif re.search(r"oauth", text, re.IGNORECASE):
                # Check OAuth last to avoid false positives when other auth methods are present
                auth_info["type"] = "oauth"
        
        # Look for header name if not already set
        if "header" not in auth_info:
            # Use the same general pattern to find Authorization header with any prefix
            general_auth_pattern = (
                r"Authorization\s*[:=]\s*"
                r"([A-Za-z][A-Za-z0-9_-]{1,})\s+"  # Prefix (any word starting with letter, at least 2 chars)
                r"([A-Za-z0-9+/=._${}-]{8,})"  # Token value
            )
            
            auth_header_match = re.search(general_auth_pattern, text, re.IGNORECASE)
            if auth_header_match:
                scheme_name = auth_header_match.group(1)
                scheme_lower = scheme_name.lower()
                
                # Same validation as above
                match_start = auth_header_match.start()
                match_end = auth_header_match.end()
                context_before = text[max(0, match_start - 100):match_start].lower()
                
                if (not any(keyword in context_before for keyword in ["import", "from", "def ", "class ", "return "]) and
                    scheme_lower not in python_keywords and scheme_lower not in common_false_positives):
                    auth_info["header"] = "Authorization"
                    if "scheme" not in auth_info:
                        auth_info["scheme"] = scheme_name
            else:
                # Fallback to generic header extraction
                header_match = re.search(r"(?:header|Authorization|X-API-Key)\s*[:=]\s*([\w\-]+)", text, re.IGNORECASE)
                if header_match:
                    auth_info["header"] = header_match.group(1)
        
        print(f"[DEBUG] Final return auth_info: {auth_info}", flush=True)
        
        # Validate with strict checklist before returning
        if auth_info and auth_info.get("type"):
            text_lower = text.lower()
            auth_type = auth_info.get("type", "").lower()
            auth_scheme = auth_info.get("scheme", "").lower()
            auth_context = ""
            scheme_def = {
                "in": auth_info.get("in", "header").lower(),
                "name": auth_info.get("header", auth_info.get("name", "")).lower()
            }
            
            # Validate with strict checklist
            passes_checklist = False
            if auth_type == "oauth2" or auth_type == "oauth":
                passes_checklist = self._check_oauth2_mentioned_strict(auth_type, auth_context, text_lower)
            elif auth_type == "bearer":
                passes_checklist = self._check_bearer_mentioned_strict(auth_type, auth_context, text_lower)
            elif auth_type == "http" and auth_scheme == "basic":
                passes_checklist = self._check_basic_mentioned_strict(auth_type, auth_context, text_lower)
            elif auth_type == "api_key" or auth_type == "apikey":
                passes_checklist = self._check_apikey_mentioned_strict(scheme_def, auth_type, auth_context, text_lower)
            else:
                # Unknown type - be conservative, only add if we have strong evidence
                passes_checklist = bool(auth_info.get("header") or auth_info.get("scheme"))
            
            if not passes_checklist:
                print(f"[DEBUG] Auth method failed strict checklist, returning empty: {auth_info}", flush=True)
                return []  # Return empty list instead of invalid auth_info
        
        # For now, return single dict (backward compatible)
        # TODO: Collect multiple auth methods and return list
        if auth_info and auth_info.get("type"):
            return auth_info
        return []  # Return empty list instead of empty dict
    
    def _check_oauth2_mentioned_strict(self, doc_auth_type: str, doc_auth_context: str, full_text_lower: str) -> bool:
        """Strict checklist for OAuth2 authentication."""
        has_oauth_keyword = (
            "oauth2" in doc_auth_type or "oauth 2" in doc_auth_type or 
            "oauth2" in doc_auth_context or "oauth 2" in doc_auth_context or 
            "oauth 2.0" in doc_auth_context or
            re.search(r"oauth\s*2\.?0|oauth2", full_text_lower) is not None
        )
        if not has_oauth_keyword:
            return False
        oauth2_patterns = [
            r"authorization\s+url|authorization_url|authorize\s+endpoint",
            r"token\s+url|token_url|token\s+endpoint|/token",
            r"grant_type|grant\s+type",
            r"authorization\s+code|authorization_code|response_type\s*=\s*code",
            r"client\s+credential|client_credential|client\s+id|client_id",
            r"access\s+token|access_token|refresh\s+token|refresh_token",
            r"oauth.*flow|oauth.*grant",
            r"scope|scopes",
        ]
        return any(re.search(pattern, full_text_lower, re.IGNORECASE) for pattern in oauth2_patterns)
    
    def _check_bearer_mentioned_strict(self, doc_auth_type: str, doc_auth_context: str, full_text_lower: str) -> bool:
        """Strict checklist for Bearer token authentication."""
        has_bearer_keyword = (
            "bearer" in doc_auth_type or 
            "bearer" in doc_auth_context or 
            "bearer" in full_text_lower
        )
        if not has_bearer_keyword:
            return False
        bearer_patterns = [
            r"authorization\s*[:=]\s*bearer",
            r"bearer\s+token|bearer\s+authentication",
            r"--header.*bearer|header.*bearer",
            r"Authorization.*Bearer|Bearer.*Authorization",
        ]
        return any(re.search(pattern, full_text_lower, re.IGNORECASE) for pattern in bearer_patterns)
    
    def _check_basic_mentioned_strict(self, doc_auth_type: str, doc_auth_context: str, full_text_lower: str) -> bool:
        """Strict checklist for Basic authentication."""
        has_basic_keyword = (
            "basic" in doc_auth_type or 
            "basic" in doc_auth_context or 
            "basic" in full_text_lower
        )
        if not has_basic_keyword:
            return False
        basic_patterns = [
            r"authorization\s*[:=]\s*basic",
            r"basic\s+auth|basic\s+authentication",
            r"username.*password|user.*pass",
            r"base64.*encode|base64.*encoded",
            r"--user|--basic",
        ]
        return any(re.search(pattern, full_text_lower, re.IGNORECASE) for pattern in basic_patterns)
    
    def _check_apikey_mentioned_strict(self, scheme_def: Dict[str, Any], doc_auth_type: str, 
                                       doc_auth_context: str, full_text_lower: str) -> bool:
        """Strict checklist for API Key authentication."""
        has_apikey_keyword = (
            "apikey" in doc_auth_type or "api key" in doc_auth_type or "api_key" in doc_auth_type or
            "apikey" in doc_auth_context or "api key" in doc_auth_context
        )
        scheme_in = scheme_def.get("in", "").lower() if scheme_def else ""
        scheme_name = scheme_def.get("name", "").lower() if scheme_def else ""
        if not has_apikey_keyword:
            if scheme_in == "query" and scheme_name:
                has_apikey_keyword = scheme_name in full_text_lower
            if not has_apikey_keyword:
                has_apikey_keyword = bool(re.search(
                    r"api\s+token|token\s+as\s+(query\s+)?param|query\s+param.*token|token.*query\s+param",
                    full_text_lower
                ))
        if not has_apikey_keyword:
            return False
        apikey_patterns = []
        if scheme_in == "header":
            apikey_patterns.extend([
                rf"{re.escape(scheme_name)}\s*[:=]" if scheme_name else r"x-api-key|x-api-token|api-key|api_key",
                r"x-api-key|x-api-token|api-key|api_key",
                r"header.*api.*key|api.*key.*header",
            ])
        if scheme_in == "query":
            apikey_patterns.extend([
                rf"\?.*{re.escape(scheme_name)}\s*=|{re.escape(scheme_name)}\s*=" if scheme_name else r"\?.*api.*key|api.*key.*\?",
                r"query.*parameter.*api|api.*key.*query",
                r"\?.*api.*key|api.*key.*\?",
            ])
        apikey_patterns.extend([
            r"api[_-]?key\s*[:=]",
            r"get\s+your\s+api\s+key|generate\s+api\s+key|create\s+api\s+key",
            r"api\s+key.*required|required.*api\s+key",
        ])
        return any(re.search(pattern, full_text_lower, re.IGNORECASE) for pattern in apikey_patterns)
    
    def _clean_text(self, text: str) -> str:
        """Clean and normalize text."""
        # Remove excessive whitespace
        text = re.sub(r"\s+", " ", text)
        
        # Normalize line breaks
        text = re.sub(r"\n\s*\n", "\n", text)
        
        # Remove leading/trailing whitespace from each line
        lines = [line.strip() for line in text.split("\n")]
        lines = [line for line in lines if line]  # Remove empty lines
        
        return "\n".join(lines)

