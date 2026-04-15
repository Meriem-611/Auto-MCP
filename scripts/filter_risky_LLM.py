"""
Module for filtering OpenAPI endpoints (LLM-assisted semantic labels).

**Structural** (non-LLM) features are only these two:
  - HTTP ``DELETE``
  - OpenAPI ``deprecated: true`` on the operation

**Semantic** (LLM) labels follow the provided Colab approach:
  - The LLM labels *unique tags per API* (not operations directly).
  - Tag labels are cached per API under ``<output_dir>/cache/<api_name>/llm_tag_labels.json``.
  - Operations inherit risky semantic labels from their tags.


Environment:
  AZURE_OPENAI_API_KEY
  AUTOMCP_AZURE_OPENAI_ENDPOINT
  AUTOMCP_AZURE_OPENAI_API_VERSION (default: 2025-01-01-preview)
  AUTOMCP_AZURE_OPENAI_MODEL (default: gpt-4.1)
"""
import copy
import json
import os
import re
from pathlib import Path

try:
    from openai import AzureOpenAI
except ImportError:  # pragma: no cover
    AzureOpenAI = None


# LLM label string -> internal bucket key
LLM_LABEL_TO_CATEGORY = {
    "Authentication": "auth",
    "Access Control & Authorization": "authorization",
    "System Configuration": "settings",
}

# Internal key -> LLM label (for cache export)
INTERNAL_TO_LLM_LABEL = {v: k for k, v in LLM_LABEL_TO_CATEGORY.items()}

# Legacy cache keys from earlier versions (map to current buckets)
LEGACY_BOOL_KEYS = {
    "admin": "settings",
    "security": "authorization",
}

DEFAULT_LLM_MODEL = "gpt-4o-mini"
DEFAULT_BATCH_SIZE = 18

# Display names for log reasons
REASON_DISPLAY = {
    "deprecated": "Deprecated",
    "destructive": "Delete",
    "auth": "Authentication",
    "authorization": "Authorization",
    "settings": "Settings",
}


def is_deprecated(op):
    """Structural: OpenAPI ``deprecated`` flag is true."""
    return op.get("deprecated", False) is True


def is_destructive(method):
    """Structural: HTTP method is ``DELETE``."""
    return method.upper() == "DELETE"


def operation_key(path, method):
    """Stable key for lookups (method lower, path as in spec)."""
    return f"{method.lower()} {path}"


def normalize_include_category(name):
    """CLI/include_overrides: accept legacy names admin/security."""
    if not name:
        return name
    n = name.lower().strip()
    if n == "admin":
        return "settings"
    if n == "security":
        return "authorization"
    return n


HTTP_METHODS = {"get", "put", "post", "delete", "patch", "options", "head", "trace"}

RISKY_LLM_CATEGORIES = {
    "Authentication",
    "Access Control & Authorization",
    "System Configuration",
}

SEMANTIC_CATEGORIES = [
    "Commerce & Inventory",
    "Data & Storage",
    "Authentication",
    "Events & Workflow Management",
    "User & Account Management",
    "Licensing & Policy Compliance",
    "Analytics & Insights",
    "Access Control & Authorization",
    "System Configuration",
    "Key & Credential Management",
    "Logging & Monitoring",
    "Organization & Environment Management",
    "Other",
]

