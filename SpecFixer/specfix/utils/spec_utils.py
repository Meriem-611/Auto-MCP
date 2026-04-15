"""
Utilities for working with OpenAPI spec fragments.
"""

import re
from typing import Any, Dict, Optional


def extract_fragment_from_location(spec: Dict[str, Any], location: str) -> Optional[Dict[str, Any]]:
    """
    Extract a spec fragment from a location path.
    
    Examples:
        - "paths./users.get" -> operation object
        - "paths./users.get.parameters[0]" -> parameter object
        - "paths./users.get.requestBody" -> request body object
        - "components.securitySchemes" -> security schemes
    
    Args:
        spec: Full OpenAPI spec
        location: Location path (e.g., "paths./users.get.parameters[0]")
    
    Returns:
        Fragment at the location, or None if not found
    """
    if not location:
        return None
    
    parts = location.split(".")
    current = spec
    
    for part in parts:
        if not part:
            continue
            
        # Handle array indices like "parameters[0]"
        if "[" in part and "]" in part:
            match = re.match(r"^(\w+)\[(\d+)\]$", part)
            if match:
                key = match.group(1)
                index = int(match.group(2))
                if key in current and isinstance(current[key], list):
                    if index < len(current[key]):
                        current = current[key][index]
                    else:
                        return None
                else:
                    return None
            else:
                return None
        else:
            if part in current:
                current = current[part]
            else:
                return None
    
    return current if isinstance(current, dict) else None


def extract_minimal_fragment(spec: Dict[str, Any], location: str, issue_type: str) -> Optional[Dict[str, Any]]:
    """
    Extract a minimal, focused fragment based on issue type.
    
    Only includes relevant fields for the specific issue type,
    reducing storage and token usage.
    
    Args:
        spec: Full OpenAPI spec
        location: Location path
        issue_type: Type of issue (e.g., "missing_description")
    
    Returns:
        Minimal fragment with only relevant fields
    """
    fragment = extract_fragment_from_location(spec, location)
    if not fragment:
        return None
    
    # For operation-level issues, extract minimal operation info
    if location.startswith("paths.") and "." in location:
        parts = location.split(".")
        if len(parts) >= 3:  # paths./users.get
            # This is an operation
            minimal = {}
            
            # Always include these for context
            if "summary" in fragment:
                minimal["summary"] = fragment["summary"]
            if "operationId" in fragment:
                minimal["operationId"] = fragment["operationId"]
            
            # Include only relevant parts based on issue type
            if issue_type in ["missing_description"]:
                minimal["description"] = fragment.get("description", "")
            
            if issue_type in ["missing_required_header", "missing_endpoint_security"]:
                if "parameters" in fragment:
                    # Only include header parameters
                    minimal["parameters"] = [
                        p for p in fragment.get("parameters", [])
                        if p.get("in") == "header"
                    ]
                if "security" in fragment:
                    minimal["security"] = fragment["security"]
            
            if issue_type in ["missing_query_parameter"]:
                if "parameters" in fragment:
                    # Only include query parameters
                    minimal["parameters"] = [
                        p for p in fragment.get("parameters", [])
                        if p.get("in") == "query"
                    ]
            
            if issue_type in ["missing_request_body_schema", "missing_example"]:
                if "requestBody" in fragment:
                    minimal["requestBody"] = fragment["requestBody"]
            
            if issue_type in ["missing_response_schema"]:
                if "responses" in fragment:
                    # Only include response codes, not full content
                    minimal["responses"] = {
                        code: {"description": resp.get("description", "")}
                        for code, resp in fragment.get("responses", {}).items()
                    }
            
            return minimal if minimal else fragment
    
    # For parameter-level issues
    if "parameters[" in location:
        # Return the parameter as-is (already minimal)
        return fragment
    
    # For other locations, return as-is but limit depth
    return _limit_fragment_depth(fragment, max_depth=2)


def _limit_fragment_depth(fragment: Dict[str, Any], max_depth: int = 2) -> Dict[str, Any]:
    """
    Limit the depth of a fragment to reduce size.
    
    Args:
        fragment: Fragment to limit
        max_depth: Maximum nesting depth
    
    Returns:
        Limited fragment
    """
    if max_depth <= 0:
        return {}
    
    limited = {}
    for key, value in fragment.items():
        if isinstance(value, dict):
            limited[key] = _limit_fragment_depth(value, max_depth - 1)
        elif isinstance(value, list):
            # Limit list to first 3 items
            limited[key] = [
                _limit_fragment_depth(item, max_depth - 1) if isinstance(item, dict) else item
                for item in value[:3]
            ]
        else:
            limited[key] = value
    
    return limited

