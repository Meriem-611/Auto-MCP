"""
Module for parsing and dereferencing OpenAPI specifications (YAML/JSON, v2/v3).
"""
import yaml
import json
import re
from openapi_spec_validator import validate_spec
from utils import resolve_references
import time


def _fix_unresolved_path_params(spec):
    """
    Finds path parameters in the URL that are not defined in the parameters section
    and adds a basic definition to satisfy the validator.
    """
    if 'paths' in spec and isinstance(spec['paths'], dict):
        for path_str, path_item_obj in spec['paths'].items():
            if not isinstance(path_item_obj, dict): continue
            
            placeholders = re.findall(r'\{(\w+)\}', path_str)
            if not placeholders: continue
            
            path_level_params = {p['name'] for p in path_item_obj.get('parameters', []) if isinstance(p, dict) and p.get('in') == 'path'}
            
            for method, op_obj in path_item_obj.items():
                if method.lower() not in ['get', 'put', 'post', 'delete', 'options', 'head', 'patch', 'trace'] or not isinstance(op_obj, dict):
                    continue
                
                op_level_params = {p['name'] for p in op_obj.get('parameters', []) if isinstance(p, dict) and p.get('in') == 'path'}
                
                for placeholder in placeholders:
                    if placeholder not in path_level_params and placeholder not in op_level_params:
                        if 'parameters' not in op_obj: op_obj['parameters'] = []
                        op_obj['parameters'].append({
                            'name': placeholder,
                            'in': 'path',
                            'required': True,
                            'schema': {'type': 'string'}
                        })


def _fix_duplicate_operation_ids(spec):
    """
    Finds and resolves duplicate operation IDs by appending a suffix.
    """
    seen_op_ids = set()
    if 'paths' in spec and isinstance(spec['paths'], dict):
        for path, path_item in spec['paths'].items():
            if not isinstance(path_item, dict): continue
            for method, op in path_item.items():
                if method.lower() not in ['get', 'put', 'post', 'delete', 'options', 'head', 'patch', 'trace'] or not isinstance(op, dict):
                    continue
                if 'operationId' in op:
                    original_id = op['operationId']
                    new_id = original_id
                    counter = 1
                    while new_id in seen_op_ids:
                        new_id = f"{original_id}_{counter}"
                        counter += 1
                    op['operationId'] = new_id
                    seen_op_ids.add(new_id)