CATEGORY_DESCRIPTIONS = {
    "Commerce & Inventory": (
        "Operations related to billing, pricing, payments, products, assets, vendors, business transactions, "
        "or inventory resources."
    ),
    "Data & Storage": (
        "Operations that manage stored data, databases, search systems, queries, datasets, indexes, exports, "
        "migrations, or structured data storage."
    ),
    "Authentication": (
        "Operations about credentials or responsible for verifying identity or issuing authentication credentials "
        "such as login sessions, tokens, OAuth tokens, or access tokens or anything credentials related."
    ),
    "Events & Workflow Management": (
        "Operations that manage events, schedules, triggers, actions, tasks, jobs, activities, or automated "
        "workflow orchestration."
    ),
    "User & Account Management": (
        "Operations for creating, updating, retrieving, or managing users, accounts, account members, "
        "or service accounts."
    ),
    "Licensing & Policy Compliance": (
        "Operations related to licenses, regulatory requirements, approvals, policy enforcement, standards, "
        "disputes, or compliance management."
    ),
    "Analytics & Insights": (
        "Operations that provide metrics, statistics, analyses, correlations, trends, insights, or informational "
        "summaries derived from system data."
    ),
    "Access Control & Authorization": (
        "Operations that manage permissions, roles, RBAC policies, allowlists, access groups, or access control "
        "rules determining what actions users or systems are allowed to perform."
    ),
    "System Configuration": (
        "Operations that manage system settings, preferences, configuration parameters, modes, tuning options, "
        "or other configurable system behavior."
    ),
    "Key & Credential Management": (
        "Operations about or that manage cryptographic keys, API keys, signing keys, certificates, secrets, "
        "or credential material itself."
    ),
    "Logging & Monitoring": (
        "Operations that record, retrieve, export, or manage logs, audit logs, monitoring data, or log entries."
    ),
    "Organization & Environment Management": (
        "Operations that manage organizations, tenants, enterprises, environments, or administrative domains."
    ),
    "Other": (
        "Use this category if the tag is ambiguous, generic, unclear, or does not clearly belong to any of the "
        "categories above."
    ),
}

def _short_text(s, max_len=1200):
    if s is None:
        return ""
    s = str(s).strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


def categorize_operation_structural(path, method, op):
    """
    Structural categories only: ``deprecated`` and ``destructive`` (DELETE).
    No path depth or other heuristics — those are not structural features in this module.
    """
    categories = []
    if is_deprecated(op):
        categories.append("deprecated")
    if is_destructive(method):
        categories.append("destructive")
    return categories


def _parse_json_response(raw: str):
    """Parse model JSON; strip ``` fences if present."""
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text, re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    return json.loads(text)


def _load_semantic_cache(path):
    """
    Load precomputed semantic labels.

    Supported shapes:
    - {"operations": [{"path": "...", "method": "get", "llm_labels": [...]}, ...]}
    - {"get /foo": {"auth": true, "authorization": true, "settings": false}}
    - Legacy bool keys: admin -> settings, security -> authorization
    """
    if not path or not os.path.isfile(path):
        return None

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    out = {}

    if isinstance(data, dict) and "operations" in data:
        for row in data["operations"]:
            p = row.get("path", "")
            m = str(row.get("method", "get")).lower()
            labels = row.get("llm_labels") or row.get("labels") or []
            if isinstance(labels, str):
                labels = [x.strip() for x in labels.split("|") if x.strip()]
            cats = set()
            for lbl in labels:
                c = LLM_LABEL_TO_CATEGORY.get(lbl)
                if c:
                    cats.add(c)
            out[operation_key(p, m)] = cats
        return out

    for k, v in data.items():
        if k in ("format", "version", "metadata", "operations"):
            continue
        if "|" in k and " " not in k.split("|")[0]:
            parts = k.split("|", 1)
            m, p = parts[0].lower(), parts[1]
        elif " " in str(k):
            m, _, p = str(k).partition(" ")
            m = m.lower()
        else:
            continue
        cats = set()
        if isinstance(v, dict):
            for name in ("auth", "authorization", "settings", "admin", "security"):
                if not v.get(name):
                    continue
                if name in LEGACY_BOOL_KEYS:
                    cats.add(LEGACY_BOOL_KEYS[name])
                else:
                    cats.add(name)
        elif isinstance(v, list):
            for lbl in v:
                c = LLM_LABEL_TO_CATEGORY.get(lbl)
                if c:
                    cats.add(c)
        out[operation_key(p, m)] = cats

    return out


def _write_semantic_cache(path, mapping):
    """Write cache in operations list form for portability."""
    operations = []
    for key, cats in sorted(mapping.items()):
        m, _, p = key.partition(" ")
        llm_labels = sorted(INTERNAL_TO_LLM_LABEL[c] for c in cats if c in INTERNAL_TO_LLM_LABEL)
        operations.append(
            {
                "path": p,
                "method": m,
                "llm_labels": llm_labels,
            }
        )
    payload = {"operations": operations, "format": "automcp_semantic_v1"}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def build_tag_definition_map(spec):
    tag_map = {}
    for tag_obj in spec.get("tags", []) or []:
        if isinstance(tag_obj, dict) and "name" in tag_obj:
            name = str(tag_obj["name"]).strip()
            desc = tag_obj.get("description")
            if isinstance(desc, str):
                desc = desc.strip()
            tag_map[name] = desc
    return tag_map


