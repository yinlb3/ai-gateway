"""
Walk the whole chain once, from a gateway payload to a cost report.

The other checks test one module each. This one proves the modules fit
together: a payload goes into the callback, a line lands on disk, the
recalculator reads it back, and the price table turns it into money.

Run directly:

    python test_end_to_end.py

Everything happens in a temporary directory, so the real logs and the
real price table are left alone.
"""

# -*- coding: utf-8 -*-
# ==========================================================================
# Founded: 2026-09-21
# Modified: 2026-09-21
# @author yinlb, deepseek-flash
# ==========================================================================

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

import arrow

import custom_callback
import recalc
from src import pricing, utils

PRICING = Path(__file__).parent / 'config' / 'pricing.yaml'


def _payload(model: str, prompt: int, answer: int, cached: int,
             alias: str = 'cline--ai-gateway') -> dict:
    """
    Build a gateway payload shaped like the real logging object.

    Args:
        model (str): Model name, as the log will record it.
        prompt (int): Prompt token count.
        answer (int): Completion token count.
        cached (int): Cached input token count.
        alias (str): Virtual key alias carrying the project.

    Returns:
        dict: Payload accepted by the callback.
    """
    return {
        'id': f'req-{model}-{prompt}',
        'model': model,
        'prompt_tokens': prompt,
        'completion_tokens': answer,
        'hidden_params': {'usage_object': {
            'prompt_cache_hit_tokens': cached}},
        'metadata': {
            'user_api_key_alias': alias,
            'user_api_key_hash': 'deadbeefcafe1234',
        },
    }


def _stamp_moment(directory: Path, ts: str) -> None:
    """
    Set the timestamp of every row in a directory.

    Cost is derived from the timestamp now, so a test controls the rate
    band by choosing when the request happened rather than by writing a
    snapshot field.

    Args:
        directory (Path): Directory holding raw JSONL files.
        ts (str): ISO timestamp to write into each row.

    Returns:
        None.
    """
    for path in directory.glob('raw_*.jsonl'):
        rows = list()
        with path.open('r', encoding='utf-8') as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line)
                    row['ts'] = ts
                    rows.append(row)
        with path.open('w', encoding='utf-8', newline='\n') as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + '\n')


def test_peak_row_costs_correctly() -> None:
    """A peak-hour flash row must cost what the price table says."""
    tmp = Path(tempfile.mkdtemp())
    try:
        logger = custom_callback.UsageLogger(log_dir=tmp)
        payload = _payload('deepseek/deepseek-flash', 12000, 3000, 10000)
        asyncio.run(logger.log(payload, ok=True))
        _stamp_moment(tmp, '2026-09-21T10:00:00')

        doc = pricing.load_pricing(PRICING)
        rows = recalc._load_rows(sorted(tmp.glob('raw_*.jsonl')))
        assert len(rows) == 1

        cost, currency = pricing.row_cost(rows[0], doc)
        # 10000*0.04 + 2000*2 + 3000*8, divided by one million.
        assert currency == 'CNY'
        assert abs(cost - 0.0284) < 1e-9, cost
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_idle_row_costs_half_of_peak() -> None:
    """The same usage costs exactly half as much off peak."""
    doc = pricing.load_pricing(PRICING)
    peak_row = {'model': 'deepseek/deepseek-flash', 'in': 12000,
                'out': 3000, 'cache_hit': 10000,
                'ts': '2026-09-21T10:00:00'}
    idle_row = dict(peak_row, ts='2026-09-21T13:00:00')

    peak_cost, _ = pricing.row_cost(peak_row, doc)
    idle_cost, _ = pricing.row_cost(idle_row, doc)
    assert abs(peak_cost / 2 - idle_cost) < 1e-12


def test_aggregate_groups_by_currency_and_key() -> None:
    """A day of rows must group correctly and never lose a row."""
    tmp = Path(tempfile.mkdtemp())
    try:
        logger = custom_callback.UsageLogger(log_dir=tmp)
        payloads = [
            _payload('deepseek/deepseek-flash', 12000, 3000, 10000),
            _payload('deepseek/deepseek-flash', 8000, 1000, 0),
            _payload('deepseek/deepseek-v4-pro', 50000, 20000, 40000,
                     alias='codex--wind-forecast'),
        ]
        for payload in payloads:
            asyncio.run(logger.log(payload, ok=True))
        _stamp_moment(tmp, '2026-09-21T10:00:00')

        doc = pricing.load_pricing(PRICING)
        rows = recalc._load_rows(sorted(tmp.glob('raw_*.jsonl')))
        assert len(rows) == 3

        result = recalc._aggregate(rows, doc)
        assert result['total_rows'] == 3
        assert not result['unknown_models']
        assert set(result['by_key']) == {'cline--ai-gateway',
                                        'codex--wind-forecast'}

        # Key subtotals must add up to the currency total.
        parts = sum(result['by_key'][k]['CNY'] for k in result['by_key'])
        assert abs(parts - result['by_currency']['CNY']) < 1e-12
    finally:
        shutil.rmtree(tmp, ignore_errors=True)



