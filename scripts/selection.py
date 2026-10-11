"""
Tool selection for AutoMCP: list categories (OpenAPI tags), let the user choose
which tools to generate (interactive checklist), and persist the choice.

Design
------
* The tools offered are AutoMCP's default output (safety filter + list/detail
  merge). In --interactive mode nothing is pre-ticked: the user ticks what they
  need; every list has an "ALL" entry (and the 'a' key) to select everything.
  Without --interactive, the full default output is generated as before.
* Endpoints removed by the safety filter are hidden. They are only offered at
  the end, on request, category by category, with a warning.
* The choice is stored as *changes relative to the defaults* in
  ``<output>/.automcp_selection.json``:
    - ``deselected``: default tools the user removed
    - ``enabled_filtered``: hidden (filtered) endpoints the user re-enabled
  Re-running keeps these changes. Anything newly added to the defaults (e.g. via
  ``--include`` or ``--include-tags``) appears automatically, while tools the
  user removed stay removed.
"""
import contextlib
import copy
import io
import json
import os
import sys
from datetime import datetime

from merge_operations import merge_operations, base_path

HTTP_METHODS = ["get", "put", "post", "delete", "patch", "options", "head", "trace"]
SELECTION_FILE = ".automcp_selection.json"

# Filter reasons (from filter_risky_LLM) -> user-facing category + warning
FILTER_CATEGORIES = {
    "Delete": ("Destructive (DELETE)", "Irreversible: the agent could permanently delete data."),
    "Deprecated": ("Deprecated", "May be removed or behave unexpectedly."),
    "Authentication": ("Authentication", "May expose credentials or tokens to the LLM."),
    "Authorization": ("Authorization", "Can change roles and permissions."),
    "Settings": ("Settings / configuration", "Can change system configuration."),
}


# --------------------------------------------------------------------------- #
# Catalog                                                                      #
# --------------------------------------------------------------------------- #
def op_key(method, path):
    return f"{method.upper()} {path}"


def _iter_ops(spec):
    for path, methods in (spec.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        for method, op in methods.items():
            if method.lower() in HTTP_METHODS and isinstance(op, dict):
                yield path, method, op


def _category_of(path, op):
    tags = op.get("tags") or []
    if tags:
        return str(tags[0]).strip() or "(untagged)"
    first = [p for p in str(path).strip("/").split("/") if p and not p.startswith("{")]
    return f"/{first[0]}" if first else "(untagged)"


def _reason_key(reason):
    """'Authentication (4/78 = 5.1% of operations)' -> 'Authentication'."""
    return str(reason).split(" (")[0].strip()


def build_catalog(spec, filtered_spec, filtered_ops, disable_merge=False):
    """
    spec:          spec after tag filtering, before the safety filter
    filtered_spec: spec after the safety filter (default tools)
    filtered_ops:  [(path, method, reason)] returned by filter_risky_endpoints_llm

    Returns a dict with:
      items:    default tools (one item per tool; a merged list/detail pair is one item)
      hidden:   endpoints removed by the safety filter, grouped by filter category
      tag_desc: tag descriptions from the spec
    """
    tag_desc = {t.get("name"): (t.get("description") or "").strip()
                for t in spec.get("tags", []) or [] if isinstance(t, dict)}

    # Which GET operations the merge step would combine (same logic as generation)
    groups = {}
    if not disable_merge:
        with contextlib.redirect_stdout(io.StringIO()):  # silent dry run of the merge step
            _, merge_groups = merge_operations(filtered_spec, None, disable_merge=False, skip_logs=True)
        merged_bases = {g["base_path"] for g in merge_groups}
        for path, method, op in _iter_ops(filtered_spec):
            if method.lower() == "get" and base_path(path) in merged_bases:
                groups.setdefault(base_path(path), []).append((path, method, op))

    items, seen = [], set()
    for base, members in groups.items():
        members.sort(key=lambda m: m[0].count("/"))
        path, method, op = members[0]
        items.append({
            "keys": [op_key(m, p) for p, m, _ in members],
            "label": op.get("operationId") or op_key(method, path),
            "detail": " + ".join(op_key(m, p) for p, m, _ in members),
            "summary": (op.get("summary") or "").strip(),
            "category": _category_of(path, op),
            "merged": True,
        })
        seen.update(items[-1]["keys"])

    for path, method, op in _iter_ops(filtered_spec):
        k = op_key(method, path)
        if k in seen:
            continue
        items.append({
            "keys": [k],
            "label": op.get("operationId") or k,
            "detail": k,
            "summary": (op.get("summary") or "").strip(),
            "category": _category_of(path, op),
            "merged": False,
        })

    ops_by_key = {op_key(m, p): (p, m, o) for p, m, o in _iter_ops(spec)}
    hidden = {}
    for path, method, reason in filtered_ops or []:
        k = op_key(method, path)
        if k not in ops_by_key:
            continue
        _, _, op = ops_by_key[k]
        hidden.setdefault(_reason_key(reason), []).append({
            "keys": [k],
            "label": op.get("operationId") or k,
            "detail": k,
            "summary": (op.get("summary") or "").strip(),
            "category": _category_of(path, op),
        })

    return {"items": items, "hidden": hidden, "tag_desc": tag_desc}


def categories(catalog):
    cats = {}
    for it in catalog["items"]:
        cats.setdefault(it["category"], []).append(it)
    return dict(sorted(cats.items(), key=lambda kv: kv[0].lower()))


# --------------------------------------------------------------------------- #
# Persistence and application                                                  #
# --------------------------------------------------------------------------- #
def load_selection(output_dir):
    path = os.path.join(output_dir, SELECTION_FILE)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {"deselected": set(data.get("deselected", [])),
                "enabled_filtered": set(data.get("enabled_filtered", []))}
    except Exception as e:  # pragma: no cover
        print(f"[WARNING] Could not read {SELECTION_FILE}: {e}")
        return None


def save_selection(output_dir, selection, api_name):
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, SELECTION_FILE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "api": api_name,
            "deselected": sorted(selection["deselected"]),
            "enabled_filtered": sorted(selection["enabled_filtered"]),
            "updated_at": datetime.now().isoformat(),
            "note": "Changes relative to AutoMCP defaults. Delete this file to return to the defaults.",
        }, f, indent=2)
    print(f"[INFO] Tool selection saved to: {path}")