def collect_unique_tags(spec):
    """
    Collect unique tags across:
      - top-level spec.tags definitions
      - operation.tags occurrences
    Returns dict: tag_name -> tag_description (or None).
    """
    top_defs = build_tag_definition_map(spec)
    unique = dict(top_defs)

    paths = spec.get("paths", {}) or {}
    for _path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, op in path_item.items():
            if method.lower() not in HTTP_METHODS:
                continue
            if not isinstance(op, dict):
                continue
            tags = op.get("tags", []) or []
            for tag in tags:
                tag = str(tag).strip()
                if not tag:
                    continue
                if tag not in unique:
                    unique[tag] = None
    return unique


def build_tag_prompt(tag_name, tag_description):
    category_block = "\n\n".join(
        f"{cat} – {CATEGORY_DESCRIPTIONS[cat]}"
        for cat in SEMANTIC_CATEGORIES
        if cat != "Other"
    )

    desc = tag_description if tag_description else "None"

    return f"""
You are analyzing an OpenAPI tag extracted from an API specification.

Your task is to classify the tag into one of the predefined functional categories below.

However, do not force a classification.
If the tag does not clearly and confidently belong to one of the categories, return "Other".

Only assign a category if the tag strongly and unambiguously corresponds to that category.

Categories:

{category_block}

Other – Use this if the tag is ambiguous, unclear, too generic, or does not clearly belong to any category.

Important distinctions:

Authentication – Credentials, verifying identity or issuing login/session tokens.

Access Control & Authorization – roles, permissions, allowlists, RBAC policies that control what an authenticated entity can do.

Key & Credential Management – management of cryptographic keys, certificates, API keys, secrets, or credential material itself.

User & Account Management – managing users, accounts, service accounts, or account-level entities.

Organization & Environment Management – managing organizations, tenants, enterprises, or environments rather than individual users.

System Configuration – managing system settings, preferences, or configuration parameters.

Guidelines:

- Be conservative.
- If the tag meaning is uncertain or generic, return Other.
- Do not guess.
- Prefer Other rather than assigning an incorrect category.
- Only assign categories when confident.

Return strictly valid JSON in this format:

{{
  "categories": ["Category Name"]
}}

Tag:
{tag_name}

Tag description:
{desc}
""".strip()


def _extract_json_object(text):
    text = text.strip().replace("```json", "").replace("```", "")
    match = re.search(r"(\{.*\})", text, re.DOTALL)
    if match:
        raw = match.group(1)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            try:
                fixed = re.sub(r",\s*([\]}])", r"\1", raw)
                return json.loads(fixed)
            except Exception:
                return None
    return None


def parse_categories(text):
    parsed = _extract_json_object(text)
    if not parsed:
        return ["Other"]

    cats = parsed.get("categories", [])
    if not isinstance(cats, list):
        return ["Other"]

    cleaned = [str(x).strip() for x in cats if str(x).strip()]
    cleaned = [x for x in cleaned if x in SEMANTIC_CATEGORIES]

    return cleaned if cleaned else ["Other"]


def _azure_client_from_env():
    if AzureOpenAI is None:
        raise RuntimeError(
            "openai package is required for AzureOpenAI tag labeling. Install 'openai' and retry."
        )

    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing AZURE_OPENAI_API_KEY for AzureOpenAI.")

    endpoint = os.getenv("AUTOMCP_AZURE_OPENAI_ENDPOINT")
    api_version = os.getenv("AUTOMCP_AZURE_OPENAI_API_VERSION") or "2025-01-01-preview"

    if not endpoint:
        raise RuntimeError("Missing AUTOMCP_AZURE_OPENAI_ENDPOINT (e.g., https://<resource>.openai.azure.com/).")

    return AzureOpenAI(api_key=api_key, api_version=api_version, azure_endpoint=endpoint)


