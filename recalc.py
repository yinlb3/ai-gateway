"""
Rebuild cost reports from raw records and a pricing file.

Raw records are treated as immutable facts: this script only reads them.
Cost is derived here, never taken from the log, so a price change
recomputes history correctly without touching a single log line.

Reports are grouped by currency and are never summed across currencies.

The three lists that must always be printed:
    unknown_models   names absent from the mapping, cost is null
    partial_rows     rows with partial=true, costed but flagged
    currency_groups  a subtotal per currency
"""

# -*- coding: utf-8 -*-
# ==========================================================================
# Founded: 2026-09-21
# Modified: 2026-09-21
# @author yinlb, deepseek-flash
# ==========================================================================

import json
import sys
from collections import defaultdict
from pathlib import Path

import arrow

from src import pricing, utils


def _load_rows(paths: list) -> list:
    """
    Read raw records from a list of JSONL files.

    A damaged line is reported and skipped, because one bad line should
    not hide the rest of the day's spend.

    Args:
        paths (list): Raw JSONL files to read.

    Returns:
        list: Decoded records, in file order.
    """
    rows = list()
    for path in paths:
        with path.open('r', encoding='utf-8') as handle:
            for number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    print(f'WARN   {path.name}:{number} skipped, {exc}')
    return rows


def _dedupe(rows: list) -> list:
    """
    Drop duplicate records, keeping the first per req_id.

    Rows without a req_id cannot be deduplicated and are kept as they
    are, so the report can state that deduplication was limited.

    Args:
        rows (list): Records sorted by ts.

    Returns:
        list: Records with duplicates removed.
    """
    seen = set()
    kept = list()
    for row in rows:
        req_id = row.get('req_id')
        if req_id is None:
            kept.append(row)
            continue
        if req_id in seen:
            continue
        seen.add(req_id)
        kept.append(row)
    return kept


def _find_files(log_dir: Path, day: str, merge: bool) -> list:
    """
    Locate the raw files for one day.

    Args:
        log_dir (Path): Directory holding the raw files.
        day (str): Date as YYYY-MM-DD.
        merge (bool): When set, every pid file of the day is included.

    Returns:
        list: Matching files, sorted by name.

    Raises:
        FileNotFoundError: No file matches the day.
    """
    if merge:
        found = sorted(log_dir.glob(f'raw_{day}*.jsonl'))
    else:
        found = sorted(log_dir.glob(f'raw_{day}.jsonl'))
    if not found:
        raise FileNotFoundError(f'no raw file for {day} in {log_dir}')
    return found


def _aggregate(rows: list, doc: dict) -> dict:
    """
    Cost every record and group the totals.

    Args:
        rows (list): Raw records to cost.
        doc (dict): Document returned by pricing.load_pricing.

    Returns:
        dict: Totals keyed by by_currency, by_model, by_key,
            unknown_models, plus row counters.
    """
    result = {
        'by_currency': defaultdict(float),
        'by_model': defaultdict(lambda: defaultdict(float)),
        'by_key': defaultdict(lambda: defaultdict(float)),
        'unknown_models': defaultdict(int),
        'partial_rows': 0,
        'failed_rows': 0,
        'no_req_id': 0,
        'total_rows': 0,
    }

    for row in rows:
        result['total_rows'] += 1
        if row.get('partial'):
            result['partial_rows'] += 1
        if not row.get('ok', True):
            result['failed_rows'] += 1
        if row.get('req_id') is None:
            result['no_req_id'] += 1

        # 1. Cost the row; a null cost means the model is unmapped.
        cost, currency = pricing.row_cost(row, doc)
        model = row.get('model', '(missing)')
        key = row.get('key', '(missing)')

        if cost is None or not currency:
            result['unknown_models'][model] += 1
            continue

        # 2. Failed calls still consume tokens, so their usage counts.
        result['by_currency'][currency] += cost
        result['by_model'][model][currency] += cost
        result['by_key'][key][currency] += cost

    return result


def _print_group(title: str, groups: dict, unknown: bool,
                 single: bool = False) -> None:
    """
    Print one grouped section of the report.

    Args:
        title (str): Section heading.
        groups (dict): Either a currency total mapping or a mapping of
            group name to per-currency totals.
        unknown (bool): Whether this group is the unknown-model list.
        single (bool): True when groups maps a currency straight to an
            amount rather than to a nested currency mapping.

    Returns:
        None.
    """
    print('')
    print(f'=== {title} ===')
    if not groups:
        print('(empty)')
        return
    for name in sorted(groups):
        if unknown:
            print(f'{name:<44}{groups[name]} row(s), cost unknown')
            continue
        if single:
            print(f'{name:<44}{groups[name]:.6f}')
            continue
        parts = [f'{cur} {amount:.6f}'
                 for cur, amount in sorted(groups[name].items())]
        print(f'{name:<44}{", ".join(parts)}')