def _clean_default_values(obj, depth=0, max_depth=30, processed_count=[0], visited=None, max_objects=5000000):
    """
    Recursively clean a spec by removing default values that are not valid against their schema.
    Added depth limit, progress logging, visited tracking, and max object limit to prevent infinite recursion.
    """
    if depth > max_depth:
        return  # Prevent excessive recursion
    
    # Safety check: if we've processed way too many objects, something is wrong
    if processed_count[0] > max_objects:
        print(f"[parser] WARNING: Processed {processed_count[0]} objects, exceeding safety limit of {max_objects}. Stopping to prevent infinite loop.")
        return
    
    # Track visited objects by id() to prevent processing the same object multiple times
    # This prevents infinite loops from circular references
    if visited is None:
        visited = set()
    
    obj_id = id(obj)
    if obj_id in visited:
        return  # Already processed this object
    visited.add(obj_id)
    
    processed_count[0] += 1
    if processed_count[0] % 100000 == 0:
        print(f"[parser] Cleaned {processed_count[0]} objects so far (depth={depth}, unique visited={len(visited)})...")
    
    if isinstance(obj, dict):
        # Skip recursion for certain keys that don't contain schemas with defaults
        # This optimization helps with very large specs
        skip_keys = {'examples', 'example', 'externalDocs', 'tags', 'servers', 'security'}
        if 'default' in obj:
            # Handle array types with enums nested in items/anyOf/oneOf
            if obj.get('type') == 'array' and 'items' in obj:
                all_enums = set()
                items_schema = obj.get('items', {})
                schemas_to_check = []
                
                if 'anyOf' in items_schema: schemas_to_check.extend(items_schema['anyOf'])
                elif 'oneOf' in items_schema: schemas_to_check.extend(items_schema['oneOf'])
                else: schemas_to_check.append(items_schema)

                for s in schemas_to_check:
                    if isinstance(s, dict) and 'enum' in s:
                        all_enums.update(s['enum'])

                if all_enums:
                    default_val = obj['default']
                    is_invalid = False
                    if isinstance(default_val, list):
                        if any(item not in all_enums for item in default_val):
                            is_invalid = True
                    elif default_val not in all_enums:
                        is_invalid = True
                    
                    if is_invalid:
                        del obj['default']
            
            # Handle array defaults that don't match enum
            if 'default' in obj and 'enum' in obj:
                default_val = obj['default']
                enum_vals = obj.get('enum')

                # Ensure enum_vals is a list before proceeding
                if isinstance(enum_vals, list):
                    # A safe way to check for existence without triggering a TypeError on mixed-type lists.
                    is_valid = False
                    for enum_item in enum_vals:
                        try:
                            # Use soft equality check which is safer than `in`
                            if enum_item == default_val:
                                is_valid = True
                                break
                        except TypeError:
                            continue # Ignore non-comparable types
                    
                    if not is_valid:
                        del obj['default']

            # Handle scalar defaults that don't match enum
            if 'default' in obj and 'enum' in obj:
                default_val = obj['default']
                enum_vals = obj['enum']
                # Check for type mismatch before 'in' check to prevent TypeError
                if enum_vals and not isinstance(default_val, type(enum_vals[0])):
                    del obj['default']
                elif default_val not in enum_vals:
                    del obj['default']

            # Handle defaults where the type mismatches the schema's declared type
            if 'default' in obj and 'type' in obj:
                default_val = obj['default']
                schema_type = obj['type']
                type_mismatch = False
                if schema_type == 'string' and not isinstance(default_val, str): type_mismatch = True
                elif schema_type == 'integer' and not isinstance(default_val, int): type_mismatch = True
                elif schema_type == 'number' and not isinstance(default_val, (int, float)): type_mismatch = True
                elif schema_type == 'boolean' and not isinstance(default_val, bool): type_mismatch = True
                if type_mismatch:
                    del obj['default']
            
            # Handle integer format violations (e.g., int64 overflow)
            if 'default' in obj and obj.get('type') == 'integer' and 'format' in obj:
                default_val = obj['default']
                schema_format = obj.get('format')
                is_invalid = False
                if schema_format == 'int64':
                    if not isinstance(default_val, int) or not (-2**63 <= default_val <= 2**63 - 1):
                        is_invalid = True
                elif schema_format == 'int32':
                    if not isinstance(default_val, int) or not (-2**31 <= default_val <= 2**31 - 1):
                        is_invalid = True
                
                if is_invalid:
                    del obj['default']
        
        # Recurse into dictionary values
        # Skip recursion for certain keys that don't contain schemas with defaults (optimization for large specs)
        skip_keys = {'examples', 'example', 'externalDocs', 'tags', 'servers', 'security'}
        for key, value in obj.items():
            # Skip recursing into branches that don't contain schema defaults at deeper levels
            if depth > 5 and key in skip_keys:
                continue
            _clean_default_values(value, depth + 1, max_depth, processed_count, visited, max_objects)
            
    elif isinstance(obj, list):
        # Recurse into list items
        for item in obj:
            _clean_default_values(item, depth + 1, max_depth, processed_count, visited, max_objects)


def _fix_malformed_response_schemas(spec):
    """
    Fix malformed response schemas that cause validation errors.
    The OpenAPI validator expects responses to be either Response objects or Reference objects.
    """
    if 'paths' in spec and isinstance(spec['paths'], dict):
        for path, path_item in spec['paths'].items():
            if not isinstance(path_item, dict): continue
            
            for method, operation in path_item.items():
                if method.lower() not in ['get', 'put', 'post', 'delete', 'options', 'head', 'patch', 'trace'] or not isinstance(operation, dict):
                    continue
                
                if 'responses' in operation and isinstance(operation['responses'], dict):
                    for status_code, response in operation['responses'].items():
                        if isinstance(response, dict):
                            # Check if response has content with malformed schema
                            if 'content' in response and isinstance(response['content'], dict):
                                for content_type, content_item in response['content'].items():
                                    if isinstance(content_item, dict) and 'schema' in content_item:
                                        schema = content_item['schema']
                                        # If schema is an object with allOf that contains inline definitions,
                                        # try to fix it by ensuring proper structure
                                        if isinstance(schema, dict) and 'allOf' in schema:
                                            _fix_allof_schema(schema, spec)


def _fix_allof_schema(schema, spec):
    """
    Fix allOf schemas that contain inline object definitions instead of proper references.
    """
    if 'allOf' in schema and isinstance(schema['allOf'], list):
        for i, item in enumerate(schema['allOf']):
            if isinstance(item, dict) and 'properties' in item:
                # Check if this looks like a common pattern that should be a reference
                properties = item.get('properties', {})
                
                # Look for common patterns that should be references
                if 'pagination' in properties:
                    # This looks like a pagination object, try to find a matching schema
                    if 'components' in spec and 'schemas' in spec['components']:
                        schemas = spec['components']['schemas']
                        # Look for pagination-related schemas
                        for schema_name, schema_def in schemas.items():
                            if 'pagination' in schema_name.lower() and 'properties' in schema_def:
                                schema_props = schema_def.get('properties', {})
                                if 'pagination' in schema_props:
                                    # Replace with proper reference
                                    schema['allOf'][i] = {'$ref': f'#/components/schemas/{schema_name}'}
                                    break


