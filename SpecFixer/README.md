# SpecFix

SpecFix analyzes and repairs OpenAPI specs using documentation plus LLM-guided fixes.

Built to work with the AUTOMCP tool (which generates MCP servers from REST APIs), but it can also be used independently.

## What It Does

- Detects common OpenAPI issues (auth mismatch, missing required headers, malformed base URLs, missing descriptions, schema gaps).
- Generates minimal fixes with an LLM.
- Applies fixes and writes diff + summaries.

## Install

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt  # optional
```

If using Playwright extraction:

```bash
playwright install
```

## CLI Usage

Analyze only:

```bash
specfix analyze --spec openapi.yaml --docs https://api.example.com/docs --output issues.json
```

Fix:

```bash
specfix fix --spec openapi.yaml --docs https://api.example.com/docs --output fixed.yaml
```

Windows PowerShell API key:

```powershell
$env:OPENAI_API_KEY="your-api-key"
```

Linux/macOS API key:

```bash
export OPENAI_API_KEY=your-api-key
```

Useful flags:

- `--docs-text` for raw/local documentation text
- `--use-playwright` for JS-rendered docs
- `--max-fixes N` to limit fixes
- `--save-llm-io llm_io.json` to save prompt/response logs

## Outputs

`specfix fix` writes to `<api_name>_output/`:

- fixed spec (`fixed_<name>.yaml|json`, or your `--output` filename)
- unified diff (`*.diff`)
- `fixes_summary.json`
- `fixes_summary.md`
- optional LLM I/O log (`--save-llm-io`)

## Current Repository Layout

```text
specfix/                  # package code (CLI, detection, extraction, fixing, loader, utils)
examples/
  apaleo/
    input/
      apaleo.yaml
    output/
      fixes_summary.json
      fixes_summary.md
      fixed_apaleo.yaml
      fixed_apaleo.diff
artifacts/
  results/                # generated analysis artifacts
README.md
requirements.txt
requirements-dev.txt
```

## Notes

- Active CLI path uses `specfix.detection` + `specfix.fixing`.


## Citation

If you use SpecFix in research:

```bibtex
@misc{mastouri2026restmcpempiricalstudy,
  title={From REST to MCP: An Empirical Study of API Wrapping and Automated Server Generation for LLM Agents},
  author={Meriem Mastouri and Emna Ksontini and Amine Barrak and Wael Kessentini},
  year={2026},
  eprint={2507.16044},
  archivePrefix={arXiv},
  primaryClass={cs.SE},
  url={https://arxiv.org/abs/2507.16044}
}
```
