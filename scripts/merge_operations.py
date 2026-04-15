"""
Module for merging list/detail operations into single MCP tools.
Detects operations that represent the same logical resource and merges them.
"""
import re
import copy
import os


def has_id_param(path_string):
    """
    Detect parameterized ID segments anywhere in the path.
    Returns True if path contains any {*id*} parameter segment.
    """
    if not path_string:
        return False
    return bool(re.search(r'\{[^}]*id[^}]*\}', str(path_string), re.IGNORECASE))


def base_path(path):
    """
    Base path for list/detail aggregation:
    - If last segment is an {id}-like param, drop it.
    - Otherwise, keep as-is.
    
    Examples:
    - /items/{itemId} -> /items
    - /users/{userId}/projects/{projectId} -> /users/{userId}/projects
    - /items -> /items (no change)
    """
    if not path:
        return "/"
    
    # Split path into segments, filtering out empty strings
    parts = [p for p in str(path).strip("/").split("/") if p]
    
    if not parts:
        return "/"
    
    # Check if last segment is an ID parameter
    last_segment = parts[-1]
    if re.match(r'\{[^}]*id[^}]*\}', last_segment, re.IGNORECASE):
        # Drop the last segment (it's an ID)
        if len(parts) == 1:
            return "/"
        return "/" + "/".join(parts[:-1])
    
    # Keep as-is
    return "/" + "/".join(parts)