def _print_report(result: dict, days: list) -> None:
    """
    Print the whole report, including the three mandatory lists.

    Args:
        result (dict): Totals returned by _aggregate.
        days (list): Dates included in the report.

    Returns:
        None.
    """
    # 1. Headline counters, so missing data is visible at a glance.
    total = result['total_rows']
    print('')
    print(f'=== Summary for {", ".join(days)} ===')
    print(f'rows                {total}')
    print(f'failed rows         {result["failed_rows"]}')
    if total:
        rate = result['partial_rows'] / total * 100
        print(f'partial rows        {result["partial_rows"]} '
              f'({rate:.1f}%)')

    # 2. Subtotals per currency, never summed across currencies.
    _print_group('Cost by currency', result['by_currency'], unknown=False,
                 single=True)

    # 3. Usage grouped by model and by source key.
    _print_group('Cost by model', result['by_model'], unknown=False)
    _print_group('Cost by key', result['by_key'], unknown=False)

    # 4. The unknown-model list, so lost spend is never silent.
    _print_group('Unknown models (cost not billed)',
                 result['unknown_models'], unknown=True)

    # 5. Deduplication limits, when merge mode could not do its job.
    if result['no_req_id']:
        print('')
        print(f'WARN   {result["no_req_id"]} row(s) carry no req_id, so '
              'deduplication was limited')


def _expand_days(text: str) -> list:
    """
    Expand a date or a date range into single dates.

    Args:
        text (str): YYYY-MM-DD or YYYY-MM-DD:YYYY-MM-DD.

    Returns:
        list: Date strings, YYYY-MM-DD.

    Raises:
        SystemExit: The text is not a valid date or range.
    """
    if ':' not in text:
        return [text]
    start_text, end_text = text.split(':', 1)
    try:
        start = arrow.get(start_text, 'YYYY-MM-DD')
        end = arrow.get(end_text, 'YYYY-MM-DD')
    except (arrow.parser.ParserError, ValueError) as exc:
        sys.exit(f'invalid date range {text}: {exc}')
    if start > end:
        sys.exit(f'date range {text} runs backwards')
    span = (end - start).days
    return [start.shift(days=offset).format('YYYY-MM-DD')
            for offset in range(span + 1)]


def _parse_args(argv: list) -> tuple:
    """
    Read the day, pricing path, log directory and merge flag.

    Usage:
        python recalc.py 2026-09-21 [--merge] [--pricing PATH]
        python recalc.py 2026-09-01:2026-09-21 [--merge]

    Args:
        argv (list): Command line arguments, without the script name.

    Returns:
        tuple: Days list, pricing path, merge flag and log directory.

    Raises:
        SystemExit: The date argument is missing or malformed.
    """
    days = list()
    merge = False
    pricing_path = Path(__file__).parent / 'config' / 'pricing.yaml'
    log_dir = Path(__file__).parent / 'logs'
    index = 0

    while index < len(argv):
        arg = argv[index]
        if arg == '--merge':
            merge = True
        elif arg == '--pricing':
            index += 1
            pricing_path = Path(argv[index])
        elif arg == '--logs':
            index += 1
            log_dir = Path(argv[index])
        elif arg.startswith('--'):
            sys.exit(f'unknown option: {arg}')
        else:
            days = _expand_days(arg)
        index += 1

    if not days:
        sys.exit('usage: recalc.py YYYY-MM-DD[:YYYY-MM-DD] '
                 '[--merge] [--pricing PATH] [--logs DIR]')
    return days, pricing_path, merge, log_dir


def main(days: list, pricing_path: Path, merge: bool,
         log_dir: Path) -> int:
    """
    Rebuild and print the report for a set of days.

    Args:
        days (list): Date strings, YYYY-MM-DD.
        pricing_path (Path): Pricing file to use.
        merge (bool): Whether to combine every pid file of each day.
        log_dir (Path): Directory holding the raw files.

    Returns:
        int: 0 when the report was produced, 1 otherwise.
    """
    # 1. Load the pricing rules once for the whole run.
    doc = pricing.load_pricing(pricing_path)
    print(f'Pricing file: {pricing_path}')
    print(f'Log directory: {log_dir}')
    print(f'Merge mode: {merge}')

    # 2. Gather the raw files for every requested day.
    rows = list()
    for day in days:
        try:
            files = _find_files(log_dir, day, merge)
        except FileNotFoundError as exc:
            print(f'WARN   {exc}')
            continue
        for path in files:
            rows += _load_rows([path])
            print(f'Read {path.name}')

    if not rows:
        print('No raw records found, nothing to report')
        return 1

    # 3. Deduplicate across days only when merge was requested.
    if merge:
        rows.sort(key=lambda row: row.get('ts', ''))
        before = len(rows)
        rows = _dedupe(rows)
        print(f'Merged {before} rows into {len(rows)} unique rows')

    # 4. Cost everything and print the report.
    result = _aggregate(rows, doc)
    _print_report(result, days)
    return 0


if __name__ == '__main__':
    print('Program recalc.py started')
    total_start = arrow.now()

    day_list, file_path, merge_flag, dir_path = _parse_args(sys.argv[1:])
    code = main(days=day_list, pricing_path=file_path, merge=merge_flag,
                log_dir=dir_path)

    total_elapsed = (arrow.now() - total_start).total_seconds()
    print(f'Program recalc.py finished, total time '
          f'{utils.format_elapsed(seconds=total_elapsed)}')
    sys.exit(code)
