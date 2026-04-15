"""
LLM-based fix generator.

Reads validated issues from issues.json and generates fixes for each one.
"""

import json
import os
import re
from typing import Any, Dict, List, Optional

import yaml

from specfix.detection.issues import Issue, IssueReport
from specfix.utils.logger import get_logger
from specfix.utils.spec_utils import extract_fragment_from_location

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


class LLMFixError(Exception):
    """Raised when LLM fix generation fails."""
    pass


class LLMFixer:
    """
    Uses LLM to generate fixes for validated issues.
    
    Reads issues from IssueReport (typically loaded from issues.json)
    and generates minimal fix fragments for each issue.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gpt-4.1",
        azure_endpoint: Optional[str] = None,
        api_version: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        """
        Initialize the fixer.
        
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
                "No API key provided. LLM fixing will not work. "
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
                logger.info(f"[INIT] Initializing Azure OpenAI client (endpoint: {self.azure_endpoint}, model: {self.model}, api_version: {self.api_version})")
                logger.info(f"[INIT] API key provided: {'Yes' if self.api_key else 'No'} (length: {len(self.api_key) if self.api_key else 0})")
                try:
                    self.client = AzureOpenAI(
                        api_key=self.api_key,
                        api_version=self.api_version,
                        azure_endpoint=self.azure_endpoint,
                    )
                    logger.info("[INIT] Azure OpenAI client initialized successfully")
                except Exception as e:
                    logger.error(f"[INIT] Failed to initialize Azure OpenAI client: {type(e).__name__}: {e}")
                    import traceback
                    logger.debug(f"[INIT] Full traceback: {traceback.format_exc()}")
                    self.client = None
                    raise
        else:
            # Use regular OpenAI
            if not OpenAI:
                raise ImportError("OpenAI client not available. Install openai package.")
            logger.info(f"[INIT] Initializing OpenAI client (model: {self.model})")
            logger.info(f"[INIT] API key provided: {'Yes' if self.api_key else 'No'} (length: {len(self.api_key) if self.api_key else 0})")
            try:
                self.client = OpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url,
                )
                logger.info("[INIT] OpenAI client initialized successfully")
            except Exception as e:
                logger.error(f"[INIT] Failed to initialize OpenAI client: {type(e).__name__}: {e}")
                import traceback
                logger.debug(f"[INIT] Full traceback: {traceback.format_exc()}")
                self.client = None
                raise

    def generate_fixes(
        self,
        issues: List[Issue],
        spec_format: str = "yaml",
        max_fixes: Optional[int] = None,
        save_llm_io: Optional[str] = None,
        spec: Optional[Dict[str, Any]] = None,
    ) -> List[Issue]:
        """
        Generate fixes for issues, handling global issues efficiently.
        
        For global issues (is_global=True), only one LLM call is made per global issue,
        and the fix is applied to all affected locations.
        
        For operation-specific issues, each issue is processed separately.
        
        Args:
            issues: List of issues to fix
            spec_format: Format of the spec ('yaml' or 'json')
            max_fixes: Maximum number of fixes to generate (None = all)
            save_llm_io: Optional path to save LLM inputs/outputs
            spec: Full spec for fragment reconstruction
        
        Returns:
            List of validated issues with suggested_fix populated (only real issues)
        """
        fixed_issues = []
        llm_io_data = [] if save_llm_io else None
        
        # Separate global and operation-specific issues (do this before client check for logging)
        global_issues = [i for i in issues if i.is_global]
        operation_issues = [i for i in issues if not i.is_global]
        
        logger.info(f"[PROCESSING] Total issues: {len(issues)}")
        logger.info(f"[PROCESSING] Global issues: {len(global_issues)}")
        logger.info(f"[PROCESSING] Operation issues: {len(operation_issues)}")
        
        if not self.client:
            logger.error("LLM client not initialized. Cannot generate fixes.")
            # Still save LLM I/O file if requested (will be empty to show no calls were made)
            if save_llm_io:
                logger.warning("[LLM I/O] Client not initialized - no API calls will be made")
                logger.warning(f"[LLM I/O] Would have processed: {len(global_issues)} global + {len(operation_issues)} operation issues")
                self._save_llm_io_file(save_llm_io, llm_io_data)
            return []
        
        # Process global issues together in one batch (one LLM call for all global issues)
        if global_issues:
            logger.info(f"[PROCESSING] Processing {len(global_issues)} global issues in one batch")
            if max_fixes and len(fixed_issues) >= max_fixes:
                logger.info("[PROCESSING] Skipping global issues - max fixes limit reached")
                pass  # Skip if limit reached
            else:
                try:
                    logger.info(f"[PROCESSING] Making batch API call for {len(global_issues)} global issues")
                    results, prompt, response_text = self.validate_and_fix_batch(global_issues, spec_format, return_io=True)
                    
                    if llm_io_data is not None:
                        llm_io_data.append({
                            "batch_type": "global",
                            "issue_count": len(global_issues),
                            "prompt": prompt,
                            "llm_response": response_text,
                            "parsed_results": results,
                        })
                    
                    # Process results for each global issue
                    for i, issue in enumerate(global_issues):
                        if i < len(results):
                            result = results[i]
                            if result and result.get("is_valid", False):
                                issue.is_validated = True
                                issue.confidence = result.get("confidence", 0.5)
                                issue.validation_reasoning = result.get("reasoning", "")
                                
                                fix = result.get("fix")
                                if fix:
                                    issue.suggested_fix = fix
                                    fixed_issues.append(issue)
                                    logger.info(f"Validated global issue {issue.id} affecting {len(issue.affected_locations)} operations (confidence: {issue.confidence:.2f})")
                except Exception as e:
                    logger.error(f"Error processing global issues batch: {e}")
        
        # Group operation-specific issues by operation (location path)
        # Extract base operation path (e.g., "paths./users.get.parameters[0]" -> "paths./users.get")
        logger.info(f"[PROCESSING] Grouping {len(operation_issues)} operation issues by operation path")
        operation_groups = {}
        for issue in operation_issues:
            # Extract operation path from location
            # Location format: "paths./users.get.parameters[0]" -> "paths./users.get"
            if ".parameters[" in issue.location:
                op_path = issue.location.split(".parameters[")[0]
            elif ".requestBody" in issue.location:
                op_path = issue.location.split(".requestBody")[0]
            elif ".responses" in issue.location:
                op_path = issue.location.split(".responses")[0]
            else:
                # Already at operation level
                op_path = issue.location.rsplit(".", 1)[0] if "." in issue.location else issue.location
            
            if op_path not in operation_groups:
                operation_groups[op_path] = []
            operation_groups[op_path].append(issue)
        
        logger.info(f"[PROCESSING] Grouped into {len(operation_groups)} unique operations")
        for op_path, op_issues in operation_groups.items():
            logger.debug(f"[PROCESSING] Operation {op_path}: {len(op_issues)} issues")
        
        # Process operation issues in batches (one LLM call per operation)
        issues_to_process = operation_issues[:max_fixes - len(fixed_issues)] if max_fixes else operation_issues
        processed_count = 0
        
        logger.info(f"[PROCESSING] Processing {len(issues_to_process)} operation issues across {len(operation_groups)} operations")
        
        for op_path, op_issues in operation_groups.items():
            if max_fixes and len(fixed_issues) >= max_fixes:
                break
            if processed_count >= len(issues_to_process):
                break
            
            # Only process issues that are in the to_process list
            op_issues_to_process = [i for i in op_issues if i in issues_to_process]
            if not op_issues_to_process:
                continue
            
            try:
                logger.info(f"[PROCESSING] Processing operation {op_path} with {len(op_issues_to_process)} issues")
                # Reconstruct spec_fragments if needed
                for issue in op_issues_to_process:
                    if not issue.spec_fragment and spec:
                        issue.spec_fragment = extract_fragment_from_location(spec, issue.location)
                
                logger.info(f"[PROCESSING] Making batch API call for operation {op_path} ({len(op_issues_to_process)} issues)")
                results, prompt, response_text = self.validate_and_fix_batch(op_issues_to_process, spec_format, return_io=True)
                
                if llm_io_data is not None:
                    logger.debug(f"[LLM I/O] Collecting I/O data for operation {op_path} ({len(op_issues_to_process)} issues)")
                    llm_io_data.append({
                        "batch_type": "operation",
                        "operation_path": op_path,
                        "issue_count": len(op_issues_to_process),
                        "prompt": prompt,
                        "llm_response": response_text,
                        "parsed_results": results,
                    })
                    logger.debug(f"[LLM I/O] I/O data collected. Total entries: {len(llm_io_data)}")
                
                # Process results for each issue in this operation
                for i, issue in enumerate(op_issues_to_process):
                    processed_count += 1
                    if i < len(results):
                        result = results[i]
                        if result and result.get("is_valid", False):
                            issue.is_validated = True
                            issue.confidence = result.get("confidence", 0.5)
                            issue.validation_reasoning = result.get("reasoning", "")
                            
                            fix = result.get("fix")
                            if fix:
                                issue.suggested_fix = fix
                                fixed_issues.append(issue)
                                logger.info(f"Validated and fixed issue {issue.id} (confidence: {issue.confidence:.2f})")
                            else:
                                logger.warning(f"Issue {issue.id} validated but no fix provided")
                        else:
                            logger.info(f"Issue {issue.id} filtered out (not a real issue)")
            except Exception as e:
                logger.error(f"Error processing operation {op_path}: {e}")
                # Fall back to individual processing for this operation
                for issue in op_issues_to_process:
                    processed_count += 1
                    try:
                        if not issue.spec_fragment and spec:
                            issue.spec_fragment = extract_fragment_from_location(spec, issue.location)
                        
                        result, prompt, response_text = self.validate_and_fix(issue, spec_format, return_io=True)
                        
                        # Collect I/O data if requested
                        if llm_io_data is not None:
                            logger.debug(f"[LLM I/O] Collecting I/O data for issue {issue.id}")
                            llm_io_data.append({
                                "issue_id": issue.id,
                                "issue_type": issue.type.value,
                                "location": issue.location,
                                "prompt": prompt,
                                "response": response_text,
                                "result": result,
                            })
                            logger.debug(f"[LLM I/O] I/O data collected. Total entries: {len(llm_io_data)}")
                        
                        if result and result.get("is_valid", False):
                            issue.is_validated = True
                            issue.confidence = result.get("confidence", 0.5)
                            issue.validation_reasoning = result.get("reasoning", "")
                            
                            fix = result.get("fix")
                            if fix:
                                issue.suggested_fix = fix
                                fixed_issues.append(issue)
                    except Exception as e2:
                        logger.error(f"Error processing issue {issue.id}: {e2}")

        logger.info(f"Validated and fixed {len(fixed_issues)} out of {len(issues)} potential issues")
        
        # Save LLM I/O if requested
        if save_llm_io:
            self._save_llm_io_file(save_llm_io, llm_io_data)
        
        return fixed_issues
    
    def _save_llm_io_file(self, save_llm_io: str, llm_io_data: Optional[List[Dict[str, Any]]]) -> None:
        """Helper method to save LLM I/O file."""
        logger.info(f"[LLM I/O] Save LLM I/O requested: {save_llm_io}")
        logger.info(f"[LLM I/O] LLM I/O data initialized: {llm_io_data is not None}")
        logger.info(f"[LLM I/O] LLM I/O data collected: {len(llm_io_data) if llm_io_data else 0} entries")
        
        from pathlib import Path
        import json
        try:
            output_path = Path(save_llm_io)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Always save the file, even if empty (to show that no API calls were made)
            data_to_save = llm_io_data if llm_io_data else []
            output_path.write_text(
                json.dumps(data_to_save, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
            
            if data_to_save:
                logger.info(f"[LLM I/O] Successfully saved LLM inputs/outputs to: {output_path}")
                logger.info(f"[LLM I/O] File size: {output_path.stat().st_size} bytes")
                logger.info(f"[LLM I/O] Contains {len(data_to_save)} API call entries")
            else:
                logger.warning(f"[LLM I/O] Saved empty LLM I/O file to: {output_path}")
                logger.warning(f"[LLM I/O] No API calls were made - check if API key was set correctly")
                logger.warning(f"[LLM I/O] File size: {output_path.stat().st_size} bytes")
        except Exception as e:
            logger.error(f"[LLM I/O] Failed to save LLM I/O file: {type(e).__name__}: {e}")
            import traceback
            logger.debug(f"[LLM I/O] Full traceback: {traceback.format_exc()}")

    def validate_and_fix_batch(
        self,
        issues: List[Issue],
        spec_format: str = "yaml",
        return_io: bool = False,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Validate and fix multiple issues in one LLM call (batching).
        
        Args:
            issues: List of issues to validate and fix together
            spec_format: Format of the spec ('yaml' or 'json')
            return_io: If True, returns (results, prompt, response_text) tuple
        
        Returns:
            If return_io=False: List of result dictionaries, or None if processing fails
            If return_io=True: Tuple of (results_list, prompt, response_text)
        """
        if not self.client:
            logger.error("LLM client not initialized. Cannot validate and fix.")
            return None
        
        if not issues:
            return [] if not return_io else ([], "", "")
        
        logger.info(f"Validating and fixing batch of {len(issues)} issues")
        
        try:
            prompt = self._build_batch_validate_and_fix_prompt(issues, spec_format)
            issue_ids = [issue.id for issue in issues]
            logger.info(f"[API CALL] Making batch API request for {len(issues)} issues: {issue_ids} (model: {self.model})")
            
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
                max_tokens=4000,  # Increased for batch processing
            )
            
            logger.info(f"[API CALL] Batch API request successful for {len(issues)} issues")
            logger.debug(f"[API CALL] Response ID: {getattr(response, 'id', 'N/A')}, Usage: {getattr(response, 'usage', 'N/A')}")
            
            response_text = response.choices[0].message.content.strip()
            logger.debug(f"[API CALL] Batch response length: {len(response_text)} characters")
            results = self._parse_batch_validate_and_fix_response(response_text, len(issues), spec_format)
            
            if results:
                valid_count = sum(1 for r in results if r and r.get("is_valid", False))
                logger.info(f"Validated batch: {valid_count}/{len(issues)} issues are real")
                
                if return_io:
                    return results, prompt, response_text
                return results
            else:
                logger.warning(f"Failed to parse batch validation/fix response")
                if return_io:
                    return None, prompt, response_text
                return None
        
        except Exception as e:
            logger.error(f"[API CALL] Batch API request FAILED for {len(issues)} issues: {type(e).__name__}: {e}")
            import traceback
            logger.debug(f"[API CALL] Full traceback: {traceback.format_exc()}")
            if return_io:
                return None, prompt if 'prompt' in locals() else "", str(e)
            raise LLMFixError(f"Failed to validate and fix batch: {e}") from e

    def validate_and_fix(
        self,
        issue: Issue,
        spec_format: str = "yaml",
        return_io: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        Validate and fix a single issue in one LLM call.
        
        First validates if the issue is real, then generates a fix if valid.
        This is more efficient than separate validation and fixing calls.
        
        Args:
            issue: The potential issue to validate and fix
            spec_format: Format of the spec ('yaml' or 'json')
            return_io: If True, returns (result, prompt, response_text) tuple
        
        Returns:
            If return_io=False: Dictionary with validation result and fix, or None if processing fails
            If return_io=True: Tuple of (result_dict, prompt, response_text)
            Format: {
                "is_valid": bool,
                "confidence": float,
                "reasoning": str,
                "fix": dict or None  // Only if is_valid=True
            }
        """
        if not self.client:
            logger.error("LLM client not initialized. Cannot validate and fix.")
            return None

        logger.info(f"Validating and fixing issue: {issue.type.value} at {issue.location}")

        try:
            prompt = self._build_validate_and_fix_prompt(issue, spec_format)
            logger.info(f"[API CALL] Making API request for issue {issue.id} (model: {self.model})")
            
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
                max_tokens=2000,
            )
            
            logger.info(f"[API CALL] API request successful for issue {issue.id}")
            logger.debug(f"[API CALL] Response ID: {getattr(response, 'id', 'N/A')}, Usage: {getattr(response, 'usage', 'N/A')}")

            response_text = response.choices[0].message.content.strip()
            logger.debug(f"[API CALL] Response length: {len(response_text)} characters")
            result = self._parse_validate_and_fix_response(response_text, spec_format)

            if result:
                if result.get("is_valid", False):
                    logger.info(f"Validated issue {issue.id} with confidence {result.get('confidence', 0.0):.2f}")
                else:
                    logger.info(f"Issue {issue.id} filtered out as not a real issue")
                
                if return_io:
                    return result, prompt, response_text
                return result
            else:
                logger.warning(f"Failed to parse validation/fix response for {issue.location}")
                if return_io:
                    return None, prompt, response_text
                return None

        except Exception as e:
            logger.error(f"[API CALL] API request FAILED for issue {issue.id}: {type(e).__name__}: {e}")
            import traceback
            logger.debug(f"[API CALL] Full traceback: {traceback.format_exc()}")
            if return_io:
                return None, prompt if 'prompt' in locals() else "", str(e)
            raise LLMFixError(f"Failed to validate and fix: {e}") from e

    def generate_fix(
        self,
        issue: Issue,
        spec_format: str = "yaml",
    ) -> Optional[Dict[str, Any]]:
        """
        Legacy method: Generate a fix for a validated issue.
        
        For backward compatibility. New code should use validate_and_fix().
        
        Args:
            issue: The issue to fix (assumed to be validated)
            spec_format: Format of the spec
        
        Returns:
            Dictionary containing the fix fragment, or None if fix generation fails
        """
        result = self.validate_and_fix(issue, spec_format)
        if result and result.get("is_valid", False):
            return result.get("fix")
        return None

    def _build_validate_and_fix_prompt(
        self, issue: Issue, spec_format: str
    ) -> str:
        """
        Build the prompt for combined validation and fixing.
        
        Args:
            issue: The potential issue to validate and fix
            spec_format: Format of the spec
        
        Returns:
            Formatted prompt string
        """
        prompt_parts = [
            "You are analyzing a potential issue in an OpenAPI specification.",
            "First, determine if this is a REAL issue that needs fixing.",
            "If it is a real issue, provide a minimal fix.",
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

        # Add documentation fragment (if available)
        # For missing descriptions, doc_fragment may be None - LLM will generate from spec
        if issue.doc_fragment:
            prompt_parts.append("")
            prompt_parts.append("Relevant Documentation:")
            # Use doc_fragment (already focused/extracted description text)
            # Limit to 800 chars for efficiency
            prompt_parts.append(issue.doc_fragment[:800])
            
            # Special instructions for header requirements - check if it's actually required
            if issue.type.value == "missing_required_header":
                prompt_parts.append("")
                prompt_parts.append("IMPORTANT - Header Requirement Validation:")
                prompt_parts.append("Carefully examine the documentation snippet above to determine if the header is ACTUALLY REQUIRED.")
                prompt_parts.append("- If the documentation says 'required', 'must', 'mandatory', or 'needed' → This is a REAL issue")
                prompt_parts.append("- If the documentation says 'encourage', 'recommend', 'should', 'may', or 'optional' → This is a FALSE POSITIVE")
                prompt_parts.append("- If the documentation only shows an example without stating it's required → This is likely a FALSE POSITIVE")
                prompt_parts.append("Only mark as valid if the documentation explicitly states the header is REQUIRED, not just recommended.")
            
            # Special instructions for missing endpoint security
            if issue.type.value == "missing_endpoint_security":
                prompt_parts.append("")
                prompt_parts.append("IMPORTANT - Security Fix Rules:")
                prompt_parts.append("1. Check the spec fragment above - it may show the global security schemes")
                prompt_parts.append("2. If the spec has a 'security' field at the root level, use those exact security scheme names")
                prompt_parts.append("3. The fix should use the ACTUAL security scheme names from the spec (e.g., 'basicAuth', 'bearerAuth', 'oauth2')")
                prompt_parts.append("4. DO NOT use placeholder names like 'YOUR_GLOBAL_SECURITY_SCHEME' or 'oauth2' if the spec uses different names")
                prompt_parts.append("5. If global security is '[{basicAuth: []}, {bearerAuth: []}]', the fix should be: {'security': [{'basicAuth': []}, {'bearerAuth': []}]}")
                prompt_parts.append("6. Match the exact structure and names from the global security definition in the spec")
            
            # Special instructions for malformed base URLs
            if issue.type.value == "malformed_base_url":
                prompt_parts.append("")
                prompt_parts.append("IMPORTANT: The documentation above contains the actual API URLs.")
                prompt_parts.append("You MUST extract the real base URL from the documentation and use it in the fix.")
                prompt_parts.append("Do NOT use placeholder URLs like 'https://api.example.com'.")
                prompt_parts.append("")
                prompt_parts.append("CRITICAL - Base URL Construction Rules:")
                prompt_parts.append("1. Check the '_example_paths' field in the spec fragment above")
                prompt_parts.append("2. Extract the base URL from documentation examples (e.g., from 'https://api.adp.com/hr/v2/workers')")
                prompt_parts.append("3. Remove any overlapping path segments from the base URL")
                prompt_parts.append("4. The base URL + path should form the full URL WITHOUT duplication")
                prompt_parts.append("")
                prompt_parts.append("Example:")
                prompt_parts.append("- If documentation shows: 'https://api.adp.com/hr/v2/workers'")
                prompt_parts.append("- And spec has path: '/hr/v2/workers'")
                prompt_parts.append("- Then base URL should be: 'https://api.adp.com' (NOT 'https://api.adp.com/hr/v2')")
                prompt_parts.append("- This ensures: base URL + path = 'https://api.adp.com' + '/hr/v2/workers' = 'https://api.adp.com/hr/v2/workers'")
                prompt_parts.append("")
                prompt_parts.append("Check ALL example paths to ensure the base URL works for all of them without overlap.")
        elif issue.type.value == "missing_description":
            # For missing descriptions without doc_fragment, provide guidance
            prompt_parts.append("")
            prompt_parts.append("Note: Generate a clear, concise description based on:")
            prompt_parts.append("- The operation summary (if available)")
            prompt_parts.append("- The API path and HTTP method")
            prompt_parts.append("- The operationId")
            prompt_parts.append("- Standard REST API conventions")
        
        # Special instructions for OAuth2 flow mismatches
        if issue.type.value == "missing_auth_documentation" and issue.spec_fragment and "expected_flow" in issue.spec_fragment:
            prompt_parts.append("")
            prompt_parts.append("CRITICAL - OAuth2 Flow Fix Rules:")
            prompt_parts.append("1. The location is 'components.securitySchemes.{scheme_name}' or 'securityDefinitions.{scheme_name}'")
            prompt_parts.append("2. You MUST REPLACE the incorrect flow with the correct one (do NOT add both)")
            prompt_parts.append("3. The fix should update the 'flows' object to contain ONLY the correct flow")
            prompt_parts.append("4. Use the 'expected_token_url' and 'expected_authorization_url' from the spec fragment if provided")
            prompt_parts.append("5. authorizationUrl should NOT include query parameters (e.g., ?response_type=code)")
            prompt_parts.append("6. authorizationUrl should be just the base URL (e.g., 'https://identity.apaleo.com/connect/authorize')")
            prompt_parts.append("7. For authorizationCode flow, you MUST include both 'authorizationUrl' and 'tokenUrl'")
            prompt_parts.append("8. For implicit flow, you only need 'authorizationUrl' (no tokenUrl)")
            prompt_parts.append("9. Preserve all scopes from the current flow(s) if they are still relevant")
            prompt_parts.append("")
            prompt_parts.append("Example fix structure for authorizationCode flow:")
            prompt_parts.append("  flows:")
            prompt_parts.append("    authorizationCode:")
            prompt_parts.append("      authorizationUrl: https://identity.apaleo.com/connect/authorize")
            prompt_parts.append("      tokenUrl: https://identity.apaleo.com/connect/token")
            prompt_parts.append("      scopes:")
            prompt_parts.append("        scope1: Description")
            prompt_parts.append("        scope2: Description")
            prompt_parts.append("")
            prompt_parts.append("IMPORTANT: The fix should be at the 'flows' level, not at the security scheme level.")
            prompt_parts.append("For location 'components.securitySchemes.oauth2', provide:")
            prompt_parts.append("  {")
            prompt_parts.append("    \"flows\": {")
            prompt_parts.append("      \"authorizationCode\": { ... }")
            prompt_parts.append("    }")
            prompt_parts.append("  }")
            prompt_parts.append("NOT: { \"authorizationCode\": { ... } } at the wrong level")


        prompt_parts.extend(
            [
                "",
                "Instructions:",
                "1. First, determine if this is a REAL issue (not a false positive)",
                "2. Be conservative - only mark as valid if clearly a real problem",
                "3. If valid, provide a minimal fix fragment",
                "4. The fix should only include what needs to be added/changed",
                "5. Do not rewrite entire sections",
                "6. Maintain all existing working parts",
                "",
                "IMPORTANT - Fix Format Rules:",
                "- The location tells you WHERE to apply the fix (e.g., 'servers[0]' means the first item in the servers array)",
                "- The fix should be a fragment that will be merged AT that location",
                "- For 'servers[0]', provide: { \"url\": \"...\" }  (NOT { \"servers\": [...] })",
                "- For 'servers[1].url', provide: { \"url\": \"...\" }  (NOT { \"servers[1].url\": \"...\" })",
                "- Only include the fields that need to change, not the entire parent structure",
                "",
                "Output format (JSON only, no markdown):",
                "{",
                '  "is_valid": true/false,',
                '  "confidence": 0.0-1.0,',
                '  "reasoning": "Brief explanation",',
                '  "fix": { ... }  // Only if is_valid=true, minimal fix fragment in ' + spec_format.upper() + ' format',
                "}",
                "",
                "If is_valid=false, omit the fix field or set it to null.",
            ]
        )

        return "\n".join(prompt_parts)
    
    def _build_batch_validate_and_fix_prompt(
        self, issues: List[Issue], spec_format: str
    ) -> str:
        """
        Build the prompt for batch validation and fixing.
        
        Args:
            issues: List of issues to validate and fix together
            spec_format: Format of the spec
        
        Returns:
            Formatted prompt string
        """
        prompt_parts = [
            "You are analyzing multiple potential issues in an OpenAPI specification.",
            "For each issue, first determine if it is a REAL issue that needs fixing.",
            "If it is a real issue, provide a minimal fix.",
            "",
            f"Total issues to analyze: {len(issues)}",
            "",
        ]
        
        # Add each issue
        for idx, issue in enumerate(issues, 1):
            prompt_parts.append(f"=== Issue {idx} ===")
            prompt_parts.append(f"Issue Type: {issue.type.value}")
            prompt_parts.append(f"Location: {issue.location}")
            prompt_parts.append(f"Description: {issue.description}")
            prompt_parts.append("")
            prompt_parts.append("Spec Fragment:")
            
            # Add spec fragment
            if issue.spec_fragment:
                if spec_format == "yaml":
                    prompt_parts.append(yaml.dump(issue.spec_fragment, default_flow_style=False))
                else:
                    prompt_parts.append(json.dumps(issue.spec_fragment, indent=2))
            else:
                prompt_parts.append("(No fragment available)")
            
            # Add documentation fragment
            if issue.doc_fragment:
                prompt_parts.append("")
                prompt_parts.append("Relevant Documentation:")
                prompt_parts.append(issue.doc_fragment[:600])  # Limit per issue in batch
                
                # Special instructions for header requirements - check if it's actually required
                if issue.type.value == "missing_required_header":
                    prompt_parts.append("")
                    prompt_parts.append("IMPORTANT: Check if the documentation explicitly says 'required'/'must' vs 'encourage'/'recommend'.")
                    prompt_parts.append("Only mark as valid if explicitly REQUIRED, not just recommended.")
                
                # Special instructions for missing endpoint security
                if issue.type.value == "missing_endpoint_security":
                    prompt_parts.append("")
                    prompt_parts.append("IMPORTANT: Use the ACTUAL security scheme names from the spec's global security.")
                    prompt_parts.append("DO NOT use placeholders. Check the spec fragment for the correct scheme names.")
                
                # Special instructions for malformed base URLs
                elif issue.type.value == "malformed_base_url":
                    prompt_parts.append("")
                    prompt_parts.append("IMPORTANT: Extract the base URL from the documentation above.")
                    prompt_parts.append("CRITICAL: Check example paths in the spec and remove overlapping segments from the base URL.")
                    prompt_parts.append("The base URL + path should form the full URL without duplication.")
                    prompt_parts.append("Example: If path is '/hr/v2/workers' and doc shows 'https://api.adp.com/hr/v2/workers',")
                    prompt_parts.append("then base URL should be 'https://api.adp.com' (not 'https://api.adp.com/hr/v2').")
                # Special instructions for OAuth2 flow mismatches
                elif issue.type.value == "missing_auth_documentation" and issue.spec_fragment and "expected_flow" in issue.spec_fragment:
                    prompt_parts.append("")
                    prompt_parts.append("CRITICAL - OAuth2 Flow Fix Rules:")
                    prompt_parts.append("1. You MUST REPLACE the incorrect flow with the correct one (do NOT add both)")
                    prompt_parts.append("2. The fix should update the 'flows' object to contain ONLY the correct flow")
                    prompt_parts.append("3. Use the 'expected_token_url' and 'expected_authorization_url' from the spec fragment if provided")
                    prompt_parts.append("4. authorizationUrl should NOT include query parameters (e.g., ?response_type=code)")
                    prompt_parts.append("5. authorizationUrl should be just the base URL (e.g., 'https://identity.apaleo.com/connect/authorize')")
                    prompt_parts.append("6. For authorizationCode flow, you MUST include both 'authorizationUrl' and 'tokenUrl'")
                    prompt_parts.append("7. For implicit flow, you only need 'authorizationUrl' (no tokenUrl)")
                    prompt_parts.append("8. Preserve all scopes from the current flow(s) if they are still relevant")
                    prompt_parts.append("")
                    prompt_parts.append("IMPORTANT: The fix should be at the 'flows' level.")
                    prompt_parts.append("For location 'components.securitySchemes.oauth2', provide: { \"flows\": { \"authorizationCode\": { ... } } }")
            elif issue.type.value == "missing_description":
                prompt_parts.append("")
                prompt_parts.append("Note: Generate description based on operation context.")
            
            prompt_parts.append("")  # Blank line between issues
        
        prompt_parts.extend([
            "Instructions:",
            "1. For each issue, determine if it is a REAL issue (not a false positive)",
            "2. Be conservative - only mark as valid if clearly a real problem",
            "3. If valid, provide a minimal fix fragment",
            "4. The fix should only include what needs to be added/changed",
            "5. Do not rewrite entire sections",
            "",
            "IMPORTANT - Fix Format Rules:",
            "- The location tells you WHERE to apply the fix (e.g., 'servers[0]' means the first item in the servers array)",
            "- The fix should be a fragment that will be merged AT that location",
            "- For 'servers[0]', provide: { \"url\": \"...\" }  (NOT { \"servers\": [...] })",
            "- For 'servers[1].url', provide: { \"url\": \"...\" }  (NOT { \"servers[1].url\": \"...\" })",
            "- Only include the fields that need to change, not the entire parent structure",
            "",
            "Output format (JSON array, one object per issue, no markdown):",
            "[",
            "  {",
            '    "is_valid": true/false,',
            '    "confidence": 0.0-1.0,',
            '    "reasoning": "Brief explanation",',
            '    "fix": { ... }  // Only if is_valid=true, minimal fix fragment in ' + spec_format.upper() + ' format',
            "  },",
            "  ...",
            "]",
            "",
            "Return the array in the same order as the issues provided.",
            "If is_valid=false, omit the fix field or set it to null.",
        ])
        
        return "\n".join(prompt_parts)
    
    def _parse_batch_validate_and_fix_response(
        self, response_text: str, expected_count: int, spec_format: str
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Parse the LLM response containing batch validation and fixes.
        
        Args:
            response_text: Raw response text from LLM
            expected_count: Expected number of results
            spec_format: Format of the spec
        
        Returns:
            List of result dictionaries, or None if parsing fails
        """
        # Clean response text
        text = response_text.strip().replace("```json", "").replace("```", "")
        
        # Try to find JSON array using regex
        match = re.search(r'(\[.*\])', text, re.DOTALL)
        if match:
            json_str = match.group(1)
            try:
                results = json.loads(json_str)
                
                # Validate structure
                if isinstance(results, list):
                    # Ensure we have the right number of results
                    if len(results) != expected_count:
                        logger.warning(f"Expected {expected_count} results, got {len(results)}")
                        # Pad with None or truncate as needed
                        if len(results) < expected_count:
                            results.extend([None] * (expected_count - len(results)))
                        else:
                            results = results[:expected_count]
                    
                    # Validate each result
                    validated_results = []
                    for i, result in enumerate(results):
                        if result is None:
                            validated_results.append({
                                "is_valid": False,
                                "confidence": 0.0,
                                "reasoning": "No response from LLM",
                                "fix": None,
                            })
                        elif isinstance(result, dict) and "is_valid" in result:
                            validated_results.append(result)
                        else:
                            logger.warning(f"Invalid result format at index {i}")
                            validated_results.append({
                                "is_valid": False,
                                "confidence": 0.0,
                                "reasoning": "Invalid response format",
                                "fix": None,
                            })
                    
                    return validated_results
                else:
                    logger.warning(f"Response is not a list: {type(results)}")
                    return None
                    
            except json.JSONDecodeError:
                try:
                    # Try fixing common issues
                    json_str_fixed = json_str.replace("\n", " ").replace("\r", "")
                    results = json.loads(json_str_fixed)
                    if isinstance(results, list):
                        return results[:expected_count] if len(results) >= expected_count else results + [None] * (expected_count - len(results))
                except Exception as e:
                    logger.warning(f"Failed to parse after fixing: {e}")
        
        # Fallback: try parsing the whole text
        try:
            results = json.loads(text)
            if isinstance(results, list):
                return results[:expected_count] if len(results) >= expected_count else results + [None] * (expected_count - len(results))
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Failed to parse batch validation/fix response: {e}")
            logger.debug(f"Response text: {response_text[:500]}")
        
        return None

    def _build_fix_prompt(
        self, issue: Issue, spec_format: str
    ) -> str:
        """
        Legacy method: Build prompt for fix generation only.
        
        For backward compatibility. New code should use _build_validate_and_fix_prompt().
        """
        return self._build_validate_and_fix_prompt(issue, spec_format)

    def _parse_validate_and_fix_response(
        self, response_text: str, spec_format: str
    ) -> Optional[Dict[str, Any]]:
        """
        Parse the LLM response containing validation and fix.
        
        Args:
            response_text: Raw response text from LLM
            spec_format: Format of the spec
        
        Returns:
            Dictionary with validation result and fix, or None if parsing fails
        """
        # Clean response text
        text = response_text.strip().replace("```json", "").replace("```", "")
        
        # Try to find JSON object using regex (same pattern as example)
        match = re.search(r'(\{.*\})', text, re.DOTALL)
        if match:
            json_str = match.group(1)
            try:
                result = json.loads(json_str)
                
                # Validate structure
                if isinstance(result, dict) and "is_valid" in result:
                    # If fix is provided and is_valid is true, ensure it's a dict
                    if result.get("is_valid") and "fix" in result:
                        fix = result["fix"]
                        if fix is None:
                            # Valid issue but no fix provided - that's okay
                            return result
                        elif isinstance(fix, dict):
                            return result
                        else:
                            logger.warning(f"Fix is not a dictionary: {type(fix)}")
                            # Still return result but without fix
                            result["fix"] = None
                            return result
                    else:
                        # Not valid or no fix needed
                        return result
                else:
                    logger.warning(f"Response missing is_valid field")
                    return None
                    
            except json.JSONDecodeError:
                try:
                    # Try fixing common issues
                    json_str_fixed = json_str.replace("\n", " ").replace("\r", "")
                    result = json.loads(json_str_fixed)
                    if isinstance(result, dict) and "is_valid" in result:
                        return result
                except Exception as e:
                    logger.warning(f"Failed to parse after fixing: {e}")
                    logger.debug(f"JSON string: {json_str[:200]}")
        
        # Fallback: try parsing the whole text
        try:
            result = json.loads(text)
            if isinstance(result, dict) and "is_valid" in result:
                return result
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Failed to parse validation/fix response: {e}")
            logger.debug(f"Response text: {response_text[:500]}")
        
        return None

    def _parse_fix(self, fix_text: str, spec_format: str) -> Optional[Dict[str, Any]]:
        """
        Legacy method: Parse fix from response.
        
        For backward compatibility. New code should use _parse_validate_and_fix_response().
        """
        result = self._parse_validate_and_fix_response(fix_text, spec_format)
        if result and result.get("is_valid", False):
            return result.get("fix")
        return None


def create_llm_fixer(
    api_key: Optional[str] = None,
    model: str = "gpt-4o-mini",
    azure_endpoint: Optional[str] = None,
    api_version: Optional[str] = None,
    base_url: Optional[str] = None,
) -> LLMFixer:
    """
    Factory function to create an LLM fixer.
    
    Supports both Azure OpenAI and regular OpenAI API.
    Auto-detects based on azure_endpoint parameter.
    
    Args:
        api_key: API key (defaults to OPENAI_API_KEY or AZURE_OPENAI_API_KEY env var)
        model: Model name
        azure_endpoint: Azure OpenAI endpoint URL (if provided, uses Azure)
        api_version: Azure API version (required for Azure)
        base_url: Custom base URL for OpenAI-compatible APIs
    
    Returns:
        LLMFixer instance
    """
    return LLMFixer(
        api_key=api_key,
        model=model,
        azure_endpoint=azure_endpoint,
        api_version=api_version,
        base_url=base_url,
    )