def apply_selection(spec, filtered_spec, selection):
    """Return filtered_spec minus deselected tools plus re-enabled hidden endpoints."""
    if not selection:
        return filtered_spec
    keep = {op_key(m, p) for p, m, _ in _iter_ops(filtered_spec)} - selection["deselected"]
    keep |= selection["enabled_filtered"]
    out = copy.deepcopy(spec)
    new_paths = {}
    for path, methods in (spec.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        item = copy.deepcopy(methods)
        for m in list(item.keys()):
            if m.lower() in HTTP_METHODS and op_key(m, path) not in keep:
                del item[m]
        if any(m.lower() in HTTP_METHODS for m in item):
            new_paths[path] = item
    out["paths"] = new_paths
    return out


def count_tools(catalog, selection):
    sel = selection or {"deselected": set(), "enabled_filtered": set()}
    n = sum(1 for it in catalog["items"] if not set(it["keys"]) & sel["deselected"])
    return n + len(sel["enabled_filtered"])


# --------------------------------------------------------------------------- #
# --list-tags                                                                  #
# --------------------------------------------------------------------------- #
def print_catalog(catalog, api_name):
    cats = categories(catalog)
    hidden_by_cat = {}
    for reason, items in catalog["hidden"].items():
        for it in items:
            hidden_by_cat[it["category"]] = hidden_by_cat.get(it["category"], 0) + 1
    names = sorted(set(cats) | set(hidden_by_cat), key=str.lower)
    width = max([len(n) for n in names] + [8])
    print()
    print(f"  Categories in {api_name}  (tools = after AutoMCP's default filtering and merging)")
    print("  " + "-" * (width + 40))
    print(f"  {'Category'.ljust(width)}  {'Tools':>5}  {'Hidden':>6}  Description")
    print("  " + "-" * (width + 40))
    for n in names:
        desc = (catalog["tag_desc"].get(n) or "")[:60]
        print(f"  {n.ljust(width)}  {len(cats.get(n, [])):>5}  {hidden_by_cat.get(n, 0) or '':>6}  {desc}")
    print("  " + "-" * (width + 40))
    total_hidden = sum(len(v) for v in catalog["hidden"].values())
    print(f"  {'Total'.ljust(width)}  {len(catalog['items']):>5}  {total_hidden:>6}")
    print()
    if any(n.startswith("/") for n in names):
        print("  Categories starting with '/' come from URL paths (the spec has no tags);")
        print("  choose them with --interactive.")
    else:
        print("  Use --include-tags \"A,B\" to generate only some categories,")
        print("  or --interactive to choose categories and individual tools.")
    print()


# --------------------------------------------------------------------------- #
# Interactive menus                                                            #
# --------------------------------------------------------------------------- #
def _require_inquirerpy():
    try:
        from InquirerPy import inquirer  # noqa: F401
        from InquirerPy.base.control import Choice  # noqa: F401
        from InquirerPy.separator import Separator  # noqa: F401
    except ImportError:
        print("[ERROR] --interactive needs the InquirerPy package: pip install InquirerPy")
        sys.exit(1)


STYLE = {
    "questionmark": "#e5c07b bold",
    "answermark": "#98c379 bold",
    "answer": "#61afef",
    "pointer": "#61afef bold",
    "checkbox": "#98c379",
    "separator": "#5c6370",
    "instruction": "#5c6370",
    "long_instruction": "#5c6370",
}

SKIP = [{"key": "escape"}, {"key": "c-z"}]
KEYS = {"toggle-all-true": [{"key": "a"}, {"key": "c-a"}],
        "toggle-all-false": [{"key": "n"}],
        "skip": SKIP}
HELP = "space = select/unselect   a = select all   n = select none   enter = confirm   esc = back"
BACK = object()  # returned when the user presses Esc


def _say(html):
    from prompt_toolkit import print_formatted_text, HTML
    print_formatted_text(HTML(html))


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _style():
    from InquirerPy.utils import get_style
    return get_style(STYLE, style_override=False)


def _run(prompt):
    """Execute a prompt with an instant Esc (no escape-sequence wait)."""
    app = getattr(prompt, "application", None)
    if app is None and getattr(prompt, "_session", None) is not None:
        app = prompt._session.app
    if app is not None:
        app.ttimeoutlen = 0.01  # wait for escape sequences
        app.timeoutlen = 0.01   # wait for multi-key bindings (alt+key starts with Esc)
    return prompt.execute()


def _checkbox(message, choices, instruction=HELP, mandatory=False):
    from InquirerPy import inquirer
    res = _run(inquirer.checkbox(
        message=message,
        choices=choices,
        long_instruction=instruction,
        keybindings=KEYS,
        style=_style(),
        cycle=True,
        max_height="70%",
        transformer=lambda r: f"{len(r)} selected",
        validate=(lambda r: len(r) > 0) if mandatory else None,
        invalid_message="Select at least one item (space to select), or press Esc to go back.",
        mandatory=False,
    ))
    return BACK if res is None else res


def _confirm(message, default):
    from InquirerPy import inquirer
    res = _run(inquirer.confirm(message=message, default=default, style=_style(),
                                keybindings={"skip": SKIP}, mandatory=False,
                                long_instruction="y = yes   n = no   enter = default   esc = back"))
    return BACK if res is None else res


def _select(message, choices, default):
    from InquirerPy import inquirer
    res = _run(inquirer.select(message=message, choices=choices, default=default, style=_style(),
                               keybindings={"skip": SKIP}, mandatory=False,
                               long_instruction="up/down = move   enter = confirm   esc = back"))
    return BACK if res is None else res


def _item_label(it, width=48):
    name = it["label"]
    tail = it["detail"] if it["detail"] != name else ""
    if it.get("merged"):
        tail = "merged: " + it["detail"]
    s = f"{name}"
    if tail:
        s += f"   ({tail})"
    if it["summary"]:
        s += f" - {it['summary'][:width]}"
    return s


ALL = "__ALL__"


def _with_all(choices, all_label, all_enabled=False):
    """Prepend an explicit 'select all' entry to a checklist."""
    from InquirerPy.base.control import Choice
    from InquirerPy.separator import Separator
    return [Choice(value=ALL, name=all_label, enabled=all_enabled), Separator("  " + "-" * 40)] + choices


def _pick(result, n):
    """Selected indices; the 'select all' entry means everything."""
    return set(range(n)) if ALL in result else {r for r in result if r != ALL}


def run_interactive(catalog, api_name, previous=None):
    """Return a selection dict, or None if the user cancelled.

    Everything starts unticked (the user picks what they need); every list has an
    explicit 'ALL' entry, and the 'a' key selects all. Esc goes back one step and
    keeps the ticks; Ctrl+C cancels. With a saved selection, the lists open with
    the previous choices ticked.
    """
    _require_inquirerpy()
    if not sys.stdin.isatty():
        print("[WARNING] --interactive needs a terminal; using the default/saved selection.")
        return previous
    try:
        return _run_steps(catalog, api_name, previous)
    except KeyboardInterrupt:
        print()
        print("[INFO] Cancelled; nothing was generated.")
        return None


def _run_steps(catalog, api_name, previous):
    from InquirerPy.base.control import Choice

    cats = categories(catalog)
    names = list(cats)
    hidden = catalog["hidden"]
    kinds = list(hidden)
    total_hidden = sum(len(v) for v in hidden.values())
    prev_desel = set(previous["deselected"]) if previous else None

    def was_selected(it):  # state from the saved selection (only when re-running)
        return prev_desel is not None and not set(it["keys"]) & prev_desel

    # ---- state kept across steps (so going back keeps the ticks) -----------
    cat_sel = {n for n in names if any(was_selected(it) for it in cats[n])}
    tool_sel = {n: {i for i, it in enumerate(cats[n]) if was_selected(it)} for n in names}
    partial_before = any(0 < len(tool_sel[n]) < len(cats[n]) for n in names)
    mode = "pick" if partial_before else "all"
    enabled = set(previous["enabled_filtered"]) if previous else set()
    kind_sel = {k for k in kinds if any(it["keys"][0] in enabled for it in hidden[k])}
    chosen, kinds_chosen, ti, ki = [], [], 0, 0

    # ---- header (on a clean screen, after the pipeline's log output) -------
    os.system("cls" if os.name == "nt" else "clear")
    _say(f"<b><ansicyan>AutoMCP</ansicyan></b> - choose the tools to generate for <b>{_esc(api_name)}</b>")
    _say(f"<ansibrightblack>{len(catalog['items'])} tools available in {len(cats)} categories"
         f"{' (' + str(total_hidden) + ' sensitive endpoints hidden for safety)' if total_hidden else ''}."
         f" Tick only what you need. Esc goes back, Ctrl+C cancels.</ansibrightblack>")
    if previous:
        _say("<ansibrightblack>Your previous selection has been loaded.</ansibrightblack>")
    print()

    def after_tools():
        return "hidden_ask" if total_hidden else "summary"

    def before_hidden():
        if mode == "pick" and chosen:
            return "tools", len(chosen) - 1
        return "mode", 0

    step = "cats"
    while True:
        # ---- Step 1: categories -----------------------------------------
        if step == "cats":
            choices = []
            for n in names:
                desc = catalog["tag_desc"].get(n, "")
                label = f"{n}  ({len(cats[n])} tools)" + (f"  - {desc[:50]}" if desc else "")
                choices.append(Choice(value=names.index(n), name=label, enabled=n in cat_sel))
            res = _checkbox("Step 1 of 2 - Which categories do you need?",
                            _with_all(choices, f"ALL categories  ({len(catalog['items'])} tools)"),
                            mandatory=True)
            if res is BACK:
                if _confirm("Quit without generating anything?", False) is True:
                    print("[INFO] Cancelled; nothing was generated.")
                    return None
                continue
            cat_sel = {names[i] for i in _pick(res, len(names))}
            chosen = [n for n in names if n in cat_sel]
            step = "mode"

        # ---- Step 2: all tools, or choose per category ---------------------
        elif step == "mode":
            r = _select("Step 2 of 2 - Tools inside the selected categories:",
                        [Choice("all", f"Include ALL tools of the {len(chosen)} selected categories"),
                         Choice("pick", "Let me choose the tools in each category")],
                        default=mode)
            if r is BACK:
                step = "cats"
                continue
            mode = r
            if mode == "pick":
                step, ti = "tools", 0
            else:
                step = after_tools()

        elif step == "tools":
            if ti < 0:
                step = "mode"
                continue
            if ti >= len(chosen):
                step = after_tools()
                continue
            n = chosen[ti]
            items = cats[n]
            ch = [Choice(value=i, name=_item_label(it), enabled=i in tool_sel[n]) for i, it in enumerate(items)]
            res = _checkbox(f"{n}: which tools?  (category {ti + 1} of {len(chosen)})",
                            _with_all(ch, f"ALL tools in {n}  ({len(items)})"))
            if res is BACK:
                ti -= 1
                continue
            tool_sel[n] = _pick(res, len(items))
            ti += 1

        # ---- Hidden (filtered) endpoints: only on request ------------------
        elif step == "hidden_ask":
            parts = ", ".join(f"{FILTER_CATEGORIES.get(r, (r,))[0]}: {len(v)}" for r, v in hidden.items())
            print()
            _say(f"<ansiyellow>Note:</ansiyellow> {total_hidden} sensitive endpoints were hidden by AutoMCP's "
                 f"safety filter <ansibrightblack>({_esc(parts)})</ansibrightblack>.")
            r = _confirm("Review hidden endpoints (advanced)?", bool(enabled))
            if r is BACK:
                step, ti = before_hidden()
                continue
            if r:
                step = "kinds"
            else:
                enabled = set()
                step = "summary"

        elif step == "kinds":
            ch = []
            for k in kinds:
                title, warn = FILTER_CATEGORIES.get(k, (k, ""))
                ch.append(Choice(value=kinds.index(k), enabled=k in kind_sel,
                                 name=f"{title}  ({len(hidden[k])})  ! {warn}"))
            res = _checkbox("Which kinds of hidden endpoints do you want to look at?",
                            _with_all(ch, f"ALL kinds  ({total_hidden} endpoints)"))
            if res is BACK:
                step = "hidden_ask"
                continue
            kind_sel = {kinds[i] for i in _pick(res, len(kinds))}
            kinds_chosen = [k for k in kinds if k in kind_sel]
            for k in kinds:
                if k not in kind_sel:
                    for it in hidden[k]:
                        enabled.discard(it["keys"][0])
            step, ki = "kind_items", 0

        elif step == "kind_items":
            if ki < 0:
                step = "kinds"
                continue
            if ki >= len(kinds_chosen):
                step = "hidden_confirm"
                continue
            k = kinds_chosen[ki]
            title, warn = FILTER_CATEGORIES.get(k, (k, ""))
            items = hidden[k]
            print()
            _say(f"<ansired>! {_esc(title)}:</ansired> {_esc(warn)}")
            ch = [Choice(value=i, enabled=it["keys"][0] in enabled,
                         name=f"[{it['category']}] {_item_label(it)}") for i, it in enumerate(items)]
            res = _checkbox(f"Enable which {title} endpoints?",
                            _with_all(ch, f"ALL {title} endpoints  ({len(items)})"))
            if res is BACK:
                ki -= 1
                continue
            pick = _pick(res, len(items))
            for i, it in enumerate(items):
                if i in pick:
                    enabled.add(it["keys"][0])
                else:
                    enabled.discard(it["keys"][0])
            ki += 1

        elif step == "hidden_confirm":
            if not enabled:
                step = "summary"
                continue
            r = _confirm(f"Include {len(enabled)} sensitive endpoint(s) in the generated server?", False)
            if r is BACK:
                step, ki = "kind_items", len(kinds_chosen) - 1
                continue
            if not r:
                enabled = set()
            step = "summary"

        # ---- Summary -------------------------------------------------------
        elif step == "summary":
            keep = set()
            for n in chosen:
                idx = range(len(cats[n])) if mode == "all" else tool_sel[n]
                keep.update(id(cats[n][i]) for i in idx)
            sel = {"deselected": set(), "enabled_filtered": set(enabled)}
            for it in catalog["items"]:
                if id(it) not in keep:
                    sel["deselected"].update(it["keys"])
            n_tools = count_tools(catalog, sel)
            print()
            _say(f"<b>Summary:</b> <ansigreen>{n_tools} tools</ansigreen> will be generated "
                 f"<ansibrightblack>(out of {len(catalog['items'])} available)</ansibrightblack>"
                 + (f", including <ansired>{len(enabled)} sensitive</ansired>" if enabled else "")
                 + ".")
            back_to = ("hidden_ask", ti) if total_hidden else before_hidden()
            if n_tools == 0:
                r = _confirm("Nothing is selected. Go back and change the selection?", True)
                if r is False:
                    print("[INFO] Cancelled; nothing was generated.")
                    return None
                step, ti = back_to
                continue
            r = _confirm("Generate the MCP server now?", True)
            if r is BACK:
                step, ti = back_to
                continue
            if not r:
                print("[INFO] Cancelled; nothing was generated.")
                return None
            return sel