def test_unknown_model_is_reported_not_zeroed() -> None:
    """A model absent from the table must reach the unknown list."""
    tmp = Path(tempfile.mkdtemp())
    try:
        logger = custom_callback.UsageLogger(log_dir=tmp)
        payload = _payload('deepseek/deepseek-future-99', 9000, 2000, 0)
        asyncio.run(logger.log(payload, ok=True))

        doc = pricing.load_pricing(PRICING)
        rows = recalc._load_rows(sorted(tmp.glob('raw_*.jsonl')))
        result = recalc._aggregate(rows, doc)

        assert result['unknown_models']['deepseek/deepseek-future-99'] == 1
        assert not result['by_currency'], 'must not be billed as zero'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_failed_request_still_costs() -> None:
    """A failed call consumed tokens, so its cost must appear."""
    tmp = Path(tempfile.mkdtemp())
    try:
        logger = custom_callback.UsageLogger(log_dir=tmp)
        payload = _payload('deepseek/deepseek-flash', 6000, 0, 0)
        payload['error_str'] = 'upstream timeout'
        asyncio.run(logger.log(payload, ok=False))
        _stamp_moment(tmp, '2026-09-21T10:00:00')

        doc = pricing.load_pricing(PRICING)
        rows = recalc._load_rows(sorted(tmp.glob('raw_*.jsonl')))
        result = recalc._aggregate(rows, doc)

        assert result['failed_rows'] == 1
        assert result['by_currency']['CNY'] > 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_partial_row_is_counted_and_reported() -> None:
    """A row missing its project must still be billed and flagged."""
    tmp = Path(tempfile.mkdtemp())
    try:
        logger = custom_callback.UsageLogger(log_dir=tmp)
        payload = _payload('deepseek/deepseek-flash', 12000, 3000, 10000,
                           alias='no-convention-here')
        asyncio.run(logger.log(payload, ok=True))
        _stamp_moment(tmp, '2026-09-21T10:00:00')

        doc = pricing.load_pricing(PRICING)
        rows = recalc._load_rows(sorted(tmp.glob('raw_*.jsonl')))
        result = recalc._aggregate(rows, doc)

        # 1. Billed, because the tokens were really spent.
        assert result['by_currency']['CNY'] > 0

        # 2. Flagged, because the attribution is missing.
        assert result['partial_rows'] == 1
        assert rows[0]['user'] is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_merge_deduplicates_by_request_id() -> None:
    """The same request written twice must be billed once."""
    tmp = Path(tempfile.mkdtemp())
    try:
        logger = custom_callback.UsageLogger(log_dir=tmp)
        payload = _payload('deepseek/deepseek-flash', 12000, 3000, 10000)
        asyncio.run(logger.log(payload, ok=True))

        # 1. The same request arriving in a second process file.
        original = sorted(tmp.glob('raw_*.jsonl'))[0]
        second = tmp / 'raw_2026-09-21.9999.jsonl'
        shutil.copy(original, second)
        _stamp_moment(tmp, '2026-09-21T10:00:00')

        files = sorted(tmp.glob('raw_*.jsonl'))
        rows = recalc._load_rows(files)
        assert len(rows) == 2, 'both copies must be read'

        deduped = recalc._dedupe(rows)
        assert len(deduped) == 1, 'the duplicate must be dropped'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_case_of_a_full_day() -> None:
    """Run every stage over a mixed day and check the invariants."""
    tmp = Path(tempfile.mkdtemp())
    try:
        logger = custom_callback.UsageLogger(log_dir=tmp)
        payloads = [
            _payload('deepseek/deepseek-flash', 12000, 3000, 10000),
            _payload('deepseek/deepseek-flash', 5000, 500, 5000),
            _payload('deepseek/deepseek-v4-flash', 8000, 1000, 0),
            _payload('deepseek/deepseek-v4-pro', 50000, 20000, 40000,
                     alias='codex--wind-forecast'),
            _payload('deepseek/deepseek-future-99', 9000, 2000, 0),
        ]
        for payload in payloads:
            asyncio.run(logger.log(payload, ok=True))
        _stamp_moment(tmp, '2026-09-21T10:00:00')

        doc = pricing.load_pricing(PRICING)
        rows = recalc._load_rows(sorted(tmp.glob('raw_*.jsonl')))
        result = recalc._aggregate(rows, doc)

        # 1. Every row was read.
        assert result['total_rows'] == len(payloads)

        # 2. Currency groups are never mixed.
        assert set(result['by_currency']) == {'CNY'}

        # 3. The unknown model is listed, not charged.
        assert len(result['unknown_models']) == 1

        # 4. Model subtotals equal the currency total.
        total = sum(sum(v.values()) for v in result['by_model'].values())
        assert abs(total - result['by_currency']['CNY']) < 1e-12
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_merge_accepts_pid_files() -> None:
    """Merge mode must pick up owner and pid files of the same day."""
    tmp = Path(tempfile.mkdtemp())
    try:
        (tmp / 'raw_2026-09-21.jsonl').write_text('', encoding='utf-8')
        (tmp / 'raw_2026-09-21_12345.jsonl').write_text('', encoding='utf-8')
        (tmp / 'raw_2026-09-21_67890.jsonl').write_text('', encoding='utf-8')

        found = recalc._find_files(tmp, '2026-09-21', merge=True)
        names = sorted(p.name for p in found)
        assert names == ['raw_2026-09-21.jsonl',
                         'raw_2026-09-21_12345.jsonl',
                         'raw_2026-09-21_67890.jsonl'], names

        # 1. Without merge, only the owner file is read.
        single = recalc._find_files(tmp, '2026-09-21', merge=False)
        assert [p.name for p in single] == ['raw_2026-09-21.jsonl']
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_merge_never_swallows_a_neighbouring_day() -> None:
    """An unresolved glob would pull in the 22nd while reading the 21st."""
    tmp = Path(tempfile.mkdtemp())
    try:
        (tmp / 'raw_2026-09-21.jsonl').write_text('', encoding='utf-8')
        (tmp / 'raw_2026-09-22.jsonl').write_text('', encoding='utf-8')
        (tmp / 'raw_2026-09-22_99.jsonl').write_text('', encoding='utf-8')

        found = recalc._find_files(tmp, '2026-09-21', merge=True)
        names = sorted(p.name for p in found)
        assert names == ['raw_2026-09-21.jsonl'], names
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_belongs_to_day() -> None:
    """Only the owner file and its pid files count as that day."""
    assert recalc._belongs_to_day('raw_2026-09-21.jsonl', '2026-09-21')
    assert recalc._belongs_to_day(
        'raw_2026-09-21_123.jsonl', '2026-09-21')
    assert not recalc._belongs_to_day(
        'raw_2026-09-22.jsonl', '2026-09-21')
    assert not recalc._belongs_to_day(
        'raw_2026-09-2.jsonl', '2026-09-21')
    assert not recalc._belongs_to_day(
        'raw_2026-09-21.txt', '2026-09-21')


def main() -> int:
    """
    Run every check and report the outcome.

    Returns:
        int: 0 when all checks passed, 1 otherwise.
    """
    checks = [value for name, value in sorted(globals().items())
              if name.startswith('test_') and callable(value)]
    failed = list()

    for check in checks:
        name = check.__name__
        try:
            check()
            print(f'PASS   {name}')
        except AssertionError as exc:
            failed.append(name)
            print(f'FAIL   {name}: {exc}')
        except Exception as exc:                      # noqa: BLE001
            failed.append(name)
            print(f'ERROR  {name}: {type(exc).__name__}: {exc}')

    print('')
    print(f'{len(checks) - len(failed)}/{len(checks)} checks passed')
    if failed:
        print(f'FAILED: {", ".join(failed)}')
        return 1
    print('All checks passed')
    return 0


if __name__ == '__main__':
    print('Program test_end_to_end.py started')
    total_start = arrow.now()

    code = main()

    total_elapsed = (arrow.now() - total_start).total_seconds()
    print(f'Program test_end_to_end.py finished, total time '
          f'{utils.format_elapsed(seconds=total_elapsed)}')
    sys.exit(code)
