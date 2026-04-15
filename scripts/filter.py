"""
Module for filtering OpenAPI endpoints by tags.
"""
import copy

def filter_endpoints_by_tags(spec, include_tags=None, exclude_tags=None):
    """
    Filter endpoints in the OpenAPI spec by include/exclude tags.
    Returns the filtered spec.
    """
    if not include_tags and not exclude_tags:
        return spec
    include_set = set(t.strip() for t in include_tags.split(",")) if include_tags else None
    exclude_set = set(t.strip() for t in exclude_tags.split(",")) if exclude_tags else set()
    filtered = copy.deepcopy(spec)
    paths = filtered.get('paths', {})
    new_paths = {}
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        new_methods = {}
        for method, op in methods.items():
            # Skip non-operation keys (like 'parameters', 'servers', etc.) - preserve them separately
            if method.lower() not in ['get', 'post', 'put', 'delete', 'patch', 'options', 'head', 'trace']:
                continue
            if not isinstance(op, dict):
                continue
            tags = set(op.get('tags', []))
            if include_set and not (tags & include_set):
                continue
            if exclude_set and (tags & exclude_set):
                continue
            new_methods[method] = op
        if new_methods:
            # Preserve path-level properties (parameters, servers, summary, description, x-* extensions, etc.)
            # by copying the entire path item and only modifying HTTP method keys
            new_path_item = copy.deepcopy(methods)
            # Update HTTP method keys: remove filtered ones, keep allowed ones
            for method in list(new_path_item.keys()):
                if method.lower() in ['get', 'post', 'put', 'delete', 'patch', 'options', 'head', 'trace']:
                    if method not in new_methods:
                        # Remove filtered operation
                        del new_path_item[method]
                    else:
                        # Keep allowed operation (using the filtered version for consistency)
                        new_path_item[method] = new_methods[method]
            # All non-HTTP method keys (parameters, servers, etc.) are automatically preserved
            new_paths[path] = new_path_item
    filtered['paths'] = new_paths
    return filtered 
