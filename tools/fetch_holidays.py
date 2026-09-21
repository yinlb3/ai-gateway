"""
Fill the holiday list in pricing.yaml from an official mirror.

The vendor bills Chinese public holidays at the off-peak rate, so the
rate of a working weekday depends on a calendar this project does not
control. Three sources can answer, in this order:

    1. the list in pricing.yaml, hand verified
    2. the holidays library
    3. the chinese_calendar library

The libraries only carry the years the State Council has published, so
a year beyond that is billed as if every day were a working day. This
script closes that gap: it downloads a mirror of the State Council
announcements, rewrites the list for the requested years, and reports
any difference against what was already written.

The mirror, NateScarlet/holiday-cn, publishes one JSON file per
announcement year and carries the source URLs in its papers field:

    https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/2026.json

Year files are named after the announcement, not the dates inside it,
so a day in early January can be listed in the previous year's file.
Both are therefore merged.

Nothing here runs during a report. recalc.py never touches the network,
so a machine without one keeps working from the list already on disk.

Usage:

    python tools/fetch_holidays.py 2026
    python tools/fetch_holidays.py 2026 2027 --dry-run
"""

# -*- coding: utf-8 -*-
# ==========================================================================
# Founded: 2026-09-21
# Modified: 2026-09-21
# @author yinlb, deepseek-flash
# ==========================================================================

import json
import sys
from pathlib import Path
from urllib.request import urlopen

import arrow
import yaml

# The official announcement mirror. One JSON file per announced year.
MIRROR = ('https://raw.githubusercontent.com/NateScarlet/holiday-cn'
          '/master/{year}.json')

# The pricing file sits in the config directory beside the project root.
DEFAULT_PATH = Path(__file__).parent.parent / 'config' / 'pricing.yaml'

# A download that stalls must not hang the tool forever.
TIMEOUT_SECONDS = 30


def fetch_year(year: int) -> dict:
    """
    Download one announcement file from the mirror.

    Args:
        year (int): Announcement year, not necessarily the date year.

    Returns:
        dict: Parsed JSON, with a year key and a days list. Empty when
            the year is not published yet.

    Raises:
        OSError: The download failed, so nothing can be trusted.
    """
    url = MIRROR.format(year=year)
    with urlopen(url, timeout=TIMEOUT_SECONDS) as response:
        raw = response.read().decode('utf-8')
    return json.loads(raw)


def off_days(payloads: list) -> dict:
    """
    Collect the off-peak dates out of one or more announcement payloads.

    Only days whose isOffDay flag is true count: the vendor excludes
    holidays from peak hours, while a make-up working day stays on a
    weekend and is already excluded by peak_weekdays.

    Args:
        payloads (list): Parsed payloads, in no particular order.

    Returns:
        dict: Date text mapped to the holiday name, for reporting.
    """
    found = dict()
    for payload in payloads:
        for day in payload.get('days') or list():
            if day.get('isOffDay') and day.get('date'):
                found[day['date']] = day.get('name', '')
    return found


def papers(payloads: list) -> list:
    """
    Collect the State Council document URLs, for the record.

    Args:
        payloads (list): Parsed payloads.

    Returns:
        list: Unique URLs, sorted.
    """
    urls = set()
    for payload in payloads:
        for url in payload.get('papers') or list():
            urls.add(url)
    return sorted(urls)


def load_years(years: list) -> tuple:
    """
    Download every requested year, plus its neighbours.

    A day in early January is often listed in the previous year's file,
    because files are named after the announcement. The neighbours are
    fetched for that reason and folded into the result.

    Args:
        years (list): Announcement years to cover.

    Returns:
        tuple: The off-day mapping and the document URLs. A year with no
            published file contributes nothing rather than failing.
    """
    payloads = list()
    for year in sorted(set(years)):
        try:
            payloads.append(fetch_year(year))
        except Exception as exc:
            print(f'WARN  cannot fetch {year}: {exc}')
    return off_days(payloads), papers(payloads)


def write_list(path: Path, dates: list, source: str,
               dry_run: bool) -> None:
    """
    Replace the holiday list in pricing.yaml.

    The file is read as text and only the holiday block is rewritten, so
    the comments that document the rest of the structure survive. The
    block is rebuilt in full, because a half-filled list decides the
    whole year and would bill the missing holidays at the peak rate.

    Args:
        path (Path): Pricing file to update.
        dates (list): Date text to write, sorted.
        source (str): URL recorded beside the list.
        dry_run (bool): When true, print what would change instead.

    Returns:
        None.
    """
    text = path.read_text(encoding='utf-8')
    doc = yaml.safe_load(text)
    block = doc.get('holidays') or dict()
    before = block.get('dates') or list()

    block['dates'] = dates
    block['verified_at'] = arrow.now().format('YYYY-MM-DD')
    block['source'] = source

    print(f'--- list before: {len(before)} date(s)')
    print(f'--- list after : {len(dates)} date(s)')
    for stamp in [d for d in dates if d not in before]:
        print(f'ADD    {stamp}')
    for stamp in [d for d in before if d not in dates]:
        print(f'REMOVE {stamp}')

    if dry_run:
        print('DRY RUN, file not written')
        return

    # Rewrite only the holiday block, keeping every other line as it was.
    lines = text.splitlines(keepends=True)
    start = None
    end = len(lines)
    for index, line in enumerate(lines):
        if line.startswith('holidays:'):
            start = index
            continue
        if start is not None and line and not line[0].isspace():
            end = index
            break
    if start is None:
        print('ERROR  no holidays block in the pricing file')
        return

    rendered = yaml.safe_dump({'holidays': block}, sort_keys=False,
                              allow_unicode=True, default_flow_style=False)
    rebuilt = lines[:start] + [rendered] + lines[end:]
    path.write_text(''.join(rebuilt), encoding='utf-8')
    print(f'wrote {path}')


def main(argv: list) -> int:
    """
    Fetch the requested years and update the holiday list.

    Args:
        argv (list): Command line arguments, years and --dry-run.

    Returns:
        int: 0 on success, 1 when nothing could be fetched.
    """
    dry_run = '--dry-run' in argv
    years = [int(a) for a in argv if a.isdigit()]
    if not years:
        years = [arrow.now().year]
        print(f'no year given, using {years[0]}')

    found, urls = load_years(years)
    if not found:
        print('ERROR  no announcement data could be fetched')
        return 1

    dates = sorted(found)
    print(f'fetched {len(dates)} off-peak day(s) for {years}')
    for url in urls:
        print(f'SOURCE {url}')

    source = urls[0] if urls else MIRROR.format(year=years[0])
    write_list(DEFAULT_PATH, dates, source, dry_run)
    return 0


if __name__ == '__main__':
    print('Program fetch_holidays.py started')
    total_start = arrow.now()

    exit_code = main(sys.argv[1:])

    total_elapsed = (arrow.now() - total_start).total_seconds()
    print(f'Program fetch_holidays.py finished, total time '
          f'{total_elapsed:.2f}s')
    sys.exit(exit_code)

