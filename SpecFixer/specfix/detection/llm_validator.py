"""
LLM-based issue validator.

Validates potential issues detected by heuristics using LLM to reduce false positives.
"""

import json
import os
import re
from typing import Any, Dict, List, Optional, Union

import yaml

from specfix.detection.issues import Issue
from specfix.extraction.structured_docs import StructuredDocumentation
from specfix.utils.logger import get_logger

logger = get_logger(__name__)

# Try to import both clients
try:
    from openai import AzureOpenAI, OpenAI
except ImportError:
    try:
        from openai import OpenAI
        AzureOpenAI = None
    except ImportError:
        OpenAI = None
        AzureOpenAI = None


class LLMValidationError(Exception):
    """Raised when LLM validation fails."""
    pass


class LLMValidator:
    """
    Uses LLM to validate potential issues detected by heuristics.
    
    For each potential issue, provides spec fragment + doc snippet to LLM
    and asks it to validate whether it's a real issue, extract details,
    and provide confidence score.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gpt-4o-mini",
        azure_endpoint: Optional[str] = None,
        api_version: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        """
        Initialize the validator.
        
        Supports both Azure OpenAI and regular OpenAI API.
        Auto-detects which to use based on provided parameters.
        
        Args:
            api_key: API key (defaults to OPENAI_API_KEY or AZURE_OPENAI_API_KEY env var)
            model: Model name to use
            azure_endpoint: Azure OpenAI endpoint URL (if provided, uses Azure)
            api_version: Azure API version (required for Azure, default: 2025-01-01-preview)
            base_url: Custom base URL for OpenAI-compatible APIs
        """
        # Determine which API to use
        azure_endpoint = azure_endpoint or os.getenv("AZURE_OPENAI_ENDPOINT")
        api_key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("AZURE_OPENAI_API_KEY")
        
        self.model = model
        self.api_key = api_key
        self.azure_endpoint = azure_endpoint
        self.api_version = api_version or "2025-01-01-preview"
        self.base_url = base_url
        self.use_azure = bool(azure_endpoint)

        if not api_key:
            logger.warning(
                "No API key provided. LLM validation will not work. "
                "Set OPENAI_API_KEY or AZURE_OPENAI_API_KEY environment variable or pass api_key parameter."
            )
            self.client = None
        elif self.use_azure:
            # Use Azure OpenAI
            if not AzureOpenAI:
                raise ImportError("Azure OpenAI client not available. Install openai package.")
            if not azure_endpoint:
                logger.warning(
                    "Azure endpoint not provided but Azure mode detected. "
                    "Set AZURE_OPENAI_ENDPOINT environment variable or pass azure_endpoint parameter."
                )
                self.client = None
            else:
                self.client = AzureOpenAI(
                    api_key=self.api_key,
                    api_version=self.api_version,
                    azure_endpoint=self.azure_endpoint,
                )
                logger.info("Using Azure OpenAI client")
        else:
            # Use regular OpenAI
            if not OpenAI:
                raise ImportError("OpenAI client not available. Install openai package.")
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )
            logger.info("Using regular OpenAI client")

    def validate_issues(
        self,
        issues: List[Issue],
        spec: Dict[str, Any],
        documentation: StructuredDocumentation,
        spec_format: str = "yaml",
    ) -> List[Issue]:
        """
        Validate a list of potential issues using LLM.
        
        Args:
            issues: List of potential issues to validate
            spec: Full OpenAPI specification
            documentation: Structured documentation
            spec_format: Format of the spec ('yaml' or 'json')
        
        Returns:
            List of validated issues (with is_validated=True, confidence, reasoning)
        """
        if not self.client:
            logger.warning("LLM client not initialized. Skipping validation.")
            return issues

        validated_issues = []
        
        for issue in issues:
            try:
                validated = self.validate_issue(issue, spec, documentation, spec_format)
                if validated:
                    validated_issues.append(validated)
                else:
                    logger.info(f"Issue {issue.id} filtered out by LLM validation")
            except Exception as e:
                logger.error(f"Failed to validate issue {issue.id}: {e}")
                # Keep the issue if validation fails
                validated_issues.append(issue)

        logger.info(f"Validated {len(validated_issues)} out of {len(issues)} issues")
        return validated_issues

    def validate_issue(
        self,
        issue: Issue,
        spec: Dict[str, Any],
        documentation: StructuredDocumentation,
        spec_format: str = "yaml",
    ) -> Optional[Issue]:
        """
        Validate a single issue using LLM.
        
        Args:
            issue: Issue to validate
            spec: Full OpenAPI specification
            documentation: Structured documentation
            spec_format: Format of the spec
        
        Returns:
            Validated issue with confidence and reasoning, or None if not a real issue
        """
        if not self.client:
            return issue

        logger.info(f"Validating issue: {issue.type.value} at {issue.location}")

        try:
            prompt = self._build_validation_prompt(issue, spec, documentation, spec_format)
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You must return strictly valid JSON. No markdown or explanations.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=1200,
            )

            response_text = response.choices[0].message.content.strip()
            validation_result = self._parse_validation_response(response_text)

            if validation_result and validation_result.get("is_valid", False):
                # Update issue with validation results
                issue.is_validated = True
                issue.confidence = validation_result.get("confidence", 0.5)
                issue.validation_reasoning = validation_result.get("reasoning", "")
                
                # Update severity if LLM suggests different
                if "severity" in validation_result:
                    issue.severity = validation_result["severity"]
                
                # Enhance doc_fragment if LLM provides better context
                if "doc_snippet" in validation_result:
                    issue.doc_fragment = validation_result["doc_snippet"]
                
                logger.info(f"Issue {issue.id} validated with confidence {issue.confidence:.2f}")
                return issue
            else:
                logger.info(f"Issue {issue.id} filtered out (not a real issue)")
                return None

        except Exception as e:
            logger.error(f"LLM validation failed for issue {issue.id}: {e}")
            # Return issue unvalidated if validation fails
            return issue

    def _build_validation_prompt(
        self,
        issue: Issue,
        spec: Dict[str, Any],
        documentation: StructuredDocumentation,
        spec_format: str,
    ) -> str:
        """Build the validation prompt."""
        prompt_parts = [
            "Validate whether this is a real issue that needs fixing:",
            "",
            f"Issue Type: {issue.type.value}",
            f"Location: {issue.location}",
            f"Description: {issue.description}",
            "",
            "Spec Fragment:",
        ]

        # Add spec fragment
        if issue.spec_fragment:
            if spec_format == "yaml":
                prompt_parts.append(yaml.dump(issue.spec_fragment, default_flow_style=False))
            else:
                prompt_parts.append(json.dumps(issue.spec_fragment, indent=2))
        else:
            prompt_parts.append("(No fragment available)")

        # Add documentation context
        prompt_parts.append("")
        prompt_parts.append("Documentation Context:")
        
        if issue.doc_fragment:
            prompt_parts.append(issue.doc_fragment[:800])  # Limit context
        else:
            prompt_parts.append("(No documentation context available)")

        prompt_parts.extend([
            "",
            "Please analyze and respond in JSON format:",
            "{",
            '  "is_valid": true/false,  // Is this a real issue?',
            '  "confidence": 0.0-1.0,   // How confident are you?',
            '  "reasoning": "...",      // Brief explanation',
            '  "severity": "low/medium/high",  // Optional: suggested severity',
            '  "doc_snippet": "..."     // Optional: better doc snippet if available',
            "}",
            "",
            "Be conservative - only mark as valid if it's clearly a real issue.",
        ])

        return "\n".join(prompt_parts)

    def _parse_validation_response(self, response_text: str) -> Optional[Dict[str, Any]]:
        """Parse LLM validation response using JSON extraction pattern."""
        # Clean response text
        text = response_text.strip().replace("```json", "").replace("```", "")
        
        # Try to find JSON object using regex (same pattern as example)
        match = re.search(r'(\[.*\]|\{.*\})', text, re.DOTALL)
        if match:
            json_str = match.group(1)
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                try:
                    # Try fixing common issues
                    json_str_fixed = json_str.replace("\n", " ").replace("\r", "")
                    return json.loads(json_str_fixed)
                except Exception as e:
                    logger.warning(f"Failed to parse JSON after fixing: {e}")
                    logger.debug(f"JSON string: {json_str[:200]}")
        
        # Fallback: try to infer from text
        if "is_valid" in text.lower() or "valid" in text.lower():
            if "true" in text.lower() or "yes" in text.lower():
                return {"is_valid": True, "confidence": 0.5, "reasoning": text}
            elif "false" in text.lower() or "no" in text.lower():
                return {"is_valid": False, "confidence": 0.5, "reasoning": text}

        logger.warning(f"Could not extract JSON from validation response")
        logger.debug(f"Response text: {response_text[:500]}")
        return None


def create_llm_validator(
    api_key: Optional[str] = None,
    model: str = "gpt-4o-mini",
    azure_endpoint: Optional[str] = None,
    api_version: Optional[str] = None,
    base_url: Optional[str] = None,
) -> LLMValidator:
    """
    Factory function to create an LLM validator.
    
    Supports both Azure OpenAI and regular OpenAI API.
    Auto-detects based on azure_endpoint parameter.
    
    Args:
        api_key: API key (defaults to OPENAI_API_KEY or AZURE_OPENAI_API_KEY env var)
        model: Model name
        azure_endpoint: Azure OpenAI endpoint URL (if provided, uses Azure)
        api_version: Azure API version (required for Azure)
        base_url: Custom base URL for OpenAI-compatible APIs
    
    Returns:
        LLMValidator instance
    """
    return LLMValidator(
        api_key=api_key,
        model=model,
        azure_endpoint=azure_endpoint,
        api_version=api_version,
        base_url=base_url,
    )

