"""
OpenAPI specification loader.

Handles loading and parsing of OpenAPI specification files in JSON or YAML format.
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from openapi_spec_validator import validate_spec
from openapi_spec_validator.exceptions import OpenAPISpecValidatorError
from prance import ResolvingParser

from specfix.utils.logger import get_logger

logger = get_logger(__name__)


class SpecLoadError(Exception):
    """Raised when spec loading fails."""

    pass


def load_spec(spec_path: Path) -> Dict[str, Any]:
    """
    Load and parse an OpenAPI specification file.
    
    Supports both JSON and YAML formats. Automatically detects format based on
    file extension or content.
    
    Args:
        spec_path: Path to the OpenAPI spec file
    
    Returns:
        Parsed OpenAPI specification as a dictionary
    
    Raises:
        SpecLoadError: If the file cannot be loaded or parsed
    """
    if not spec_path.exists():
        raise SpecLoadError(f"Spec file not found: {spec_path}")
    
    logger.info(f"Loading OpenAPI spec from: {spec_path}")
    
    try:
        content = spec_path.read_text(encoding="utf-8")
    except Exception as e:
        raise SpecLoadError(f"Failed to read spec file: {e}") from e
    
    # Detect format
    is_yaml = spec_path.suffix.lower() in (".yaml", ".yml")
    is_json = spec_path.suffix.lower() == ".json"
    
    # If extension is ambiguous, try to detect from content
    if not is_yaml and not is_json:
        is_yaml = not content.strip().startswith("{")
    
    try:
        if is_yaml:
            spec = yaml.safe_load(content)
        else:
            spec = json.loads(content)
    except (yaml.YAMLError, json.JSONDecodeError) as e:
        raise SpecLoadError(f"Failed to parse spec file: {e}") from e
    
    # Validate basic structure
    if not isinstance(spec, dict):
        raise SpecLoadError("Spec file must contain a dictionary/object")
    
    # Check for OpenAPI version
    if "openapi" not in spec and "swagger" not in spec:
        logger.warning("Spec file does not contain 'openapi' or 'swagger' field")
    
    # Try to resolve references using prance (optional - gracefully fallback if it fails)
    # Reference resolution is nice-to-have but not required for the tool to work
    try:
        # Try with different backends/options
        try:
            # First try with openapi-spec-validator backend (strict)
            parser = ResolvingParser(
                str(spec_path),
                backend="openapi-spec-validator",
                strict=False,  # Don't fail on minor validation issues
            )
            resolved_spec = parser.specification
            logger.info("Successfully resolved spec references")
            return resolved_spec
        except Exception as e1:
            # If that fails, try with flex backend (more lenient)
            try:
                parser = ResolvingParser(
                    str(spec_path),
                    backend="flex",
                    strict=False,
                )
                resolved_spec = parser.specification
                logger.info("Successfully resolved spec references (using flex backend)")
                return resolved_spec
            except Exception as e2:
                # Both backends failed - log and continue with original
                logger.debug(f"Reference resolution failed with openapi-spec-validator: {e1}")
                logger.debug(f"Reference resolution failed with flex: {e2}")
                raise e2  # Re-raise to be caught by outer except
    except Exception as e:
        # If reference resolution fails, that's okay - we can work with unresolved refs
        # This is common for specs with external references or validation issues
        logger.info(f"Reference resolution skipped (this is usually fine): {type(e).__name__}")
        logger.debug(f"Resolution error details: {e}")
        logger.info("Continuing with original spec (references will remain unresolved)")
        return spec


def validate_spec_structure(spec: Dict[str, Any]) -> bool:
    """
    Validate OpenAPI specification structure.
    
    Args:
        spec: OpenAPI specification dictionary
    
    Returns:
        True if valid, False otherwise
    """
    try:
        validate_spec(spec)
        logger.info("Spec validation passed")
        return True
    except OpenAPISpecValidatorError as e:
        logger.warning(f"Spec validation warnings: {e}")
        return False
    except Exception as e:
        logger.warning(f"Spec validation error: {e}")
        return False


def get_spec_format(spec_path: Path) -> str:
    """
    Determine the format of a spec file.
    
    Args:
        spec_path: Path to the spec file
    
    Returns:
        'yaml' or 'json'
    """
    suffix = spec_path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        return "yaml"
    elif suffix == ".json":
        return "json"
    else:
        # Try to detect from content
        content = spec_path.read_text(encoding="utf-8").strip()
        if content.startswith("{"):
            return "json"
        return "yaml"

