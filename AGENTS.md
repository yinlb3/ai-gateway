---
name: ai-gateway
description: Rebuild AI token cost reports from LiteLLM raw request records.
---

# AI Gateway Agent Guide

> **Reference**: This document only records project-specific information.

## 1. Context

### Your role

- Maintain the cost accounting toolchain of this project.
- Keep the two layers separate: raw records are facts that are only
  appended, prices are rules that are edited by hand.
- Never let an accounting failure interrupt the gateway itself.

### Project knowledge

**Background**

The project measures AI token spend routed through a LiteLLM gateway.
Every API call is recorded as a raw line, and cost is recomputed later
from those lines plus a hand-maintained price table. Cost is never
stored in a record, so a price change recomputes history correctly
without touching a single log line.

**Tech Stack**

- Python 3.12
- `pyyaml` for the price table
- `arrow` for timing and date handling
- No database, no network access at report time

**File Structure**

- `config/pricing.yaml`: the price table. One tier is one complete set
  of prices; `model_to_tier` maps a model name to a tier.
- `src/pricing.py`: parses the price table, decides peak status, picks a
  rate band and costs one record. It never writes files.
- `src/utils.py`: formatting helpers, currently elapsed time.
- `check_pricing.py`: validates the price table and prints a currency
  overview. Exits 1 when an error is found.
- `recalc.py`: rebuilds and prints the cost report for one or more days.
- `logs/`: raw records, one JSONL file per day. Runtime data, never
  edited by hand.

## 2. Commands

Run everything with the `current` conda environment, whose interpreter
is `C:\ProgramData\miniconda3\envs\current\python.exe`. The commands
below use `python` for brevity.

```bat
python check_pricing.py
python recalc.py 2026-09-21
```

Validate the price table after any price edit:

```bat
python check_pricing.py
```

Rebuild a single day, or a date range:

```bat
python recalc.py 2026-09-21
python recalc.py 2026-09-01:2026-09-21
python recalc.py 2026-09-21 --merge
```

Checks before running:

- `arrow` and `pyyaml` must be importable by the chosen interpreter.
- `config/pricing.yaml` must exist. A missing file is reported, not
  recreated.
- `logs/` may be absent until the gateway has served a request.

## 3. Testing

There is no unit test suite. Verify by hand:

1. `check_pricing.py` must print `OK, no error found` and exit 0.
2. `recalc.py` must print the three mandatory sections: cost by
   currency, cost by model, and the unknown-model list.
3. Cost cross-check: compute one record by hand and compare. For
   example, with `in=12000`, `cache_hit=10000`, `out=3000` at the peak
   band of the flash tier, cost is `10000*0.04 + 2000*2 + 3000*8` and
   the result is divided by one million.
4. Peak boundary checks: a weekday at 10:00 is peak; a weekday at

## 4. Code Style

- English only in comments, docstrings, output and exceptions.
- Paths are built with `pathlib.Path` and the `/` operator.
- Use `print` for output; do not use `logging`.
- Every function carries a Google-style docstring and type hints.
- Long functions are split with numbered section comments.
- Lines are at most 80 characters.
- Variable names are self-explanatory and at most 20 characters.
- Single quotes for strings, except inside an f-string.
- The off-peak band is spelled `idle`, never `off`: YAML 1.1 parses a
  bare `off` as the boolean `False`.
- Prices are stored per band and never derived by halving. The vendor
  states that the idle price is half the peak price, but that ratio is
  a hint for manual review only, never a formula in code.

### Project-specific conventions

- A tier without `peak_windows` is flat: the `peak` field in a record
  is ignored for that tier.
- `peak_windows` never span midnight. A window whose start is not
  before its end can never match, so it is rejected rather than
  ignored.
- `cache_hit` is required only when `cache_miss` is present.
- A model absent from `model_to_tier` is reported, never billed as zero.
- No currency conversion. Reports are grouped by currency and never
  summed across currencies.

## 5. Git Workflow

- Commit messages are English and start with a verb: Add, Fix, Update,
  Remove, Refactor, Docs, Test, Config.
- One line, verb and colon first, for example
  `Add: validate peak windows in the price checker`.
- Do not commit or push without an explicit instruction.

### Additional notes

- `logs/` is ignored: it holds runtime facts, not source.
- `config/pricing.yaml` is versioned on purpose so that price changes
  stay traceable. The published prices are public information.

## 6. Boundaries

**Always do**

- Read the whole file before editing it.
- Run `python -m py_compile` after every code change.
- Run `check_pricing.py` after every price edit.
- Keep cost derived, never stored in a raw record.

**Ask first**

- Anything that changes the environment: installing, removing or
  upgrading a package.
- Anything that changes a stored record format, since old records would
  no longer be comparable.
- Anything that would make an accounting failure block the gateway.

**Never do**

- Write to an existing raw record file.
- Bill an unmapped model as zero instead of reporting it.
- Sum costs across currencies.
- Guess a price. An unverified price is worse than a missing one.

## 7. Milestones

### Completed

| Item | Note |
|------|------|
| Price table | Two DeepSeek tiers with peak and idle bands |
| Price table checker | Field, window, cache and staleness checks |
| Cost report | Per-currency report with the unknown-model list |

### In Progress

| Item | Note |
|------|------|
| Gateway callback | Needs a live LiteLLM instance to confirm usage fields |

### Todo

| Priority | Item | Note |
|----------|------|------|
| P1 | Gateway callback | Record raw lines from real requests |
| P2 | Field verification | Dump a real logging object for cache field names |
| P3 | Project README | Usage, install notes and milestones |

Last Updated: 2026-09-21

   13:00 is idle; a Saturday at 10:00 is idle.
5. An unmapped model must appear under the unknown-model list with no
   cost, never billed as zero.