def _call_model_for_tag(prompt):
    client = _azure_client_from_env()
    model = os.getenv("AUTOMCP_AZURE_OPENAI_MODEL") or "gpt-4.1"

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are an expert in semantic classification of API concepts."},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_tokens=300,
    )

    usage = getattr(response, "usage", None)
    in_toks = getattr(usage, "prompt_tokens", 0)
    out_toks = getattr(usage, "completion_tokens", 0)
    content = response.choices[0].message.content.strip()

    return content, int(in_toks), int(out_toks)


def _load_cached_tag_labels(cache_path):
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_cached_tag_labels(cache_path, data):
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def label_unique_tags_for_api(api_name, unique_tags, output_dir):
    """
    Labels each unique tag once per API and caches the result.
    Cache path: <output_dir>/cache/<api_name>/llm_tag_labels.json
    """
    api_cache_dir = os.path.join(output_dir, "cache", api_name)
    os.makedirs(api_cache_dir, exist_ok=True)

    cache_path = os.path.join(api_cache_dir, "llm_tag_labels.json")
    raw_output_path = os.path.join(api_cache_dir, "llm_tag_raw_output.json")

    cached = _load_cached_tag_labels(cache_path)
    raw_outputs = []

    all_cached = all(tag_name in cached for tag_name in unique_tags.keys())
    if all_cached and cached:
        print(f"[filter_risky_LLM] Reusing cached tag labels for {api_name}")
        return cached, raw_outputs, True

    print(f"[filter_risky_LLM] Labeling {len(unique_tags)} unique tags for {api_name} (AzureOpenAI)...")

    idx = 0
    for tag_name in sorted(unique_tags.keys()):
        if tag_name in cached:
            continue
        idx += 1
        tag_def = unique_tags[tag_name]
        prompt = build_tag_prompt(tag_name, tag_def)
        raw_text, in_toks, out_toks = _call_model_for_tag(prompt)
        labels = parse_categories(raw_text)

        cached[tag_name] = {
            "tag": tag_name,
            "tag_definition": tag_def if tag_def else "",
            "llm_labels": labels,
            "input_tokens": in_toks,
            "output_tokens": out_toks,
        }

        raw_outputs.append(
            {
                "tag": tag_name,
                "tag_definition": tag_def if tag_def else "",
                "raw_output": raw_text,
                "parsed_labels": labels,
                "input_tokens": in_toks,
                "output_tokens": out_toks,
            }
        )

        if idx % 10 == 0:
            print(f"[filter_risky_LLM]   processed {idx} new tags...")

    _save_cached_tag_labels(cache_path, cached)
    with open(raw_output_path, "w", encoding="utf-8") as f:
        json.dump(raw_outputs, f, indent=2, ensure_ascii=False)

    return cached, raw_outputs, False


def classify_semantic_with_llm(all_operations, *, api_name, spec, output_dir):
    """
    Tag-based semantic classification (matches Colab): label unique tags and then
    propagate risky labels onto operations via their tags.

    Returns dict operation_key -> set of {'auth','authorization','settings'}.
    """
    unique_tags = collect_unique_tags(spec)
    tag_label_map, _raw_outputs, _used_cache = label_unique_tags_for_api(api_name, unique_tags, output_dir)

    tag_to_primary = {}
    for tag_name, info in tag_label_map.items():
        labels = info.get("llm_labels", ["Other"])
        primary = labels[0] if labels else "Other"
        tag_to_primary[tag_name] = primary

    result = {}
    for path, method, op in all_operations:
        tags = op.get("tags", []) or []
        risky_semantic_labels = set()
        for t in tags:
            t = str(t).strip()
            if not t:
                continue
            primary = tag_to_primary.get(t, "Other")
            if primary in RISKY_LLM_CATEGORIES:
                risky_semantic_labels.add(primary)

        cats = set()
        for lbl in risky_semantic_labels:
            c = LLM_LABEL_TO_CATEGORY.get(lbl)
            if c:
                cats.add(c)

        result[operation_key(path, method)] = cats

    return result


