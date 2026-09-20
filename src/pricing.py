"""
Pricing rules shared by check_pricing.py and recalc.py.

This module is the single place that understands the pricing.yaml
structure. It never writes files and never reads the raw log directory,
so it stays safe to import anywhere.

A tier is one complete set of prices. A model name maps to a tier
through the model_to_tier table. Cost is always derived, never stored in
the raw records.
"""

# -*- coding: utf-8 -*-
# ==========================================================================
# Founded: 2026-09-21
# Modified: 2026-09-21
# @author yinlb, deepseek-flash
# ==========================================================================

from datetime import datetime, time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import yaml

# A price is either a flat number or a peak/idle pair.
Price = Union[float, Dict[str, float]]

# Weekdays treated as working days when a tier omits peak_weekdays.
DEFAULT_WEEKDAYS = [1, 2, 3, 4, 5]

VALID_BILLING = ['per_token', 'subscription']
VALID_CURRENCY = ['CNY', 'USD']


def load_pricing(path: Path) -> dict:
    """
    Read and structurally validate a pricing.yaml file.

    Args:
        path (Path): Path of the pricing file to read.

    Returns:
        dict: Parsed document with the keys tiers and model_to_tier.

    Raises:
        FileNotFoundError: The pricing file does not exist.
        ValueError: The document is not a mapping or a section is missing.
    """
    if not path.is_file():
        raise FileNotFoundError(f'pricing file not found: {path}')

    text = path.read_text(encoding='utf-8')
    doc = yaml.safe_load(text)

    # 1. Check the document shape before touching any field.
    if not isinstance(doc, dict):
        raise ValueError('pricing file must be a mapping at top level')
    for key in ('tiers', 'model_to_tier'):
        if not isinstance(doc.get(key), dict):
            raise ValueError(f'pricing file needs a mapping named {key}')

    # 2. Reject mapping entries that point at an unknown tier.
    tiers = doc['tiers']
    for model, tier_name in doc['model_to_tier'].items():
        if tier_name not in tiers:
            raise ValueError(f'{model} maps to unknown tier {tier_name}')

    return doc


def to_time(value: str) -> time:
    """
    Convert a HH:MM window boundary into a time object.

    The literal 24:00 is allowed and means the end of the day. Python
    cannot build time(24, 0), so it is translated to the last
    representable instant.

    Args:
        value (str): Boundary text, HH:MM, 24:00 allowed for an end.

    Returns:
        time: Parsed boundary.

    Raises:
        ValueError: The text is not a valid HH:MM boundary.
    """
    if value == '24:00':
        return time(23, 59, 59, 999999)
    return time.fromisoformat(value)


def check_window(start: str, end: str) -> Optional[str]:
    """
    Validate one peak window and return a complaint, if any.

    Windows are half-open [start, end). A window whose start is not
    before its end can never match, so it fails silently and every hour
    gets billed at the off-peak rate. Such windows are rejected here
    rather than ignored.

    Args:
        start (str): Window start, HH:MM.
        end (str): Window end, HH:MM, or 24:00 for the end of the day.

    Returns:
        Optional[str]: An English problem description, or None when the
            window is valid.
    """
    try:
        start_t = to_time(start)
        end_t = to_time(end)
    except ValueError as exc:
        return f'invalid time {start}-{end}: {exc}'
    if start == '24:00':
        return f'window {start}-{end}: 24:00 is only valid as an end'
    if start_t >= end_t:
        return (f'window {start}-{end} does not span forward; '
                'split a night window into two instead')
    return None


def in_windows(moment: time, windows: List[List[str]]) -> bool:
    """
    Decide whether a clock time falls inside any window.

    Args:
        moment (time): Time of day to test.
        windows (List[List[str]]): Half-open windows as text pairs.

    Returns:
        bool: True when moment lies in at least one window.
    """
    for start, end in windows:
        if to_time(start) <= moment < to_time(end):
            return True
    return False


def is_peak(ts: datetime, tier: dict) -> bool:
    """
    Decide peak status for a tier at a moment in local time.

    A tier without peak_windows is flat: it always returns False and the
    peak field in a record is ignored.

    Args:
        ts (datetime): Timestamp in the timezone the windows are
            written in.
        tier (dict): Tier block from pricing.yaml.

    Returns:
        bool: True when the moment falls in a peak window on a working
            day.
    """
    windows = tier.get('peak_windows')
    if not windows:
        return False
    weekdays = tier.get('peak_weekdays', DEFAULT_WEEKDAYS)
    if ts.isoweekday() not in weekdays:
        return False
    return in_windows(ts.time(), windows)


def pick_price(price: Price, peak: bool) -> float:
    """
    Select the applicable price from a flat or peak-aware value.

    Args:
        price (Price): Either a number or an idle/peak mapping.
        peak (bool): Whether the peak rate applies.

    Returns:
        float: The price to charge.

    Raises:
        ValueError: A peak mapping lacks the needed key.
    """
    if isinstance(price, dict):
        key = 'peak' if peak else 'idle'
        if key not in price:
            raise ValueError(f'price mapping lacks key {key}: {price}')
        return float(price[key])
    return float(price)


def find_tier(model: str, doc: dict) -> Optional[Tuple[str, dict]]:
    """
    Look up the tier for a model name.

    The mapping is matched exactly, first match wins. Nothing is guessed
    from prefixes: an unmatched name is reported, never billed as zero.

    Args:
        model (str): Model field from a log record.
        doc (dict): Document returned by load_pricing.

    Returns:
        Optional[Tuple[str, dict]]: Tier name and tier block, or None
            when the model is absent from the mapping.
    """
    name = doc['model_to_tier'].get(model)
    if name is None:
        return None
    return name, doc['tiers'][name]


def row_cost(row: dict, doc: dict) -> Tuple[Optional[float], str]:
    """
    Compute the cost of one raw record.

    Input is billed as three parts: cached reads, cache writes, and the
    remaining non-cached input. The non-cached part is floored at zero,
    because a provider may report cached tokens that overlap the total.

    Args:
        row (dict): One raw record.
        doc (dict): Document returned by load_pricing.

    Returns:
        Tuple[Optional[float], str]: Cost and currency. Both are None
            when the model has no tier. Cost is 0 for subscription
            tiers, while usage is still counted.

    Raises:
        ValueError: A required field is malformed.
    """
    model = row.get('model')
    found = find_tier(model, doc)
    if found is None:
        return None, ''
    tier_name, tier = found

    # 1. A subscription tier keeps usage but bills nothing.
    if tier.get('billing') == 'subscription':
        return 0.0, tier['currency']

    # 2. Pick the rate band from the peak snapshot in the record.
    peak = bool(row.get('peak'))
    total_in = int(row.get('in', 0))
    cache_hit = int(row.get('cache_hit', 0))
    cache_write = int(row.get('cache_write', 0))
    out = int(row.get('out', 0))

    # 3. Split input into cached and non-cached parts.
    miss = max(0, total_in - cache_hit - cache_write)

    cost = 0.0
    if cache_hit:
        cost += cache_hit * pick_price(tier['cache_hit'], peak)
    if cache_write:
        cost += cache_write * pick_price(tier['cache_write'], peak)
    if miss:
        if 'cache_miss' in tier:
            cost += miss * pick_price(tier['cache_miss'], peak)
        else:
            cost += miss * pick_price(tier['input'], peak)
    if out:
        cost += out * pick_price(tier['output'], peak)

    return cost / 1_000_000, tier['currency']
