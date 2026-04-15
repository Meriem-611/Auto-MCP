# AutoMCP: Automatic MCP Server Generator from OpenAPI

A tool that automatically generates Model Context Protocol (MCP) server stubs from OpenAPI specifications. This project converts OpenAPI/Swagger definitions into fully functional MCP servers that can be used with AI assistants and development tools. It doesn't count on naive one-to-one mappings but instead it filters risky operations and groups low level operations.

## 🚀 Features

- **Automatic MCP Server Generation**: Converts OpenAPI specs to FastMCP-compatible server stubs
- **OAuth2 Support**: Generates Flask-based OAuth2 login servers for secure authentication
- **Multiple Authentication Methods**: Supports API keys, Bearer tokens, Basic auth, and OAuth2 flows
- **Endpoint Filtering**: Filter endpoints by tags (include/exclude)
- **Risky Endpoint Filtering (LLM + Structural)**: Filters risky operations using structural rules (`DELETE`, `deprecated`) and semantic LLM categories (Authentication, Access Control & Authorization, System Configuration)
- **Operation Merging**: Merges compatible list/detail GET operations into a single tool
- **Environment Configuration**: Auto-generates `.env` files with proper authentication placeholders
- **OpenAPI 2.0 & 3.x Support**: Handles both Swagger and OpenAPI specifications
- **Robust Error Handling**: Comprehensive validation and error handling for malformed specs
- **Unicode Sanitization**: Handles problematic characters and emojis in descriptions