def build_semantic_categories(
    all_operations,
    *,
    api_name,
    spec,
    output_dir,
    use_llm=True,
):
    """Build mapping operation_key -> semantic categories (auth, authorization, settings)."""
    if not use_llm:
        print("[filter_risky_LLM] No semantic cache and use_llm=False; semantic categories empty.")
        return {}

    return classify_semantic_with_llm(
        all_operations,
        api_name=api_name,
        spec=spec,
        output_dir=output_dir,
    )


def categorize_operation(path, method, op, semantic_map):
    """Structural (deprecated + DELETE only) plus LLM semantic buckets."""
    categories = categorize_operation_structural(path, method, op)
    key = operation_key(path, method)
    for cat in semantic_map.get(key, ()):
        if cat in ("auth", "authorization", "settings") and cat not in categories:
            categories.append(cat)
    return categories


def filter_risky_endpoints_llm(
    spec,
    output_dir,
    include_overrides=None,
    skip_logs=False,
    api_name=None,
    use_llm=True,
):
    """
    Filter endpoints using structural signals (DELETE, deprecated) and LLM semantics.

    Structural = only ``deprecated`` and HTTP DELETE. Semantic = auth / authorization / settings
    from the model or cache.

    Returns (filtered_spec, filtered_operations_list) like ``filter_risky.filter_risky_endpoints``.

    include_overrides: categories to keep (not filter), e.g.
      deprecated, destructive, auth, authorization, settings
    Legacy aliases: admin -> settings, security -> authorization.
    """
    if include_overrides is None:
        include_overrides = set()
    include_overrides = {normalize_include_category(x) for x in include_overrides}

    paths = spec.get("paths", {})
    if not isinstance(paths, dict):
        return spec, []

    if not api_name:
        api_name = "api"

    all_operations = []
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, op in methods.items():
            if method.lower() not in HTTP_METHODS:
                continue
            if not isinstance(op, dict):
                continue
            all_operations.append((path, method, op))

    semantic_map = build_semantic_categories(
        all_operations,
        api_name=api_name,
        spec=spec,
        output_dir=output_dir,
        use_llm=use_llm,
    )

    categorized_ops = {
        "deprecated": [],
        "destructive": [],
        "auth": [],
        "authorization": [],
        "settings": [],
    }

    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, op in methods.items():
            if method.lower() not in ["get", "put", "post", "delete", "options", "head", "patch", "trace"]:
                continue
            if not isinstance(op, dict):
                continue

            categories = categorize_operation(path, method, op, semantic_map)
            for category in categories:
                if category in categorized_ops:
                    categorized_ops[category].append((path, method, op))

    total_ops = len(all_operations)
    print(f"[filter_risky_LLM] Total operations found: {total_ops}")

    auth_count = len(categorized_ops["auth"])
    authorization_count = len(categorized_ops["authorization"])
    settings_count = len(categorized_ops["settings"])

    auth_pct = (auth_count / total_ops * 100) if total_ops > 0 else 0
    authorization_pct = (authorization_count / total_ops * 100) if total_ops > 0 else 0
    settings_pct = (settings_count / total_ops * 100) if total_ops > 0 else 0

    print(
        f"[filter_risky_LLM] Semantic category percentages - "
        f"Authentication: {auth_pct:.1f}%, "
        f"Authorization: {authorization_pct:.1f}%, "
        f"Settings: {settings_pct:.1f}%"
    )

    filtered_operations = []
    filtered_ops_set = set()

    # Structural only: deprecated flag + DELETE (see module docstring)
    always_filter = ["deprecated", "destructive"]

    for category in always_filter:
        if category in include_overrides:
            print(f"[filter_risky_LLM] Keeping {category} operations (user override via --include {category})")
            continue

        for path, method, op in categorized_ops[category]:
            key = (path, method.lower())
            if key not in filtered_ops_set:
                reason = REASON_DISPLAY.get(category, category.title())
                filtered_operations.append((path, method, reason))
                filtered_ops_set.add(key)

    if total_ops < 10:
        print(
            f"[filter_risky_LLM] Skipping conditional filtering (authentication/authorization/settings) "
            f"for small API ({total_ops} operations < 10)"
        )
        print(
            "[filter_risky_LLM] Still applying filters for deprecated and DELETE"
        )
    else:
        percentage_map = {
            "auth": auth_pct,
            "authorization": authorization_pct,
            "settings": settings_pct,
        }

        conditional_categories = {
            "auth": (auth_pct < 30, "auth" not in include_overrides),
            "authorization": (authorization_pct < 30, "authorization" not in include_overrides),
            "settings": (settings_pct < 30, "settings" not in include_overrides),
        }

        for category, (should_filter, not_overridden) in conditional_categories.items():
            if should_filter and not_overridden:
                for path, method, op in categorized_ops[category]:
                    key = (path, method.lower())
                    if key not in filtered_ops_set:
                        pct = percentage_map[category]
                        label = REASON_DISPLAY.get(category, category.title())
                        reason = (
                            f"{label} ({len(categorized_ops[category])}/{total_ops} = {pct:.1f}% of operations)"
                        )
                        filtered_operations.append((path, method, reason))
                        filtered_ops_set.add(key)
            else:
                if not should_filter:
                    pct = percentage_map[category]
                    print(
                        f"[filter_risky_LLM] Keeping {category} operations "
                        f"({len(categorized_ops[category])} operations, {pct:.1f}% of total)"
                    )
                else:
                    print(
                        f"[filter_risky_LLM] Keeping {category} operations "
                        f"(user override via --include {category})"
                    )

    allowed_operations = set()
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, op in methods.items():
            if method.lower() not in ["get", "put", "post", "delete", "options", "head", "patch", "trace"]:
                continue
            if not isinstance(op, dict):
                continue
            key = (path, method.lower())
            if key not in filtered_ops_set:
                allowed_operations.add((path, method))

    filtered_spec = copy.deepcopy(spec)
    filtered_paths = {}

    if filtered_operations:
        for path, methods in paths.items():
            if not isinstance(methods, dict):
                continue

            filtered_methods = {}
            for method, op in methods.items():
                if method.lower() not in ["get", "post", "put", "delete", "patch", "options", "head", "trace"]:
                    continue
                if not isinstance(op, dict):
                    continue
                if (path, method) in allowed_operations:
                    filtered_methods[method] = op

            if filtered_methods:
                new_path_item = copy.deepcopy(methods)
                for method in list(new_path_item.keys()):
                    if method.lower() in ["get", "post", "put", "delete", "patch", "options", "head", "trace"]:
                        if method not in filtered_methods:
                            del new_path_item[method]
                        else:
                            new_path_item[method] = filtered_methods[method]
                filtered_paths[path] = new_path_item

        filtered_spec["paths"] = filtered_paths
    else:
        filtered_spec["paths"] = paths

    if not skip_logs:
        os.makedirs(output_dir, exist_ok=True)
        filtered_log_path = os.path.join(output_dir, "filtered_operations.log")

        with open(filtered_log_path, "w", encoding="utf-8") as f:
            f.write("=" * 80 + "\n")
            f.write("FILTERED OPERATIONS LOG\n")
            f.write("=" * 80 + "\n\n")
            f.write(f"Total operations analyzed: {total_ops}\n")
            f.write(f"Operations filtered: {len(filtered_operations)}\n")
            f.write(f"Operations allowed: {len(allowed_operations)}\n\n")
            f.write(
                "Filter criteria — structural: HTTP DELETE, deprecated. "
                "Semantic (LLM): authentication, authorization, settings.\n\n"
            )
            f.write("=" * 80 + "\n")
            f.write("FILTERED OPERATIONS:\n")
            f.write("=" * 80 + "\n\n")

            if filtered_operations:
                for path, method, reason in filtered_operations:
                    f.write(f"Method: {method.upper()}\n")
                    f.write(f"Path: {path}\n")
                    f.write(f"Reason: {reason}\n")
                    f.write("-" * 80 + "\n")
            else:
                f.write("No operations were filtered.\n")

        print(f"[filter_risky_LLM] Filtered operations log written to: {filtered_log_path}")
    else:
        print("[filter_risky_LLM] Skipping log file generation (stub-only mode)")

    print(f"[filter_risky_LLM] Filtered {len(filtered_operations)} operations")
    print(f"[filter_risky_LLM] Allowed {len(allowed_operations)} operations")

    return filtered_spec, filtered_operations
