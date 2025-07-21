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

def generate_mcp_stub(spec, output_dir):
    """
    Generate two files:
    1. oauth_login_server.py (if OAuth2 is detected): Flask server for OAuth2 login/callback, updates .env with tokens.
    2. server_stub.py: MCP server using FastMCP, loads tokens from .env, no Flask imports, only outputs valid JSON.
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

    
    # 1. Generate oauth_login_server.py if OAuth2 is present
    if oauth2_scheme and oauth2_details:
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
                    f.write(f'''@app.route('/callback')\ndef callback():\n    code = request.args.get('code')\n    if not code:\n        return 'No code provided', 400\n    client_id = os.getenv('{oauth2_scheme}_{flow_upper}_CLIENT_ID')\n    client_secret = os.getenv('{oauth2_scheme}_{flow_upper}_CLIENT_SECRET')\n    redirect_uri = os.getenv('{oauth2_scheme}_{flow_upper}_REDIRECT_URI')\n    token_url = '{token_url}'\n    use_pkce = os.getenv('{oauth2_scheme}_{flow_upper}_PKCE', 'false').lower() == 'true'\n    headers = {{'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'}}    data = {{       'grant_type': 'authorization_code',\n        'code': code,\n        'redirect_uri': redirect_uri,\n        'client_id': client_id}} \n    if use_pkce and 'code_verifier' in session:\n        data['code_verifier'] = session['code_verifier']\n    else:\n        data['client_secret'] = client_secret\n    response = requests.post(token_url, headers=headers, data=data)\n    if response.status_code != 200:\n        return f'Failed to get token: {{response.text}}', 400\n    # Robustly handle both JSON and URL-encoded responses\n    try:\n        tokens = response.json()\n    except Exception:\n        import urllib.parse\n        tokens = dict(urllib.parse.parse_qsl(response.text))\n    from dotenv import set_key\n    env_path = os.path.join(os.path.dirname(__file__), ".env")\n    set_key(env_path, f"{oauth2_scheme}_{flow_upper}_ACCESS_TOKEN", tokens.get("access_token", ""))\n    set_key(env_path, f"{oauth2_scheme}_{flow_upper}_REFRESH_TOKEN", tokens.get("refresh_token", ""))\n    return f'Access token: {{tokens.get("access_token")}}<br>Refresh token: {{tokens.get("refresh_token")}}'\n\n''')
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
        f.write('import os\nimport json\nfrom typing import Optional, Union, Any\nfrom dotenv import load_dotenv\nload_dotenv()\n')
        f.write('from fastmcp import FastMCP\n')
        f.write('import requests\n')
        f.write('print("[MCP SERVER] FastMCP server running.")\n')
        
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
                
                # Use json.dumps to create a safely escaped Python string literal.
                # The result includes quotes, so we slice them off.
                sanitized_description = json.dumps(full_description)[1:-1]

                # Determine the correct base URL, prioritizing operation, then path, then global
                op_servers = op.get('servers', [])
                path_servers = methods.get('servers', [])
                global_servers = spec.get('servers', [])
                
                base_url = 'https://api.example.com' # Default fallback
                if op_servers and isinstance(op_servers[0], dict) and 'url' in op_servers[0]:
                    base_url = op_servers[0]['url']
                elif path_servers and isinstance(path_servers[0], dict) and 'url' in path_servers[0]:
                    base_url = path_servers[0]['url']
                elif global_servers and isinstance(global_servers[0], dict) and 'url' in global_servers[0]:
                    base_url = global_servers[0]['url']
                # ADDITION: fallback for Swagger 2.0 (host, basePath, schemes)
                elif 'host' in spec:
                    scheme = spec.get('schemes', ['https'])[0]
                    base_path = spec.get('basePath', '')
                    base_url = f"{scheme}://{spec['host']}{base_path}"

                # From here, we can safely use .get() on 'op'
                # Prefer operationId or x-* vendor extension for function name if present
                raw_func_name = op.get('operationId')
                if not raw_func_name:
                    # Check for vendor extension keys that look like x-*
                    for k in op.keys():
                        if k.startswith('x-'):
                            raw_func_name = k
                            break
                if not raw_func_name:
                    func_name_base = f"{method.lower()}_{re.sub(r'[^a-zA-Z0-9_]', '_', path.strip('/').replace('{', '').replace('}', ''))}"
                else:
                    func_name_base = raw_func_name
                # Sanitize the function name
                func_name = sanitize_func_name(func_name_base)[:64].rstrip('_')
                
                # Write the tool decorator
                f.write(f'{indent}@mcp.tool(name=\'{func_name}\', description="{sanitized_description}")\n')
                
                # Get parameters from both path and operation level
                path_level_params = methods.get('parameters', [])
                op_level_params = op.get('parameters', [])

                # Ensure we have lists to avoid errors
                if not isinstance(path_level_params, list):
                    path_level_params = []
                if not isinstance(op_level_params, list):
                    op_level_params = []
                
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
                                var_name = f"api_key_{sec_name.lower()}"
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
                                    auth_code += f"{indent*4}token_bytes = f'{username}:{password}'.encode()\n"
                                    auth_code += f"{indent*4}basic_token = base64.b64encode(token_bytes).decode()\n"
                                    auth_code += f"{indent*4}headers['Authorization'] = f'Basic {{basic_token}}'\n"
                        # If all AND requirements passed:
                        auth_code += f"{indent*4}auth_satisfied = True\n"
                        auth_code += f"{indent*3}except Exception as e:\n"
                        auth_code += f"{indent*4}auth_error_msgs.append(str(e))\n"
                    auth_code += f"{indent*2}if not auth_satisfied:\n{indent*3}raise ValueError('No valid authentication found. Details: ' + '; '.join(auth_error_msgs))\n"
                # --- End new OR/AND security logic ---
                
                # Build function signature
                func_params = []
                
                # Add path parameters (required)
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
                    func_params.append(("body: Optional[dict] = None", 'body'))
                
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
                sanitized_path = re.sub(r'\{(\w+)\}', repl, path)

                # Build the handler
                f.write(f"{indent}def {func_name}({params_str}):\n")
                f.write(f'{indent*2}{docstring_delim}{description}{docstring_delim}\n')
                if param_lines_mcp:
                    f.write('\n'.join(param_lines_mcp) + '\n')
                
                # Initialize request components
                f.write(f'{indent*2}base_url = "{base_url}"\n')
                f.write(f'{indent*2}url = f"{{base_url}}{sanitized_path}"\n')
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
                f.write(f"{indent*4}print('Failed to parse EXTRA_HEADERS:', e)\n")
                
                # Add auth code
                if auth_code:
                    f.write(auth_code)
                
                # Handle path parameters (validation only)
                for param in path_params:
                    if not isinstance(param, dict): continue
                    original_name = param.get('name')
                    pname = sanitize_param_name(original_name)
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
                    f.write(f"{indent*2}json_data = body if body is not None else None\n")
                    f.write(f"{indent*2}headers['Content-Type'] = '{content_type}'\n")
                    body_handling = ', json=json_data'
                
                # Make the HTTP request
                f.write(f"{indent*2}try:\n")
                f.write(f"{indent*3}resp = requests.{method.lower()}(url, params=params, headers=headers, cookies=cookies{body_handling})\n")
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