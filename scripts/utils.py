"""
Utility functions for OpenAPI MCP server stub generator.
"""
import copy
import sys


def resolve_references(spec):
    """
    Recursively resolve $ref references in the OpenAPI spec (local refs only).
    Returns the dereferenced spec.
    Unresolved or non-standard $ref paths are left as-is with a warning.
    Preserves $ref in response schemas to avoid validation errors.
    Now handles circular references and memoizes resolved refs.
    """
    resolved_count = [0]
    max_depth = [0]
    memo = {}
    def _resolve(obj, root, path=None, depth=0, ref_stack=None):
        if path is None:
            path = []
        if ref_stack is None:
            ref_stack = []
        if depth > max_depth[0]:
            max_depth[0] = depth
        # Check if we're in a response schema - if so, preserve $ref
        in_response_schema = False
        if len(path) >= 4:
            path_str = '/'.join(map(str, path))
            if 'responses' in path_str and 'content' in path_str and 'schema' in path_str:
                responses_idx = path_str.rfind('responses')
                content_idx = path_str.rfind('content')
                schema_idx = path_str.rfind('schema')
                if responses_idx < content_idx < schema_idx:
                    in_response_schema = True
        if isinstance(obj, dict):
            if "$ref" in obj:
                ref_path = obj["$ref"]
                if ref_path in ref_stack:
                    print(f"[resolve_references] Circular $ref detected: {ref_path} (in stack). Skipping further resolution.")
                    return obj  # Return the $ref as-is to break the cycle
                if ref_path in memo:
                    return memo[ref_path]
                if in_response_schema:
                    return obj
                if not ref_path.startswith("#/"):
                    print(f"[resolve_references] Skipping non-local $ref: {ref_path}")
                    return obj
                parts = ref_path.lstrip("#/").split("/")
                ref_obj = root
                try:
                    for part in parts:
                        if not isinstance(ref_obj, dict) or part not in ref_obj:
                            print(f"[resolve_references] Could not resolve $ref: {ref_path}")
                            return obj
                        ref_obj = ref_obj[part]
                    resolved_count[0] += 1
                    if resolved_count[0] % 1000 == 0:
                        print(f"[resolve_references] Resolved {resolved_count[0]} $ref so far (depth={depth}, max_depth={max_depth[0]})")
                    # Add to stack before resolving
                    ref_stack.append(ref_path)
                    resolved = _resolve(ref_obj, root, path + ['ref'], depth + 1, ref_stack)
                    ref_stack.pop()
                    memo[ref_path] = resolved
                    return resolved
                except Exception as e:
                    print(f"[resolve_references] Exception resolving $ref {ref_path}: {e}")
                    return obj
            else:
                return {k: _resolve(v, root, path + [k], depth + 1, ref_stack) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [_resolve(i, root, path + [str(idx)], depth + 1, ref_stack) for idx, i in enumerate(obj)]
        else:
            return obj
    result = _resolve(copy.deepcopy(spec), spec)
    print(f"[resolve_references] Total $ref resolved: {resolved_count[0]}, max recursion depth: {max_depth[0]}")
    return result 