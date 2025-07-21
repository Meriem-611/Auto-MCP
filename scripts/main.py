"""
Main entry point for the OpenAPI MCP server stub generator.
Handles CLI arguments and orchestrates the workflow.
"""
import argparse
import sys
from parser import parse_and_dereference
from auth_handler import extract_auth_methods, enforce_auth_method
from filter import filter_endpoints_by_tags
from env_generator import generate_env_file
from mcp_generator import generate_mcp_stub
import time


def main():
    parser = argparse.ArgumentParser(
        description="Generate MCP-compatible server stubs from OpenAPI specs."
    )
    parser.add_argument('--input', '-i', required=True, help='Path to OpenAPI spec (YAML or JSON)')
    parser.add_argument('--output', '-o', required=True, help='Output directory for generated code')
    parser.add_argument('--auth', help='Enforce a specific authentication method (e.g., apiKey, oauth2)')
    parser.add_argument('--include-tags', help='Comma-separated list of tags to include')
    parser.add_argument('--exclude-tags', help='Comma-separated list of tags to exclude')
    args = parser.parse_args()

    try:
        start_total = time.time()
        print(f"[INFO] Parsing and dereferencing spec: {args.input}")
        start_parse = time.time()
        spec = parse_and_dereference(args.input)
        print(f"[INFO] Done parsing and dereferencing in {time.time() - start_parse:.2f}s")
        print(f"DEBUG: Type after parse_and_dereference: {type(spec)}")
        auth_methods = extract_auth_methods(spec)
        print(f"DEBUG: Extracted auth_methods: {auth_methods}")
        if args.auth:
            enforce_auth_method(auth_methods, args.auth)
        start_filter = time.time()
        filtered_spec = filter_endpoints_by_tags(spec, args.include_tags, args.exclude_tags)
        print(f"[INFO] Done filtering endpoints in {time.time() - start_filter:.2f}s")
        print(f"DEBUG: Type after filter_endpoints_by_tags: {type(filtered_spec)}")
        print("DEBUG: About to generate .env file...")
        start_env = time.time()
        generate_env_file(auth_methods, args.output)
        print(f"DEBUG: Finished generating .env file in {time.time() - start_env:.2f}s.")
        print("DEBUG: About to generate MCP server stub...")
        start_stub = time.time()
        generate_mcp_stub(filtered_spec, args.output)
        print(f"DEBUG: Finished generating MCP server stub in {time.time() - start_stub:.2f}s.")
        print(f"MCP server stub generated in {args.output}")
        print(f"[INFO] Total time: {time.time() - start_total:.2f}s")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main() 
