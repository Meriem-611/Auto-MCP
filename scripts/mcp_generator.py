"""
Module for generating MCP-compatible server stub code from OpenAPI specs.
"""
import os
import re
import keyword
import json

def sanitize_param_name(name):
    """Sanitize parameter names to be valid Python identifiers."""
    if not name:
        return "param"
    # Replace invalid characters with underscores
    sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    # Ensure it doesn't start with a number
    if sanitized and sanitized[0].isdigit():
        sanitized = 'p_' + sanitized
    # Ensure it's not empty
    if not sanitized:
        sanitized = 'param'
    # Append underscore if it's a Python keyword
    if keyword.iskeyword(sanitized):
        sanitized += '_'
    return sanitized

def sanitize_func_name(name):
    """Sanitize function names to be valid Python identifiers."""
    if not name:
        return "func"
    sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    if sanitized and sanitized[0].isdigit():
        sanitized = 'f_' + sanitized
    if not sanitized:
        sanitized = 'func'
    if keyword.iskeyword(sanitized):
        sanitized += '_'
    return sanitized

def sanitize_unicode(text):
    """Remove or replace problematic Unicode characters that cause JSON serialization errors."""
    if not text:
        return text
    
    # Remove emojis and problematic characters
    # Remove emoji and other problematic Unicode
    text = re.sub(r'[\U0001F600-\U0001F64F]', '', text)  # Emoticons
    text = re.sub(r'[\U0001F300-\U0001F5FF]', '', text)  # Misc symbols and pictographs
    text = re.sub(r'[\U0001F680-\U0001F6FF]', '', text)  # Transport and map symbols
    text = re.sub(r'[\U0001F1E0-\U0001F1FF]', '', text)  # Regional indicator symbols
    text = re.sub(r'[\U00002600-\U000027BF]', '', text)  # Misc symbols
    text = re.sub(r'[\U0001F900-\U0001F9FF]', '', text)  # Supplemental symbols and pictographs
    
    # Replace specific problematic characters
    #text = text.replace('', '[US]')
    text = text.replace('⚠️', '[WARNING]')
    text = text.replace('🔗', '[LINK]')
    text = text.replace('🇺🇸', '[US]')
    
    # Handle surrogate pairs and invalid Unicode
    try:
        text.encode('utf-8')
    except UnicodeEncodeError:
        # Remove problematic characters
        text = ''.join(char for char in text if ord(char) < 0x10000)
    
    return text
def resolve_param_ref(param, spec):
    """Resolve a $ref in a parameter object if present."""
    if not isinstance(param, dict):
        return param
    if '$ref' in param:
        ref_path = param['$ref']
        if ref_path.startswith('#/'):
            parts = ref_path.lstrip('#/').split('/')
            ref_obj = spec
            try:
                for part in parts:
                    if not isinstance(ref_obj, dict) or part not in ref_obj:
                        return param  # Return original if can't resolve
                    ref_obj = ref_obj[part]
                return ref_obj if isinstance(ref_obj, dict) else param
            except Exception:
                return param
    return param 
def detect_and_rename_duplicates(func_params):
    """Detect duplicate parameter names and rename them with location prefixes."""
    seen_params = {}
    renamed_params = []
    
    for param_tuple in func_params:
        param_str, location = param_tuple
        
        # Extract parameter name (everything before ':')
        if ':' in param_str:
            param_name = param_str.split(':')[0].strip()
            param_rest = param_str.split(':', 1)[1].strip()
        else:
            param_name = param_str.strip()
            param_rest = ''
        
        if param_name in seen_params:
            # This is a duplicate - rename based on location
            original_location = seen_params[param_name]['location']
            original_name = param_name
            
            # Add location prefix
            if location == 'query':
                param_name = f"q_{param_name}"
            elif location == 'header':
                param_name = f"h_{param_name}"
            elif location == 'path':
                param_name = f"p_{param_name}"
            else:
                param_name = f"x_{param_name}"
            
            # Update the parameter string
            if param_rest:
                param_str = f"{param_name}: {param_rest}"
            else:
                param_str = param_name
                
            print(f"Warning: Renamed duplicate parameter '{original_name}' to '{param_name}' (original: {original_location}, duplicate: {location})")
        else:
            # First time seeing this parameter
            seen_params[param_name] = {'location': location}
        
        renamed_params.append(param_str)
    
    return renamed_params
