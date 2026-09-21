"""
Check a pricing.yaml file against the project rules.

Every problem found is printed in English with the tier name attached.
The exit code is 1 when an error was found, so the script can be used in
a pre-commit hook or a scheduled task.

Checks performed:
    1. Required fields present, and their values valid.
    2. peak_windows run forward, no window spans midnight.
    3. cache_hit present whenever cache_miss is present.
    4. verified_at parses and is not older than STALE_DAYS.
    5. A currency overview of every tier, for manual review.
    6. Off-peak price equals half the peak price, as the vendor states.
"""

# -*- coding: utf-8 -*-
# ==========================================================================
# Founded: 2026-09-21
# Modified: 2026-09-21
# @author yinlb, deepseek-flash
# ==========================================================================

import sys
from datetime import date, datetime
from pathlib import Path

import arrow

from src import compare, pricing, utils

# A price checked more than this many days ago needs review.
STALE_DAYS = 90

# The pricing file sits in the config directory beside the project root.
DEFAULT_PATH = Path(__file__).parent / 'config' / 'pricing.yaml'

# Fields every tier must carry, and the price fields.
REQUIRED = ['currency', 'billing', 'output', 'verified_at', 'source']
PRICE_KEYS = ['cache_hit', 'cache_write', 'cache_miss', 'input', 'output']


def _check_required(name: str, tier: dict) -> list:
    """
    Check that every required field is present and non-empty.

    Args:
        name (str): Tier name, used in the messages.
        tier (dict): Tier block from pricing.yaml.

    Returns:
        list: English problem descriptions.
    """
    problems = list()
    for key in REQUIRED:
        if key not in tier:
            problems.append(f'{name}: missing required field {key}')
        elif tier[key] == '' or tier[key] is None:
            problems.append(f'{name}: field {key} is empty')
    if tier.get('currency') not in pricing.VALID_CURRENCY:
        problems.append(f'{name}: currency must be one of '
                        f'{pricing.VALID_CURRENCY}')
    if tier.get('billing') not in pricing.VALID_BILLING:
        problems.append(f'{name}: billing must be one of '
                        f'{pricing.VALID_BILLING}')
    return problems


def _check_windows(name: str, tier: dict) -> list:
    """
    Check every peak window in a tier.

    Args:
        name (str): Tier name, used in the messages.
        tier (dict): Tier block from pricing.yaml.

    Returns:
        list: English problem descriptions.
    """
    problems = list()
    windows = tier.get('peak_windows')
    if windows is None:
        return problems
    if not isinstance(windows, list) or not windows:
        problems.append(f'{name}: peak_windows must be a non-empty list')
        return problems
    for window in windows:
        if not isinstance(window, list) or len(window) != 2:
            problems.append(f'{name}: window {window} needs two bounds')
            continue
        complaint = pricing.check_window(window[0], window[1])
        if complaint:
            problems.append(f'{name}: {complaint}')

    # A tier with windows needs weekdays that make sense.
    days = tier.get('peak_weekdays', pricing.DEFAULT_WEEKDAYS)
    for day in days:
        if not isinstance(day, int) or not 1 <= day <= 7:
            problems.append(f'{name}: peak_weekdays entry {day} must be '
                            'an ISO weekday 1-7')
    return problems


def _check_cache_rule(name: str, tier: dict) -> list:
    """
    Check the conditional requirement on cache_hit.

    cache_hit is required only when cache_miss is configured, because a
    tier that prices cache misses must also price cache hits. A tier
    with a plain input price has no cache concept, so cache_hit is
    legitimately absent.

    Args:
        name (str): Tier name, used in the messages.
        tier (dict): Tier block from pricing.yaml.

    Returns:
        list: English problem descriptions.
    """
    problems = list()
    if 'cache_miss' in tier and 'cache_hit' not in tier:
        problems.append(f'{name}: cache_miss is set but cache_hit is '
                        'missing')
    if 'cache_miss' in tier and 'input' in tier:
        problems.append(f'{name}: both cache_miss and input are set, '
                        'cache_miss wins and input is ignored')
    if 'cache_miss' not in tier and 'input' not in tier:
        problems.append(f'{name}: neither cache_miss nor input is set, '
                        'no way to bill non-cached input')
    return problems



