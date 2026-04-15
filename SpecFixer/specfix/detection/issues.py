"""
Issue representation for detected inconsistencies.

Defines dataclasses for representing issues found in OpenAPI specifications.
Enhanced with validation fields for LLM validation phase.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class IssueType(Enum):
    """Types of issues that can be detected."""

    MISSING_DESCRIPTION = "missing_description"
    MISSING_AUTH_DOC = "missing_auth_documentation"
    MISSING_REQUIRED_HEADER = "missing_required_header"
    MISSING_EXAMPLE = "missing_example"
    MISSING_REQUEST_BODY_SCHEMA = "missing_request_body_schema"
    WRONG_PARAMETER_TYPE = "wrong_parameter_type"
    MISSING_FIELD = "missing_field"
    MISSING_ENDPOINT_SECURITY = "missing_endpoint_security"
    MISSING_QUERY_PARAMETER = "missing_query_parameter"
    MALFORMED_BASE_URL = "malformed_base_url"
    MISSING_RESPONSE_SCHEMA = "missing_response_schema"
    CUSTOM_AUTH_PREFIX = "custom_auth_prefix"


@dataclass
class Issue:
    """
    Represents a detected inconsistency in an OpenAPI specification.
    
    Enhanced with validation fields for LLM validation phase.
    
    Attributes:
        id: Unique identifier for the issue
        type: Type of issue
        location: Path to the location in the spec (e.g., "paths./users.get.parameters[0]")
        description: Human-readable description of the issue
        severity: Severity level (low, medium, high)
        spec_fragment: Relevant fragment from the spec
        doc_fragment: Relevant fragment from documentation (extracted description text or focused snippet)
        is_global: Whether this issue affects all operations (e.g., missing global header)
        affected_locations: List of locations affected by this global issue (only if is_global=True)
        is_validated: Whether this issue has been validated by LLM
        confidence: Confidence score from LLM validation (0.0-1.0)
        validation_reasoning: LLM's reasoning for validation decision
        suggested_fix_reasoning: Reasoning for the suggested fix
        suggested_fix: Suggested fix (optional, generated in fixing phase)
    """

    type: IssueType
    location: str
    description: str
    severity: str = "medium"  # low, medium, high
    spec_fragment: Optional[Dict[str, Any]] = None
    doc_fragment: Optional[str] = None  # Extracted description text or focused snippet
    is_global: bool = False  # True if this issue affects all/multiple operations
    affected_locations: List[str] = field(default_factory=list)  # Locations affected by global issue
    is_validated: bool = False  # Set to True after LLM validation
    confidence: float = 0.0  # Confidence score from validation
    validation_reasoning: str = ""  # LLM's reasoning
    suggested_fix_reasoning: str = ""
    suggested_fix: Optional[Dict[str, Any]] = None
    id: Optional[str] = None  # Unique ID (generated if not provided)

    def __post_init__(self):
        """Generate ID if not provided."""
        if self.id is None:
            import hashlib
            id_str = f"{self.type.value}:{self.location}:{self.description}"
            self.id = hashlib.md5(id_str.encode()).hexdigest()[:8]

    def to_dict(self, include_validation_fields: bool = False) -> Dict[str, Any]:
        """
        Convert issue to dictionary for serialization.
        
        Args:
            include_validation_fields: If True, include validation/fix fields even if empty.
                                     If False (default), only include them if they have values.
        """
        result = {
            "id": self.id,
            "type": self.type.value,
            "location": self.location,
            "description": self.description,
            "severity": self.severity,
            "spec_fragment": self.spec_fragment,
            "doc_fragment": self.doc_fragment,
        }
        
        # Include global issue fields
        if self.is_global:
            result["is_global"] = True
            if self.affected_locations:
                result["affected_locations"] = self.affected_locations
        
        # Only include validation/fix fields if they have values or explicitly requested
        if include_validation_fields or self.is_validated:
            result["is_validated"] = self.is_validated
        if include_validation_fields or self.confidence > 0.0:
            result["confidence"] = self.confidence
        if include_validation_fields or self.validation_reasoning:
            result["validation_reasoning"] = self.validation_reasoning
        if include_validation_fields or self.suggested_fix_reasoning:
            result["suggested_fix_reasoning"] = self.suggested_fix_reasoning
        if include_validation_fields or self.suggested_fix is not None:
            result["suggested_fix"] = self.suggested_fix
        
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Issue":
        """Create issue from dictionary."""
        return cls(
            id=data.get("id"),
            type=IssueType(data["type"]),
            location=data["location"],
            description=data["description"],
            severity=data.get("severity", "medium"),
            spec_fragment=data.get("spec_fragment"),
            doc_fragment=data.get("doc_fragment"),
            is_global=data.get("is_global", False),
            affected_locations=data.get("affected_locations", []),
            is_validated=data.get("is_validated", False),
            confidence=data.get("confidence", 0.0),
            validation_reasoning=data.get("validation_reasoning", ""),
            suggested_fix_reasoning=data.get("suggested_fix_reasoning", ""),
            suggested_fix=data.get("suggested_fix"),
        )


@dataclass
class IssueReport:
    """
    Collection of issues found during analysis.
    
    Can be serialized to JSON for intermediate storage.
    
    Attributes:
        issues: List of detected issues
        total_count: Total number of issues
        by_severity: Count of issues by severity level
        metadata: Additional metadata about the report
    """

    issues: List[Issue] = field(default_factory=list)
    total_count: int = 0
    by_severity: Dict[str, int] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_issue(self, issue: Issue) -> None:
        """Add an issue to the report."""
        self.issues.append(issue)
        self.total_count += 1
        self.by_severity[issue.severity] = self.by_severity.get(
            issue.severity, 0
        ) + 1

    def get_issues_by_type(self, issue_type: IssueType) -> List[Issue]:
        """Get all issues of a specific type."""
        return [issue for issue in self.issues if issue.type == issue_type]

    def get_issues_by_severity(self, severity: str) -> List[Issue]:
        """Get all issues of a specific severity."""
        return [issue for issue in self.issues if issue.severity == severity]

    def get_validated_issues(self) -> List[Issue]:
        """Get all validated issues."""
        return [issue for issue in self.issues if issue.is_validated]

    def to_dict(self, include_validation_fields: bool = False) -> Dict[str, Any]:
        """
        Convert report to dictionary for serialization.
        
        Groups issues by: global issues first, then operation-specific issues grouped by operation.
        
        Args:
            include_validation_fields: If True, include validation/fix fields in issues even if empty.
        """
        # Separate global and operation-specific issues
        global_issues = [issue for issue in self.issues if issue.is_global]
        operation_issues = [issue for issue in self.issues if not issue.is_global]
        
        # Group operation issues by location (operation path)
        operation_groups: Dict[str, List[Issue]] = {}
        for issue in operation_issues:
            # Extract operation path (e.g., "paths./users.get" -> "paths./users.get")
            op_path = issue.location
            if op_path not in operation_groups:
                operation_groups[op_path] = []
            operation_groups[op_path].append(issue)
        
        # Build structured output
        result = {
            "total_count": self.total_count,
            "by_severity": self.by_severity,
            "metadata": self.metadata,
            "global_issues": [issue.to_dict(include_validation_fields=include_validation_fields) for issue in global_issues],
            "operation_issues": {
                op_path: [issue.to_dict(include_validation_fields=include_validation_fields) for issue in issues]
                for op_path, issues in operation_groups.items()
            },
            # Keep flat list for backwards compatibility
            "issues": [issue.to_dict(include_validation_fields=include_validation_fields) for issue in self.issues],
        }
        
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IssueReport":
        """Create report from dictionary."""
        report = cls(
            total_count=data.get("total_count", 0),
            by_severity=data.get("by_severity", {}),
            metadata=data.get("metadata", {}),
        )
        for issue_data in data.get("issues", []):
            report.add_issue(Issue.from_dict(issue_data))
        return report

    def save_to_file(self, filepath: str) -> None:
        """Save report to JSON file."""
        import json
        from pathlib import Path
        
        path = Path(filepath)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load_from_file(cls, filepath: str) -> "IssueReport":
        """Load report from JSON file."""
        import json
        from pathlib import Path
        
        path = Path(filepath)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data)

