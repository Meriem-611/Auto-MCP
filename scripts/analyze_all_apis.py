"""
Analyze all OpenAPI specs to count tools after filtering and ID merging.
Reports statistics for each API.
"""
import os
import sys
import json
import yaml
import argparse
from pathlib import Path
from parser import parse_and_dereference
from filter_risky_LLM import filter_risky_endpoints_llm
from merge_operations import merge_operations
import pandas as pd

# Fix Windows console encoding for emojis
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


def count_operations_in_spec(spec):
    """Count total operations in an OpenAPI spec."""
    count = 0
    for path, methods in spec.get('paths', {}).items():
        if isinstance(methods, dict):
            for method, op in methods.items():
                if method.lower() in ['get', 'put', 'post', 'delete', 'options', 'head', 'patch', 'trace']:
                    if isinstance(op, dict):
                        count += 1
    return count


def load_spec_fast(spec_path):
    """Load OpenAPI spec without full dereferencing (faster)."""
    with open(spec_path, 'r', encoding='utf-8') as f:
        if spec_path.endswith(('.yaml', '.yml')):
            return yaml.safe_load(f)
        elif spec_path.endswith('.json'):
            return json.load(f)
        else:
            raise ValueError("Unsupported file format")


def analyze_api(spec_path, include_overrides=None, disable_merge=False, output_dir=None, skip_parsing=False):
    """
    Analyze a single OpenAPI spec.
    Returns statistics about operations before/after filtering and merging.
    """
    try:
        if skip_parsing:
            # Fast path: just load the spec without dereferencing
            spec = load_spec_fast(spec_path)
        else:
            # Full path: parse and dereference (slower but more accurate)
            spec = parse_and_dereference(spec_path)
        
        # Count original operations
        original_count = count_operations_in_spec(spec)
        
        # Apply risky filtering
        temp_output = output_dir or os.path.dirname(spec_path)
        api_name = Path(spec_path).stem
        filtered_spec, filtered_ops = filter_risky_endpoints_llm(
            spec,
            temp_output,
            include_overrides or set(),
            api_name=api_name,
        )
        after_filtering_count = count_operations_in_spec(filtered_spec)
        filtered_count = len(filtered_ops)
        
        # Apply ID merging
        merged_spec, merge_groups = merge_operations(
            filtered_spec, 
            temp_output, 
            disable_merge
        )
        after_merging_count = count_operations_in_spec(merged_spec)
        merged_groups_count = len(merge_groups)
        
        # Calculate reduction
        ops_removed_by_filtering = original_count - after_filtering_count
        ops_removed_by_merging = after_filtering_count - after_merging_count
        total_reduction = original_count - after_merging_count
        
        return {
            'api_name': Path(spec_path).stem,
            'original_ops': original_count,
            'after_filtering': after_filtering_count,
            'after_merging': after_merging_count,
            'filtered_ops': filtered_count,
            'merged_groups': merged_groups_count,
            'ops_removed_by_filtering': ops_removed_by_filtering,
            'ops_removed_by_merging': ops_removed_by_merging,
            'total_reduction': total_reduction,
            'pct_reduction': round((total_reduction / original_count * 100) if original_count > 0 else 0, 2),
            'status': 'success'
        }
    except Exception as e:
        return {
            'api_name': Path(spec_path).stem,
            'original_ops': 0,
            'after_filtering': 0,
            'after_merging': 0,
            'filtered_ops': 0,
            'merged_groups': 0,
            'ops_removed_by_filtering': 0,
            'ops_removed_by_merging': 0,
            'total_reduction': 0,
            'pct_reduction': 0.0,
            'status': f'error: {str(e)}'
        }


def find_openapi_files(directory):
    """Find all OpenAPI spec files (YAML/JSON) in a directory."""
    openapi_files = []
    for root, dirs, files in os.walk(directory):
        # Skip common directories
        dirs[:] = [d for d in dirs if d not in ['node_modules', '.git', '__pycache__', 'output_directory', 'notion_mcp', 'redis_mcp', 'Github_mcp', 'apaleo_output_files', 'advice_mcp']]
        
        for file in files:
            if file.endswith(('.yaml', '.yml', '.json')):
                filepath = os.path.join(root, file)
                # Skip output files
                if 'output' in filepath.lower() or 'mcp' in filepath.lower():
                    continue
                openapi_files.append(filepath)
    return openapi_files


