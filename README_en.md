# ai-gateway

Rebuild AI token cost reports from LiteLLM raw request records.

## Table of Contents

- [Usage](#usage)
- [How It Works](#how-it-works)
- [Milestones](#milestones)

## Usage

Record raw lines through the gateway, then rebuild the report for a day:

```bat
python recalc.py 2026-09-21
```

A date range, or several process files of the same day:

```bat
python recalc.py 2026-09-01:2026-09-21
python recalc.py 2026-09-21 --merge
```

Validate the price table after editing a price:

```bat
python check_pricing.py
```

Requirements: Python 3.12 with `pyyaml` and `arrow`.

## How It Works

Two layers stay apart on purpose.

| Layer | Holds | Lifetime | File |
|-------|-------|----------|------|
| Facts | Time, model, token counts | Append only | `logs/raw_YYYY-MM-DD.jsonl` |
| Rules | Prices, currency, peak hours | Edited by hand | `config/pricing.yaml` |

Cost is derived from the two layers, never stored in a record. Editing
a price therefore corrects the whole history without touching a single
log line.

Peak hours for DeepSeek are Beijing time, Monday to Friday, 09:00-12:00
and 14:00-18:00. Every other time, weekends included, is idle.

A model name maps to a tier through `model_to_tier`, so one price entry
covers its old names as well. A model absent from that table is listed
under `unknown_models` with no cost, never billed as zero.

Reports are grouped by currency and never summed across currencies.

## Milestones

| Item | Status | Note |
|------|--------|------|
| Price table | Completed | Two DeepSeek tiers, peak and idle bands |
| Price checker | Completed | Field, window, cache and staleness checks |
| Cost report | Completed | Per-currency report with unknown-model list |
| Gateway callback | In Progress | Needs live LiteLLM data to confirm usage fields |

- **Author**: yinlb<yinlb3@foxmail.com>, deepseek-flash

Last Updated: 2026-09-21

[中文版](README_cn.md)
