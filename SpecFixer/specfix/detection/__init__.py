"""
Issue detection module.

Handles heuristic-based detection and LLM validation of issues.
"""

from specfix.detection.heuristic_detector import HeuristicDetector
from specfix.detection.llm_validator import LLMValidator
from specfix.detection.issues import Issue, IssueReport, IssueType

__all__ = ["HeuristicDetector", "LLMValidator", "Issue", "IssueReport", "IssueType"]

