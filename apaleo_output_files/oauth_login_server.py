# OAuth2 Login Flask Server (generated)
import os
import base64
import requests
from flask import Flask, request, redirect, session
from dotenv import load_dotenv
import secrets
import hashlib
import base64 as b64
load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("OAUTH2_SECRET_KEY", secrets.token_hex(16))

print("[OAUTH LOGIN SERVER] Flask OAuth2 login server running.")
@app.route('/login')
def login():
    client_id = os.getenv('OAUTH2_AUTHORIZATIONCODE_CLIENT_ID')
    redirect_uri = os.getenv('OAUTH2_AUTHORIZATIONCODE_REDIRECT_URI')
    scopes = os.getenv('OAUTH2_AUTHORIZATIONCODE_SCOPES', '')
    use_pkce = os.getenv('OAUTH2_AUTHORIZATIONCODE_PKCE', 'false').lower() == 'true'
    auth_url = 'https://identity.apaleo.com/connect/authorize'
    if use_pkce:
        code_verifier = b64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b'=').decode('utf-8')
        session['code_verifier'] = code_verifier
        code_challenge = b64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b'=').decode('utf-8')
        pkce_params = f'&code_challenge={code_challenge}&code_challenge_method=S256'
    else:
        pkce_params = ''
    url = (f"{auth_url}?client_id={client_id}&response_type=code&redirect_uri={redirect_uri}&scope={scopes.replace(' ', '%20')}" + pkce_params)
    return f'<a href="{url}">Login</a>'

@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code:
        return 'No code provided', 400
    client_id = os.getenv('OAUTH2_AUTHORIZATIONCODE_CLIENT_ID')
    client_secret = os.getenv('OAUTH2_AUTHORIZATIONCODE_CLIENT_SECRET')
    redirect_uri = os.getenv('OAUTH2_AUTHORIZATIONCODE_REDIRECT_URI')
    token_url = 'https://identity.apaleo.com/connect/token'
    use_pkce = os.getenv('OAUTH2_AUTHORIZATIONCODE_PKCE', 'false').lower() == 'true'
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    data = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri,
        'client_id': client_id
    }
    if use_pkce and 'code_verifier' in session:
        data['code_verifier'] = session['code_verifier']
    else:
        data['client_secret'] = client_secret
    response = requests.post(token_url, headers=headers, data=data)
    if response.status_code != 200:
        return f'Failed to get token: {response.text}', 400
    tokens = response.json()
    from dotenv import set_key
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    set_key(env_path, f"OAUTH2_AUTHORIZATIONCODE_ACCESS_TOKEN", tokens.get("access_token", ""))
    set_key(env_path, f"OAUTH2_AUTHORIZATIONCODE_REFRESH_TOKEN", tokens.get("refresh_token", ""))
    return f'Access token: {tokens.get("access_token")}<br>Refresh token: {tokens.get("refresh_token")}'

if __name__ == '__main__':
    app.run(debug=True, port=8888)