def merge_operations(spec, output_dir, disable_merge=False, skip_logs=False):
    """
    Merge list/detail operations into single MCP tools.
    
    Args:
        spec: OpenAPI specification dictionary (already filtered)
        output_dir: Directory to write merge log
        disable_merge: If True, skip merging and return spec as-is
    
    Returns:
        tuple: (merged_spec, merge_groups)
               merge_groups is a list of dicts describing what was merged
    """
    if disable_merge:
        print("[merge_operations] Merging disabled by user")
        return spec, []
    
    paths = spec.get('paths', {})
    if not isinstance(paths, dict):
        return spec, []
    
    # Group GET operations by base path
    # Only process GET operations for merging
    base_path_groups = {}
    
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        
        for method, op in methods.items():
            # Only merge GET operations
            if method.upper() != 'GET' or not isinstance(op, dict):
                continue
            
            # Compute base path (removes trailing ID segment if present)
            base = base_path(path)
            
            # Check if this operation has an ID parameter
            has_id = has_id_param(path)
            
            if base not in base_path_groups:
                base_path_groups[base] = {
                    'list_ops': [],    # Collection operations (no IDs)
                    'detail_ops': []   # Detail operations (with IDs)
                }
            
            # Categorize operation
            if has_id:
                base_path_groups[base]['detail_ops'].append({
                    'path': path,
                    'method': method,
                    'operation': op
                })
            else:
                base_path_groups[base]['list_ops'].append({
                    'path': path,
                    'method': method,
                    'operation': op
                })
    
    # Find groups that can be merged (have both list and detail operations)
    merge_groups = []
    merged_spec = copy.deepcopy(spec)
    merged_paths = {}
    
    # Track which (path, method) combinations have been merged (so we don't include them twice)
    # This is more general than tracking just paths - allows for any operation type to be merged
    merged_operations_set = set()
    
    for base, group in base_path_groups.items():
        list_ops = group['list_ops']
        detail_ops = group['detail_ops']
        
        # Only merge if we have at least one list operation AND one detail operation
        if len(list_ops) > 0 and len(detail_ops) > 0:
            # Use the first list and first detail operation as the base
            list_op = list_ops[0]
            detail_op = detail_ops[0]
            
            # Create merged operation
            merged_op = merge_operation_pair(list_op, detail_op)
            
            # Use the list (collection) path as the merged path (it's simpler, no ID params)
            merged_path = list_op['path']
            
            # Store merge info
            merge_groups.append({
                'base_path': base,
                'list_path': list_op['path'],
                'detail_path': detail_op['path'],
                'merged_path': merged_path,
                'list_ops_count': len(list_ops),
                'detail_ops_count': len(detail_ops)
            })
            
            # Add merged operation to new paths
            if merged_path not in merged_paths:
                merged_paths[merged_path] = {}
            merged_paths[merged_path]['get'] = merged_op
            
            # Mark both original (path, method) combinations as merged
            merged_operations_set.add((list_op['path'], list_op['method'].lower()))
            merged_operations_set.add((detail_op['path'], detail_op['method'].lower()))
            
            # If there are multiple list/detail ops in the same group, mark them all
            for op_info in list_ops[1:] + detail_ops[1:]:
                merged_operations_set.add((op_info['path'], op_info['method'].lower()))
        else:
            # Keep operations as-is (no merge possible)
            for op_info in list_ops + detail_ops:
                path = op_info['path']
                method = op_info['method']
                if path not in merged_paths:
                    merged_paths[path] = {}
                merged_paths[path][method] = op_info['operation']
    
    # Copy all non-merged operations
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        
        # Copy all operations from this path
        for method, op in methods.items():
            # Skip operations that were merged (they're already in merged_paths)
            # This works for any operation type (GET, POST, PUT, etc.)
            if (path, method.lower()) in merged_operations_set:
                continue
            
            # Add non-merged operations
            if path not in merged_paths:
                merged_paths[path] = {}
            merged_paths[path][method] = op
    
    merged_spec['paths'] = merged_paths
    
    # Write merge log (skip in stub-only mode)
    if not skip_logs:
        os.makedirs(output_dir, exist_ok=True)
        merge_log_path = os.path.join(output_dir, 'merged_operations.log')
        
        with open(merge_log_path, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write("MERGED OPERATIONS LOG\n")
            f.write("=" * 80 + "\n\n")
            f.write(f"Total merge groups: {len(merge_groups)}\n\n")
            
            if merge_groups:
                f.write("=" * 80 + "\n")
                f.write("MERGED OPERATIONS:\n")
                f.write("=" * 80 + "\n\n")
                
                for i, mg in enumerate(merge_groups, 1):
                    f.write(f"Merge Group {i}:\n")
                    f.write(f"  Base Path: {mg['base_path']}\n")
                    f.write(f"  List: GET {mg['list_path']}\n")
                    f.write(f"  Detail: GET {mg['detail_path']}\n")
                    f.write(f"  Merged Into: GET {mg['merged_path']}\n")
                    f.write(f"  List Operations: {mg['list_ops_count']}\n")
                    f.write(f"  Detail Operations: {mg['detail_ops_count']}\n")
                    f.write("-" * 80 + "\n\n")
            else:
                f.write("No operations were merged.\n")
        
        print(f"[merge_operations] Merge log written to: {merge_log_path}")
    else:
        print(f"[merge_operations] Skipping log file generation (stub-only mode)")
    
    print(f"[merge_operations] Merged {len(merge_groups)} operation groups")
    
    return merged_spec, merge_groups


def merge_operation_pair(list_op, detail_op):
    """
    Merge a list (collection) operation and a detail operation into a single operation.
    The merged operation will support both list and get-by-id behaviors.
    """
    merged = copy.deepcopy(list_op['operation'])
    
    # Extract parameters from detail operation
    detail_path = detail_op['path']
    detail_params = detail_op['operation'].get('parameters', [])
    
    # Get all path parameters from detail operation
    detail_path_params = []
    id_params = []
    for param in detail_params:
        if isinstance(param, dict) and param.get('in') == 'path':
            detail_path_params.append(param)
            param_name = param.get('name', '')
            # Check if this is an ID parameter (contains 'id' in the name)
            if 'id' in param_name.lower():
                id_params.append(param)
    
    # Merge descriptions
    list_desc = list_op['operation'].get('description', '')
    list_summary = list_op['operation'].get('summary', '')
    detail_desc = detail_op['operation'].get('description', '')
    detail_summary = detail_op['operation'].get('summary', '')
    
    # Create combined description
    combined_summary = list_summary or detail_summary or "Get resource"
    combined_desc_parts = []
    if list_desc:
        combined_desc_parts.append(f"List: {list_desc}")
    if detail_desc:
        combined_desc_parts.append(f"Get by ID: {detail_desc}")
    
    if combined_desc_parts:
        combined_desc = " | ".join(combined_desc_parts)
    else:
        combined_desc = f"Get list of resources or get a specific resource by ID. Provide 'id' parameter to get a specific resource."
    
    merged['summary'] = combined_summary
    merged['description'] = combined_desc
    
    # Merge parameters: combine list params with detail params
    # Start with list operation's parameters
    list_params = merged.get('parameters', [])
    
    # Create a set of existing parameter names (by name and location) to avoid duplicates
    existing_params = {(p.get('name'), p.get('in')) for p in list_params if isinstance(p, dict)}
    
    # Add path parameters from detail operation that aren't already in list
    # ID parameters become optional, others stay as they were defined
    for detail_param in detail_path_params:
        param_name = detail_param.get('name', '')
        param_key = (param_name, 'path')
        
        # Skip if parameter already exists
        if param_key in existing_params:
            continue
        
        param_copy = copy.deepcopy(detail_param)
        
        # If it's an ID parameter, make it optional
        if 'id' in param_name.lower():
            param_copy['required'] = False
            # Add description note
            original_desc = param_copy.get('description', '')
            param_copy['description'] = f"{original_desc} (Optional: if provided, returns single resource; if omitted, returns list)".strip()
        
        list_params.append(param_copy)
        existing_params.add(param_key)
    
    merged['parameters'] = list_params
    
    # Merge tags (union of both)
    list_tags = set(list_op['operation'].get('tags', []))
    detail_tags = set(detail_op['operation'].get('tags', []))
    merged['tags'] = list(list_tags | detail_tags)
    
    # Use the more specific operationId if available, or create one
    if not merged.get('operationId'):
        # Try to derive from list path
        list_path = list_op['path']
        path_parts = [p for p in list_path.strip('/').split('/') if p and not p.startswith('{')]
        if path_parts:
            merged['operationId'] = f"get_{'_'.join(path_parts)}"
        else:
            merged['operationId'] = "get_resource"
    
    # Add metadata about the merge
    merged['x-merged'] = True
    merged['x-collection-path'] = list_op['path']
    merged['x-detail-path'] = detail_op['path']
    
    return merged