#this added to handle required body schema and add it in the description
def format_request_body_schema(req_body, spec):
    """
    Extract and format requestBody schema information for tool description.
    Returns a formatted string describing the expected body structure.
    """
    if not req_body or not isinstance(req_body, dict):
        return ""
    
    content = req_body.get('content', {})
    if not content:
        return ""
    
    # Get JSON schema (most common)
    json_content = content.get('application/json', {})
    if not json_content:
        # Try to get first content type
        json_content = list(content.values())[0] if content else {}
    
    schema = json_content.get('schema', {})
    if not schema:
        return ""
    
    # Resolve $ref if present
    if '$ref' in schema:
        ref_path = schema['$ref']
        if ref_path.startswith('#/components/schemas/'):
            schema_name = ref_path.split('/')[-1]
            components = spec.get('components', {}).get('schemas', {})
            if schema_name in components:
                schema = components[schema_name]
    
    # Handle allOf by merging schemas
    if 'allOf' in schema:
        merged_schema = {'properties': {}, 'required': []}
        for item in schema.get('allOf', []):
            # Resolve $ref in allOf items
            if '$ref' in item:
                ref_path = item['$ref']
                if ref_path.startswith('#/components/schemas/'):
                    schema_name = ref_path.split('/')[-1]
                    components = spec.get('components', {}).get('schemas', {})
                    if schema_name in components:
                        item = components[schema_name]
            
            # Merge properties
            if 'properties' in item:
                merged_schema['properties'].update(item['properties'])
            # Merge required fields
            if 'required' in item:
                merged_schema['required'].extend(item['required'])
        
        # Use merged schema
        schema = merged_schema
    
    # Format schema information
    schema_info = []
    
    # Get required fields
    required = schema.get('required', [])
    
    # Get properties
    properties = schema.get('properties', {})
    if properties:
        schema_info.append("\n\nRequest Body Schema:")
        schema_info.append("The 'body' parameter should be a JSON object with the following structure:")
        
        for prop_name, prop_schema in properties.items():
            prop_type = prop_schema.get('type', 'any')
            prop_desc = prop_schema.get('description', '')
            is_required = prop_name in required
            
            # Handle array types
            if prop_type == 'array':
                items = prop_schema.get('items', {})
                item_type = items.get('type', 'any')
                if '$ref' in items:
                    ref_name = items['$ref'].split('/')[-1]
                    item_type = ref_name
                prop_type = f"array of {item_type}"
            
            # Handle object types with $ref
            if prop_type == 'object' and '$ref' in prop_schema:
                ref_name = prop_schema['$ref'].split('/')[-1]
                prop_type = ref_name
            
            req_marker = " (required)" if is_required else " (optional)"
            schema_info.append(f"  - {prop_name} ({prop_type}){req_marker}")
            if prop_desc:
                schema_info.append(f"    {prop_desc[:100]}")
    
    # Add examples if available
    examples = json_content.get('examples', {})
    if examples:
        schema_info.append("\nExample request body:")
        # Get first example
        first_example = list(examples.values())[0]
        if isinstance(first_example, dict) and 'value' in first_example:
            example_value = first_example['value']
        else:
            example_value = first_example
        
        # Format as JSON string (truncated if too long)
        try:
            example_json = json.dumps(example_value, indent=2)
            if len(example_json) > 500:
                example_json = example_json[:500] + "..."
            schema_info.append(f"```json\n{example_json}\n```")
        except:
            pass
    
    return "\n".join(schema_info)

def generate_mcp_stub_stub_only(spec, output_dir):
    """
    Generate only server_stub.py (stub-only mode).
    Does NOT generate .env file or log files.
    Used when only the server stub needs to be regenerated.
    
    Args:
        spec: The OpenAPI spec (should already be filtered and merged)
        output_dir: Output directory for generated files
    """
    return generate_mcp_stub(spec, output_dir, stub_only=True)

