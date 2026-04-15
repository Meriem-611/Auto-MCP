"""
Patch application for OpenAPI specifications.

Applies LLM-generated fixes to the original specification.
"""

import copy
from typing import Any, Dict, List

from specfix.detection.issues import Issue
from specfix.utils.logger import get_logger

logger = get_logger(__name__)


class PatchError(Exception):
    """Raised when patch application fails."""
    pass


class SpecPatcher:
    """
    Applies fixes to OpenAPI specifications.
    
    Takes LLM-generated fixes and merges them into the original spec
    while preserving all working parts.
    """

    def __init__(self, spec: Dict[str, Any]):
        """
        Initialize the patcher with a spec.
        
        Args:
            spec: Original OpenAPI specification
        """
        self.original_spec = spec
        self.patched_spec = copy.deepcopy(spec)

    def apply_fix(self, issue: Issue) -> bool:
        """
        Apply a fix to the patched spec.
        
        For global issues, applies the fix to all affected locations.
        
        Args:
            issue: Issue with suggested_fix populated
        
        Returns:
            True if fix was applied successfully, False otherwise
        """
        if not issue.suggested_fix:
            logger.warning(f"No suggested fix for issue at {issue.location}")
            return False

        try:
            if issue.is_global and issue.affected_locations:
                # Apply fix to all affected locations
                success_count = 0
                for location in issue.affected_locations:
                    try:
                        location_parts = location.split(".")
                        self._apply_fix_at_location(
                            self.patched_spec, location_parts, issue.suggested_fix, issue.type
                        )
                        success_count += 1
                    except Exception as e:
                        logger.warning(f"Failed to apply fix at {location}: {e}")
                
                if success_count > 0:
                    logger.info(f"Applied global fix to {success_count}/{len(issue.affected_locations)} locations")
                    return True
                return False
            else:
                # Apply fix to single location
                location_parts = issue.location.split(".")
                self._apply_fix_at_location(
                    self.patched_spec, location_parts, issue.suggested_fix, issue.type
                )
                logger.info(f"Applied fix for {issue.location}")
                return True
        except Exception as e:
            logger.error(f"Failed to apply fix at {issue.location}: {e}")
            return False

    def _apply_fix_at_location(
        self,
        spec: Dict[str, Any],
        location_parts: List[str],
        fix: Dict[str, Any],
        issue_type: Any,
    ) -> None:
        """
        Apply fix at a specific location in the spec.
        
        Args:
            spec: Current spec dictionary (modified in place)
            location_parts: List of location path components
            fix: Fix to apply
            issue_type: Type of issue (for context)
        """
        if not location_parts:
            # Merge fix into current location
            spec.update(fix)
            return

        current_key = location_parts[0]

        # Handle array indices like "parameters[0]"
        if "[" in current_key and "]" in current_key:
            key, index_str = current_key.split("[")
            index = int(index_str.rstrip("]"))
            if key not in spec:
                raise PatchError(f"Key '{key}' not found in spec")
            if not isinstance(spec[key], list):
                raise PatchError(f"'{key}' is not a list")
            if index >= len(spec[key]):
                raise PatchError(f"Index {index} out of range for '{key}'")
            self._apply_fix_at_location(
                spec[key][index], location_parts[1:], fix, issue_type
            )
        else:
            if current_key not in spec:
                # Create missing path
                if len(location_parts) == 1:
                    spec[current_key] = fix
                else:
                    spec[current_key] = {}
                    self._apply_fix_at_location(
                        spec[current_key], location_parts[1:], fix, issue_type
                    )
            else:
                if len(location_parts) == 1:
                    # Merge at final location
                    if isinstance(spec[current_key], dict) and isinstance(fix, dict):
                        spec[current_key].update(fix)
                    else:
                        spec[current_key] = fix
                else:
                    self._apply_fix_at_location(
                        spec[current_key], location_parts[1:], fix, issue_type
                    )

    def get_patched_spec(self) -> Dict[str, Any]:
        """
        Get the patched specification.
        
        Returns:
            Patched OpenAPI specification
        """
        return self.patched_spec

    def apply_all_fixes(self, issues: List[Issue]) -> int:
        """
        Apply all fixes from a list of issues.
        
        Args:
            issues: List of issues with suggested_fix populated
        
        Returns:
            Number of successfully applied fixes
        """
        applied_count = 0
        for issue in issues:
            if self.apply_fix(issue):
                applied_count += 1

        logger.info(f"Applied {applied_count} out of {len(issues)} fixes")
        return applied_count

