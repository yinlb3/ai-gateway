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

from datetime import date, datetime, time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import yaml

# A price is either a flat number or a peak/idle pair.
Price = Union[float, Dict[str, float]]

# Weekdays treated as working days when a tier omits peak_weekdays.
DEFAULT_WEEKDAYS = [1, 2, 3, 4, 5]

VALID_BILLING = ['per_token', 'subscription']
VALID_CURRENCY = ['CNY', 'USD']

# Years already reported as having no holiday calendar, so the warning is
# printed once per run rather than once per record.
_WARNED_YEARS = set()


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

    # 3. The holiday block is optional, but a malformed one would make
    #    every day look like a working day, so it is checked here.
    block = doc.get('holidays')
    if block is not None:
        if not isinstance(block, dict):
            raise ValueError('holidays must be a mapping')
        dates = block.get('dates')
        if dates is not None and not isinstance(dates, list):
            raise ValueError('holidays.dates must be a list of YYYY-MM-DD')

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


def _row_moment(row: dict) -> datetime:
    """
    Read the timestamp out of a raw record.

    The record stores its time as an ISO string. An unreadable value
    cannot be placed in a peak window, so it is treated as idle rather
    than raising: a single bad line should not stop the whole report.

    Args:
        row (dict): One raw record.

    Returns:
        datetime: Timestamp taken from the record, or the epoch when it
            cannot be read.
    """
    raw = row.get('ts')
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
    return datetime.fromtimestamp(0)


def library_covered_years() -> set:
    """
    List the years the installed calendar has complete data for.

    A year is complete only when the State Council arrangements are
    known, since the lunar festivals and the make-up days come from
    them. Outside those years the fixed-date holidays are still known,
    which is not enough to decide whether a day is off.

    Returns:
        set: Years with complete holiday data, empty when no library is
            installed or none exposes the table.
    """
    try:
        from holidays.countries.china import ChinaStaticHolidays
        return set(ChinaStaticHolidays.special_public_holidays.keys())
    except Exception:
        return set()


def _warn_once(year: int, complaint: str) -> None:
    """
    Print a complaint, at most once per year.

    A missing calendar would otherwise repeat the same line for every
    record of the day, and a report over a range would bury the message.
    The years already reported are remembered in _WARNED_YEARS.

    Args:
        year (int): Year the complaint belongs to.
        complaint (str): English description to print.

    Returns:
        None.
    """
    if year in _WARNED_YEARS:
        return
    _WARNED_YEARS.add(year)
    print(f'WARN  {complaint}')


def _library_holiday(day: date) -> Optional[bool]:
    """
    Ask the installed calendar libraries about one day.

    holidays is consulted first: it is more widely maintained. Its China
    data is only complete for the years listed in its own
    special_public_holidays table, which carries the State Council
    arrangements for the lunar festivals and the make-up days. Outside
    those years it still answers for the fixed-date holidays, so a
    missing lunar festival would look like an ordinary day and the day
    would be billed at the peak rate. Such a year is reported as
    unknown instead.

    chinese_calendar is the fallback. It raises rather than answering
    for a year it does not carry, so its exception is the same signal.

    Args:
        day (date): Day to test.

    Returns:
        Optional[bool]: True for a public holiday, False for an ordinary
            day, None when no installed library knows the year well
            enough to be trusted.
    """
    try:
        import holidays
        if day.year in library_covered_years():
            return day in holidays.CN(years=[day.year])
    except Exception:
        pass
    try:
        import chinese_calendar
        return bool(chinese_calendar.is_holiday(day))
    except Exception:
        return None


def _configured_holiday(day: date, doc: Optional[dict]) -> Optional[bool]:
    """
    Read the hand-verified holiday list out of the pricing document.

    Args:
        day (date): Day to test.
        doc (Optional[dict]): Whole pricing document, or None.

    Returns:
        Optional[bool]: True when the day is listed as a holiday, None
            when the list does not settle the question.
    """
    if not isinstance(doc, dict):
        return None
    block = doc.get('holidays')
    if not isinstance(block, dict):
        return None
    dates = block.get('dates')
    if not isinstance(dates, list) or not dates:
        return None
    return day.isoformat() in dates


def is_public_holiday(day: date,
                      doc: Optional[dict] = None) -> Tuple[bool, str]:
    """
    Decide whether the vendor bills the whole day at the idle rate.

    Checks run in a fixed order, first answer wins:
        1. the hand-verified list in pricing.yaml
        2. the holidays library
        3. the chinese_calendar library

    A year none of them covers is reported to the caller rather than
    decided here, so the caller can say so once and carry on. Costs are
    a derived figure, and one unknown calendar must not stop a report.

    Args:
        day (date): Day to test.
        doc (Optional[dict]): Whole pricing document, for the list.

    Returns:
        Tuple[bool, str]: Whether the day is a public holiday, and an
            English complaint when no source covered the year. The
            complaint is empty on success.
    """
    configured = _configured_holiday(day, doc)
    if configured is not None:
        return configured, ''

    found = _library_holiday(day)
    if found is not None:
        return found, ''

    complaint = (f'no holiday calendar covers {day.year}, so '
                 f'{day.isoformat()} is billed as a working day; install '
                 f'or update holidays, or list the date in pricing.yaml')
    return False, complaint


def is_peak(ts: datetime, tier: dict, doc: Optional[dict] = None) -> bool:
    """
    Decide peak status for a tier at a moment in local time.

    A tier without peak_windows is flat: it always returns False and no
    peak concept applies to it.

    The vendor excludes Chinese public holidays from peak hours, so a
    working weekday that falls inside one is billed at the idle rate.
    An unknown holiday calendar is reported once per year and then
    treated as a working day, rather than stopping the report.

    Args:
        ts (datetime): Timestamp in the timezone the windows are
            written in.
        tier (dict): Tier block from pricing.yaml.
        doc (Optional[dict]): Whole pricing document, needed for the
            hand-maintained holiday list. When None, only the calendar
            libraries are consulted.

    Returns:
        bool: True when the moment falls in a peak window on a working
            day that is not a public holiday.
    """
    windows = tier.get('peak_windows')
    if not windows:
        return False
    weekdays = tier.get('peak_weekdays', DEFAULT_WEEKDAYS)
    if ts.isoweekday() not in weekdays:
        return False
    holiday, complaint = is_public_holiday(ts.date(), doc)
    if complaint:
        _warn_once(ts.year, complaint)
    if holiday:
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

    # 2. Peak status is derived from the record's own timestamp, so a
    #    rate band never has to be stored in the record. Recomputing a
    #    historical day with an older price table is a matter of checking
    #    out that version, which git already keeps.
    peak = is_peak(_row_moment(row), tier, doc)
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