def generate_mcp_stub(spec, output_dir, stub_only=False):
    """
    Generate two files:
    1. oauth_login_server.py (if OAuth2 is detected): Flask server for OAuth2 login/callback, updates .env with tokens.
    2. server_stub.py: MCP server using FastMCP, loads tokens from .env, no Flask imports, only outputs valid JSON.
    
    Args:
        spec: The OpenAPI spec
        output_dir: Output directory for generated files
        stub_only: If True, only generate server_stub.py (skip oauth_login_server.py)
    """
    os.makedirs(output_dir, exist_ok=True)
    stub_path = os.path.join(output_dir, 'server_stub.py')
    oauth_path = os.path.join(output_dir, 'oauth_login_server.py')
    
    # Detect OAuth2 scheme and flow
    oauth2_scheme = None
    oauth2_flow = None
    oauth2_details = None
    auth_methods = spec.get('components', {}).get('securitySchemes', spec.get('securityDefinitions', {}))

    # ✅ Normalize: handle both dict and list
    if isinstance(auth_methods, list):
        # Convert list to dict-like structure (assign index as key if unnamed)
        auth_methods = {
            f"scheme_{i}": method for i, method in enumerate(auth_methods) if isinstance(method, dict)
        }
    elif not isinstance(auth_methods, dict):
        auth_methods = {}

    # Now safe to iterate
    for scheme_name, details in auth_methods.items():
        if not isinstance(details, dict):
            continue
        if details.get('type', '').lower() == 'oauth2':
            oauth2_scheme = scheme_name.upper()
            oauth2_details = details
            flows = details.get('flows', {})
            if flows and isinstance(flows, dict):
                oauth2_flow = list(flows.keys())[0].upper()
            break

    # Detect required header parameters with enum values from spec
    # These will be automatically set in generated code
    auto_header_defaults = {}
    components = spec.get('components', {})
    parameters = components.get('parameters', {})
    if isinstance(parameters, dict):
        for param_name, param_def in parameters.items():
            if not isinstance(param_def, dict):
                continue
            # Check if it's a required header parameter
            if param_def.get('in') == 'header' and param_def.get('required', False):
                header_name = param_def.get('name')
                schema = param_def.get('schema', {})
                if isinstance(schema, dict):
                    enum_values = schema.get('enum', [])
                    if enum_values and header_name:
                        # Store the header name and its default enum value
                        auto_header_defaults[header_name] = enum_values[0]  # Use first enum value
    
    # 1. Generate oauth_login_server.py if OAuth2 is present (skip in stub_only mode)
    if oauth2_scheme and oauth2_details and not stub_only:
        flows = oauth2_details.get('flows', {})
        with open(oauth_path, 'w', encoding='utf-8') as f:
            f.write('# OAuth2 Login Flask Server (generated)\n')
            f.write('import os\nimport base64\nimport requests\nfrom flask import Flask, request, redirect, session\nfrom dotenv import load_dotenv\nimport secrets\nimport hashlib\nimport base64 as b64\nload_dotenv()\napp = Flask(__name__)\napp.secret_key = os.getenv("OAUTH2_SECRET_KEY", secrets.token_hex(16))\n\n')
            f.write('print("[OAUTH LOGIN SERVER] Flask OAuth2 login server running.")\n')
            for flow_key, flow_details in flows.items():
                flow_upper = flow_key.upper()
                authorization_url = flow_details.get('authorizationUrl', '')
                token_url = flow_details.get('tokenUrl', '')
                # Authorization Code Flow (with PKCE support)
                if flow_key == 'authorizationCode':
                    # /login endpoint (with PKCE support)
                    f.write(f'''@app.route('/login')\ndef login():\n    client_id = os.getenv('{oauth2_scheme}_{flow_upper}_CLIENT_ID')\n    redirect_uri = os.getenv('{oauth2_scheme}_{flow_upper}_REDIRECT_URI')\n    scopes = os.getenv('{oauth2_scheme}_{flow_upper}_SCOPES', '')\n    use_pkce = os.getenv('{oauth2_scheme}_{flow_upper}_PKCE', 'false').lower() == 'true'\n    auth_url = '{authorization_url}'\n    if use_pkce:\n        code_verifier = b64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b'=').decode('utf-8')\n        session['code_verifier'] = code_verifier\n        code_challenge = b64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b'=').decode('utf-8')\n        pkce_params = f'&code_challenge={{code_challenge}}&code_challenge_method=S256'\n    else:\n        pkce_params = ''\n    state = secrets.token_urlsafe(16)\n    session['oauth_state'] = state\n    url = (f"{{auth_url}}?client_id={{client_id}}&response_type=code&redirect_uri={{redirect_uri}}&scope={{scopes.replace(' ', '%20')}}" + pkce_params + f"&state={{state}}")\n    return f'<a href="{{url}}">Login</a>'\n\n''')
                    # /callback endpoint (with PKCE support)
                    f.write(f'''@app.route('/callback')\ndef callback():\n    code = request.args.get('code')\n    if not code:\n        return 'No code provided', 400\n    client_id = os.getenv('{oauth2_scheme}_{flow_upper}_CLIENT_ID')\n    client_secret = os.getenv('{oauth2_scheme}_{flow_upper}_CLIENT_SECRET')\n    redirect_uri = os.getenv('{oauth2_scheme}_{flow_upper}_REDIRECT_URI')\n    token_url = '{token_url}'\n    use_pkce = os.getenv('{oauth2_scheme}_{flow_upper}_PKCE', 'false').lower() == 'true'\n    headers = {{'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'}}\n    data = {{\n        'grant_type': 'authorization_code',\n        'code': code,\n        'redirect_uri': redirect_uri,\n        'client_id': client_id\n    }}\n    if use_pkce and 'code_verifier' in session:\n        data['code_verifier'] = session['code_verifier']\n    else:\n        data['client_secret'] = client_secret\n    response = requests.post(token_url, headers=headers, data=data)\n    if response.status_code != 200:\n        return f'Failed to get token: {{response.text}}', 400\n    # Robustly handle both JSON and URL-encoded responses\n    try:\n        tokens = response.json()\n    except Exception:\n        import urllib.parse\n        tokens = dict(urllib.parse.parse_qsl(response.text))\n    from dotenv import set_key\n    env_path = os.path.join(os.path.dirname(__file__), ".env")\n    set_key(env_path, f"{oauth2_scheme}_{flow_upper}_ACCESS_TOKEN", tokens.get("access_token", ""))\n    set_key(env_path, f"{oauth2_scheme}_{flow_upper}_REFRESH_TOKEN", tokens.get("refresh_token", ""))\n    return f'Access token: {{tokens.get("access_token")}}<br>Refresh token: {{tokens.get("refresh_token")}}'\n\n''')
                # Implicit Flow
                elif flow_key == 'implicit':
                    f.write(f'''@app.route('/login_implicit')\ndef login_implicit():\n    client_id = os.getenv('{oauth2_scheme}_{flow_upper}_CLIENT_ID')\n    redirect_uri = os.getenv('{oauth2_scheme}_{flow_upper}_REDIRECT_URI')\n    scopes = os.getenv('{oauth2_scheme}_{flow_upper}_SCOPES', '')\n    auth_url = '{authorization_url}'\n    state = secrets.token_urlsafe(16)\n    session['oauth_state'] = state\n    url = f"{{auth_url}}?client_id={{client_id}}&response_type=token&redirect_uri={{redirect_uri}}&scope={{scopes.replace(' ', '%20')}}&state={{state}}"\n    return redirect(url)\n\n''')
                # Client Credentials Flow
                elif flow_key == 'clientCredentials':
                    f.write(f"""
@app.route('/token_client_credentials')
def token_client_credentials():
    client_id = os.getenv('{oauth2_scheme}_{flow_upper}_CLIENT_ID')
    client_secret = os.getenv('{oauth2_scheme}_{flow_upper}_CLIENT_SECRET')
    token_url = '{token_url}'
    import base64
    headers = {{
        'Content-Type': 'application/x-www-form-urlencoded',
        'Authorization': 'Basic ' + base64.b64encode(f'{{client_id}}:{{client_secret}}'.encode()).decode()
    }}
    data = {{'grant_type': 'client_credentials'}}
    response = requests.post(token_url, headers=headers, data=data)
    if response.status_code != 200:
        return f'Failed to get token: {{response.text}}', 400
    tokens = response.json()
    from dotenv import set_key
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    set_key(env_path, f"{oauth2_scheme}_{flow_upper}_ACCESS_TOKEN", tokens.get("access_token", ""))
    return f'Access token: {{tokens.get("access_token")}}'

""")
            f.write("if __name__ == '__main__':\n    app.run(debug=True, port=8888)\n")
    
    # 2. Generate server_stub.py (MCP only, no Flask)
    with open(stub_path, 'w', encoding='utf-8') as f:
        indent = '    '
        f.write('# This file is auto-generated by mcp_generator.py.\n')
        f.write('# MCP server stub generated from OpenAPI spec.\n')
        f.write('import os\nimport json\nimport re\nfrom typing import Optional, Union, Any\nfrom dotenv import load_dotenv\nload_dotenv()\n')
        f.write('from fastmcp import FastMCP\n')
        f.write('import requests\n')
        f.write('import sys\n')
        f.write('# MCP servers must only output JSON-RPC to stdout, so use stderr for logging\n')
        f.write('print("[MCP SERVER] FastMCP server running.", file=sys.stderr)\n')
        
        f.write('if __name__ == "__main__":\n')
        f.write(f'{indent}mcp = FastMCP("my-server")\n\n')
        
        # Generate MCP tools
        for path, methods in spec.get('paths', {}).items():
            # Ensure 'methods' is a dictionary before iterating
            if not isinstance(methods, dict):
                continue

            for method, op in methods.items():
                # The operation object MUST be a dictionary.
                if not isinstance(op, dict):
                    continue
                
                # Get raw description and summary
                description = op.get('description', '')
                summary = op.get('summary', '')
                
                # Combine summary and description for a more complete tool description
                full_description = f"{summary}: {description}" if summary and description else summary or description
                
                # Sanitize Unicode characters that cause JSON serialization errors
                full_description = sanitize_unicode(full_description)

                # Determine the correct base URL, prioritizing operation, then path, then global
                op_servers = op.get('servers', [])
                path_servers = methods.get('servers', [])
                global_servers = spec.get('servers', [])
                
                base_url = 'https://api.example.com' # Default fallback
                server_obj = None
                if op_servers and isinstance(op_servers[0], dict) and 'url' in op_servers[0]:
                    server_obj = op_servers[0]
                    base_url = op_servers[0]['url']
                elif path_servers and isinstance(path_servers[0], dict) and 'url' in path_servers[0]:
                    server_obj = path_servers[0]
                    base_url = path_servers[0]['url']
                elif global_servers and isinstance(global_servers[0], dict) and 'url' in global_servers[0]:
                    server_obj = global_servers[0]
                    base_url = global_servers[0]['url']
                # ADDITION: fallback for Swagger 2.0 (host, basePath, schemes)
                elif 'host' in spec:
                    scheme = spec.get('schemes', ['https'])[0]
                    base_path = spec.get('basePath', '')
                    base_url = f"{scheme}://{spec['host']}{base_path}"
                
                # Resolve server variables (e.g., {protocol}, {domain}, {port}) using defaults
                if server_obj and 'variables' in server_obj:
                    variables = server_obj.get('variables', {})
                    for var_name, var_def in variables.items():
                        if isinstance(var_def, dict):
                            # Use default value if available
                            default_value = var_def.get('default', '')
                            if default_value:
                                # Replace {variable} with default value
                                base_url = base_url.replace(f'{{{var_name}}}', str(default_value))
                            else:
                                # If no default, try to get from enum (use first value)
                                enum_values = var_def.get('enum', [])
                                if enum_values:
                                    base_url = base_url.replace(f'{{{var_name}}}', str(enum_values[0]))
                                else:
                                    # If no default or enum, try environment variable
                                    env_var = os.getenv(f'MEILISEARCH_{var_name.upper()}')
                                    if env_var:
                                        base_url = base_url.replace(f'{{{var_name}}}', env_var)
                                    else:
                                        # Last resort: use a reasonable default based on variable name
                                        if var_name.lower() == 'protocol':
                                            base_url = base_url.replace(f'{{{var_name}}}', 'https')
                                        elif var_name.lower() == 'domain':
                                            base_url = base_url.replace(f'{{{var_name}}}', 'localhost')
                                        elif var_name.lower() == 'port':
                                            base_url = base_url.replace(f'{{{var_name}}}', '7700')

                # From here, we can safely use .get() on 'op'
                # Prefer operationId for function name if present
                raw_func_name = op.get('operationId')
                # If no operationId, generate from path and method (don't use x-* vendor extensions)
                if not raw_func_name:
                    func_name_base = f"{method.lower()}_{re.sub(r'[^a-zA-Z0-9_]', '_', path.strip('/').replace('{', '').replace('}', ''))}"
                else:
                    func_name_base = raw_func_name
                # Sanitize the function name
                func_name = sanitize_func_name(func_name_base)[:64].rstrip('_')
                # If sanitization resulted in empty string (e.g., Unicode-only operationId), fall back to path-based name
                if not func_name or func_name == '_' or func_name.strip() == '':
                    func_name_base = f"{method.lower()}_{re.sub(r'[^a-zA-Z0-9_]', '_', path.strip('/').replace('{', '').replace('}', ''))}"
                    func_name = sanitize_func_name(func_name_base)[:64].rstrip('_')
                # Final safety check - ensure func_name is never empty
                if not func_name or func_name.strip() == '':
                    func_name = f"{method.lower()}_endpoint"
                
                # Get parameters from both path and operation level
                path_level_params = methods.get('parameters', [])
                op_level_params = op.get('parameters', [])

                # Ensure we have lists to avoid errors
                if not isinstance(path_level_params, list):
                    path_level_params = []
                if not isinstance(op_level_params, list):
                    op_level_params = []
                
                # Resolve any $refs in parameters (in case they weren't resolved earlier)
                path_level_params = [resolve_param_ref(p, spec) for p in path_level_params]
                op_level_params = [resolve_param_ref(p, spec) for p in op_level_params]
                
                # Combine and remove duplicates, preferring operation-level definitions
                combined_params_dict = {}
                for p in path_level_params:
                    if isinstance(p, dict) and 'name' in p and 'in' in p:
                        combined_params_dict[(p['name'], p['in'])] = p
                for p in op_level_params:
                    if isinstance(p, dict) and 'name' in p and 'in' in p:
                        combined_params_dict[(p['name'], p['in'])] = p
                
                params = list(combined_params_dict.values())

                # Separate parameters by type, safely handling non-dict elements
                path_params = [p for p in params if isinstance(p, dict) and p.get('in') == 'path']
                query_params = [p for p in params if isinstance(p, dict) and p.get('in') == 'query']
                header_params = [p for p in params if isinstance(p, dict) and p.get('in') == 'header']
                
                # Get request body info
                has_body = 'requestBody' in op
                
                # Add requestBody schema information to description if present
                if has_body:
                    req_body = op.get('requestBody', {})
                    schema_info = format_request_body_schema(req_body, spec)
                    if schema_info:
                        full_description += schema_info
                        # Re-sanitize after adding schema info
                        full_description = sanitize_unicode(full_description)
                
                # Use json.dumps to create a safely escaped Python string literal.
                # The result includes quotes, so we slice them off.
                sanitized_description = json.dumps(full_description)[1:-1]
                
                # Write the tool decorator (moved here so sanitized_description is available)
                f.write(f'{indent}@mcp.tool(name=\'{func_name}\', description="{sanitized_description}")\n')
                
                # Auth handling
                # Safely extract security, prioritizing operation-level definitions
                security = op.get("security", None)
                if security is None:
                    security = spec.get("security", [])
                # Ensure security is a list
                if not isinstance(security, list):
                    security = []
                auth_methods = spec.get("components", {}).get("securitySchemes")
                if not auth_methods:
                    auth_methods = spec.get("securityDefinitions", {})
                if not isinstance(auth_methods, dict):
                    auth_methods = {}
                if isinstance(auth_methods, list):
                    auth_methods = {f"scheme_{i}": m for i, m in enumerate(auth_methods) if isinstance(m, dict)}
                elif not isinstance(auth_methods, dict):
                    auth_methods = {}

                # --- Begin new OR/AND security logic ---
                auth_code = ''
                auth_headers_code = ''
                if security:
                    auth_code += f"{indent*2}auth_satisfied = False\n"
                    auth_code += f"{indent*2}auth_error_msgs = []\n"
                    for idx, sec_entry in enumerate(security):
                        if not isinstance(sec_entry, dict):
                            continue
                        # Start try block for this security option
                        auth_code += f"{indent*2}if not auth_satisfied:\n"
                        auth_code += f"{indent*3}try:\n"
                        # For AND logic within this option
                        for sec_name in sec_entry:
                            details = auth_methods.get(sec_name)
                            if not isinstance(details, dict):
                                continue
                            typ = details.get("type", "").lower()
                            if typ == "apikey":
                                in_ = details.get("in", "header")
                                name = details.get("name", "X-API-KEY")
                                env_var_name = f"{sec_name.upper()}_API_KEY"
                                # Sanitize variable name to ensure it's valid Python identifier
                                var_name = sanitize_param_name(f"api_key_{sec_name.lower()}")
                                auth_code += f"{indent*4}{var_name} = os.getenv('{env_var_name}')\n"
                                auth_code += f"{indent*4}if not {var_name}:\n{indent*5}raise ValueError('Missing API key in .env: {env_var_name}')\n"
                                if in_ == "header":
                                    auth_code += f"{indent*4}headers['{name}'] = {var_name}\n"
                                elif in_ == "query":
                                    auth_code += f"{indent*4}params['{name}'] = {var_name}\n"
                            elif typ == "oauth2":
                                flows = details.get("flows", {})
                                flow = list(flows.keys())[0] if isinstance(flows, dict) and flows else "authorizationCode"
                                env_var = f"{sec_name.upper()}_{flow.upper()}_ACCESS_TOKEN"
                                auth_code += f"{indent*4}access_token = os.getenv('{env_var}')\n"
                                auth_code += f"{indent*4}if not access_token:\n{indent*5}raise ValueError('Missing OAuth2 access token in .env: {env_var}')\n"
                                auth_code += f"{indent*4}headers['Authorization'] = f'Bearer {{access_token}}'\n"
                            elif typ == "http":
                                scheme = details.get("scheme", "").lower()
                                if scheme == "bearer":
                                    env_var_name = f"{sec_name.upper()}_TOKEN"
                                    auth_code += f"{indent*4}token = os.getenv('{env_var_name}')\n"
                                    auth_code += f"{indent*4}if not token:\n{indent*5}raise ValueError('Missing Bearer token in .env: {env_var_name}')\n"
                                    auth_code += f"{indent*4}headers['Authorization'] = f'Bearer {{token}}'\n"
                                elif scheme == "basic":
                                    user_env = f"{sec_name.upper()}_USERNAME"
                                    pass_env = f"{sec_name.upper()}_PASSWORD"
                                    auth_code += f"{indent*4}username = os.getenv('{user_env}')\n"
                                    auth_code += f"{indent*4}password = os.getenv('{pass_env}')\n"
                                    auth_code += f"{indent*4}if not (username and password):\n{indent*5}raise ValueError('Missing Basic Auth credentials in .env: {user_env}, {pass_env}')\n"
                                    auth_code += f"{indent*4}import base64\n"
                                    auth_code += f"{indent*4}token_bytes = (username + ':' + password).encode()\n"
                                    auth_code += f"{indent*4}basic_token = base64.b64encode(token_bytes).decode()\n"
                                    auth_code += f"{indent*4}headers['Authorization'] = f'Basic {{basic_token}}'\n"
                        # If all AND requirements passed:
                        auth_code += f"{indent*4}auth_satisfied = True\n"
                        auth_code += f"{indent*3}except Exception as e:\n"
                        auth_code += f"{indent*4}auth_error_msgs.append(str(e))\n"
                    auth_code += f"{indent*2}if not auth_satisfied:\n{indent*3}raise ValueError('No valid authentication found. Details: ' + '; '.join(auth_error_msgs))\n"
                # --- End new OR/AND security logic ---
                
                # Check if this is a merged operation
                is_merged = op.get('x-merged', False)
                collection_path = op.get('x-collection-path', '')
                detail_path = op.get('x-detail-path', '')
                
                # Build function signature
                func_params = []
                
                # For merged operations, ID parameters become optional
                # For regular operations, path parameters are required
                for param in path_params:
                    pname = sanitize_param_name(param.get('name'))
                    param_type = 'str'  # Most path params are strings
                    
                    # Safely get schema and check its type
                    schema_obj = param.get('schema')
                    schema = schema_obj if isinstance(schema_obj, dict) else {}
                    
                    if schema.get('type') == 'integer':
                        param_type = 'int'
                    elif schema.get('type') == 'number':
                        param_type = 'float'
                    
                    # Check if this is an ID parameter (for merged operations)
                    param_name = param.get('name', '').lower()
                    is_id_param = 'id' in param_name
                    
                    if is_merged and is_id_param:
                        # Make ID parameter optional for merged operations
                        func_params.append((f"{pname}: Optional[{param_type}] = None", 'path'))
                    else:
                        # Regular required path parameter
                        func_params.append((f"{pname}: {param_type}", 'path'))
                
                # Add query parameters (optional with defaults)
                for param in query_params:
                    pname = sanitize_param_name(param.get('name'))
                    param_type = 'Optional[str]'
                    
                    # Safely get schema and check its type
                    schema_obj = param.get('schema')
                    schema = schema_obj if isinstance(schema_obj, dict) else {}

                    if schema.get('type') == 'integer':
                        param_type = 'Optional[int]'
                    elif schema.get('type') == 'number':
                        param_type = 'Optional[float]'
                    elif schema.get('type') == 'boolean':
                        param_type = 'Optional[bool]'
                    func_params.append((f"{pname}: {param_type} = None", 'query'))
                
                # Add header parameters (optional)
                for param in header_params:
                    if not isinstance(param, dict): continue
                    pname = sanitize_param_name(param.get('name'))
                    func_params.append((f"{pname}: Optional[str] = None", 'header'))
                
                # Add body parameter if present
                if has_body:
                    # Determine the type of requestBody from the spec
                    req_body = op.get('requestBody', {})
                    body_type = 'dict'  # default
                    is_array_body = False
                    if isinstance(req_body, dict):
                        content = req_body.get('content', {})
                        if content:
                            # Get the first content type (usually application/json)
                            first_content = list(content.values())[0] if content else {}
                            schema = first_content.get('schema', {})
                            if isinstance(schema, dict) and schema.get('type') == 'array':
                                # Use Union[str, dict] instead of including list, since MCP tool interface
                                # doesn't support list types directly (users can pass JSON string or dict)
                                body_type = 'Union[str, dict]'  # Accept JSON string or dict (not list - tool interface limitation)
                                is_array_body = True
                    func_params.append((f"body: Optional[{body_type}] = None", 'body'))
                
                # Detect and rename duplicates
                func_params = detect_and_rename_duplicates(func_params)
                
                # Create function signature
                params_str = ', '.join(func_params) if func_params else ''
                
                # HTTP method
                http_method = method.upper()
                
                # Sanitize description for safe single-line string
                description = op.get('description', f'Auto-generated MCP handler for {http_method} {path}')
                if description:
                    # Sanitize Unicode characters
                    description = sanitize_unicode(description)
                    # Replace problematic newlines
                    description = description.replace('\n', ' ').replace('\r', ' ')
                    # Replace both triple double and triple single quotes with safe placeholders
                    description = description.replace('"""', '[TRIPLE_DQ]')
                    description = description.replace("'''", '[TRIPLE_SQ]')
                    docstring_delim = "'''"
                else:
                    description = f'Auto-generated MCP handler for {http_method} {path}'
                    docstring_delim = '"""'
                
                # Only add parameters to the handler if there are any
                param_lines_mcp = []
                if param_lines_mcp:
                    params_str = 'request'
                
                # Sanitize path placeholders to match sanitized param names
                def repl(m):
                    original_name = m.group(1)
                    sanitized_name = sanitize_param_name(original_name)
                    return f'{{{sanitized_name}}}'
                
                # For merged operations, we'll build the path conditionally
                if is_merged:
                    sanitized_collection_path = re.sub(r'\{([\w-]+)\}', repl, collection_path)
                    sanitized_detail_path = re.sub(r'\{([\w-]+)\}', repl, detail_path)
                else:
                    sanitized_path = re.sub(r'\{([\w-]+)\}', repl, path)

                # Build the handler
                f.write(f"{indent}def {func_name}({params_str}):\n")
                f.write(f'{indent*2}{docstring_delim}{description}{docstring_delim}\n')
                if param_lines_mcp:
                    f.write('\n'.join(param_lines_mcp) + '\n')
                
                # Initialize request components
                # Allow base URL to be overridden via environment variable
                f.write(f'{indent*2}base_url = os.getenv("BASE_URL", "{base_url}")\n')
                
                # For merged operations, choose path based on ID parameter
                # Use 'api_url' instead of 'url' to avoid conflicts with query parameters named 'url'
                if is_merged:
                    # Find the ID parameter name
                    id_param_name = None
                    for param in path_params:
                        param_name = param.get('name', '').lower()
                        if 'id' in param_name:
                            id_param_name = sanitize_param_name(param.get('name'))
                            break
                    
                    if id_param_name:
                        f.write(f'{indent*2}# Merged operation: use detail path if ID provided, collection path otherwise\n')
                        f.write(f'{indent*2}if {id_param_name} is not None:\n')
                        f.write(f'{indent*3}api_url = f"{{base_url}}{sanitized_detail_path}"\n')
                        f.write(f'{indent*2}else:\n')
                        f.write(f'{indent*3}api_url = f"{{base_url}}{sanitized_collection_path}"\n')
                    else:
                        # Fallback if we can't find ID param
                        f.write(f'{indent*2}api_url = f"{{base_url}}{sanitized_collection_path}"\n')
                else:
                    f.write(f'{indent*2}api_url = f"{{base_url}}{sanitized_path}"\n')
                # Fix double slashes in URL (except after protocol like https://)
                f.write(f'{indent*2}# Fix double slashes in URL (except after protocol)\n')
                f.write(f'{indent*2}api_url = re.sub(r"(?<!:)/+", "/", api_url)\n')
                f.write(f'{indent*2}params = {{}}\n')
                f.write(f'{indent*2}headers = {{}}\n')
                f.write(f'{indent*2}cookies = {{}}\n')
                # Inject extra headers from .env if present (inside function body, after headers dict is created)
                f.write(f"{indent*2}extra_headers = os.getenv('EXTRA_HEADERS')\n")
                f.write(f"{indent*2}if extra_headers:\n")
                f.write(f"{indent*3}try:\n")
                f.write(f"{indent*4}for k, v in json.loads(extra_headers).items():\n")
                f.write(f"{indent*5}headers[k] = v\n")
                f.write(f"{indent*3}except Exception as e:\n")
                f.write(f"{indent*4}print('Failed to parse EXTRA_HEADERS:', e, file=sys.stderr)\n")
                
                # Add auth code
                if auth_code:
                    f.write(auth_code)
                
                # Handle path parameters (validation and URL substitution)
                for param in path_params:
                    if not isinstance(param, dict): continue
                    original_name = param.get('name')
                    pname = sanitize_param_name(original_name)
                    param_name_lower = original_name.lower()
                    is_id_param = 'id' in param_name_lower
                    
                    # For merged operations, ID params are optional and already handled in URL selection above
                    # Skip validation for merged ID params (URL is already set conditionally)
                    if is_merged and is_id_param:
                        # ID parameter is optional - URL selection already handled above, no need for validation
                        pass
                    else:
                        # Required path parameter
                        f.write(f"{indent*2}if {pname} is None:\n")
                        f.write(f"{indent*3}raise ValueError('Missing required path parameter: {original_name}')\n")
                
                # Handle query parameters
                for param in query_params:
                    if not isinstance(param, dict): continue
                    original_name = param.get('name')
                    pname = sanitize_param_name(original_name)
                    f.write(f"{indent*2}if {pname} is not None:\n")
                    f.write(f"{indent*3}params['{original_name}'] = {pname}\n")
              
                # Handle header parameters
                for param in header_params:
                    pname = sanitize_param_name(param['name'])
                    env_var = param['name'].upper().replace('-', '_')
                    f.write(f"{indent*2}header_value = {pname} if {pname} is not None else os.getenv('{env_var}')\n")
                    f.write(f"{indent*2}if header_value is not None:\n{indent*3}headers['{param['name']}'] = header_value\n")
                
                # Automatically set required headers with enum defaults from spec (if not already set)
                if auto_header_defaults:
                    for header_name, default_value in auto_header_defaults.items():
                        f.write(f"{indent*2}# Auto-set required header '{header_name}' from spec\n")
                        f.write(f"{indent*2}if '{header_name}' not in headers:\n")
                        f.write(f"{indent*3}headers['{header_name}'] = '{default_value}'\n")
                
                # Add auth headers
                if auth_headers_code:
                    f.write(auth_headers_code)
                
                # Handle request body
                body_handling = ''
                if has_body:
                    # Try to get content type
                    content_type = 'application/json'
                    req_body = op.get('requestBody', {})
                    content = req_body.get('content', {})
                    if content:
                        content_type = list(content.keys())[0]
                    
                    # Check if this is an array request body that needs normalization
                    is_array_body = False
                    if isinstance(req_body, dict):
                        content_obj = req_body.get('content', {})
                        if content_obj:
                            first_content = list(content_obj.values())[0] if content_obj else {}
                            schema = first_content.get('schema', {})
                            if isinstance(schema, dict) and schema.get('type') == 'array':
                                is_array_body = True
                    
                    if is_array_body:
                        # Normalize body to list format - accept JSON string or dict (not list - MCP tool interface limitation)
                        f.write(f"{indent*2}# Normalize body to list format (accept JSON string or dict with list value)\n")
                        f.write(f"{indent*2}if body is not None:\n")
                        f.write(f"{indent*3}if isinstance(body, str):\n")
                        f.write(f"{indent*4}# Parse JSON string\n")
                        f.write(f"{indent*4}try:\n")
                        f.write(f"{indent*5}json_data = json.loads(body)\n")
                        f.write(f"{indent*4}except json.JSONDecodeError:\n")
                        f.write(f"{indent*5}raise ValueError('Invalid JSON string in body parameter')\n")
                        f.write(f"{indent*3}elif isinstance(body, dict):\n")
                        f.write(f"{indent*4}# Extract list from dict (try common keys or first list value)\n")
                        f.write(f"{indent*4}if 'ips' in body and isinstance(body['ips'], list):\n")
                        f.write(f"{indent*5}json_data = body['ips']\n")
                        f.write(f"{indent*4}elif 'items' in body and isinstance(body['items'], list):\n")
                        f.write(f"{indent*5}json_data = body['items']\n")
                        f.write(f"{indent*4}elif 'data' in body and isinstance(body['data'], list):\n")
                        f.write(f"{indent*5}json_data = body['data']\n")
                        f.write(f"{indent*4}else:\n")
                        f.write(f"{indent*5}# Try to find first list value in dict\n")
                        f.write(f"{indent*5}list_values = [v for v in body.values() if isinstance(v, list)]\n")
                        f.write(f"{indent*5}if list_values:\n")
                        f.write(f"{indent*6}json_data = list_values[0]\n")
                        f.write(f"{indent*5}else:\n")
                        f.write(f"{indent*6}raise ValueError('Could not extract list from dict body. Provide a JSON string or dict with list value.')\n")
                        f.write(f"{indent*3}else:\n")
                        f.write(f"{indent*4}raise ValueError(f'Body must be a JSON string or dict, got {{type(body).__name__}}')\n")
                        f.write(f"{indent*2}else:\n")
                        f.write(f"{indent*3}json_data = None\n")
                    else:
                        f.write(f"{indent*2}json_data = body if body is not None else None\n")
                    f.write(f"{indent*2}headers['Content-Type'] = '{content_type}'\n")
                    body_handling = ', json=json_data'
                
                # Make the HTTP request
                f.write(f"{indent*2}try:\n")
                f.write(f"{indent*3}resp = requests.{method.lower()}(api_url, params=params, headers=headers, cookies=cookies{body_handling})\n")
                f.write(f"{indent*3}resp.raise_for_status()  # Raise an exception for bad status codes\n")
                f.write(f"{indent*3}if 'application/json' in resp.headers.get('Content-Type', ''):\n")
                f.write(f"{indent*4}return resp.json()\n")
                f.write(f"{indent*3}else:\n")
                f.write(f"{indent*4}return {{'raw': resp.text, 'status_code': resp.status_code}}\n")
                f.write(f"{indent*2}except requests.exceptions.RequestException as e:\n")
                f.write(f"{indent*3}raise ValueError(f'HTTP request failed: {{str(e)}}')\n")
                f.write(f"{indent*2}except Exception as e:\n")
                f.write(f"{indent*3}raise ValueError(f'Unexpected error: {{str(e)}}')\n\n")
        
        f.write(f'{indent}mcp.run(transport="stdio")\n')