def _check_number(name: str, label: str, value: object,
                  problems: list) -> None:
    """
    Append a complaint when a price is not a positive number.

    Args:
        name (str): Tier name, used in the messages.
        label (str): Field label, used in the messages.
        value (object): Value to test.
        problems (list): List that complaints are appended to.

    Returns:
        None.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        problems.append(f'{name}.{label}: price {value!r} is not a number')
    elif value < 0:
        problems.append(f'{name}.{label}: price {value} is negative')


def _check_prices(name: str, tier: dict) -> list:
    """
    Check that every price field is numeric and not negative.

    Args:
        name (str): Tier name, used in the messages.
        tier (dict): Tier block from pricing.yaml.

    Returns:
        list: English problem descriptions.
    """
    problems = list()
    for key in PRICE_KEYS:
        if key not in tier:
            continue
        value = tier[key]
        if isinstance(value, dict):
            for band in ('idle', 'peak'):
                if band not in value:
                    problems.append(f'{name}.{key}: missing band {band}')
                    continue
                _check_number(name, f'{key}.{band}', value[band], problems)
        else:
            _check_number(name, key, value, problems)
    return problems


def _check_halving(name: str, tier: dict) -> list:
    """
    Check that the off-peak price is half the peak price.

    The vendor states this rule, so a deviation means either a mistyped
    number or a rule change. It is reported as a note rather than an
    error, because the two prices are stored independently on purpose:
    the ratio must never be relied on in code.

    Args:
        name (str): Tier name, used in the messages.
        tier (dict): Tier block from pricing.yaml.

    Returns:
        list: English note descriptions.
    """
    notes = list()
    for key in PRICE_KEYS:
        value = tier.get(key)
        if not isinstance(value, dict):
            continue
        if 'idle' not in value or 'peak' not in value:
            continue
        expected = value['peak'] / 2
        if abs(value['idle'] - expected) > 1e-9:
            notes.append(f'{name}.{key}: idle {value["idle"]} is not half '
                         f'of peak {value["peak"]}')
    return notes


def _check_verified(name: str, tier: dict) -> list:
    """
    Check the verified_at date for staleness.

    Args:
        name (str): Tier name, used in the messages.
        tier (dict): Tier block from pricing.yaml.

    Returns:
        list: English problem descriptions.
    """
    problems = list()
    raw = tier.get('verified_at')
    if not raw:
        return problems
    try:
        checked = date.fromisoformat(str(raw))
    except ValueError:
        problems.append(f'{name}: verified_at {raw!r} is not YYYY-MM-DD')
        return problems
    age = (date.today() - checked).days
    if age > STALE_DAYS:
        problems.append(f'{name}: verified_at is {age} days old, review '
                        f'the price (limit {STALE_DAYS} days)')
    return problems


def _run_checks(doc: dict) -> tuple:
    """
    Run every check over the whole pricing document.

    Args:
        doc (dict): Document returned by pricing.load_pricing.

    Returns:
        tuple: Two lists, problems and notes, both of English text.
    """
    problems = list()
    notes = list()

    # 1. Check every tier block against the field rules.
    for name, tier in doc['tiers'].items():
        if not isinstance(tier, dict):
            problems.append(f'{name}: tier must be a mapping')
            continue
        problems += _check_required(name, tier)
        problems += _check_windows(name, tier)
        problems += _check_cache_rule(name, tier)
        problems += _check_prices(name, tier)
        problems += _check_verified(name, tier)
        notes += _check_halving(name, tier)

    # 2. Check mapping coverage, the usual source of lost spend.
    used = set(doc['model_to_tier'].values())
    for name in doc['tiers']:
        if name not in used:
            notes.append(f'{name}: no model maps to this tier')

    # 3. Compare against the LiteLLM table, for the tiers that ask for
    #    it. Anything found here is a hint to check the official page,
    #    never proof that the local entry is wrong.
    compare_problems, compare_notes = compare.run(doc)
    problems += compare_problems
    notes += compare_notes
    return problems, notes


def _print_overview(doc: dict) -> None:
    """
    Print a currency overview, for manual review.

    The tool cannot tell whether a currency label is simply wrong, so
    the table is printed for a human to confirm.

    Args:
        doc (dict): Document returned by pricing.load_pricing.

    Returns:
        None.
    """
    print('')
    print('=== Currency overview ===')
    print(f'{"tier":<24}{"currency":<10}{"billing":<14}models')
    for name, tier in doc['tiers'].items():
        models = [m for m, t in doc['model_to_tier'].items() if t == name]
        listing = ', '.join(models) if models else '(none)'
        print(f'{name:<24}{tier.get("currency", "?"):<10}'
              f'{tier.get("billing", "?"):<14}{listing}')


def main(path: Path) -> int:
    """
    Check one pricing file and report the result.

    Args:
        path (Path): Pricing file to check.

    Returns:
        int: 0 when no error was found, 1 otherwise.
    """
    # 1. Load the file, which already rejects broken links.
    print(f'Checking pricing file: {path}')
    try:
        doc = pricing.load_pricing(path)
    except (FileNotFoundError, ValueError) as exc:
        print(f'ERROR: {exc}')
        return 1

    # 2. Run the field and window checks.
    problems, notes = _run_checks(doc)

    # 3. Print the findings.
    print('')
    for note in notes:
        print(f'NOTE   {note}')
    for problem in problems:
        print(f'ERROR  {problem}')
    _print_overview(doc)

    # 4. Summarise.
    print('')
    print(f'{len(doc["tiers"])} tiers, {len(doc["model_to_tier"])} '
          f'mapping entries')
    if problems:
        print(f'FAILED with {len(problems)} error(s)')
        return 1
    print('OK, no error found')
    return 0


if __name__ == '__main__':
    print('Program check_pricing.py started')
    total_start = arrow.now()

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH
    exit_code = main(path=target)

    total_elapsed = (arrow.now() - total_start).total_seconds()
    print(f'Program check_pricing.py finished, total time '
          f'{utils.format_elapsed(seconds=total_elapsed)}')
    sys.exit(exit_code)
