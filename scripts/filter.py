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
        new_methods = {}
        for method, op in methods.items():
            tags = set(op.get('tags', []))
            if include_set and not (tags & include_set):
                continue
            if exclude_set and (tags & exclude_set):
                continue
            new_methods[method] = op
        if new_methods:
            new_paths[path] = new_methods
    filtered['paths'] = new_paths
    return filtered 