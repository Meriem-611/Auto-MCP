"""
Main entry point for the OpenAPI MCP server stub generator.
Handles CLI arguments and orchestrates the workflow.
"""
import argparse
import sys
import os
import json
from datetime import datetime
from pathlib import Path
from parser import parse_and_dereference
from auth_handler import extract_auth_methods, enforce_auth_method
from filter import filter_endpoints_by_tags
from filter_risky_LLM import filter_risky_endpoints_llm
from merge_operations import merge_operations
from env_generator import generate_env_file
from mcp_generator import generate_mcp_stub
import time


def save_generation_config(output_dir, args):
    """Save generation configuration to a JSON file for later reuse."""
    config_path = os.path.join(output_dir, '.automcp_config.json')
    config = {
        'input': args.input,
        'output': args.output,
        'auth': args.auth,
        'include_tags': args.include_tags,
        'exclude_tags': args.exclude_tags,
        'include': args.include if args.include else [],
        'disable_id_merge': args.disable_id_merge,
        'generated_at': datetime.now().isoformat(),
        'version': '1.0'
    }
    os.makedirs(output_dir, exist_ok=True)
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2)
    print(f"[INFO] Generation config saved to: {config_path}")


def load_generation_config(output_dir):
    """Load generation configuration from JSON file."""
    config_path = os.path.join(output_dir, '.automcp_config.json')
    if not os.path.exists(config_path):
        return None
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        return config
    except Exception as e:
        print(f"[WARNING] Could not load generation config: {e}")
        return None


def merge_args_with_config(args, config):
    """Merge command-line args with saved config, giving precedence to CLI args."""
    if not config:
        return args
    
    # Create a new namespace with config values as defaults
    merged = argparse.Namespace()
    
    # Use CLI args if provided, otherwise use config
    merged.input = args.input if args.input else config.get('input')
    merged.output = args.output if args.output else config.get('output')
    merged.auth = args.auth if args.auth else config.get('auth')
    merged.include_tags = args.include_tags if args.include_tags else config.get('include_tags')
    merged.exclude_tags = args.exclude_tags if args.exclude_tags else config.get('exclude_tags')
    merged.include = args.include if args.include else config.get('include', [])
    merged.disable_id_merge = args.disable_id_merge if args.disable_id_merge else config.get('disable_id_merge', False)
    merged.stub_only = args.stub_only  # stub_only is always from CLI (regeneration mode)
    
    return merged


def main():
    parser = argparse.ArgumentParser(
        description="Generate MCP-compatible server stubs from OpenAPI specs."
    )
    parser.add_argument('--input', '-i', required=True, help='Path to OpenAPI spec (YAML or JSON)')
    parser.add_argument('--output', '-o', required=True, help='Output directory for generated code')
    parser.add_argument('--auth', help='Enforce a specific authentication method (e.g., apiKey, oauth2)')
    parser.add_argument('--include-tags', help='Comma-separated list of tags to include')
    parser.add_argument('--exclude-tags', help='Comma-separated list of tags to exclude')
    parser.add_argument(
        '--include',
        action='append',
        help='Keep these categories instead of filtering them (repeatable). '
        'Categories: deprecated, destructive (DELETE), auth, authorization, settings. '
        'Legacy aliases: admin (settings), security (authorization).',
    )
    parser.add_argument('--disable-id-merge', action='store_true', help='Disable merging of list/detail operations into single tools')
    parser.add_argument('--stub-only', action='store_true', help='Only regenerate server_stub.py (skip .env generation and log files)')
    parser.add_argument('--ignore-config', action='store_true', help='Ignore saved generation config and use only CLI arguments')
    args = parser.parse_args()

    # Load saved config if it exists and user hasn't explicitly ignored it
    config = None
    if not args.ignore_config:
        config = load_generation_config(args.output)
        if config:
            print(f"[INFO] Loaded saved generation config from previous run")
            # In stub-only mode, we can use saved input if not provided
            if args.stub_only and not args.input:
                if config.get('input'):
                    args.input = config['input']
                    print(f"[INFO] Using saved input spec: {args.input}")
            
            # Merge config with CLI args (CLI takes precedence)
            # Only merge flags that weren't explicitly provided
            if config:
                if not args.include_tags and config.get('include_tags'):
                    args.include_tags = config.get('include_tags')
                if not args.exclude_tags and config.get('exclude_tags'):
                    args.exclude_tags = config.get('exclude_tags')
                if not args.include and config.get('include'):
                    args.include = config.get('include', [])
                if not args.disable_id_merge and config.get('disable_id_merge'):
                    args.disable_id_merge = config.get('disable_id_merge', False)
                if not args.auth and config.get('auth'):
                    args.auth = config.get('auth')
                
                if args.stub_only:
                    print(f"[INFO] Using saved flags: include={config.get('include', [])}, disable_id_merge={config.get('disable_id_merge', False)}, include_tags={config.get('include_tags')}, exclude_tags={config.get('exclude_tags')}")
    
    # Ensure input is set (required)
    if not args.input:
        print("[ERROR] --input is required. Provide the OpenAPI spec path.")
        sys.exit(1)

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
        print(f"[INFO] Done filtering endpoints by tags in {time.time() - start_filter:.2f}s")
        print(f"DEBUG: Type after filter_endpoints_by_tags: {type(filtered_spec)}")
        
        # Apply risky endpoint filtering
        start_risky_filter = time.time()
        include_overrides = set()
        if args.include:
            include_overrides = set(cat.lower() for cat in args.include)
            print(f"[INFO] Include overrides: {include_overrides}")
        api_name = Path(args.input).stem
        filtered_spec, filtered_ops = filter_risky_endpoints_llm(
            filtered_spec,
            args.output,
            include_overrides,
            skip_logs=args.stub_only,
            api_name=api_name,
        )
        print(f"[INFO] Done filtering risky endpoints in {time.time() - start_risky_filter:.2f}s")
        print(f"DEBUG: Type after filter_risky_endpoints_llm: {type(filtered_spec)}")
        
        # Apply operation merging (list/detail merge)
        start_merge = time.time()
        filtered_spec, merge_groups = merge_operations(filtered_spec, args.output, args.disable_id_merge, skip_logs=args.stub_only)
        print(f"[INFO] Done merging operations in {time.time() - start_merge:.2f}s")
        print(f"DEBUG: Type after merge_operations: {type(filtered_spec)}")
        if not args.stub_only:
            print("DEBUG: About to generate .env file...")
            start_env = time.time()
            generate_env_file(auth_methods, args.output)
            print(f"DEBUG: Finished generating .env file in {time.time() - start_env:.2f}s.")
        else:
            print("[INFO] Stub-only mode: Skipping .env file generation")
        
        print("DEBUG: About to generate MCP server stub...")
        start_stub = time.time()
        generate_mcp_stub(filtered_spec, args.output, stub_only=args.stub_only)
        print(f"DEBUG: Finished generating MCP server stub in {time.time() - start_stub:.2f}s.")
        
        # Save generation config for future regenerations (only if not stub-only or if it's a full generation)
        if not args.stub_only:
            save_generation_config(args.output, args)
        
        print(f"MCP server stub generated in {args.output}")
        print(f"[INFO] Total time: {time.time() - start_total:.2f}s")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main() 
