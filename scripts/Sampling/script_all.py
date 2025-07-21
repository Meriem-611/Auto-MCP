import os
import pandas as pd
import importlib.util
import sys

def import_stats_module(stats_path):
    spec = importlib.util.spec_from_file_location("stats", stats_path)
    stats = importlib.util.module_from_spec(spec)
    sys.modules["stats"] = stats
    spec.loader.exec_module(stats)
    return stats

def main():
    stats = import_stats_module('stats.py')
    specs = [f for f in os.listdir('.') if f.endswith(('.json', '.yaml', '.yml'))]
    print(f"Found {len(specs)} OpenAPI specs.")

    results = []
    for spec_file in specs:
        print(f"Analyzing {spec_file}...")
        try:
            spec = stats.load_spec(spec_file)
            data = stats.analyze_openapi(spec)
            data['API Name'] = spec_file
            results.append(data)
            print(f"✔ Done with {spec_file}")
        except Exception as e:
            print(f"❌ Error analyzing {spec_file}: {e}")
            # Add a row with just the API name and error
            results.append({'API Name': spec_file, 'Error': str(e)})

    df = pd.DataFrame(results)
    cols = [
        'API Name', 'Total Endpoints', 'Endpoints by HTTP method',
        'Endpoints by authentication type', 'Endpoints using parameterization',
        'Resource groups', 'Selected sampled endpoints', 'Total Sampled Endpoints', 'Error'
    ]
    for col in cols:
        if col not in df.columns:
            df[col] = ''
    df = df[cols]
    df.to_excel('openapi_full_output.xlsx', index=False)
    print("\n✅ Full output saved to openapi_full_output.xlsx")

if __name__ == "__main__":
    main()
