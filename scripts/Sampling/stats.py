import sys
import json
import yaml
from collections import defaultdict, Counter

COMMON_PREFIXES = {'v0','2','0','rest','1','3','4','v1', 'v2', 'v3', 'v4', 'v5', 'api'}

def load_spec(filename):
    with open(filename, 'r', encoding='utf-8') as f:
        if filename.endswith('.json'):
            return json.load(f)
        else:
            return yaml.safe_load(f)

def get_auth_types(spec):
    auth_types = {}
    if 'securitySchemes' in spec.get('components', {}):
        for name, scheme in spec['components']['securitySchemes'].items():
            auth_types[name] = scheme['type']
    return auth_types

def get_resource_group(path):
    segments = [seg for seg in path.strip('/').split('/') if seg]
    for seg in segments:
        if seg.lower() not in COMMON_PREFIXES:
            return seg
    return '/'  # fallback if no valid segment found

def analyze_openapi(spec):
    paths = spec.get('paths', {})
    method_counter = Counter()
    auth_counter = Counter()
    param_counter = Counter()
    resource_group_counter = Counter()
    resource_groups = defaultdict(list)
    auth_types = get_auth_types(spec)
    endpoint_count = 0

    for path, methods in paths.items():
        resource_group = get_resource_group(path)
        for method, op in methods.items():
            if method.lower() not in ['get', 'post', 'put', 'delete', 'patch', 'options', 'head', 'trace']:
                continue
            endpoint_count += 1
            method_upper = method.upper()
            method_counter[method_upper] += 1
            resource_group_counter[resource_group] += 1

            # Authentication
            security = op.get('security', spec.get('security', []))
            if not security:
                auth_set = {'none'}
            else:
                auth_set = set()
                for sec in security:
                    for sec_name in sec:
                        auth_set.add(auth_types.get(sec_name, sec_name))
            for auth in auth_set:
                auth_counter[auth] += 1

            # Parameterization
            params = op.get('parameters', [])
            param_types = set()
            for p in params:
                param_types.add(p.get('in'))
            if op.get('requestBody'):
                param_types.add('body')
            if 'path' in param_types:
                param_counter['path'] += 1
            if 'query' in param_types:
                param_counter['query'] += 1
            if 'body' in param_types:
                param_counter['body'] += 1
            if not param_types:
                param_counter['none'] += 1

            # Store detailed info for sampling
            resource_groups[resource_group].append({
                'method': method_upper,
                'path': path,
                'auth': auth_set,
                'parameterization': param_types,
            })

    # Prepare analysis summary as a dictionary
    results = {}
    results['Total Endpoints'] = endpoint_count
    results['Endpoints by HTTP method'] = '\n'.join(f"  {method}: {count}" for method, count in method_counter.items())
    results['Endpoints by authentication type'] = '\n'.join(f"  {auth}: {count}" for auth, count in auth_counter.items())
    results['Endpoints using parameterization'] = '\n'.join(f"  {ptype}: {count}" for ptype, count in param_counter.items())
    results['Resource groups'] = '\n'.join(f"  {group}: {count} endpoints" for group, count in resource_group_counter.items())

    # Optimized sampling strategy
    sampled_endpoints = []
    sampled_endpoints_str = []
    for group, endpoints in resource_groups.items():
        sampled = []
        methods_needed = set(ep['method'] for ep in endpoints)
        auth_needed = set()
        for ep in endpoints:
            auth_needed.update(ep['auth'])
        param_with_needed = any(ep['parameterization'] for ep in endpoints)
        param_without_needed = any(not ep['parameterization'] for ep in endpoints)

        endpoint_scores = []
        for ep in endpoints:
            score = 0
            if ep['method'] in methods_needed:
                score += 1
            score += len(ep['auth'] & auth_needed)
            if ep['parameterization'] and param_with_needed:
                score += 1
            if not ep['parameterization'] and param_without_needed:
                score += 1
            endpoint_scores.append((score, ep))

        endpoint_scores.sort(reverse=True, key=lambda x: x[0])

        for _, ep in endpoint_scores:
            added = False
            if ep['method'] in methods_needed:
                methods_needed.remove(ep['method'])
                added = True
            covered_auths = ep['auth'] & auth_needed
            if covered_auths:
                auth_needed -= covered_auths
                added = True
            if ep['parameterization'] and param_with_needed:
                param_with_needed = False
                added = True
            if not ep['parameterization'] and param_without_needed:
                param_without_needed = False
                added = True
            if added:
                sampled.append(ep)
            if not methods_needed and not auth_needed and not (param_with_needed or param_without_needed):
                break

        unique_sampled = { (ep['method'], ep['path']): ep for ep in sampled }
        sampled_list = list(unique_sampled.values())
        sampled_endpoints.extend(sampled_list)

        # Prepare string for each sampled endpoint
        sampled_endpoints_str.append(f"\nResource group: {group}")
        for ep in sampled_list:
            params = list(filter(None, ep['parameterization'])) if ep['parameterization'] else []
            sampled_endpoints_str.append(f"  {ep['method']} {ep['path']} | Auth: {sorted(ep['auth'])} | Params: {sorted(params) if params else 'none'}")

    results['Selected sampled endpoints'] = '\n'.join(sampled_endpoints_str)
    results['Total Sampled Endpoints'] = len(sampled_endpoints)
    return results

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python stats.py <openapi_spec.yaml|json>")
        sys.exit(1)
    spec = load_spec(sys.argv[1])
    results = analyze_openapi(spec)
    print(json.dumps(results, indent=2))
