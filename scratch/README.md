# scratch/

One-off / dated dev scripts. **Not part of the tool pipeline** — kept only as
reference for the report payload schema and past live runs.

They import core modules by bare name (`from db import ...`), so to run one:

```bash
cd /home/benjaminherro/clawd/projects/property-hunter
PYTHONPATH=. ./venv/bin/python scratch/_build_folio.py
```

- `_build_example.py`, `_build_folio.py`, `_build_folio_data.py` — authored folio
  payloads from the 2026-06-03 run (see `report_builder.sample_payload()` for the
  maintained example).
- `_enrich_shortlist.py`, `_enrich_retry.py` — one-off enrichment passes.
