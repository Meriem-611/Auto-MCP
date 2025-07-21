"""
Module for generating a .env file with authentication placeholders.
"""
import os

def generate_env_file(auth_methods, output_dir):
    """
    Generate a .env file in the output directory with placeholders for auth credentials.
    """
    os.makedirs(output_dir, exist_ok=True)
    env_path = os.path.join(output_dir, '.env')
    with open(env_path, 'w', encoding='utf-8') as f:
        for name, details in auth_methods.items():
            # Add description as a comment if present
            description = details.get('description')
            if description:
                for line in description.splitlines():
                    f.write(f"# {line}\n")
            typ = details.get('type', '').lower()
            if typ == 'apikey':
                f.write(f"{name.upper()}_API_KEY=\n")
                if 'name' in details:
                    f.write(f"{name.upper()}_HEADER={details['name']}\n")
            elif typ == 'http':
                scheme = details.get('scheme', '').lower()
                if scheme == 'basic':
                    f.write(f"{name.upper()}_USERNAME=\n{name.upper()}_PASSWORD=\n")
                elif scheme == 'bearer':
                    f.write(f"{name.upper()}_TOKEN=\n")
                else:
                    f.write(f"{name.upper()}_CREDENTIAL=\n")
            elif typ == 'oauth2':
                flows = details.get('flows', {})
                for flow_name, flow in flows.items():
                    prefix = f"{name.upper()}_{flow_name.upper()}"
                    if flow_name == 'authorizationCode':
                        f.write(f"{prefix}_CLIENT_ID=\n")
                        f.write(f"{prefix}_CLIENT_SECRET=\n")
                        f.write(f"{prefix}_REDIRECT_URI=\n")
                        if 'authorizationUrl' in flow or 'authorization_url' in flow:
                            f.write(f"{prefix}_AUTH_URL={flow.get('authorizationUrl', flow.get('authorization_url', ''))}\n")
                        if 'tokenUrl' in flow or 'token_url' in flow:
                            f.write(f"{prefix}_TOKEN_URL={flow.get('tokenUrl', flow.get('token_url', ''))}\n")
                        scopes = flow.get('scopes', {})
                        if scopes:
                            f.write(f"{prefix}_SCOPES={' '.join(scopes.keys())}\n")
                        f.write(f"{prefix}_PKCE=false\n")
                    elif flow_name == 'implicit':
                        f.write(f"{prefix}_CLIENT_ID=\n")
                        f.write(f"{prefix}_REDIRECT_URI=\n")
                        if 'authorizationUrl' in flow or 'authorization_url' in flow:
                            f.write(f"{prefix}_AUTH_URL={flow.get('authorizationUrl', flow.get('authorization_url', ''))}\n")
                        scopes = flow.get('scopes', {})
                        if scopes:
                            f.write(f"{prefix}_SCOPES={' '.join(scopes.keys())}\n")
                    elif flow_name == 'clientCredentials':
                        f.write(f"{prefix}_CLIENT_ID=\n")
                        f.write(f"{prefix}_CLIENT_SECRET=\n")
                        if 'tokenUrl' in flow or 'token_url' in flow:
                            f.write(f"{prefix}_TOKEN_URL={flow.get('tokenUrl', flow.get('token_url', ''))}\n")
                        scopes = flow.get('scopes', {})
                        if scopes:
                            f.write(f"{prefix}_SCOPES={' '.join(scopes.keys())}\n")
            elif typ == 'openidconnect':
                f.write(f"{name.upper()}_ID_TOKEN=\n")
                if 'openIdConnectUrl' in details:
                    f.write(f"{name.upper()}_OPENID_URL={details['openIdConnectUrl']}\n")
            else:
                f.write(f"{name.upper()}_CREDENTIAL=\n") 