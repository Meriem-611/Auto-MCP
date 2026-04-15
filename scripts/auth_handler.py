"""
Module for extracting and enforcing authentication methods from OpenAPI specs.
"""

def extract_auth_methods(spec):
    """
    Extract authentication methods from OpenAPI spec (global and per-endpoint).
    Returns a dict: {method_name: details}
    """
    auth_methods = {}
    # OpenAPI 3.x: components > securitySchemes
    if 'openapi' in spec:
        schemes = spec.get('components', {}).get('securitySchemes', {})
        for name, details in schemes.items():
            auth_methods[name] = details
    # OpenAPI 2.0: securityDefinitions
    elif 'swagger' in spec:
        schemes = spec.get('securityDefinitions', {})
        for name, details in schemes.items():
            auth_methods[name] = details
    # Per-endpoint security (collect all unique methods)
    for path, methods in spec.get('paths', {}).items():
        for op, op_obj in methods.items():
            if isinstance(op_obj, dict):
                for sec in op_obj.get('security', []):
                    for name in sec:
                        if name not in auth_methods:
                            auth_methods[name] = {}
    return auth_methods

def enforce_auth_method(auth_methods, enforced_method):
    """
    Enforce a specific authentication method, raising if not present.
    """
    if enforced_method not in auth_methods:
        raise ValueError(f"Authentication method '{enforced_method}' not found in spec.") 