## 📋 Prerequisites
Before using this server, make sure you have:
- Python 3.8+
- pip installed
- Basic knowledge of OpenAPI files (.yaml or .json)
- [Claude](https://www.anthropic.com/product/claude) or [Cursor](https://www.cursor.so/) installed if integrating

## 🛠️ Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd Auto-MCP
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

## 📖 Usage

### Basic Usage

Generate an MCP server from an OpenAPI specification:

```bash
python scripts/main.py --input path/to/spec.yaml --output output_directory
```

### Advanced Usage

```bash
python scripts/main.py \
  --input spotify.yaml \
  --output output_dir_spotify \
  --include-tags "playlists,albums" \
  --exclude-tags "deprecated"
```

### Command Line Options

- `--input, -i`: Path to OpenAPI specification file (YAML or JSON) **[Required]**
- `--output, -o`: Output directory for generated files **[Required]**
- `--include-tags`: Comma-separated list of tags to include to select the tools you want in the generated MCP Server
- `--exclude-tags`: Comma-separated list of tags to exclude the tools you don't need if any
- `--include`: Keep categories that would otherwise be filtered. Repeatable. Supported values: `deprecated`, `destructive`, `auth`, `authorization`, `settings` 
- `--disable-id-merge`: Disable list/detail GET merge step
- `--stub-only`: Regenerate `server_stub.py` only (skips `.env` generation and log files)

## 📁 Generated Files

The tool generates the following files in your output directory:
(apaleo_output_files is provided as an example)

### 1. `server_stub.py`
The main MCP server implementation using FastMCP:
- Auto-generated API endpoint handlers
- Proper authentication handling
- Request/response processing
- Error handling and validation

### 2. `oauth_login_server.py` (if OAuth2 detected)
Flask-based OAuth2 authentication server:
- Login endpoints for different OAuth2 flows
- PKCE (Proof Key for Code Exchange) support
- Token management and storage
- Automatic `.env` file updates

### 3. `.env`
Environment configuration file with:
- Authentication credentials placeholders
- OAuth2 configuration variables
- API keys and tokens
- Custom headers support

### 4. `filtered_operations.log`
Risk filtering report:
- Total operations analyzed, filtered, and allowed
- Per-operation filtering reasons

### 5. `merged_operations.log`
Merge report:
- Number of merge groups
- Which list/detail GET operations were merged

## 🔧 Authentication Support

### API Key Authentication
```bash
# Generated .env entry
API_KEY_NAME_API_KEY=your_api_key_here
```

### OAuth2 Authentication
```bash
# Generated .env entries for Authorization Code flow
OAUTH2_AUTHORIZATIONCODE_CLIENT_ID=your_client_id
OAUTH2_AUTHORIZATIONCODE_CLIENT_SECRET=your_client_secret
OAUTH2_AUTHORIZATIONCODE_REDIRECT_URI=http://localhost:8888/callback
OAUTH2_AUTHORIZATIONCODE_SCOPES=read write
```

### Bearer Token Authentication
```bash
# Generated .env entry
AUTH_NAME_TOKEN=your_bearer_token_here
```

## 🚀 Running the Generated Server

### 1. Set up authentication
Edit the generated `.env` file with your credentials:
```bash
cd output_directory
# Edit .env file with your API credentials
```

### 2. For OAuth2 services, start the login server first:
```bash
python oauth_login_server.py
```

**Available OAuth2 endpoints:**
- **Authorization Code Flow**: Visit `http://localhost:8888/login` to authenticate
- **Implicit Flow**: Visit `http://localhost:8888/login_implicit` to authenticate  
- **Client Credentials Flow**: Visit `http://localhost:8888/token_client_credentials` to get access token
- **PKCE Support**: Add `PKCE=true` to your `.env` file for enhanced security

**Note**: The specific endpoints available depend on the OAuth2 flows defined in the OpenAPI specification.

### 3. Run the MCP server:
```bash
python server_stub.py
```

## 🎧 Example Workflow (Spotify)

### Spotify API Example

1. **Generate the server stub:**
```bash
python scripts/main.py --input spotify.yaml --output spotify_mcp
```

2. **Configure authentication:**
```bash
cd spotify_mcp
# Edit .env with your Spotify API credentials
```

3. **Start OAuth2 login (if needed):**
```bash
python oauth_login_server.py
# Visit http://localhost:8888/login
```

4. **Run the MCP server:**
```bash
python server_stub.py
```

## ⚙️ Tool Integration: Claude Desktop vs Cursor

### ✅ Claude Integration (Claude Desktop Only)
You must have the Claude Desktop App installed. Then:

1. Go to **Settings → Developer Settings**
2. Click **“Edit Configuration”** (`claude_desktop_config.json`)
3. Paste the following into your config (example shown for a Spotify tool):

```json
{
  "mcpServers": {
    "spotify": {
      "command": "python",                               
      "args": [
        "C:/Path/To/Your/AutoMCP/spotify_mcp/server_stub.py",             // 🔁 Change to the full path of your tool folder                          
      ]
    }
  }
}
```

---

### ✅ Cursor Integration
You can use the same config block, but the file that needs to be updated is `mcp.json` inside Cursor.

## 🏛️ Architecture

### Core Components

- **`scripts/main.py`**: Entry point and CLI orchestration
- **`mcp_generator.py`**: Core MCP server generation logic
- **`parser.py`**: OpenAPI spec parsing and validation
- **`auth_handler.py`**: Authentication method extraction and enforcement
- **`filter.py`**: Endpoint filtering by tags
- **`filter_risky_LLM.py`**: Risk filtering (structural + LLM semantic categories)
- **`merge_operations.py`**: List/detail GET operation merging
- **`env_generator.py`**: Environment file generation

## 🤖 LLM Risk Filtering

Risk filtering combines:

- **Structural categories**:
  - `Destructive` (HTTP `DELETE`)
  - `Deprecated` (operation has `deprecated: true`)
- **Semantic LLM categories**:
  - `Authentication`
  - `Access Control & Authorization`
  - `System Configuration`

Semantic categories are applied conservatively and then included/excluded with the same CLI flow as other filters.

## 🔐 Azure OpenAI Setup (for LLM filtering)

We used Azure OpenAI for our LLM filtering setup in this project, but you can use whichever provider/setup you prefer as long as your integration supplies equivalent semantic labels.

If you use the current Azure-based implementation, set:

```bash
AZURE_OPENAI_API_KEY=<your_key>
AUTOMCP_AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com/
AUTOMCP_AZURE_OPENAI_API_VERSION=2025-01-01-preview
AUTOMCP_AZURE_OPENAI_MODEL=gpt-4.1
```

### Generated Code Structure

```python
# Example generated MCP tool
@mcp.tool(name='get_user_profile', description="Get user profile information")
def get_user_profile(user_id: str):
    '''Get user profile information'''
    base_url = "https://api.example.com"
    url = f"{base_url}/users/{user_id}"
    params = {}
    headers = {}
    cookies = {}
    
    # Authentication handling
    # ... auth code ...
    
    # Make HTTP request
    resp = requests.get(url, params=params, headers=headers, cookies=cookies)
    resp.raise_for_status()
    return resp.json()
```

## 🔍 Supported OpenAPI Features

- ✅ OpenAPI 2.0 (Swagger) and 3.x specifications
- ✅ All HTTP methods (GET, POST, PUT, DELETE, etc.)
- ✅ Path parameters, query parameters, headers
- ✅ Request bodies (JSON)
- ✅ Response schemas
- ✅ Security schemes (API Key, OAuth2, Basic Auth, Bearer)
- ✅ Server configurations
- ✅ Operation IDs and descriptions
- ✅ Tag-based filtering to control the number of generated tools

## 🛡️ Error Handling

Robust error handling is built into AutoMCP, but issues may still arise if your OpenAPI specification is malformed or incomplete. Common sources of errors include:

- **Missing or Incorrect Authentication Schemes:**  
  Ensure all security schemes (API keys, OAuth2, etc.) are properly defined in your spec.
- **Parameter Type Mismatches:**  
  Check that parameter types in your spec (e.g., string, integer, boolean) match their intended usage.
- **Invalid or Missing Base URLs:**  
  Verify that the `servers` section (OpenAPI 3.x) or `host`/`basePath` (Swagger 2.0) is correctly set.
  
Review error messages when calling the MCP server tools for details on what went wrong.


## Optional: SpecFixer Pre/Post Step

Optionally, you can use `SpecFixer` with AutoMCP in either of these ways:

- **Before AutoMCP generation**: run SpecFixer to analyze and repair OpenAPI issues first, then generate the MCP server from the improved spec.
- **After AutoMCP generation**: if you notice spec quality issues during or after server generation, run SpecFixer on the OpenAPI file and regenerate.

For complete SpecFixer usage, read `SpecFixer/README.md`.

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request



## Acknowledgments

- Built with [FastMCP](https://github.com/fastmcp/fastmcp) for MCP server functionality
- Uses [openapi-spec-validator](https://github.com/p1c2u/openapi-spec-validator) for specification validation

**Happy MCP Server Generation! 🚀**