def main():
    parser = argparse.ArgumentParser(
        description="Analyze all OpenAPI specs and report tool counts after filtering and merging."
    )
    parser.add_argument('--directory', '-d', default='OpenAPI_examples',
                       help='Directory containing OpenAPI specs (default: OpenAPI_examples)')
    parser.add_argument('--output', '-o', default='api_analysis_results.csv',
                       help='Output CSV file (default: api_analysis_results.csv)')
    parser.add_argument('--include', action='append',
                       help='Include category (can be used multiple times)')
    parser.add_argument('--disable-merge', action='store_true',
                       help='Disable ID merging for analysis')
    parser.add_argument('--skip-parsing', action='store_true',
                       help='Skip expensive parsing/dereferencing (faster but may be less accurate)')
    args = parser.parse_args()

    print("=" * 80)
    print("OPENAPI ANALYSIS: Filtering and ID Merging Statistics")
    print("=" * 80)
    print(f"\nScanning directory: {args.directory}")
    
    # Find all OpenAPI files
    openapi_files = find_openapi_files(args.directory)
    
    if not openapi_files:
        print(f"\n[ERROR] No OpenAPI files found in {args.directory}")
        return 1
    
    print(f"Found {len(openapi_files)} OpenAPI specification files\n")
    
    # Set up include overrides
    include_overrides = set()
    if args.include:
        include_overrides = set(cat.lower() for cat in args.include)
        print(f"Include overrides: {include_overrides}\n")
    
    # Analyze each API
    results = []
    for i, spec_path in enumerate(openapi_files, 1):
        api_name = Path(spec_path).name
        print(f"[{i}/{len(openapi_files)}] Analyzing: {api_name}")
        
        result = analyze_api(
            spec_path, 
            include_overrides, 
            args.disable_merge,
            output_dir=None,  # Use temp directory
            skip_parsing=args.skip_parsing
        )
        results.append(result)
        
        if result['status'] == 'success':
            print(f"  [OK] Original: {result['original_ops']} ops")
            print(f"  [OK] After filtering: {result['after_filtering']} ops (removed {result['ops_removed_by_filtering']})")
            print(f"  [OK] After merging: {result['after_merging']} ops (merged {result['merged_groups']} groups, removed {result['ops_removed_by_merging']})")
            print(f"  [OK] Total reduction: {result['total_reduction']} ops ({result['pct_reduction']}%)")
        else:
            print(f"  [ERROR] Error: {result['status']}")
        print()
    
    # Create DataFrame
    df = pd.DataFrame(results)
    
    # Sort by original operations (descending)
    df = df.sort_values('original_ops', ascending=False)
    
    # Save to CSV
    df.to_csv(args.output, index=False)
    print(f"\n[SUCCESS] Results saved to: {args.output}")
    
    # Print summary statistics
    print("\n" + "=" * 80)
    print("SUMMARY STATISTICS")
    print("=" * 80)
    
    successful = df[df['status'] == 'success']
    if len(successful) > 0:
        print(f"\nTotal APIs analyzed: {len(df)}")
        print(f"Successful: {len(successful)}")
        print(f"Errors: {len(df) - len(successful)}")
        
        print(f"\n[STATS] Operations Statistics:")
        print(f"  Total original operations: {successful['original_ops'].sum():,}")
        print(f"  Total after filtering: {successful['after_filtering'].sum():,}")
        print(f"  Total after merging: {successful['after_merging'].sum():,}")
        print(f"  Total removed by filtering: {successful['ops_removed_by_filtering'].sum():,}")
        print(f"  Total removed by merging: {successful['ops_removed_by_merging'].sum():,}")
        print(f"  Total reduction: {successful['total_reduction'].sum():,}")
        
        overall_pct = (successful['total_reduction'].sum() / successful['original_ops'].sum() * 100) if successful['original_ops'].sum() > 0 else 0
        print(f"  Overall reduction: {overall_pct:.2f}%")
        
        print(f"\n[MERGE] Merging Statistics:")
        print(f"  Total merge groups: {successful['merged_groups'].sum():,}")
        print(f"  APIs with merges: {(successful['merged_groups'] > 0).sum()}")
        
        print(f"\n[TOP 10] Top 10 APIs by Original Operations:")
        top10 = successful.head(10)[['api_name', 'original_ops', 'after_filtering', 'after_merging', 'pct_reduction']]
        print(top10.to_string(index=False))
        
        print(f"\n[REDUCTION] APIs with Most Reduction:")
        top_reduction = successful.nlargest(10, 'total_reduction')[['api_name', 'original_ops', 'after_merging', 'total_reduction', 'pct_reduction']]
        print(top_reduction.to_string(index=False))
    
    # Print errors if any
    errors = df[df['status'] != 'success']
    if len(errors) > 0:
        print(f"\n[WARNING] APIs with Errors:")
        for _, row in errors.iterrows():
            print(f"  - {row['api_name']}: {row['status']}")
    
    print("\n" + "=" * 80)
    print("[SUCCESS] Analysis complete!")
    print("=" * 80)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

