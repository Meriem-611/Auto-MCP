"""
Data structures for structured documentation.

Represents extracted documentation with preserved structure and context.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union


@dataclass
class DocumentationPage:
    """Represents a single documentation page."""
    
    url: str
    title: str
    content: str
    structured_elements: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    links: List[str] = field(default_factory=list)  # Links found on this page


@dataclass
class StructuredDocumentation:
    """
    Structured representation of extracted documentation.
    
    Preserves context, relationships, and structured elements.
    """
    
    pages: List[DocumentationPage] = field(default_factory=list)
    full_text: str = ""
    
    # Extracted structured elements
    endpoints: List[Dict[str, Any]] = field(default_factory=list)
    parameters: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)  # endpoint -> params
    headers: List[str] = field(default_factory=list)
    auth_info: Optional[Union[Dict[str, Any], List[Dict[str, Any]]]] = None  # Can be dict (single) or list (multiple)
    examples: List[Dict[str, Any]] = field(default_factory=list)
    
    # Global information
    global_headers: List[str] = field(default_factory=list)
    global_header_contexts: Dict[str, str] = field(default_factory=dict)  # header -> documentation snippet
    global_auth_mentioned: bool = False
    server_info: List[str] = field(default_factory=list)
    
    def get_text_for_endpoint(self, path: str, method: str = None, max_length: int = 1000) -> str:
        """
        Extract relevant documentation text for a specific endpoint.
        
        Args:
            path: API path (e.g., "/users")
            method: HTTP method (optional)
            max_length: Maximum length of returned text (default: 1000 chars)
        
        Returns:
            Relevant documentation snippet (focused and limited)
        """
        search_terms = [path]
        if method:
            search_terms.append(method.upper())
        
        relevant_snippets = []
        total_length = 0
        
        for page in self.pages:
            page_lower = page.content.lower()
            page_content = page.content
            
            # Check if any search term appears in this page
            for term in search_terms:
                term_lower = term.lower()
                if term_lower in page_lower:
                    # Find the position of the mention
                    idx = page_lower.find(term_lower)
                    
                    # Extract context around the mention (500 chars before and after)
                    start = max(0, idx - 500)
                    end = min(len(page_content), idx + len(term) + 500)
                    snippet = page_content[start:end]
                    
                    # Clean up snippet (remove partial words at boundaries)
                    if start > 0:
                        # Find first sentence start - look for sentence boundary before the term
                        # Search backwards from the term position
                        term_pos_in_snippet = idx - start
                        # Look for sentence boundary in first 200 chars
                        search_start = max(0, term_pos_in_snippet - 200)
                        search_area = snippet[search_start:term_pos_in_snippet]
                        last_period = search_area.rfind('. ')
                        last_exclamation = search_area.rfind('! ')
                        last_question = search_area.rfind('? ')
                        last_boundary = max(last_period, last_exclamation, last_question)
                        
                        if last_boundary > 0:
                            # Found sentence boundary, start from after it
                            snippet = snippet[search_start + last_boundary + 2:]
                        else:
                            # No sentence boundary found, try to find word boundary
                            # Remove leading punctuation and lowercase (mid-sentence)
                            snippet = re.sub(r'^[,;:\s\-]+', '', snippet)
                            # If starts with lowercase, it's likely mid-sentence - skip it
                            if snippet and snippet[0].islower():
                                # Try to find next sentence start
                                next_sentence = re.search(r'[.!?]\s+([A-Z])', snippet)
                                if next_sentence:
                                    snippet = snippet[next_sentence.start() + 2:]
                    
                    if end < len(page_content):
                        # Find last sentence end
                        last_period = snippet.rfind('. ')
                        last_exclamation = snippet.rfind('! ')
                        last_question = snippet.rfind('? ')
                        last_boundary = max(last_period, last_exclamation, last_question)
                        if last_boundary > len(snippet) - 150:  # If in last 150 chars
                            snippet = snippet[:last_boundary + 1]
                    
                    if snippet and snippet not in relevant_snippets:
                        relevant_snippets.append(snippet.strip())
                        total_length += len(snippet)
                        
                        # Stop if we have enough content
                        if total_length >= max_length:
                            break
                
                if total_length >= max_length:
                    break
            
            if total_length >= max_length:
                break
        
        # Combine snippets, limiting total length
        if relevant_snippets:
            combined = "\n\n".join(relevant_snippets)
            if len(combined) > max_length:
                # Truncate to max_length, but try to end at a sentence
                combined = combined[:max_length]
                last_period = combined.rfind('. ')
                if last_period > max_length * 0.8:  # If period is in last 20%, use it
                    combined = combined[:last_period + 1]
            return combined
        
        # Fallback: return limited global context
        if self.full_text:
            return self.full_text[:max_length]
        return ""
    
    def get_primary_auth_type(self) -> Optional[str]:
        """Get primary auth type (for backward compatibility)."""
        if not self.auth_info:
            return None
        if isinstance(self.auth_info, list):
            return self.auth_info[0].get("type") if self.auth_info else None
        return self.auth_info.get("type")
    
    def get_primary_auth_info(self) -> Optional[Dict[str, Any]]:
        """Get primary auth info dict (for backward compatibility)."""
        if not self.auth_info:
            return None
        if isinstance(self.auth_info, list):
            return self.auth_info[0] if self.auth_info else None
        return self.auth_info
    
    def get_all_auth_types(self) -> List[Dict[str, Any]]:
        """Get all auth types as a list."""
        if not self.auth_info:
            return []
        if isinstance(self.auth_info, list):
            return self.auth_info
        return [self.auth_info]  # Wrap single dict in list
    
    def get_global_context(self) -> str:
        """Get global documentation context (auth, headers, etc.) with enhanced text snippets."""
        context_parts = []
        
        if self.global_auth_mentioned:
            context_parts.append("Authentication is mentioned in documentation")
            # Include auth type(s) if available
            primary_auth_type = self.get_primary_auth_type()
            if primary_auth_type:
                all_auth_types = self.get_all_auth_types()
                if len(all_auth_types) > 1:
                    auth_types_str = ", ".join([auth.get("type", "unknown") for auth in all_auth_types])
                    context_parts.append(f"Auth types: {auth_types_str}")
                else:
                    context_parts.append(f"Auth type: {primary_auth_type}")
            
            # Extract text snippets with context around auth mentions
            if self.full_text:
                auth_keywords = ["apikey", "api_key", "api-key", "api key", "authentication", "auth", "token", "bearer", "oauth"]
                text_snippets = []
                
                for keyword in auth_keywords:
                    # Find all occurrences of the keyword
                    pattern = re.compile(re.escape(keyword), re.IGNORECASE)
                    matches = list(pattern.finditer(self.full_text))
                    
                    for match in matches[:2]:  # Limit to first 2 matches per keyword
                        # Extract context: 200 chars before, 300 chars after
                        start = max(0, match.start() - 200)
                        end = min(len(self.full_text), match.end() + 300)
                        snippet = self.full_text[start:end]
                        
                        # Try to start/end at sentence boundaries
                        if start > 0:
                            # Find last sentence boundary before match
                            before_text = self.full_text[:match.start()]
                            last_period = before_text.rfind('. ')
                            last_exclamation = before_text.rfind('! ')
                            last_question = before_text.rfind('? ')
                            last_newline = before_text.rfind('\n')
                            boundary = max(last_period, last_exclamation, last_question, last_newline)
                            if boundary > start - 100:  # If boundary is close, use it
                                snippet = self.full_text[boundary + 2:end] if boundary >= 0 else snippet
                                snippet = "..." + snippet
                        
                        if end < len(self.full_text):
                            # Find next sentence boundary after match
                            after_text = self.full_text[match.end():]
                            next_period = after_text.find('. ')
                            next_exclamation = after_text.find('! ')
                            next_question = after_text.find('? ')
                            next_newline = after_text.find('\n')
                            boundary = min(
                                x for x in [next_period, next_exclamation, next_question, next_newline] 
                                if x >= 0
                            ) if any(x >= 0 for x in [next_period, next_exclamation, next_question, next_newline]) else -1
                            if boundary >= 0 and boundary < 200:
                                snippet = snippet[:snippet.find(after_text[:boundary + 2]) + len(after_text[:boundary + 2])]
                                snippet = snippet + "..."
                        
                        # Clean up snippet (remove excessive whitespace)
                        snippet = re.sub(r'\s+', ' ', snippet).strip()
                        if snippet and snippet not in text_snippets:
                            text_snippets.append(snippet)
                    
                    if len(text_snippets) >= 2:  # Limit total snippets
                        break
                
                if text_snippets:
                    context_parts.append(f"\nRelevant documentation snippets:\n" + "\n\n---\n\n".join(text_snippets[:2]))
                
                # Search for OAuth/OAuth2 mentions in full_text
                oauth_matches = []
                # Look for OAuth 2.0 mentions (case-insensitive)
                oauth_patterns = [
                    r"oauth\s*2\.?0[^\s]*",
                    r"oauth2[^\s]*",
                    r"oauth[^\s]*"
                ]
                for pattern in oauth_patterns:
                    matches = re.findall(pattern, self.full_text, re.IGNORECASE)
                    if matches:
                        oauth_matches.extend(matches[:3])  # Limit to first 3 matches
                        break  # Use most specific pattern first
                
                if oauth_matches:
                    context_parts.append(f"OAuth mentions: {', '.join(set(oauth_matches[:3]))}")
        
        if self.global_headers:
            context_parts.append(f"Global headers mentioned: {', '.join(self.global_headers)}")
        
        return "\n".join(context_parts)