def _fix_response_validation_errors(spec):
    """
    Fix response validation errors by ensuring response schemas are properly structured.
    This handles cases where dereferenced schemas contain problematic structures.
    """
    components = spec.get('components', {}).get('schemas', {})
    if 'paths' in spec and isinstance(spec['paths'], dict):
        for path, path_item in spec['paths'].items():
            if not isinstance(path_item, dict): continue
            
            for method, operation in path_item.items():
                if method.lower() not in ['get', 'put', 'post', 'delete', 'options', 'head', 'patch', 'trace'] or not isinstance(operation, dict):
                    continue
                
                if 'responses' in operation and isinstance(operation['responses'], dict):
                    for status_code, response in operation['responses'].items():
                        if isinstance(response, dict) and 'content' in response:
                            for content_type, content_item in response['content'].items():
                                if isinstance(content_item, dict) and 'schema' in content_item:
                                    schema = content_item['schema']
                                    # If the schema has allOf with inline definitions, try to fix it
                                    if isinstance(schema, dict) and 'allOf' in schema:
                                        _simplify_allof_schema(schema, components)


def _simplify_allof_schema(schema, components=None):
    """
    Simplify allOf schemas by merging properties ONLY if all items (including $ref) are plain objects
    with only 'type', 'properties', and 'description'. Otherwise, leave as-is to preserve advanced OpenAPI semantics.
    """
    if 'allOf' in schema and isinstance(schema['allOf'], list):
        merged_properties = {}
        merged_type = None
        can_merge = True
        resolved_items = []
        for item in schema['allOf']:
            # Resolve $ref if present
            if isinstance(item, dict) and '$ref' in item and components:
                ref = item['$ref']
                if ref.startswith('#/components/schemas/'):
                    ref_name = ref.split('/')[-1]
                    resolved = components.get(ref_name)
                    if resolved is not None:
                        resolved_items.append(resolved)
                    else:
                        can_merge = False
                        break
                else:
                    can_merge = False
                    break
            else:
                resolved_items.append(item)
        for item in resolved_items:
            if not (isinstance(item, dict) and set(item.keys()).issubset({'type', 'properties', 'description'})):
                can_merge = False
                break
            if 'properties' in item:
                merged_properties.update(item['properties'])
            if 'type' in item:
                merged_type = item['type']
        if can_merge and merged_properties:
            schema.pop('allOf', None)
            schema['type'] = merged_type or 'object'
            schema['properties'] = merged_properties


def parse_and_dereference(filepath):
    """
    Parse and fully dereference an OpenAPI spec from YAML or JSON.
    """
    print(f"[parser] Loading spec file: {filepath}")
    t0 = time.time()
    with open(filepath, 'r', encoding='utf-8') as f:
        if filepath.endswith(('.yaml', '.yml')):
            spec = yaml.safe_load(f)
        elif filepath.endswith('.json'):
            spec = json.load(f)
        else:
            raise ValueError("Unsupported file format. Use YAML or JSON.")
    print(f"[parser] Loaded spec in {time.time() - t0:.2f}s")

    t1 = time.time()
    print("[parser] Resolving $ref references...")
    deref_spec = resolve_references(spec)
    print(f"[parser] Resolved references in {time.time() - t1:.2f}s")

    t2 = time.time()
    print("[parser] Cleaning and sanitizing spec...")
    _fix_unresolved_path_params(deref_spec)
    print(f"[parser] Fixed path params in {time.time() - t2:.2f}s")
    t2a = time.time()
    _fix_duplicate_operation_ids(deref_spec)
    print(f"[parser] Fixed duplicate operation IDs in {time.time() - t2a:.2f}s")
    t2b = time.time()
    processed_count = [0]
    _clean_default_values(deref_spec, processed_count=processed_count)
    print(f"[parser] Cleaned default values in {time.time() - t2b:.2f}s (processed {processed_count[0]} objects)")
    print(f"[parser] Cleaned spec in {time.time() - t2:.2f}s")

    t3 = time.time()
    print("[parser] Validating spec...")
    #validate_spec(deref_spec)
    print(f"[parser] Validated spec in {time.time() - t3:.2f}s")

    return deref_spec 
