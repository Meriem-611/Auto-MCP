"""
Heuristic-based issue detector.

Uses rule-based heuristics to detect potential issues by comparing
OpenAPI specifications with structured documentation.
"""

import re
from typing import Any, Dict, List, Optional

from specfix.detection.issues import Issue, IssueReport, IssueType
from specfix.extraction.structured_docs import StructuredDocumentation
from specfix.utils.logger import get_logger
from specfix.utils.spec_utils import extract_minimal_fragment

logger = get_logger(__name__)


class HeuristicDetector:
    """
    Detects inconsistencies between OpenAPI specs and documentation.
    
    Uses heuristics to identify potential issues at both global and
    operation levels. Extracts relevant documentation context for each issue.
    """

    def __init__(
        self, spec: Dict[str, Any], documentation: StructuredDocumentation
    ):
        """
        Initialize the detector.
        
        Args:
            spec: OpenAPI specification dictionary
            documentation: Structured documentation object
        """
        self.spec = spec
        self.documentation = documentation

    def _limit_doc_context(self, doc_context: str, max_length: int = 800) -> str:
        """
        Limit documentation context to a reasonable length.
        
        Args:
            doc_context: Full documentation context
            max_length: Maximum length (default: 800 chars)
        
        Returns:
            Limited documentation context
        """
        if not doc_context or len(doc_context) <= max_length:
            return doc_context
        
        # Try to truncate at a sentence boundary
        truncated = doc_context[:max_length]
        last_period = truncated.rfind('. ')
        if last_period > max_length * 0.7:  # If period is in last 30%, use it
            return truncated[:last_period + 1]
        return truncated

    def _extract_description_text(self, doc_context: str, path: str, method: str = None) -> str:
        """
        Extract description text for an endpoint from documentation.
        
        Tries to find the actual description text that describes what the endpoint does.
        
        Args:
            doc_context: Documentation context for the endpoint
            path: API path (e.g., "/users")
            method: HTTP method (optional)
        
        Returns:
            Extracted description text, or empty string if not found
        """
        if not doc_context:
            return ""
        
        # Clean up: remove leading/trailing whitespace and fix mid-sentence starts
        doc_context = doc_context.strip()
        
        # Fix common mid-sentence starts
        # Remove leading punctuation and lowercase letters that indicate mid-word
        doc_context = re.sub(r'^[,;:\s]+', '', doc_context)  # Remove leading punctuation/whitespace
        # If starts with lowercase (likely mid-sentence), try to find sentence start
        if doc_context and doc_context[0].islower():
            # Look for previous sentence boundary
            # This is a fallback - ideally get_text_for_endpoint should handle this
            pass
        
        # Look for patterns like:
        # "GET /users\nRetrieves a list of users."
        # "Retrieves a list of users"
        # "This endpoint retrieves..."
        # "Retrieves a user by their unique identifier"
        
        # Pattern 1: Look for action verbs at start (Retrieves, Creates, Updates, etc.)
        action_pattern = r'^(Retrieves?|Creates?|Updates?|Deletes?|Gets?|Lists?|Searches?|Queries?|Finds?|Returns?|Sends?|Posts?|Patches?)\s+[^.!?]{10,200}[.!?]'
        match = re.search(action_pattern, doc_context, re.IGNORECASE)
        if match:
            desc = match.group(0).strip()
            # Ensure it ends with punctuation
            if not desc[-1] in '.!?':
                desc += '.'
            return desc
        
        # Pattern 2: Look for "This endpoint..." or "This operation..."
        this_pattern = r'(This\s+(?:endpoint|operation|API|method)\s+[^.!?]{10,200}[.!?])'
        match = re.search(this_pattern, doc_context, re.IGNORECASE)
        if match:
            desc = match.group(1).strip()
            if not desc[-1] in '.!?':
                desc += '.'
            return desc
        
        # Pattern 3: Find endpoint mention and extract following sentence
        search_terms = [path]
        if method:
            search_terms.append(method.upper())
        
        doc_lower = doc_context.lower()
        doc_original = doc_context
        
        for term in search_terms:
            term_lower = term.lower()
            if term_lower in doc_lower:
                idx = doc_lower.find(term_lower)
                
                # Look for description after the endpoint mention
                after_mention = doc_original[idx + len(term):idx + len(term) + 500]
                
                # Remove HTTP method prefixes
                after_mention = re.sub(r'^(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)\s+', '', after_mention, flags=re.IGNORECASE)
                after_mention = after_mention.strip()
                
                # Remove leading punctuation/whitespace
                after_mention = re.sub(r'^[,;:\s\-]+', '', after_mention)
                
                # Extract first complete sentence
                sentences = re.split(r'([.!?]\s+)', after_mention)
                if len(sentences) >= 2:
                    first_sentence = sentences[0] + sentences[1]  # Sentence + punctuation
                    first_sentence = first_sentence.strip()
                    # Must be meaningful (not too short, not just punctuation)
                    if len(first_sentence) > 20 and not first_sentence.startswith(','):
                        return first_sentence
        
        # Pattern 4: Extract first meaningful sentence from paragraphs
        paragraphs = doc_context.split('\n\n')
        for para in paragraphs[:3]:
            para = para.strip()
            # Skip if it contains endpoint path (likely a header/title)
            if any(term.lower() in para.lower() for term in search_terms):
                continue
            # Skip if too short, all caps, or starts with #
            if len(para) < 30 or para.isupper() or para.startswith('#'):
                continue
            
            # Extract first sentence
            sentences = re.split(r'([.!?]\s+)', para)
            if len(sentences) >= 2:
                first_sent = sentences[0] + sentences[1]
                first_sent = first_sent.strip()
                # Must start with capital letter and be meaningful
                if first_sent and first_sent[0].isupper() and len(first_sent) > 20:
                    return first_sent
        
        # Last resort: return empty (better than broken text)
        return ""

    def _extract_param_snippet(self, doc_context: str, param_name: str, max_length: int = 300) -> str:
        """
        Extract a focused snippet mentioning a specific parameter.
        
        Args:
            doc_context: Full documentation context
            param_name: Name of the parameter to find
            max_length: Maximum length of snippet
        
        Returns:
            Focused snippet mentioning the parameter
        """
        doc_lower = doc_context.lower()
        param_lower = param_name.lower()
        
        if param_lower not in doc_lower:
            return doc_context[:max_length] if doc_context else ""
        
        # Find the position of the parameter mention
        idx = doc_lower.find(param_lower)
        
        # Extract context around the mention (150 chars before and after)
        start = max(0, idx - 150)
        end = min(len(doc_context), idx + len(param_name) + 150)
        snippet = doc_context[start:end]
        
        # Limit to max_length
        if len(snippet) > max_length:
            snippet = snippet[:max_length]
            last_period = snippet.rfind('. ')
            if last_period > max_length * 0.7:
                snippet = snippet[:last_period + 1]
        
        return snippet.strip()

    def detect_all(self) -> IssueReport:
        """
        Run all detection methods and return a comprehensive report.
        
        Returns:
            IssueReport with all detected issues (with doc context)
        """
        report = IssueReport()

        # Run all detection methods
        all_issues = []
        try:
            all_issues.extend(self.detect_global_issues())
        except Exception as e:
            logger.error(f"Error in detect_global_issues: {e}", exc_info=True)
        
        try:
            all_issues.extend(self.detect_operation_issues())
        except Exception as e:
            logger.error(f"Error in detect_operation_issues: {e}", exc_info=True)

        # Add issues to report
        for issue in all_issues:
            report.add_issue(issue)

        logger.info(
            f"Detected {report.total_count} potential issues: {report.by_severity}"
        )
        return report

    def detect_global_issues(self) -> List[Issue]:
        """Detect global-level issues that affect all or multiple operations."""
        issues = []
        
        try:
            # Missing authentication documentation
            issues.extend(self.detect_missing_auth_documentation())
        except Exception as e:
            logger.error(f"Error in detect_missing_auth_documentation: {e}", exc_info=True)
        
        try:
            # Malformed base URLs
            issues.extend(self.detect_malformed_base_urls())
        except Exception as e:
            logger.error(f"Error in detect_malformed_base_urls: {e}", exc_info=True)
        
        try:
            # Global header requirements (affect all operations)
            issues.extend(self.detect_global_header_requirements())
        except Exception as e:
            logger.error(f"Error in detect_global_header_requirements: {e}", exc_info=True)
        
        return issues

    def detect_operation_issues(self) -> List[Issue]:
        """
        Detect operation-level issues (conservative - only critical issues).
        
        Detects:
        1. Missing descriptions (from spec only, no doc parsing)
        2. Endpoint security overwriting (security: [] when global security is required)
        3. Parameter type mismatches (when there's strong evidence)
        """
        issues = []
        paths = self.spec.get("paths", {})

        for path, path_item in paths.items():
            for method, operation in path_item.items():
                if method not in [
                    "get",
                    "post",
                    "put",
                    "delete",
                    "patch",
                    "options",
                    "head",
                ]:
                    continue

                location = f"paths.{path}.{method}"
                
                # Get documentation context for this endpoint
                doc_context = self.documentation.get_text_for_endpoint(path, method)
                
                # Only detect missing descriptions from spec (no doc parsing)
                issues.extend(self.detect_missing_descriptions(operation, location))
                
                # Detect endpoint security overwriting (security: [] when global security required)
                issues.extend(self.detect_missing_endpoint_security(operation, location))
                
                # Detect parameter type mismatches (conservative - only strong evidence)
                issues.extend(self.detect_parameter_type_mismatches(operation, location, path, doc_context))

        return issues

    def detect_missing_auth_documentation(self) -> List[Issue]:
        """
        Detect missing authentication documentation.
        Handles both OpenAPI 2 (Swagger) and OpenAPI 3.
        """
        issues = []

        if not self.documentation:
            logger.debug("No documentation available for auth detection")
            return issues
        
        logger.debug(f"Documentation available: global_auth_mentioned={self.documentation.global_auth_mentioned}, has_auth_info={bool(self.documentation.auth_info)}")
        
        if not self.documentation.global_auth_mentioned:
            logger.debug(f"Auth not mentioned in documentation (global_auth_mentioned={self.documentation.global_auth_mentioned})")
            return issues

        logger.debug(f"Auth mentioned in documentation, checking spec for security schemes...")
        if self.documentation and self.documentation.global_auth_mentioned:
            # Check OpenAPI 3 location
            security_schemes = self.spec.get("components", {}).get(
                "securitySchemes", {}
            )
            # Check OpenAPI 2 location
            security_definitions = self.spec.get("securityDefinitions", {})
            
            # Check if spec is OpenAPI 2 or 3
            is_openapi3 = "openapi" in self.spec
            is_swagger2 = "swagger" in self.spec and self.spec.get("swagger", "").startswith("2")
            
            has_security = bool(security_schemes) or bool(security_definitions)
            
            if not has_security:
                # Extract auth context from docs
                auth_context = self.documentation.get_global_context()
                primary_auth = self.documentation.get_primary_auth_info()
                if primary_auth:
                    all_auth_types = self.documentation.get_all_auth_types()
                    if len(all_auth_types) > 1:
                        auth_types_str = ", ".join([auth.get("type", "unknown") for auth in all_auth_types])
                        auth_context += f"\nAuth types: {auth_types_str}"
                    else:
                        auth_context += f"\nAuth type: {primary_auth.get('type', 'unknown')}"
                
                # Double-check: if api_key is detected, verify if it's actually Bearer authentication
                bearer_in_code = self._check_bearer_in_code_examples()
                if bearer_in_code and primary_auth:
                    # Override auth type to bearer if found in code examples
                    auth_context += f"\nNote: Bearer authentication found in code examples (Authorization: Bearer)"
                
                # Extract authentication table context (includes full table rows with "Required" column)
                auth_table_context = self._extract_auth_table_context()
                if auth_table_context:
                    auth_context += f"\n\nAuthentication parameter from documentation table:\n{auth_table_context}"
                
                # Extract authentication code fragments (with enhanced context)
                auth_code_fragments = self._extract_auth_code_fragments()
                if auth_code_fragments:
                    auth_context += f"\n\nAuthentication examples from documentation:\n{auth_code_fragments}"
                
                # Determine correct location based on spec version
                if is_swagger2:
                    location = "securityDefinitions"
                else:
                    location = "components.securitySchemes"
                
                issues.append(
                    Issue(
                        type=IssueType.MISSING_AUTH_DOC,
                        location=location,
                        description="Documentation mentions authentication but spec has no security schemes defined",
                        severity="high",
                        doc_fragment=auth_context,
                    )
                )
            else:
                # Check if security scheme type matches documentation
                issues.extend(self.detect_incorrect_security_scheme_type(security_schemes or security_definitions))
                # Check for custom auth prefix mismatches (separate from type mismatches)
                issues.extend(self.detect_custom_auth_prefix(security_schemes or security_definitions))

        return issues
    
    def _is_spec_auth_type_mentioned_in_docs(self, scheme_type: str, scheme_def: Dict[str, Any], 
                                             doc_auth_type: str, doc_auth_context: str, full_text_lower: str) -> bool:
        """
        Check if the spec's auth type is also mentioned in the documentation using strict checklists.
        This prevents false positives by requiring multiple pieces of evidence.
        
        Args:
            scheme_type: The auth type in the spec (e.g., "oauth2", "http", "apikey")
            scheme_def: The security scheme definition from the spec
            doc_auth_type: The auth type extracted from documentation
            doc_auth_context: The auth context from documentation (lowercase)
            full_text_lower: The full documentation text (lowercase)
        
        Returns:
            True if the spec's auth type is STRICTLY mentioned in docs (passes checklist), False otherwise
        """
        if scheme_type == "oauth2":
            return self._check_oauth2_mentioned_strict(doc_auth_type, doc_auth_context, full_text_lower)
        elif scheme_type == "http":
            scheme = scheme_def.get("scheme", "").lower()
            if scheme == "bearer":
                return self._check_bearer_mentioned_strict(doc_auth_type, doc_auth_context, full_text_lower)
            elif scheme == "basic":
                return self._check_basic_mentioned_strict(doc_auth_type, doc_auth_context, full_text_lower)
        elif scheme_type == "apikey":
            return self._check_apikey_mentioned_strict(scheme_def, doc_auth_type, doc_auth_context, full_text_lower)
        
        return False
    
    def _check_oauth2_mentioned_strict(self, doc_auth_type: str, doc_auth_context: str, full_text_lower: str) -> bool:
        """
        Strict checklist for OAuth2 authentication.
        Requires: OAuth2 keyword + at least one OAuth2-specific pattern.
        """
        # Checklist item 1: Must have OAuth2 keyword
        has_oauth_keyword = (
            "oauth2" in doc_auth_type or "oauth 2" in doc_auth_type or 
            "oauth2" in doc_auth_context or "oauth 2" in doc_auth_context or 
            "oauth 2.0" in doc_auth_context or
            re.search(r"oauth\s*2\.?0|oauth2", full_text_lower) is not None
        )
        
        if not has_oauth_keyword:
            return False
        
        # Checklist item 2: Must have at least ONE of these OAuth2-specific patterns
        oauth2_patterns = [
            r"authorization\s+url|authorization_url|authorize\s+endpoint",  # Authorization URL
            r"token\s+url|token_url|token\s+endpoint|/token",  # Token URL
            r"grant_type|grant\s+type",  # Grant type mentioned
            r"authorization\s+code|authorization_code|response_type\s*=\s*code",  # Auth code flow
            r"client\s+credential|client_credential|client\s+id|client_id",  # Client credentials
            r"access\s+token|access_token|refresh\s+token|refresh_token",  # Token types
            r"oauth.*flow|oauth.*grant",  # OAuth flow/grant mentioned
            r"scope|scopes",  # Scopes mentioned (OAuth-specific)
        ]
        
        has_oauth2_pattern = any(re.search(pattern, full_text_lower, re.IGNORECASE) for pattern in oauth2_patterns)
        
        return has_oauth2_pattern
    
    def _check_bearer_mentioned_strict(self, doc_auth_type: str, doc_auth_context: str, full_text_lower: str) -> bool:
        """
        Strict checklist for Bearer token authentication.
        Requires: Bearer keyword + at least one Bearer-specific pattern.
        """
        # Checklist item 1: Must have Bearer keyword
        has_bearer_keyword = (
            "bearer" in doc_auth_type or 
            "bearer" in doc_auth_context or 
            "bearer" in full_text_lower
        )
        
        if not has_bearer_keyword:
            return False
        
        # Checklist item 2: Must have at least ONE of these Bearer-specific patterns
        bearer_patterns = [
            r"authorization\s*[:=]\s*bearer",  # "Authorization: Bearer" or "Authorization: Bearer"
            r"bearer\s+token|bearer\s+authentication",  # "Bearer token" or "Bearer authentication"
            r"--header.*bearer|header.*bearer",  # curl header with Bearer
            r"Authorization.*Bearer|Bearer.*Authorization",  # Authorization header with Bearer
        ]
        
        has_bearer_pattern = any(re.search(pattern, full_text_lower, re.IGNORECASE) for pattern in bearer_patterns)
        
        return has_bearer_pattern
    
    def _check_basic_mentioned_strict(self, doc_auth_type: str, doc_auth_context: str, full_text_lower: str) -> bool:
        """
        Strict checklist for Basic authentication.
        Requires: Basic keyword + at least one Basic-specific pattern.
        """
        # Checklist item 1: Must have Basic keyword
        has_basic_keyword = (
            "basic" in doc_auth_type or 
            "basic" in doc_auth_context or 
            "basic" in full_text_lower
        )
        
        if not has_basic_keyword:
            return False
        
        # Checklist item 2: Must have at least ONE of these Basic-specific patterns
        basic_patterns = [
            r"authorization\s*[:=]\s*basic",  # "Authorization: Basic"
            r"basic\s+auth|basic\s+authentication",  # "Basic auth" or "Basic authentication"
            r"username.*password|user.*pass",  # Username/password mentioned together
            r"base64.*encode|base64.*encoded",  # Base64 encoding (Basic auth uses base64)
            r"--user|--basic",  # curl flags for Basic auth
        ]
        
        has_basic_pattern = any(re.search(pattern, full_text_lower, re.IGNORECASE) for pattern in basic_patterns)
        
        return has_basic_pattern
    
    def _check_apikey_mentioned_strict(self, scheme_def: Dict[str, Any], doc_auth_type: str, 
                                       doc_auth_context: str, full_text_lower: str) -> bool:
        """
        Strict checklist for API Key authentication.
        Requires: API Key keyword + at least one API Key-specific pattern.
        """
        # Checklist item 1: Must have API Key keyword
        has_apikey_keyword = (
            "apikey" in doc_auth_type or "api key" in doc_auth_type or "api_key" in doc_auth_type or
            "apikey" in doc_auth_context or "api key" in doc_auth_context
        )
        
        # Also check for query parameter API keys if spec defines it as query param
        scheme_in = scheme_def.get("in", "").lower()
        scheme_name = scheme_def.get("name", "").lower()
        
        if not has_apikey_keyword:
            # If spec has query param API key, check if the param name is mentioned
            if scheme_in == "query" and scheme_name:
                has_apikey_keyword = scheme_name in full_text_lower
            # Check for generic token as query param patterns
            if not has_apikey_keyword:
                has_apikey_keyword = bool(re.search(
                    r"api\s+token|token\s+as\s+(query\s+)?param|query\s+param.*token|token.*query\s+param",
                    full_text_lower
                ))
        
        if not has_apikey_keyword:
            return False
        
        # Checklist item 2: Must have at least ONE of these API Key-specific patterns
        apikey_patterns = []
        
        # Header-based API key patterns
        if scheme_in == "header":
            apikey_patterns.extend([
                rf"{re.escape(scheme_name)}\s*[:=]",  # Header name with colon/equals
                r"x-api-key|x-api-token|api-key|api_key",  # Common API key header names
                r"header.*api.*key|api.*key.*header",  # Header with API key
            ])
        
        # Query parameter API key patterns
        if scheme_in == "query":
            apikey_patterns.extend([
                rf"\?.*{re.escape(scheme_name)}\s*=|{re.escape(scheme_name)}\s*=",  # Query param with equals
                r"query.*parameter.*api|api.*key.*query",  # Query parameter with API key
                r"\?.*api.*key|api.*key.*\?",  # Query string with API key
            ])
        
        # Generic API key patterns (if scheme_in not specified or both)
        apikey_patterns.extend([
            r"api[_-]?key\s*[:=]",  # "api-key:" or "api_key="
            r"get\s+your\s+api\s+key|generate\s+api\s+key|create\s+api\s+key",  # API key generation/retrieval
            r"api\s+key.*required|required.*api\s+key",  # API key required
        ])
        
        has_apikey_pattern = any(re.search(pattern, full_text_lower, re.IGNORECASE) for pattern in apikey_patterns)
        
        return has_apikey_pattern
    
    def detect_custom_auth_prefix(self, security_schemes: Dict[str, Any]) -> List[Issue]:
        """
        Detect custom authentication prefix mismatches (e.g., "Bearer ", "Token ", custom prefix).
        
        This is separate from auth type mismatches - it checks if the documentation requires
        a specific prefix in the Authorization header that doesn't match the spec.
        """
        issues = []
        
        # First, check if spec has HTTP Bearer or OAuth2 anywhere (these automatically include Bearer prefix)
        spec_has_bearer_auth = False
        for scheme_def in security_schemes.values():
            scheme_type = scheme_def.get("type", "").lower()
            spec_scheme = scheme_def.get("scheme", "").strip().lower()
            if (scheme_type == "http" and spec_scheme == "bearer") or scheme_type == "oauth2":
                spec_has_bearer_auth = True
                break
        
        # Check each security scheme in spec
        # Check both apiKey and HTTP Bearer type schemes for prefix mismatches
        for scheme_name, scheme_def in security_schemes.items():
            scheme_type = scheme_def.get("type", "").lower()
            spec_scheme = scheme_def.get("scheme", "").strip().lower()
            
            # Only check apiKey and HTTP Bearer schemes
            if scheme_type not in ["apikey", "http"]:
                continue
            
            # For HTTP Bearer, only check if it's actually a Bearer scheme
            if scheme_type == "http" and spec_scheme != "bearer":
                continue
            
            # Check if documentation specifies a required prefix
            doc_scheme = None
            if self.documentation:
                primary_auth = self.documentation.get_primary_auth_info()
                if primary_auth:
                    doc_scheme = primary_auth.get("scheme", "").strip()
            
            # Filter out HTTP methods (GET, POST, DELETE, etc.) - these are not auth prefixes
            http_methods = {"get", "post", "put", "delete", "patch", "options", "head", "trace", "connect"}
            if doc_scheme and doc_scheme.lower() in http_methods:
                doc_scheme = None  # Ignore HTTP methods as auth prefixes
            
            # If docs specify a scheme prefix, check if spec matches
            if doc_scheme:
                # Normalize for comparison (case-insensitive)
                doc_scheme_lower = doc_scheme.lower()
                spec_scheme_lower = spec_scheme.lower() if spec_scheme else ""
                
                # For HTTP Bearer schemes:
                if scheme_type == "http" and spec_scheme == "bearer":
                    # If prefix is "Bearer", it's standard - ignore it
                    if doc_scheme_lower == "bearer":
                        continue
                    # If prefix is something else (e.g., "Token", "Custom"), flag it
                    if doc_scheme_lower != "bearer":
                        is_openapi3 = "openapi" in self.spec
                        if is_openapi3:
                            location = f"components.securitySchemes.{scheme_name}"
                        else:
                            location = f"securityDefinitions.{scheme_name}"
                        
                        # Build full security scheme fragment with scheme name
                        full_spec_fragment = self._build_full_security_scheme_fragment(scheme_name, scheme_def, security_schemes)
                        
                        issues.append(
                            Issue(
                                type=IssueType.CUSTOM_AUTH_PREFIX,
                                location=location,
                                description=f"Security scheme prefix mismatch: documentation requires '{doc_scheme}' prefix but spec has HTTP Bearer (standard 'Bearer' prefix). Expected: HTTP Bearer with '{doc_scheme}' prefix in Authorization header",
                                severity="high",
                                spec_fragment=full_spec_fragment,
                                doc_fragment=f"Documentation requires '{doc_scheme}' prefix in Authorization header",
                            )
                        )
                    continue
                
                # For apiKey schemes, check if the prefix matches
                # Skip Bearer prefix checks for apiKey (those are for HTTP Bearer, not apiKey)
                if scheme_type == "apikey" and doc_scheme_lower == "bearer":
                    # Bearer is for HTTP Bearer auth, not apiKey - skip this check
                    continue
                
                # Check for mismatch (for apiKey with custom prefixes)
                if scheme_type == "apikey" and doc_scheme_lower != spec_scheme_lower:
                    is_openapi3 = "openapi" in self.spec
                    if is_openapi3:
                        location = f"components.securitySchemes.{scheme_name}"
                    else:
                        location = f"securityDefinitions.{scheme_name}"
                    
                    # Determine expected scheme type for apiKey
                    if doc_scheme_lower in ["token", "apikey", "api_key", "api-key"]:
                        # For Token/ApiKey prefixes, should be apiKey type
                        expected_scheme_type = f"apiKey (with '{doc_scheme}' prefix in Authorization header)"
                    else:
                        # Custom prefix for apiKey
                        expected_scheme_type = f"apiKey (with '{doc_scheme}' prefix in Authorization header)"
                    
                    # Extract actual documentation context showing the prefix usage
                    doc_fragment = self._extract_prefix_fragment(doc_scheme)
                    
                    # Build full security scheme fragment with scheme name
                    full_spec_fragment = self._build_full_security_scheme_fragment(scheme_name, scheme_def, security_schemes)
                    
                    issues.append(
                        Issue(
                            type=IssueType.CUSTOM_AUTH_PREFIX,
                            location=location,
                            description=f"Security scheme prefix mismatch: documentation requires '{doc_scheme}' prefix but spec has '{spec_scheme or 'none'}'. Expected: {expected_scheme_type}",
                            severity="high",
                            spec_fragment=full_spec_fragment,
                            doc_fragment=doc_fragment,
                        )
                    )
        
        return issues
    
    def _extract_prefix_fragment(self, prefix: str) -> str:
        """
        Extract documentation fragments showing where a specific auth prefix is used.
        Similar to _extract_auth_code_fragments but focused on prefix usage.
        
        Args:
            prefix: The auth prefix to search for (e.g., "ApiKey", "Token")
            
        Returns:
            A string containing relevant documentation fragments showing the prefix usage
        """
        if not self.documentation:
            return f"Documentation requires '{prefix}' prefix in Authorization header"
        
        fragments = []
        
        # Search in full_text
        if self.documentation.full_text:
            full_text = self.documentation.full_text
            full_text_lower = full_text.lower()
            prefix_lower = prefix.lower()
            
            # Search for "Authorization: <prefix>" or "Authorization": "<prefix>" patterns
            # Pattern 1: "Authorization: ApiKey ..." or "Authorization: Token ..."
            pattern1 = re.compile(
                rf"Authorization\s*[:=]\s*['\"]?{re.escape(prefix)}\s+",
                re.IGNORECASE
            )
            matches1 = list(pattern1.finditer(full_text))
            
            # Pattern 2: "Authorization": "ApiKey ..." (JSON format)
            pattern2 = re.compile(
                rf"['\"]?Authorization['\"]?\s*:\s*['\"]{re.escape(prefix)}\s+",
                re.IGNORECASE
            )
            matches2 = list(pattern2.finditer(full_text))
            
            # Combine matches
            all_matches = matches1 + matches2
            
            for match in all_matches[:3]:  # Limit to first 3 matches
                match_start = match.start()
                match_end = match.end()
                
                # Extract surrounding context: 200 chars before, 500 chars after
                context_start = max(0, match_start - 200)
                context_end = min(len(full_text), match_end + 500)
                fragment = full_text[context_start:context_end]
                
                # Try to start/end at line boundaries for better readability
                # Find the start of the line
                line_start = fragment.rfind('\n', 0, match_start - context_start)
                line_start = line_start + 1 if line_start > 0 else 0
                
                # Find the end of the line (or next few lines for code examples)
                line_end = fragment.find('\n', match_end - context_start)
                if line_end > 0:
                    # Include up to 2 more lines for code examples
                    next_line_end = fragment.find('\n', line_end + 1)
                    if next_line_end > 0:
                        next_next_line_end = fragment.find('\n', next_line_end + 1)
                        line_end = next_next_line_end if next_next_line_end > 0 else len(fragment)
                    else:
                        line_end = len(fragment)
                else:
                    line_end = len(fragment)
                
                # Extract the line-aligned fragment
                aligned_fragment = fragment[line_start:line_end].strip()
                
                # Clean up: ensure it's meaningful
                if len(aligned_fragment) > 30:  # Only include substantial fragments
                    fragments.append(aligned_fragment)
        
        # Also search in code blocks
        if self.documentation.pages:
            for page in self.documentation.pages:
                code_blocks = page.structured_elements.get("code_blocks", [])
                for code_block in code_blocks:
                    if isinstance(code_block, str):
                        # Search for the prefix in code blocks
                        if re.search(rf"Authorization\s*[:=]\s*['\"]?{re.escape(prefix)}\s+", code_block, re.IGNORECASE):
                            # Include the full code block if it's not too long
                            if len(code_block) < 1000:
                                fragments.append(code_block)
                            else:
                                # Extract relevant portion
                                match = re.search(
                                    rf"Authorization\s*[:=]\s*['\"]?{re.escape(prefix)}\s+",
                                    code_block,
                                    re.IGNORECASE
                                )
                                if match:
                                    start = max(0, match.start() - 100)
                                    end = min(len(code_block), match.end() + 300)
                                    fragments.append(code_block[start:end])
        
        # Remove duplicates while preserving order
        seen = set()
        unique_fragments = []
        for frag in fragments:
            frag_normalized = frag.lower().strip()[:100]  # Use first 100 chars as key
            if frag_normalized not in seen:
                seen.add(frag_normalized)
                unique_fragments.append(frag)
        
        # Combine fragments (limit to 2-3 best ones)
        if unique_fragments:
            # Prefer fragments that are longer and contain more context
            unique_fragments.sort(key=len, reverse=True)
            return "\n\n---\n\n".join(unique_fragments[:2])
        else:
            # Fallback
            return f"Documentation requires '{prefix}' prefix in Authorization header"
    
    def detect_incorrect_security_scheme_type(self, security_schemes: Dict[str, Any]) -> List[Issue]:
        """
        Detect incorrect security scheme type (e.g., docs say Bearer token but spec has apiKey).
        Also checks for OAuth2 flow mismatches.
        """
        issues = []
        
        # Get full auth context for better matching
        doc_auth_context = self.documentation.get_global_context().lower() if self.documentation else ""
        full_text_lower = self.documentation.full_text.lower() if self.documentation and self.documentation.full_text else ""
        
        # Check if auth_info exists, otherwise use empty dict
        doc_auth_type = ""
        doc_auth_scheme = ""
        if self.documentation:
            primary_auth = self.documentation.get_primary_auth_info()
            if primary_auth:
                doc_auth_type = primary_auth.get("type", "").lower()
                doc_auth_scheme = primary_auth.get("scheme", "").lower()
        
        # First, check if ANY of the spec's auth methods are mentioned in docs
        # If spec has multiple auth methods and docs mention at least one, don't flag others as mismatches
        spec_has_multiple_auth = len(security_schemes) > 1
        any_spec_auth_mentioned = False
        if spec_has_multiple_auth:
            # Check if any of the spec's auth types are mentioned in docs
            for other_scheme_def in security_schemes.values():
                other_scheme_type = other_scheme_def.get("type", "").lower()
                if self._is_spec_auth_type_mentioned_in_docs(other_scheme_type, other_scheme_def, doc_auth_type, doc_auth_context, full_text_lower):
                    any_spec_auth_mentioned = True
                    break
        
        # Check each security scheme in spec
        for scheme_name, scheme_def in security_schemes.items():
            scheme_type = scheme_def.get("type", "").lower()
            scheme_in = scheme_def.get("in", "").lower()
            scheme_name_param = scheme_def.get("name", "").lower()
            
            # Check if type matches documentation
            # Common mappings:
            # - "bearer" or "token" in docs -> should be "http" with scheme "bearer"
            # - "apikey" or "key" in docs -> should be "apiKey"
            # - "basic" or "basic auth" in docs -> should be "http" with scheme "basic"
            # - "oauth" or "oauth2" in docs -> should be "oauth2"
            
            type_mismatch = False
            expected_type = None
            
            # If spec has multiple auth methods and at least one is mentioned in docs, 
            # don't flag mismatches for the others (API supports multiple methods)
            if spec_has_multiple_auth and any_spec_auth_mentioned:
                # Check if THIS scheme is mentioned in docs
                if not self._is_spec_auth_type_mentioned_in_docs(scheme_type, scheme_def, doc_auth_type, doc_auth_context, full_text_lower):
                    # This scheme is not mentioned, but another one is - skip mismatch check
                    continue
            
            # Check for OAuth2/OAuth first - OAuth2 uses Bearer tokens, so if OAuth2 is mentioned,
            # the spec having oauth2 is correct even if Bearer is also mentioned
            # Also check the full_text directly for OAuth mentions
            oauth_mentioned = (
                "oauth2" in doc_auth_type or 
                "oauth 2" in doc_auth_type or
                "oauth2" in doc_auth_context.lower() or 
                "oauth 2" in doc_auth_context.lower() or
                "oauth 2.0" in doc_auth_context.lower() or
                re.search(r"oauth\s*2\.?0|oauth2", full_text_lower) is not None
            )
            
            # Check if docs mention multiple auth methods (e.g., "OAuth2, API key, etc.")
            # If so, we should be lenient - if spec has one of the mentioned methods, it's OK
            # But we need to be strict: require explicit mention of "API key" as an auth method,
            # not just the presence of "api" and "token" words separately
            api_key_explicitly_mentioned = (
                "apikey" in doc_auth_type or "api_key" in doc_auth_type or
                "api key" in doc_auth_type or
                re.search(r"api\s+key|api_key|api-key", doc_auth_context.lower()) is not None or
                re.search(r"(?:use|require|support|authenticate|auth).*api\s+key|api\s+key.*(?:auth|authenticate|method)", full_text_lower) is not None
            )
            multiple_auth_mentioned = (
                ("oauth" in doc_auth_context.lower() or oauth_mentioned) and
                api_key_explicitly_mentioned
            )
            
            if oauth_mentioned:
                # If multiple auth methods are mentioned and spec has apiKey, that's acceptable
                if multiple_auth_mentioned and scheme_type == "apikey":
                    # Docs mention multiple methods (OAuth2, API key, etc.) and spec has API key - this is OK
                    type_mismatch = False
                elif scheme_type != "oauth2":
                    # When OAuth is mentioned in docs but spec has a different auth type (e.g., apiKey),
                    # only skip the mismatch if the spec's auth type is EXPLICITLY mentioned as an auth method
                    # Don't use the lenient _is_spec_auth_type_mentioned_in_docs check here - be strict
                    spec_auth_explicitly_mentioned = False
                    if scheme_type == "apikey":
                        # For apiKey, require explicit mention of "API key" as an auth method
                        spec_auth_explicitly_mentioned = api_key_explicitly_mentioned
                    elif scheme_type == "http":
                        scheme = scheme_def.get("scheme", "").lower()
                        if scheme == "bearer":
                            # Spec has HTTP Bearer - check if "bearer" is mentioned (but OAuth also uses Bearer, so this is OK)
                            spec_auth_explicitly_mentioned = ("bearer" in doc_auth_type or "bearer" in doc_auth_scheme or "bearer" in doc_auth_context.lower())
                        elif scheme == "basic":
                            # Spec has HTTP Basic - check if "basic" is mentioned in type, scheme, or context
                            spec_auth_explicitly_mentioned = ("basic" in doc_auth_type or "basic" in doc_auth_scheme or "basic" in doc_auth_context.lower())
                            logger.debug(f"Basic auth check: doc_auth_type='{doc_auth_type}', doc_auth_scheme='{doc_auth_scheme}', spec_auth_explicitly_mentioned={spec_auth_explicitly_mentioned}")
                    
                    if spec_auth_explicitly_mentioned:
                        # Spec's auth type is explicitly mentioned in docs as an auth method, so it's OK
                        logger.debug(f"Spec's auth type ({scheme_type}) is explicitly mentioned in docs - skipping mismatch")
                        type_mismatch = False
                    else:
                        type_mismatch = True
                        # Determine expected_type based on what was actually detected in auth_info
                        # If auth_info detected "bearer", use "http with scheme: bearer"
                        # If auth_info detected "oauth", use "oauth2"
                        # Otherwise, default to "oauth2" since OAuth was mentioned
                        if doc_auth_type == "bearer" and doc_auth_scheme == "bearer":
                            expected_type = "http with scheme: bearer"
                        elif doc_auth_type == "oauth" or doc_auth_type == "oauth2":
                            expected_type = "oauth2"
                        else:
                            # OAuth was mentioned but auth_info shows something else - default to oauth2
                            expected_type = "oauth2"
                else:
                    # OAuth2 type matches - now check flow type
                    # Handle both OpenAPI 3.0 (flows) and OpenAPI 2.0 (flow)
                    flows = scheme_def.get("flows", {})
                    # For OpenAPI 2.0 (Swagger 2.0), flow is a single string at the top level
                    if not flows and "flow" in scheme_def:
                        flow_name = scheme_def.get("flow", "").lower()
                        # Map OpenAPI 2.0 flow names to OpenAPI 3.0 flow names
                        flow_name_mapping = {
                            "implicit": "implicit",
                            "password": "password",
                            "application": "clientCredentials",  # OpenAPI 2.0 uses "application" for client credentials
                            "accessCode": "authorizationCode"  # OpenAPI 2.0 uses "accessCode" for authorization code
                        }
                        mapped_flow = flow_name_mapping.get(flow_name, flow_name)
                        # Create a flows dict structure similar to OpenAPI 3.0
                        flows = {mapped_flow: {
                            "authorizationUrl": scheme_def.get("authorizationUrl"),
                            "tokenUrl": scheme_def.get("tokenUrl"),
                            "scopes": scheme_def.get("scopes", {})
                        }}
                    if flows:
                        # Get documented flow type from auth_info if available
                        doc_flow = ""
                        if self.documentation:
                            primary_auth = self.documentation.get_primary_auth_info()
                            if primary_auth:
                                doc_flow = primary_auth.get("flow", "").lower()
                        
                        # If not in auth_info, search full_text directly for flow mentions
                        # Priority: Look for actual flow descriptions, not just mentions in lists
                        if not doc_flow and full_text_lower:
                            # Check for authorization code flow (highest priority - most common)
                            # Look for: response_type=code, authorization code flow, exchange code for token
                            if (re.search(r"response_type\s*=\s*code|exchange\s+code\s+for\s+token|authorization\s+code\s+flow", full_text_lower) or
                                re.search(r"authorization\s+code|authorization_code|grant_type\s*=\s*authorization_code", full_text_lower)):
                                doc_flow = "authorizationcode"
                            # Check for client credentials flow
                            # Only detect if it's described as a flow (not just mentioned in a list)
                            # Look for: grant_type=client_credentials, client credentials flow, client credentials grant flow
                            elif (re.search(r"grant_type\s*=\s*client_credential|client\s+credential\s+flow|client\s+credential\s+grant\s+flow", full_text_lower) or
                                  (re.search(r"client\s+credential|client_credential", full_text_lower) and 
                                   not re.search(r"authorization\s+code|response_type\s*=\s*code|exchange\s+code", full_text_lower))):
                                # Only use client credentials if authorization code is NOT mentioned
                                doc_flow = "clientcredentials"
                            # Check for implicit flow
                            elif re.search(r"\bimplicit\s+flow|\bimplicit\s+grant|response_type\s*=\s*token", full_text_lower):
                                doc_flow = "implicit"
                            # Check for password flow
                            elif re.search(r"password\s+flow|password\s+grant|grant_type\s*=\s*password", full_text_lower):
                                doc_flow = "password"
                        
                        # Map documented flow names to OpenAPI flow names
                        flow_mapping = {
                            "authorizationcode": "authorizationCode",
                            "authorization_code": "authorizationCode",
                            "clientcredentials": "clientCredentials",
                            "client_credentials": "clientCredentials",
                            "implicit": "implicit",
                            "password": "password"
                        }
                        
                        # Normalize documented flow name
                        doc_flow_normalized = flow_mapping.get(doc_flow, doc_flow)
                        
                        # Check which flows are defined in spec
                        spec_flows = [flow for flow in flows.keys() if flow in ["authorizationCode", "implicit", "clientCredentials", "password"]]
                        
                        # If documentation mentions a specific flow, check if it's in the spec
                        if doc_flow_normalized:
                            # Before flagging mismatch, check if spec's current flow(s) are also mentioned in docs
                            spec_flow_also_mentioned = False
                            for spec_flow in spec_flows:
                                # Check if this spec flow is mentioned in documentation
                                flow_keywords = {
                                    "authorizationCode": [r"authorization\s+code", r"authorization_code", r"grant_type\s*=\s*authorization_code"],
                                    "clientCredentials": [r"client\s+credential", r"client_credential", r"grant_type\s*=\s*client_credential"],
                                    "implicit": [r"\bimplicit\s+flow", r"\bimplicit\s+grant", r"response_type\s*=\s*token"],
                                    "password": [r"password\s+flow", r"password\s+grant", r"grant_type\s*=\s*password"]
                                }
                                
                                keywords = flow_keywords.get(spec_flow, [])
                                for keyword in keywords:
                                    if re.search(keyword, full_text_lower, re.IGNORECASE):
                                        spec_flow_also_mentioned = True
                                        break
                                if spec_flow_also_mentioned:
                                    break
                            
                            if spec_flow_also_mentioned:
                                # Spec's flow is also mentioned in docs, so it's OK (API supports multiple flows)
                                pass  # Don't create issue
                            elif doc_flow_normalized not in spec_flows:
                                # Flow mismatch - create issue
                                is_openapi3 = "openapi" in self.spec
                                if is_openapi3:
                                    location = f"components.securitySchemes.{scheme_name}"
                                else:
                                    location = f"securityDefinitions.{scheme_name}"
                                
                                # Build full security scheme fragment with scheme name
                                full_spec_fragment = self._build_full_security_scheme_fragment(scheme_name, scheme_def, security_schemes)
                                
                                # Add detailed flow information to the fragment
                                # Merge flow details into the full fragment structure
                                detailed_spec_fragment = full_spec_fragment.copy()
                                
                                # Add flow information at top level for easy access
                                detailed_spec_fragment.update({
                                    "type": "oauth2",
                                    "flows": flows,
                                    "current_flows": spec_flows,
                                    "expected_flow": doc_flow_normalized
                                })
                                
                                # Add details about the current flow(s) in spec
                                for flow_name, flow_details in flows.items():
                                    if flow_name in spec_flows:
                                        detailed_spec_fragment[f"current_{flow_name}_details"] = {
                                            "authorizationUrl": flow_details.get("authorizationUrl"),
                                            "tokenUrl": flow_details.get("tokenUrl"),
                                            "refreshUrl": flow_details.get("refreshUrl"),
                                            "scopes": flow_details.get("scopes", {})
                                        }
                                
                                # Try to extract flow details from documentation for the expected flow
                                if full_text_lower:
                                    # Extract token URL - try multiple patterns
                                    token_url = None
                                    
                                    # Pattern 1: "token url: https://..."
                                    token_url_match = re.search(r"token\s+url[:\s]+(https?://[^\s<>\"\'\)]+)", full_text_lower, re.IGNORECASE)
                                    if token_url_match:
                                        token_url = token_url_match.group(1)
                                    else:
                                        # Pattern 2: Look for URLs containing /token in the text
                                        all_urls = re.findall(r"https?://[^\s<>\"\'\)]+", full_text_lower, re.IGNORECASE)
                                        for url in all_urls:
                                            if "/token" in url.lower():
                                                token_url = url
                                                break
                                    
                                    if token_url:
                                        detailed_spec_fragment["expected_token_url"] = token_url
                                    
                                    # Extract authorization URL - try multiple patterns
                                    auth_url = None
                                    
                                    # Pattern 1: "authorization url: https://..."
                                    auth_url_match = re.search(r"authoriz[^\s]*\s+url[:\s]+(https?://[^\s<>\"\'\)]+)", full_text_lower, re.IGNORECASE)
                                    if auth_url_match:
                                        auth_url = auth_url_match.group(1)
                                    else:
                                        # Pattern 2: Look for URLs containing /authorize in the text
                                        all_urls = re.findall(r"https?://[^\s<>\"\'\)]+", full_text_lower, re.IGNORECASE)
                                        for url in all_urls:
                                            if "/authorize" in url.lower():
                                                auth_url = url
                                                break
                                    
                                    if auth_url:
                                        detailed_spec_fragment["expected_authorization_url"] = auth_url
                                
                                issues.append(
                                    Issue(
                                        type=IssueType.MISSING_AUTH_DOC,
                                        location=location,
                                        description=f"OAuth2 flow mismatch: documentation mentions '{doc_flow_normalized}' flow but spec has {spec_flows}",
                                        severity="high",
                                        spec_fragment=detailed_spec_fragment,
                                        doc_fragment=f"Documentation indicates OAuth2 flow: {doc_flow_normalized}",
                                    )
                                )
            # Check for Bearer token only if OAuth2 is NOT mentioned (OAuth2 uses Bearer tokens, so it's not a mismatch)
            elif "bearer" in doc_auth_type or "via bearer" in doc_auth_context.lower() or "http bearer" in doc_auth_context.lower():
                if scheme_type != "http" or scheme_def.get("scheme", "").lower() != "bearer":
                    # Before flagging mismatch, check if spec's auth type is also mentioned in docs
                    if self._is_spec_auth_type_mentioned_in_docs(scheme_type, scheme_def, doc_auth_type, doc_auth_context, full_text_lower):
                        # Spec's auth type is also mentioned in docs, so it's OK
                        type_mismatch = False
                    else:
                        type_mismatch = True
                        expected_type = "http with scheme: bearer"
            # Only check for apiKey if Bearer and OAuth are NOT mentioned
            # But if multiple auth methods are mentioned, having apiKey in spec is acceptable
            elif ("apikey" in doc_auth_type or "api key" in doc_auth_type or "api_key" in doc_auth_type):
                # Double-check: if api_key is detected, verify if it's actually Bearer authentication
                # Look for "Authorization: Bearer" patterns in code examples
                bearer_in_code = self._check_bearer_in_code_examples()
                
                if bearer_in_code:
                    # Found Bearer in code examples - this should be HTTP Bearer, not API Key
                    if scheme_type != "http" or scheme_def.get("scheme", "").lower() != "bearer":
                        # Before flagging mismatch, check if spec's auth type is also mentioned in docs
                        if self._is_spec_auth_type_mentioned_in_docs(scheme_type, scheme_def, doc_auth_type, doc_auth_context, full_text_lower):
                            type_mismatch = False
                        else:
                            type_mismatch = True
                            expected_type = "http with scheme: bearer"
                # If multiple auth methods are mentioned, having apiKey is acceptable even if OAuth is also mentioned
                elif multiple_auth_mentioned and scheme_type == "apikey":
                    # Docs mention multiple methods and spec has API key - this is OK
                    type_mismatch = False
                elif "bearer" not in doc_auth_context.lower() and "oauth" not in doc_auth_context.lower() and "via" not in doc_auth_context.lower():
                    if scheme_type != "apikey":
                        # Before flagging mismatch, check if spec's auth type is also mentioned in docs
                        if self._is_spec_auth_type_mentioned_in_docs(scheme_type, scheme_def, doc_auth_type, doc_auth_context, full_text_lower):
                            type_mismatch = False
                        else:
                            type_mismatch = True
                            expected_type = "apiKey"
            elif "basic" in doc_auth_type or "basic" in doc_auth_scheme:
                if scheme_type != "http" or scheme_def.get("scheme", "").lower() != "basic":
                    # Before flagging mismatch, check if spec's auth type is also mentioned in docs
                    if self._is_spec_auth_type_mentioned_in_docs(scheme_type, scheme_def, doc_auth_type, doc_auth_context, full_text_lower):
                        type_mismatch = False
                    else:
                        type_mismatch = True
                        expected_type = "http with scheme: basic"
            
            if type_mismatch:
                # Determine location based on where security schemes are defined
                is_openapi3 = "openapi" in self.spec
                if is_openapi3:
                    location = f"components.securitySchemes.{scheme_name}"
                else:
                    location = f"securityDefinitions.{scheme_name}"
                
                # Extract surrounding context around where the auth method is mentioned in documentation
                # This provides natural context showing how the auth method is described
                doc_fragment = ""
                
                if self.documentation and self.documentation.full_text:
                    full_text = self.documentation.full_text
                    full_text_lower = full_text.lower()
                    
                    # Determine search keywords based on detected auth type
                    search_keywords = []
                    if doc_auth_type == "bearer" and doc_auth_scheme == "bearer":
                        search_keywords = ["authorization", "bearer"]
                    elif doc_auth_type == "oauth" or doc_auth_type == "oauth2" or oauth_mentioned:
                        search_keywords = ["oauth 2.0", "oauth2", "oauth"]
                    elif doc_auth_type == "apikey" or doc_auth_type == "api_key":
                        search_keywords = ["api key", "api_key", "apikey"]
                    elif doc_auth_type == "basic":
                        search_keywords = ["basic auth", "basic authentication", "basic"]
                    else:
                        search_keywords = [doc_auth_type] if doc_auth_type else []
                    
                    # Find all mentions of the auth method in the documentation
                    fragments = []
                    for keyword in search_keywords:
                        # Search for the keyword (case-insensitive)
                        pattern = re.escape(keyword)
                        matches = list(re.finditer(pattern, full_text_lower, re.IGNORECASE))
                        
                        for match in matches[:3]:  # Limit to first 3 matches
                            match_start = match.start()
                            match_end = match.end()
                            
                            # Extract surrounding context: 200 chars before, 300 chars after
                            context_start = max(0, match_start - 200)
                            context_end = min(len(full_text), match_end + 300)
                            fragment = full_text[context_start:context_end]
                            
                            # Try to start/end at sentence boundaries for better readability
                            # Find the start of the sentence
                            sentence_start = fragment.rfind('.', 0, match_start - context_start)
                            sentence_start = sentence_start + 1 if sentence_start > 0 else 0
                            
                            # Find the end of the sentence (or next sentence)
                            sentence_end = fragment.find('.', match_end - context_start)
                            if sentence_end > 0:
                                sentence_end = min(sentence_end + 1, len(fragment))
                            else:
                                sentence_end = len(fragment)
                            
                            # Extract the sentence-aligned fragment
                            aligned_fragment = fragment[sentence_start:sentence_end].strip()
                            
                            # Clean up: remove leading/trailing whitespace, ensure it's meaningful
                            if len(aligned_fragment) > 50:  # Only include substantial fragments
                                fragments.append(aligned_fragment)
                    
                    # Remove duplicates while preserving order
                    seen = set()
                    unique_fragments = []
                    for frag in fragments:
                        frag_normalized = frag.lower().strip()[:100]  # Use first 100 chars as key
                        if frag_normalized not in seen:
                            seen.add(frag_normalized)
                            unique_fragments.append(frag)
                    
                    # Combine fragments (limit to 2-3 best ones)
                    if unique_fragments:
                        # Prefer fragments that are longer and contain more context
                        unique_fragments.sort(key=len, reverse=True)
                        doc_fragment = "\n\n---\n\n".join(unique_fragments[:2])
                    else:
                        # Fallback: use expected_type
                        doc_fragment = f"Documentation indicates: {expected_type}"
                else:
                    # Fallback if no full text
                    doc_fragment = f"Documentation indicates: {expected_type}"
                
                # Build full security scheme fragment with scheme name
                full_spec_fragment = self._build_full_security_scheme_fragment(scheme_name, scheme_def, security_schemes)
                
                issues.append(
                    Issue(
                        type=IssueType.MISSING_AUTH_DOC,
                        location=location,
                        description=f"Security scheme type mismatch: documentation indicates '{expected_type}' but spec has '{scheme_type}'",
                        severity="high",
                        spec_fragment=full_spec_fragment,
                        doc_fragment=doc_fragment,
                    )
                )
        
        return issues
    
    def _build_full_security_scheme_fragment(self, scheme_name: str, scheme_def: Dict[str, Any], 
                                             all_security_schemes: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build a complete security scheme fragment including ALL security schemes from the spec.
        This provides full context to the LLM, not just the problematic scheme.
        
        Args:
            scheme_name: The name of the security scheme with the issue (e.g., "basicAuth", "bearerAuth")
            scheme_def: The security scheme definition (not used, but kept for API compatibility)
            all_security_schemes: All security schemes from the spec (for context)
        
        Returns:
            A dict containing the full security schemes structure with ALL schemes
        """
        # Determine if this is OpenAPI 3 or Swagger 2
        is_openapi3 = "openapi" in self.spec
        is_swagger2 = "swagger" in self.spec and self.spec.get("swagger", "").startswith("2")
        
        # Copy all security schemes to avoid modifying originals
        all_schemes_copy = {
            name: scheme.copy() for name, scheme in all_security_schemes.items()
        }
        
        if is_openapi3:
            # OpenAPI 3 structure - include ALL security schemes
            return {
                "components": {
                    "securitySchemes": all_schemes_copy
                }
            }
        elif is_swagger2:
            # Swagger 2 structure - include ALL security definitions
            return {
                "securityDefinitions": all_schemes_copy
            }
        else:
            # Fallback: include all schemes with their names
            return {
                "security_schemes": all_schemes_copy,
                "issue_scheme": scheme_name  # Indicate which scheme has the issue
            }
    
    def _check_bearer_in_code_examples(self) -> bool:
        """
        Check if Bearer authentication is mentioned in code examples.
        
        Returns:
            True if "Authorization: Bearer" pattern is found in code examples
        """
        if not self.documentation:
            return False
        
        # Patterns to look for Bearer authentication
        bearer_patterns = [
            r"authorization.*?bearer\s+[A-Z0-9_-]+",  # Authorization: Bearer TOKEN
            r"--header\s+['\"]authorization:\s*bearer",  # curl --header "Authorization: Bearer"
            r"Authorization:\s*Bearer",  # Authorization: Bearer
            r"bearer\s+[A-Z0-9_-]{10,}",  # Bearer followed by token-like string
        ]
        
        # Check in code blocks from structured elements
        for page in self.documentation.pages:
            code_blocks = page.structured_elements.get("code_blocks", [])
            for code_block in code_blocks:
                code_lower = code_block.lower()
                for pattern in bearer_patterns:
                    if re.search(pattern, code_lower, re.IGNORECASE):
                        return True
        
        # Check in examples
        for example in self.documentation.examples:
            example_content = example.get("content", "")
            if example_content:
                example_lower = example_content.lower()
                for pattern in bearer_patterns:
                    if re.search(pattern, example_lower, re.IGNORECASE):
                        return True
        
        # Check in full text (as fallback)
        if self.documentation.full_text:
            full_text_lower = self.documentation.full_text.lower()
            for pattern in bearer_patterns:
                if re.search(pattern, full_text_lower, re.IGNORECASE):
                    return True
        
        return False
    
    def _extract_auth_table_context(self) -> str:
        """
        Extract table rows that mention authentication-related parameters.
        Includes full table row context (parameter name, type, required, default, description)
        so LLM can determine if it's actually required or optional.
        
        Returns:
            String containing formatted table rows with auth mentions
        """
        if not self.documentation:
            return ""
        
        fragments = []
        auth_keywords = ["apikey", "api_key", "api-key", "api key", "authentication", "auth", "token", "bearer", "oauth"]
        
        for page in self.documentation.pages:
            tables = page.structured_elements.get("tables", [])
            for table in tables:
                if not table or len(table) < 2:  # Need at least header + 1 row
                    continue
                
                # Get header row (first row)
                header = table[0]
                if not header:
                    continue
                
                # Find columns that might contain "Required" or similar
                required_col_idx = None
                param_col_idx = None
                type_col_idx = None
                default_col_idx = None
                desc_col_idx = None
                
                header_lower = [str(cell).lower().strip() for cell in header]
                for idx, cell in enumerate(header_lower):
                    cell_clean = cell.strip()
                    # Check for required/mandatory columns (more flexible matching)
                    if any(keyword in cell_clean for keyword in ["required", "mandatory", "must", "need"]):
                        required_col_idx = idx
                    elif any(keyword in cell_clean for keyword in ["parameter", "param", "name", "field", "key"]):
                        param_col_idx = idx
                    elif "type" in cell_clean:
                        type_col_idx = idx
                    elif any(keyword in cell_clean for keyword in ["default", "value", "example"]):
                        default_col_idx = idx
                    elif any(keyword in cell_clean for keyword in ["description", "desc", "details", "info", "note"]):
                        desc_col_idx = idx
                
                # If no "Required" column found, try to infer from common patterns
                # But be more flexible - check if any column has boolean-like values
                if required_col_idx is None:
                    # Look for columns that contain boolean-like values (yes/no, true/false, required/optional)
                    # Check a few sample rows to see which column has these values
                    sample_rows = table[1:min(4, len(table))]  # Check first 3 data rows
                    for col_idx in range(min(len(header), 5)):  # Check first 5 columns
                        if col_idx >= len(header):
                            continue
                        # Check if this column consistently has boolean-like values
                        boolean_values = []
                        for row in sample_rows:
                            if col_idx < len(row):
                                cell_value = str(row[col_idx]).lower().strip()
                                if cell_value in ["yes", "no", "true", "false", "required", "optional", "y", "n", "mandatory"]:
                                    boolean_values.append(cell_value)
                        # If most cells in this column are boolean-like, it's likely the "Required" column
                        if len(boolean_values) >= len(sample_rows) * 0.5:  # At least 50% are boolean-like
                            required_col_idx = col_idx
                            break
                
                # Check each data row for auth keywords
                for row in table[1:]:
                    if not row or len(row) == 0:
                        continue
                    
                    # Check if any cell in the row contains auth keywords
                    row_text = " ".join([str(cell) for cell in row]).lower()
                    has_auth_mention = any(keyword in row_text for keyword in auth_keywords)
                    
                    if has_auth_mention:
                        # Format the table row with context
                        # Include header and the full row
                        formatted_row = []
                        
                        # Add header
                        formatted_row.append("Table Row:")
                        formatted_row.append(" | ".join([str(cell) for cell in header]))
                        formatted_row.append(" | ".join([str(cell) for cell in row]))
                        
                        # Add context: highlight if "Required" column indicates it's optional
                        if required_col_idx is not None and required_col_idx < len(row):
                            required_value = str(row[required_col_idx]).strip()
                            required_lower = required_value.lower()
                            # Check for various ways of saying "not required"
                            optional_indicators = [
                                "no", "false", "optional", "n", "not required", 
                                "not mandatory", "optional (yes/no)", "no (optional)"
                            ]
                            is_optional = (
                                required_lower in optional_indicators or
                                required_lower.startswith("no") or
                                (required_lower.startswith("optional") and "required" not in required_lower)
                            )
                            if is_optional:
                                formatted_row.append(f"\nNote: This parameter is marked as '{required_value}' (not required)")
                            elif required_lower in ["yes", "true", "y", "required", "mandatory"]:
                                formatted_row.append(f"\nNote: This parameter is marked as '{required_value}' (required)")
                        
                        fragments.append("\n".join(formatted_row))
        
        if fragments:
            return "\n\n---\n\n".join(fragments)
        return ""
    
    def _extract_auth_code_fragments(self, max_fragments: int = 3) -> str:
        """
        Extract code fragments that show authentication examples.
        Includes surrounding context (more lines before/after) for better LLM understanding.
        
        Args:
            max_fragments: Maximum number of fragments to extract (default: 3)
        
        Returns:
            String containing authentication code fragments, separated by newlines
        """
        if not self.documentation:
            return ""
        
        fragments = []
        
        # Patterns to identify authentication-related code
        auth_patterns = [
            r"authorization.*?bearer",
            r"authorization.*?token",
            r"--header.*?authorization",
            r"api[_-]?key",
            r"x-api-key",
            r"authentication",
        ]
        
        # Extract from code blocks with more context
        for page in self.documentation.pages:
            code_blocks = page.structured_elements.get("code_blocks", [])
            for code_block in code_blocks:
                code_lower = code_block.lower()
                # Check if this code block contains authentication patterns
                for pattern in auth_patterns:
                    match = re.search(pattern, code_lower, re.IGNORECASE)
                    if match:
                        # Extract with more context (before and after the match)
                        match_start = match.start()
                        match_end = match.end()
                        
                        # Get context: 300 chars before, 500 chars after
                        context_start = max(0, match_start - 300)
                        context_end = min(len(code_block), match_end + 500)
                        snippet = code_block[context_start:context_end]
                        
                        # Try to start/end at line boundaries for better readability
                        lines = snippet.split('\n')
                        if len(lines) > 1:
                            # If we truncated, add ellipsis
                            if context_start > 0:
                                snippet = "..." + '\n'.join(lines)
                            if context_end < len(code_block):
                                snippet = '\n'.join(lines) + "..."
                        else:
                            snippet = code_block[:800]  # Fallback: just take first 800 chars
                            if len(code_block) > 800:
                                snippet += "..."
                        
                        fragments.append(snippet)
                        break  # Found auth pattern, move to next code block
                
                if len(fragments) >= max_fragments:
                    break
            
            if len(fragments) >= max_fragments:
                break
        
        # If not enough from code blocks, check examples
        if len(fragments) < max_fragments:
            for example in self.documentation.examples:
                example_content = example.get("content", "")
                if example_content:
                    example_lower = example_content.lower()
                    for pattern in auth_patterns:
                        match = re.search(pattern, example_lower, re.IGNORECASE)
                        if match:
                            # Extract with context
                            match_start = match.start()
                            match_end = match.end()
                            context_start = max(0, match_start - 300)
                            context_end = min(len(example_content), match_end + 500)
                            snippet = example_content[context_start:context_end]
                            
                            if context_start > 0:
                                snippet = "..." + snippet
                            if context_end < len(example_content):
                                snippet = snippet + "..."
                            
                            fragments.append(snippet)
                            break
                
                if len(fragments) >= max_fragments:
                    break
        
        # Join fragments with separators
        if fragments:
            return "\n\n---\n\n".join(fragments)
        
        return ""

    def detect_malformed_base_urls(self) -> List[Issue]:
        """
        Detect malformed or missing base URLs.
        Validates by checking if base URL + path would form a valid URL.
        Rejects template variables ({{variable}}) as they are incorrect.
        """
        issues = []
        
        # Get all paths from spec to validate base URL
        paths = self.spec.get("paths", {})
        sample_paths = list(paths.keys())[:5]  # Use first 5 paths as samples
        
        # Check OpenAPI 3 servers
        servers = self.spec.get("servers", [])
        # Check OpenAPI 2 host/basePath
        host = self.spec.get("host", "")
        base_path = self.spec.get("basePath", "")
        
        is_openapi3 = "openapi" in self.spec
        is_swagger2 = "swagger" in self.spec and self.spec.get("swagger", "").startswith("2")
        
        if is_swagger2:
            # OpenAPI 2: check host/basePath
            if not host:
                # Extract base URLs from documentation
                doc_fragment = self._extract_base_urls_from_docs()
                
                # Include example paths in spec_fragment
                example_paths = list(paths.keys())[:5] if paths else []
                spec_fragment = {
                    "_example_paths": example_paths
                } if example_paths else None
                
                issues.append(
                    Issue(
                        type=IssueType.MALFORMED_BASE_URL,
                        location="host",
                        description="No host defined in spec (OpenAPI 2)",
                        severity="high",
                        spec_fragment=spec_fragment,
                        doc_fragment=doc_fragment,
                    )
                )
            else:
                # Check if host contains template variables (wrong)
                if "{{" in host or (host.startswith("{") and host.endswith("}")):
                    issues.append(
                        Issue(
                            type=IssueType.MALFORMED_BASE_URL,
                            location="host",
                            description=f"Host '{host}' contains template variables which are incorrect for base URLs",
                            severity="high",
                            spec_fragment={"host": host, "basePath": base_path},
                        )
                    )
                elif not self._is_valid_base_url_with_paths(host, base_path, sample_paths):
                    issues.append(
                        Issue(
                            type=IssueType.MALFORMED_BASE_URL,
                            location="host",
                            description=f"Host '{host}' combined with paths does not form valid URLs",
                            severity="high",
                            spec_fragment={"host": host, "basePath": base_path},
                        )
                    )
        elif is_openapi3:
            # OpenAPI 3: check servers
            # Servers can be defined at root, path, or operation level
            # First check root-level servers
            if not servers:
                # Check if any path has servers defined
                path_has_servers = False
                for path_item in paths.values():
                    # Path-level servers (OpenAPI 3.0)
                    if isinstance(path_item, dict) and path_item.get("servers"):
                        path_has_servers = True
                        break
                    # Operation-level servers
                    if isinstance(path_item, dict):
                        for method, operation in path_item.items():
                            if method.lower() in ["get", "post", "put", "patch", "delete", "options", "head", "trace"]:
                                if isinstance(operation, dict) and operation.get("servers"):
                                    path_has_servers = True
                                    break
                        if path_has_servers:
                            break
                
                if not path_has_servers:
                    # Extract base URLs from documentation
                    doc_fragment = self._extract_base_urls_from_docs()
                    
                    # Include example paths in spec_fragment
                    example_paths = list(paths.keys())[:5] if paths else []
                    spec_fragment = {
                        "_example_paths": example_paths
                    } if example_paths else None
                    
                    issues.append(
                        Issue(
                            type=IssueType.MALFORMED_BASE_URL,
                            location="servers",
                            description="No servers/base URLs defined in spec (checked root, path, and operation levels)",
                            severity="high",
                            spec_fragment=spec_fragment,
                            doc_fragment=doc_fragment,
                        )
                    )
            else:
                for idx, server in enumerate(servers):
                    url = server.get("url", "")
                    if not url:
                        issues.append(
                            Issue(
                                type=IssueType.MALFORMED_BASE_URL,
                                location=f"servers[{idx}]",
                                description="Server URL is empty",
                                severity="high",
                                spec_fragment=server,
                            )
                        )
                    else:
                        # Check if URL is missing protocol (http:// or https://)
                        if not url.startswith(("http://", "https://")):
                            # Check if it looks like a hostname (contains dot) - missing protocol
                            if "." in url or url.lower() in ["localhost", "127.0.0.1"]:
                                # Extract URL information from documentation
                                doc_fragment = self._extract_url_info_from_docs(url, server)
                                
                                # Include example paths in spec_fragment so LLM can check for overlap
                                paths = self.spec.get("paths", {})
                                example_paths = list(paths.keys())[:5]  # First 5 paths as examples
                                
                                spec_fragment_with_paths = {
                                    **server,
                                    "_example_paths": example_paths  # Add example paths for overlap checking
                                }
                                
                                issues.append(
                                    Issue(
                                        type=IssueType.MALFORMED_BASE_URL,
                                        location=f"servers[{idx}]",
                                        description=f"Server URL '{url}' is missing protocol (http:// or https://)",
                                        severity="high",
                                        spec_fragment=spec_fragment_with_paths,
                                        doc_fragment=doc_fragment,
                                    )
                                )
                                continue  # Skip other checks for this URL
                        
                        # Check if URL contains template variables (wrong)
                        if "{{" in url or (url.startswith("{") and url.endswith("}") and not url.startswith("http")):
                            # Extract URL information from documentation
                            doc_fragment = self._extract_url_info_from_docs(url, server)
                            
                            # Include example paths in spec_fragment so LLM can check for overlap
                            paths = self.spec.get("paths", {})
                            example_paths = list(paths.keys())[:5]  # First 5 paths as examples
                            
                            spec_fragment_with_paths = {
                                **server,
                                "_example_paths": example_paths  # Add example paths for overlap checking
                            }
                            
                            issues.append(
                                Issue(
                                    type=IssueType.MALFORMED_BASE_URL,
                                    location=f"servers[{idx}]",
                                    description=f"Server URL '{url}' contains template variables which are incorrect for base URLs",
                                    severity="high",
                                    spec_fragment=spec_fragment_with_paths,
                                    doc_fragment=doc_fragment,
                                )
                            )
                        elif not self._is_valid_base_url_with_paths(url, "", sample_paths):
                            issues.append(
                                Issue(
                                    type=IssueType.MALFORMED_BASE_URL,
                                    location=f"servers[{idx}]",
                                    description=f"Server URL '{url}' combined with paths does not form valid URLs",
                                    severity="high",
                                    spec_fragment=server,
                                )
                            )

        return issues
    
    def _is_valid_base_url_with_paths(self, base_url: str, base_path: str, sample_paths: List[str]) -> bool:
        """
        Check if base URL combined with paths would form valid URLs.
        Does not require http/https prefix, but validates the structure.
        
        Args:
            base_url: Base URL or host
            base_path: Base path (for OpenAPI 2)
            sample_paths: List of sample path strings from the spec
            
        Returns:
            True if base URL + paths would form valid URLs
        """
        if not base_url:
            return False
        
        # Reject template variables
        if "{{" in base_url or (base_url.startswith("{") and base_url.endswith("}") and not base_url.startswith("http")):
            return False
        
        # If no paths to validate against, do basic validation
        if not sample_paths:
            # Basic checks: should be hostname-like or start with http/https
            if base_url.startswith(("http://", "https://")):
                return True
            # Hostname format (contains dot or is localhost)
            if "." in base_url or base_url.lower() in ["localhost", "127.0.0.1"]:
                return True
            # Relative path
            if base_url.startswith("/"):
                return True
            return False
        
        # Try combining base URL with sample paths
        for path in sample_paths[:3]:  # Check first 3 paths
            # Construct full URL
            if base_path:
                full_path = base_path.rstrip("/") + "/" + path.lstrip("/")
            else:
                full_path = path
            
            # Combine base URL with path
            if base_url.startswith(("http://", "https://")):
                # Already has protocol
                combined = base_url.rstrip("/") + "/" + full_path.lstrip("/")
            elif base_url.startswith("/"):
                # Base URL is a path, combine with path
                combined = base_url.rstrip("/") + "/" + full_path.lstrip("/")
            else:
                # Assume hostname, add protocol for validation
                combined = "https://" + base_url.rstrip("/") + "/" + full_path.lstrip("/")
            
            # Basic URL validation: should have at least hostname and path structure
            # Remove protocol for validation
            url_without_protocol = combined.split("://", 1)[-1] if "://" in combined else combined
            
            # Should have at least a hostname-like part and a path
            parts = url_without_protocol.split("/", 1)
            if len(parts) < 2:
                # No path part, might be valid if base URL is complete
                continue
            
            hostname_part = parts[0]
            # Hostname should contain at least one dot or be localhost
            if not ("." in hostname_part or hostname_part.lower() in ["localhost", "127.0.0.1"]):
                # Might be a relative path, which is okay
                if not base_url.startswith(("http://", "https://")):
                    continue
            
            # If we get here, the combination looks valid
            return True
        
        # If we couldn't validate with any path, do basic check
        return self._basic_url_validation(base_url)
    
    def _basic_url_validation(self, url: str) -> bool:
        """Basic URL validation without requiring protocol."""
        if not url:
            return False
        
        # Has protocol - definitely valid
        if url.startswith(("http://", "https://")):
            return True
        
        # Hostname format (contains dot or is localhost)
        if "." in url or url.lower() in ["localhost", "127.0.0.1"]:
            return True
        
        # Relative path
        if url.startswith("/"):
            return True
        
        return False
    
    def _extract_base_url_from_full_url(self, url: str) -> str:
        """
        Extract base URL (protocol + hostname + port) from a full URL.
        
        Args:
            url: Full URL (e.g., "https://api.example.com/v1/users")
            
        Returns:
            Base URL (e.g., "https://api.example.com")
        """
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            # Reconstruct base URL: protocol + hostname + port
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            return base_url
        except Exception:
            # Fallback: simple string manipulation
            if "://" in url:
                parts = url.split("://", 1)
                if len(parts) == 2:
                    path_start = parts[1].find("/")
                    if path_start != -1:
                        return f"{parts[0]}://{parts[1][:path_start]}"
            return url
        return url
    
    def _build_base_url_doc_fragment(self, full_text: str, links: Optional[List[str]] = None) -> Optional[str]:
        """
        Build a documentation fragment for base URL discovery using priority order.
        Does NOT include any extracted "Base URL: ..." in the fragment so the LLM
        derives the base URL from the raw documentation and is not misled by our parsing.
        
        Priority:
        1. "base url:" and variations (e.g. "Base URL:", "base url is") - snippet containing keyword + URL
        2. Example endpoints: GET/POST/PUT/PATCH/DELETE followed by full URL (method + https://...)
        3. Other full URLs in docs with context
        
        Returns:
            Single string with "Documentation excerpt:" snippets only (no extracted base URL lines)
        """
        if not full_text or not full_text.strip():
            return None
        
        # Standard URLs (excludes < > to avoid HTML)
        url_pattern = r'https?://[^\s<>"\'\)]+'
        # Template URLs with <placeholder> (e.g. https://<project_ref>.supabase.co/rest/v1/)
        template_url_pattern = re.compile(
            r'https?://(?:[^\s"\'\)<>]|<[^>]+>)+',
            re.IGNORECASE
        )
        snippets = []
        seen_snippets = set()  # avoid duplicate snippets (normalize by first 80 chars)
        
        def add_snippet(label: str, text: str) -> bool:
            normalized = text.strip()[:120].replace("\n", " ")
            if normalized in seen_snippets:
                return False
            seen_snippets.add(normalized)
            snippets.append(f"Documentation excerpt ({label}):\n{text.strip()}")
            return True
        
        def has_any_url(text: str) -> bool:
            return bool(re.search(url_pattern, text, re.IGNORECASE) or template_url_pattern.search(text))
        
        # --- Priority 1: "base url:" and variations ---
        base_url_keywords = [
            "base url:",
            "base url is",
            "base url -",
            "base url =",
            "base url=",
            "api base url:",
            "api base url is",
            "the base url is",
            "the base url:",
        ]
        full_text_lower = full_text.lower()
        for kw in base_url_keywords:
            idx = 0
            while idx < len(full_text_lower):
                idx = full_text_lower.find(kw, idx)
                if idx == -1:
                    break
                # Take snippet: from 80 chars before keyword to 350 chars after (enough to include a URL)
                start = max(0, idx - 80)
                end = min(len(full_text), idx + len(kw) + 350)
                snippet = full_text[start:end]
                if has_any_url(snippet):
                    if add_snippet("mentions base URL", snippet):
                        if len(snippets) >= 3:
                            break
                idx += len(kw)
            if len(snippets) >= 3:
                break
        
        # --- Priority 2: Example endpoints (METHOD + full URL) ---
        if len(snippets) < 5:
            # Standard URLs
            method_url_pattern = re.compile(
                r'\b(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s+(https?://[^\s<>"\'\)]+)',
                re.IGNORECASE
            )
            curl_pattern = re.compile(
                r'curl\s+(?:-[A-Z]\s+)?(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s+["\']?(https?://[^\s<>"\']+)',
                re.IGNORECASE
            )
            # Template URLs (e.g. GET https://<project_ref>.supabase.co/rest/v1/)
            method_template_pattern = re.compile(
                r'\b(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s+(https?://(?:[^\s"\'\)<>]|<[^>]+>)+)',
                re.IGNORECASE
            )
            curl_template_pattern = re.compile(
                r'curl\s+(?:-[A-Z]\s+)?(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s+["\']?(https?://(?:[^\s"\'\)<>]|<[^>]+>)+)',
                re.IGNORECASE
            )
            
            seen_base_urls = set()
            for pattern in (method_url_pattern, curl_pattern, method_template_pattern, curl_template_pattern):
                for m in pattern.finditer(full_text):
                    url = m.group(2)
                    # Dedupe by base URL; for template URLs use normalized form (replace <...> with <>) 
                    base_key = self._extract_base_url_from_full_url(url) if "<" not in url else re.sub(r'<[^>]+>', '<>', url)
                    if base_key in seen_base_urls:
                        continue
                    seen_base_urls.add(base_key)
                    
                    start = max(0, m.start() - 60)
                    end = min(len(full_text), m.end() + 120)
                    snippet = full_text[start:end]
                    if add_snippet("example endpoint (method + full URL)", snippet):
                        if len(snippets) >= 5:
                            break
                if len(snippets) >= 5:
                    break
        
        # --- Priority 3: Any full URL with context (standard + template) ---
        if len(snippets) < 5:
            for m in re.finditer(url_pattern, full_text, re.IGNORECASE):
                url = m.group(0)
                start = max(0, m.start() - 100)
                end = min(len(full_text), m.end() + 150)
                snippet = full_text[start:end]
                if start > 0:
                    first_word = snippet.find(" ")
                    if 0 < first_word < 40:
                        snippet = snippet[first_word:].strip()
                if add_snippet("URL in documentation", snippet):
                    if len(snippets) >= 5:
                        break
            if len(snippets) < 5:
                for m in template_url_pattern.finditer(full_text):
                    url = m.group(0)
                    start = max(0, m.start() - 100)
                    end = min(len(full_text), m.end() + 150)
                    snippet = full_text[start:end]
                    if start > 0:
                        first_word = snippet.find(" ")
                        if 0 < first_word < 40:
                            snippet = snippet[first_word:].strip()
                    if add_snippet("URL in documentation (template)", snippet):
                        if len(snippets) >= 5:
                            break
        
        # --- Links (from pages): include as raw URLs only, no extracted base ---
        if links and len(snippets) < 5:
            for link in links[:5]:
                if re.match(r'^https?://', link, re.IGNORECASE):
                    # Find this link in full_text for context if possible
                    idx = full_text.find(link)
                    if idx != -1:
                        start = max(0, idx - 80)
                        end = min(len(full_text), idx + len(link) + 80)
                        snippet = full_text[start:end]
                        if add_snippet("linked URL", snippet):
                            pass
                    else:
                        if add_snippet("linked URL", f"URL: {link}"):
                            pass
                    if len(snippets) >= 5:
                        break
        
        if not snippets:
            return None
        combined = "\n\n".join(snippets)
        if len(combined) > 1500:
            combined = combined[:1500]
            last_newline = combined.rfind("\n\n")
            if last_newline > 1000:
                combined = combined[:last_newline]
        logger.debug(f"Built base URL doc fragment ({len(snippets)} snippets, length {len(combined)})")
        return combined
    
    def _extract_base_urls_from_docs(self) -> Optional[str]:
        """
        Extract documentation fragment for base URL when no servers are defined in spec.
        Uses priority: "base url:" keywords first, then example endpoints (METHOD + URL), then other URLs.
        Does NOT include any extracted "Base URL: ..." in the fragment (LLM derives from raw docs).
        """
        if not self.documentation:
            logger.debug("No documentation object available for base URL extraction")
            return None
        
        full_text = ""
        if hasattr(self.documentation, 'full_text') and self.documentation.full_text:
            full_text = self.documentation.full_text
        if not full_text or not full_text.strip():
            logger.debug("Documentation full_text is empty")
            return None
        
        links = []
        if hasattr(self.documentation, 'pages') and self.documentation.pages:
            for page in self.documentation.pages:
                if hasattr(page, 'links') and page.links:
                    links.extend(page.links)
        
        return self._build_base_url_doc_fragment(full_text, links=links or None)
    
    def _extract_url_info_from_docs(self, template_url: str, server: Dict[str, Any]) -> Optional[str]:
        """
        Extract URL information from documentation for a template URL.
        Uses same priority as _extract_base_urls_from_docs: "base url:" keywords first,
        then example endpoints (METHOD + full URL), then other URLs.
        Does NOT include any extracted "Base URL: ..." in the fragment (LLM derives from raw docs).
        
        Args:
            template_url: The template URL from the spec (e.g., "{{service-root}}")
            server: The server object from the spec
            
        Returns:
            Documentation snippet (raw excerpts only, no code-injected base URL)
        """
        if not self.documentation:
            logger.debug("No documentation object available for URL extraction")
            return None
        
        if not hasattr(self.documentation, 'full_text') or not self.documentation.full_text:
            return None
        
        full_text = self.documentation.full_text
        if not full_text or not full_text.strip():
            return None
        
        logger.debug(f"Extracting URL info from documentation (full_text length: {len(full_text)})")
        
        # Optional: if spec has server variables with a constructed URL, mention it as a hint
        # (do not present as "the" base URL from docs - LLM must verify from documentation)
        prefix = ""
        server_variables = server.get("variables", {})
        if server_variables:
            constructed_url = None
            if "protocol" in server_variables and "domain" in server_variables:
                protocol = server_variables["protocol"].get("default", "https")
                domain = server_variables["domain"].get("default", "")
                port = server_variables.get("port", {}).get("default", "")
                if domain:
                    if domain.startswith(("http://", "https://")):
                        constructed_url = domain
                        if port and port not in domain:
                            if ":" not in domain.split("://")[1].split("/")[0]:
                                constructed_url = f"{domain}:{port}"
                    else:
                        constructed_url = f"{protocol}://{domain}:{port}" if port else f"{protocol}://{domain}"
            if constructed_url:
                prefix = f"Spec server variables suggest (verify in documentation): {constructed_url}\n\n"
        
        links = []
        if hasattr(self.documentation, 'pages') and self.documentation.pages:
            for page in self.documentation.pages:
                if hasattr(page, 'links') and page.links:
                    links.extend(page.links)
        
        doc_fragment = self._build_base_url_doc_fragment(full_text, links=links or None)
        if not doc_fragment:
            logger.debug(f"No URL information found in documentation for template URL: {template_url}")
            return prefix.strip() or None
        combined = prefix + doc_fragment
        if len(combined) > 1000:
            combined = combined[:1000]
            last = combined.rfind("\n\n")
            if last > 700:
                combined = combined[:last]
        return combined

    def detect_missing_descriptions(
        self, operation: Dict[str, Any], location: str
    ) -> List[Issue]:
        """
        Detect missing descriptions from spec only (no doc parsing).
        
        LLM will generate descriptions in fixing phase based on:
        - Summary if available
        - API path and method
        - Operation context
        """
        issues = []

        # Only check if description is missing in spec
        if not operation.get("description"):
            # Extract minimal spec fragment (only relevant fields)
            minimal_fragment = extract_minimal_fragment(
                self.spec, location, IssueType.MISSING_DESCRIPTION.value
            ) or {
                "summary": operation.get("summary", ""),
                "description": operation.get("description", ""),
                "operationId": operation.get("operationId", ""),
            }
            
            issues.append(
                Issue(
                    type=IssueType.MISSING_DESCRIPTION,
                    location=location,
                    description=f"Operation {location} is missing a description",
                    severity="medium",
                    spec_fragment=minimal_fragment,
                    doc_fragment=None,  # No doc fragment - LLM will generate from spec
                )
            )

        return issues

    def detect_global_header_requirements(self) -> List[Issue]:
        """
        Detect global header requirements that affect all operations.
        Returns a single issue per missing global header with list of affected operations.
        """
        issues = []
        paths = self.spec.get("paths", {})
        
        # Collect all operation locations
        all_operations = []
        for path, path_item in paths.items():
            for method, operation in path_item.items():
                if method not in ["get", "post", "put", "delete", "patch", "options", "head"]:
                    continue
                location = f"paths.{path}.{method}"
                operation_headers = {
                    p.get("name", "").lower()
                    for p in operation.get("parameters", [])
                    if p.get("in") == "header"
                }
                all_operations.append((location, operation, operation_headers))
        
        # Check global headers - create one issue per missing global header
        # Detect version headers (like Notion-Version, API-Version, etc.) and Accept headers
        version_header_patterns = ["version", "api-version", "api_version"]
        # Also check for Accept headers that appear consistently in examples (like application/vnd.*)
        accept_header_patterns = ["accept"]
        
        for header in self.documentation.global_headers:
            header_lower = header.lower()
            # Flag version-related headers (most common and critical)
            is_version_header = any(pattern in header_lower for pattern in version_header_patterns)
            # Flag Accept headers (often required for API versioning)
            is_accept_header = any(pattern in header_lower for pattern in accept_header_patterns)
            
            if not is_version_header and not is_accept_header:
                continue  # Skip headers that aren't version or Accept headers
            
            affected_locations = []
            for location, operation, operation_headers in all_operations:
                if header.lower() not in operation_headers:
                    affected_locations.append(location)
            
            if affected_locations:
                # Get the documentation context for this header
                doc_context = self.documentation.global_header_contexts.get(header, "")
                if not doc_context:
                    # Fallback: try to find the header mention in full_text
                    header_pos = self.documentation.full_text.find(header)
                    if header_pos != -1:
                        context_start = max(0, header_pos - 200)
                        context_end = min(len(self.documentation.full_text), header_pos + len(header) + 300)
                        doc_context = self.documentation.full_text[context_start:context_end].strip()
                
                # Build doc_fragment with the actual documentation snippet
                if doc_context:
                    doc_fragment = f"Documentation mentions '{header}' header:\n\n{doc_context[:600]}"
                else:
                    doc_fragment = f"Global header requirement: {header}"
                
                # Determine header type for description
                if is_accept_header:
                    header_type = "Accept"
                elif is_version_header:
                    header_type = "Version"
                else:
                    header_type = "Required"
                
                issues.append(
                    Issue(
                        type=IssueType.MISSING_REQUIRED_HEADER,
                        location="paths",  # Global location
                        description=f"{header_type} header '{header}' is mentioned in documentation and missing from {len(affected_locations)} operation(s)",
                        severity="high",
                        spec_fragment=None,  # Global issue, no specific fragment
                        doc_fragment=doc_fragment,
                        is_global=True,
                        affected_locations=affected_locations,
                    )
                )
        
        # Note: Authorization header detection removed - only version headers are detected globally
        # Security overwriting (security: []) is handled in detect_missing_endpoint_security()
        
        return issues

    def detect_missing_required_headers(
        self, operation: Dict[str, Any], location: str, doc_context: str
    ) -> List[Issue]:
        """Detect missing required headers (operation-specific only, not global)."""
        issues = []

        operation_headers = {
            p.get("name", "").lower()
            for p in operation.get("parameters", [])
            if p.get("in") == "header"
        }

        # Skip global headers (handled in detect_global_header_requirements)
        # Only check operation-specific headers here
        
        # Check for provider-specific headers in context (operation-specific)
        doc_lower = doc_context.lower()
        # Common patterns (but skip if it's a global header)
        if "notion-version" in doc_lower and "notion-version" not in operation_headers:
            # Only flag if it's mentioned specifically for this endpoint, not globally
            if "notion-version" not in [h.lower() for h in self.documentation.global_headers]:
                issues.append(
                    Issue(
                        type=IssueType.MISSING_REQUIRED_HEADER,
                        location=location,
                        description="Notion-Version header is missing",
                        severity="medium",
                        spec_fragment=extract_minimal_fragment(
                            self.spec, location, IssueType.MISSING_REQUIRED_HEADER.value
                        ) or {"summary": operation.get("summary", "")},
                        doc_fragment="Notion-Version header required",
                    )
                )

        # Content-Type for JSON request bodies
        request_body = operation.get("requestBody")
        if request_body and "content" in request_body:
            if "application/json" in request_body["content"]:
                if "content-type" not in operation_headers:
                    issues.append(
                        Issue(
                            type=IssueType.MISSING_REQUIRED_HEADER,
                            location=location,
                            description="Content-Type header should be specified for JSON request body",
                            severity="low",
                            spec_fragment=operation,
                            doc_fragment=doc_context[:300] if doc_context else "",
                        )
                    )

        return issues

    def detect_missing_request_body_schemas(
        self, operation: Dict[str, Any], location: str, method: str, doc_context: str
    ) -> List[Issue]:
        """Detect missing request body schemas."""
        issues = []

        if method in ["post", "put", "patch"]:
            request_body = operation.get("requestBody")

            if not request_body:
                # Check if docs mention request body for this endpoint
                if any(keyword in doc_context.lower() for keyword in ["request body", "body", "payload", "data"]):
                    issues.append(
                        Issue(
                            type=IssueType.MISSING_REQUEST_BODY_SCHEMA,
                            location=location,
                            description=f"{method.upper()} operation is missing a request body but docs mention one",
                            severity="medium",
                            spec_fragment=extract_minimal_fragment(
                                self.spec, location, IssueType.MISSING_REQUEST_BODY_SCHEMA.value
                            ) or {"summary": operation.get("summary", "")},
                            doc_fragment=doc_context[:400] if doc_context else "",
                        )
                    )
            else:
                # Check if schema is defined
                content = request_body.get("content", {})
                has_schema = False
                for media_type in content.values():
                    if media_type.get("schema"):
                        has_schema = True
                        break

                if not has_schema:
                    issues.append(
                        Issue(
                            type=IssueType.MISSING_REQUEST_BODY_SCHEMA,
                            location=f"{location}.requestBody",
                            description=f"Request body is missing a schema definition",
                            severity="high",
                            spec_fragment={"content": request_body.get("content", {})} if request_body else None,
                            doc_fragment=doc_context[:400] if doc_context else "",
                        )
                    )

        return issues

    def detect_missing_query_parameters(
        self, operation: Dict[str, Any], location: str, path: str, method: str, doc_context: str
    ) -> List[Issue]:
        """Detect missing query parameters mentioned in documentation."""
        issues = []

        # Get parameters for this specific endpoint from structured docs
        endpoint_params = self.documentation.parameters.get(path, [])
        
        operation_params = {
            p.get("name", "").lower()
            for p in operation.get("parameters", [])
            if p.get("in") in ["query", "path"]
        }

        # Only check params mentioned for THIS endpoint
        for param_info in endpoint_params:
            param_name = param_info.get("name", "").lower()
            if param_name and param_name not in operation_params:
                # Verify it's actually mentioned in doc context
                if param_name in doc_context.lower():
                    issues.append(
                        Issue(
                            type=IssueType.MISSING_QUERY_PARAMETER,
                            location=location,
                            description=f"Parameter '{param_info['name']}' mentioned in documentation for this endpoint but not in spec",
                            severity="medium",
                            spec_fragment=extract_minimal_fragment(
                                self.spec, location, IssueType.MISSING_QUERY_PARAMETER.value
                            ) or {"summary": operation.get("summary", "")},
                            doc_fragment=f"Parameter: {param_info['name']}",
                        )
                    )

        return issues

    def detect_missing_endpoint_security(
        self, operation: Dict[str, Any], location: str
    ) -> List[Issue]:
        """
        Detect endpoint security overwriting.
        
        Specifically: operation has security: [] (empty array) which explicitly
        disables security, but security should be required.
        
        Uses multiple heuristics to avoid false positives:
        1. Check if docs explicitly say "all endpoints require auth"
        2. Check ratio of endpoints with/without security (if >90% have security, flag)
        3. Check operation description/tags for "public", "unauthenticated" keywords
        4. Check if other similar operations (same method/path pattern) are also public
        """
        issues = []

        global_security = self.spec.get("security", [])
        has_security_schemes = bool(
            self.spec.get("components", {}).get("securitySchemes", {})
        )

        if not has_security_schemes:
            return issues  # No security schemes defined, skip

        operation_security = operation.get("security")

        # Only check if operation explicitly disables security (security: [])
        if operation_security != []:
            return issues  # Operation has security defined or inherits from global

        # Heuristic 1: Check if documentation explicitly says all endpoints require auth
        docs_require_all_auth = False
        if self.documentation and self.documentation.full_text:
            full_text_lower = self.documentation.full_text.lower()
            # Look for explicit statements that all endpoints require auth
            all_auth_patterns = [
                r"all\s+(?:api\s+)?(?:requests|endpoints|operations|calls)\s+(?:require|need|must)\s+(?:authentication|auth|authorization)",
                r"(?:authentication|auth|authorization)\s+(?:is\s+)?(?:required|needed|mandatory)\s+(?:for\s+)?(?:all|every)\s+(?:api\s+)?(?:requests|endpoints|operations|calls)",
                r"every\s+(?:api\s+)?(?:request|endpoint|operation|call)\s+(?:requires|needs|must)\s+(?:authentication|auth|authorization)",
            ]
            import re
            for pattern in all_auth_patterns:
                if re.search(pattern, full_text_lower):
                    docs_require_all_auth = True
                    break
        
        # Heuristic 2: Calculate ratio of endpoints with security
        # Count total operations and how many have security
        total_operations = 0
        operations_with_security = 0
        operations_without_security = 0
        
        paths = self.spec.get("paths", {})
        for path, path_item in paths.items():
            for method in ["get", "post", "put", "patch", "delete", "options", "head"]:
                op = path_item.get(method, {})
                if not op:
                    continue
                
                total_operations += 1
                op_security = op.get("security")
                if op_security is None:
                    # Inherits from global - count as having security if global_security exists
                    if global_security:
                        operations_with_security += 1
                    else:
                        operations_without_security += 1
                elif op_security == []:
                    operations_without_security += 1
                else:
                    operations_with_security += 1
        
        # Calculate ratio
        security_ratio = operations_with_security / total_operations if total_operations > 0 else 0
        
        # Heuristic 3: Check operation description/tags for public/unauthenticated keywords
        operation_text = ""
        if operation.get("summary"):
            operation_text += operation.get("summary", "").lower() + " "
        if operation.get("description"):
            operation_text += operation.get("description", "").lower() + " "
        if operation.get("tags"):
            operation_text += " ".join([tag.lower() for tag in operation.get("tags", [])]) + " "
        
        is_public_operation = False
        public_keywords = [
            "public", "unauthenticated", "no auth", "no authentication", 
            "no authorization", "anonymous", "open", "free", "no token required",
            "publicly accessible", "does not require", "optional authentication"
        ]
        for keyword in public_keywords:
            if keyword in operation_text:
                is_public_operation = True
                break
        
        # Heuristic 4: Check path pattern - common public endpoints
        path_from_location = location.split("paths.")[-1].split(".")[0] if "paths." in location else ""
        common_public_paths = ["/health", "/status", "/ping", "/metrics", "/docs", "/openapi", "/swagger"]
        is_common_public_path = any(public_path in path_from_location.lower() for public_path in common_public_paths)
        
        # Decision logic: Only flag if it's likely an issue
        # Flag if:
        # 1. Docs explicitly say all endpoints require auth, OR
        # 2. High security ratio (>90%) AND not marked as public, AND not a common public path
        should_flag = False
        
        if docs_require_all_auth and not is_public_operation:
            should_flag = True
        elif security_ratio > 0.90 and not is_public_operation and not is_common_public_path:
            # High ratio suggests most endpoints require auth, so this one probably should too
            should_flag = True
        elif global_security and security_ratio > 0.95:
            # Very high ratio + global security = likely all should have security
            should_flag = True
        
        if should_flag:
            # Build spec_fragment with global security info so LLM knows what to use
            spec_fragment = extract_minimal_fragment(
                self.spec, location, IssueType.MISSING_ENDPOINT_SECURITY.value
            ) or {
                "summary": operation.get("summary", ""),
                "security": operation.get("security", []),
            }
            
            # Add global security information so LLM can use the correct scheme names
            if global_security:
                spec_fragment["global_security"] = global_security
            
            # Also include security scheme names if available
            security_schemes = self.spec.get("components", {}).get("securitySchemes", {})
            if security_schemes:
                spec_fragment["available_security_schemes"] = list(security_schemes.keys())
            
            # Add context about why this was flagged
            reason_parts = []
            if docs_require_all_auth:
                reason_parts.append("documentation states all endpoints require authentication")
            if security_ratio > 0.90:
                reason_parts.append(f"{int(security_ratio * 100)}% of endpoints require security")
            
            description = f"Operation explicitly disables security (security: [])"
            if reason_parts:
                description += f" but {' and '.join(reason_parts)}"
            
            issues.append(
                Issue(
                    type=IssueType.MISSING_ENDPOINT_SECURITY,
                    location=location,
                    description=description,
                    severity="high",
                    spec_fragment=spec_fragment,
                    doc_fragment="Security is required for all requests" if docs_require_all_auth else None,
                )
            )

        return issues
    
    def detect_parameter_type_mismatches(
        self, operation: Dict[str, Any], location: str, path: str, doc_context: str
    ) -> List[Issue]:
        """
        Detect parameter type mismatches when there's strong evidence.
        Only flags when documentation clearly specifies a type that conflicts with spec.
        """
        issues = []
        
        if not doc_context:
            return issues
        
        # Get parameters from operation
        parameters = operation.get("parameters", [])
        if not parameters:
            return issues
        
        # Type keywords to look for in documentation
        type_patterns = {
            "integer": r'\b(integer|int|number|numeric|whole\s+number)\b',
            "string": r'\b(string|text|characters?|alphanumeric)\b',
            "boolean": r'\b(boolean|bool|true/false|yes/no)\b',
            "array": r'\b(array|list|collection|multiple)\b',
            "object": r'\b(object|json|dictionary|map)\b',
        }
        
        doc_lower = doc_context.lower()
        
        for param in parameters:
            param_name = param.get("name", "").lower()
            param_schema = param.get("schema", {})
            param_type = param_schema.get("type", "") if param_schema else param.get("type", "")
            
            if not param_name or not param_type:
                continue
            
            # Look for parameter mention in documentation
            if param_name not in doc_lower:
                continue
            
            # Extract context around parameter mention
            param_idx = doc_lower.find(param_name)
            param_context = doc_context[max(0, param_idx - 100):param_idx + len(param_name) + 200].lower()
            
            # Only flag if documentation EXPLICITLY mentions a type that conflicts
            # Be very conservative - only flag when docs explicitly state the type (e.g., "param_name integer" or "param_name: integer")
            # Don't rely on parameter name patterns or inferred types
            
            # Look for explicit type mentions in documentation near the parameter name
            # Pattern: "param_name type" or "param_name: type" or "type: type_name" near param_name
            escaped_param_name = re.escape(param_name)
            
            for doc_type, doc_pattern in type_patterns.items():
                if doc_type == param_type:
                    continue  # Types match, no issue
                
                # Skip if this looks like a return type (appears after "returns", "response", etc.)
                type_match = re.search(doc_pattern, param_context, re.IGNORECASE)
                if type_match:
                    type_pos = type_match.start()
                    before_type = param_context[:type_pos].lower()
                    if any(indicator in before_type for indicator in ["returns", "response", "response body", "response type", "returns the", "returns a"]):
                        continue  # Skip - this is a return type, not parameter type
                
                # Look for explicit type specification patterns:
                # 1. "param_name type" (e.g., "completion_id string")
                # 2. "param_name: type" (e.g., "completion_id: string")
                # 3. "type: type_name" near param_name (e.g., "completion_id ... type: string")
                explicit_patterns = [
                    # Pattern 1: param_name followed by type word (most common in API docs)
                    rf'\b{escaped_param_name}\s+({doc_type}|{doc_pattern})\b',
                    # Pattern 2: param_name: type
                    rf'\b{escaped_param_name}\s*:\s*({doc_type}|{doc_pattern})\b',
                    # Pattern 3: type: type_name near param_name (within 50 chars)
                    rf'\b{escaped_param_name}.*?type[:\s]+({doc_type}|{doc_pattern})\b',
                ]
                
                for explicit_pattern in explicit_patterns:
                    try:
                        if re.search(explicit_pattern, param_context, re.IGNORECASE):
                            # Found explicit type mention that conflicts with spec
                            # Double-check: make sure the type word is actually referring to this parameter
                            # and not another parameter or return value
                            match = re.search(explicit_pattern, param_context, re.IGNORECASE)
                            if match:
                                # Verify the type mention is close to the parameter name (within 30 chars)
                                param_pos = param_context.find(param_name)
                                type_mention_pos = match.start()
                                distance = abs(type_mention_pos - param_pos)
                                
                                if distance <= 50:  # Type mention is close to parameter name
                                    issues.append(
                                        Issue(
                                            type=IssueType.WRONG_PARAMETER_TYPE,
                                            location=f"{location}.parameters[{parameters.index(param)}]",
                                            description=f"Parameter '{param.get('name')}' type mismatch: documentation explicitly states '{doc_type}' but spec has '{param_type}'",
                                            severity="medium",
                                            spec_fragment=extract_minimal_fragment(
                                                self.spec, f"{location}.parameters[{parameters.index(param)}]", IssueType.WRONG_PARAMETER_TYPE.value
                                            ) or {
                                                "name": param.get("name"),
                                                "type": param_type,
                                                "schema": param_schema,
                                            },
                                            doc_fragment=self._extract_param_snippet(doc_context, param.get("name"), max_length=300),
                                        )
                                    )
                                    break  # Only flag once per parameter
                    except re.error:
                        # Skip if regex pattern is invalid
                        continue
                else:
                    continue  # No match for this doc_type, try next
                break  # Found a match, stop checking other types
        
        return issues

    def detect_missing_examples(
        self, operation: Dict[str, Any], location: str, doc_context: str
    ) -> List[Issue]:
        """Detect missing examples in request/response schemas."""
        issues = []

        # Check request body examples
        request_body = operation.get("requestBody")
        if request_body:
            has_example = False
            content = request_body.get("content", {})
            for media_type in content.values():
                if media_type.get("example") or media_type.get("examples"):
                    has_example = True
                    break

            if not has_example:
                # Only flag if docs show examples
                if any(keyword in doc_context.lower() for keyword in ["example", "sample", "{"]):
                    issues.append(
                        Issue(
                            type=IssueType.MISSING_EXAMPLE,
                            location=f"{location}.requestBody",
                            description="Request body is missing examples",
                            severity="medium",
                            spec_fragment={"content": request_body.get("content", {})} if request_body else None,
                            doc_fragment=doc_context[:400] if doc_context else "",
                        )
                    )

        # Check response examples
        responses = operation.get("responses", {})
        for status_code, response in responses.items():
            has_example = False
            content = response.get("content", {})
            for media_type in content.values():
                if media_type.get("example") or media_type.get("examples"):
                    has_example = True
                    break

            if not has_example and status_code.startswith("2"):
                issues.append(
                    Issue(
                        type=IssueType.MISSING_EXAMPLE,
                        location=f"{location}.responses.{status_code}",
                        description=f"Response {status_code} is missing examples",
                        severity="low",
                        spec_fragment={"description": response.get("description", ""), "content": response.get("content", {})},
                        doc_fragment=doc_context[:400] if doc_context else "",
                    )
                )

        return issues

