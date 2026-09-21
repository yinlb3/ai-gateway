# ai-gateway

Track AI token spend per tool and per project through a LiteLLM gateway.

## Table of Contents

- [Install](#install)
- [Usage](#usage)
- [How It Works](#how-it-works)
- [Milestones](#milestones)

## Install

```bat
pip install -r requirements.txt
```

Python 3.12 or newer is required. The accounting dependencies are `pyyaml`
for the price table and `arrow` for timing.

Only the machine that runs the gateway needs LiteLLM as well:

```bat
pip install "litellm[proxy,extra-proxy]"
```

The two halves are separate on purpose. The callback and every reporting
module work from raw records alone, so a report can be rebuilt on a
machine that never hosts a gateway. Install this extra with pip rather
than conda: several packages it pulls in are absent from conda-forge.

## Usage

The toolchain has two halves. The gateway callback writes facts while
requests are running; the report turns those facts into money later.

### 1. Point the gateway at the callback

Copy `config/config.example.yaml` to `config.yaml`, fill in the model
names and the environment variable holding your API key, then start
LiteLLM. Add this line so every finished request reaches the callback:

```yaml
litellm_settings:
  callbacks: custom_callback.proxy_handler_instance
```

Requests are attributed to a project either by a
`x-litellm-spend-logs-metadata` header or by the virtual key alias, which
is written as `<tool>--<project>`. See `docs/key_setup_cn.md` for the
full rules.

### 2. Rebuild the report

```bat
python recalc.py 2026-09-21
```

A date range, or several process files of the same day:

```bat
python recalc.py 2026-09-01:2026-09-21
python recalc.py 2026-09-21 --merge
```

### 3. Check the price table after editing a price

```bat
python check_pricing.py
```

### 4. Run the checks

```bat
python test_callback.py
python test_usage_logger.py
python test_end_to_end.py
```

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

The callback never breaks a request. Every entry point catches its own
errors, the module does nothing at import time, and a missing project
is recorded as a gap rather than raised as a failure.

## Milestones

| Item | Status | Note |
|------|--------|------|
| Price table | Completed | Two DeepSeek tiers, peak and idle bands |
| Price checker | Completed | Field, window, cache and staleness checks |
| Cost report | Completed | Per-currency report with unknown-model list |
| Gateway callback | Completed | One JSON line per request, failed calls included |
| Live field check | In Progress | Confirm cache key names against a real gateway |

- **Author**: yinlb<yinlb3@foxmail.com>, deepseek-flash

Last Updated: 2026-09-21

[中文版](README_cn.md)
