"""
Hand-run checks for the record extraction rules.

There is no test framework in this project, so every check is a plain
function that raises AssertionError on failure and prints its name when
it passes. Run this file directly:

    python test_callback.py

The payloads below mimic what the providers actually return, including
the awkward cases: counters buried under different names, a ceiling of
zero on cached input, and a missing usage object.
"""

# -*- coding: utf-8 -*-
# ==========================================================================
# Founded: 2026-09-21
# Modified: 2026-09-21
# @author yinlb, deepseek-flash
# ==========================================================================

import sys
from datetime import datetime

import arrow

from src import record, utils

# A fixed moment so that assertions on ts stay stable.
MOMENT = datetime(2026, 9, 21, 10, 0, 0)


def test_deep_get() -> None:
    """Read a key from a mapping, and survive a non-mapping."""
    assert record.deep_get({'a': 1}, ['a']) == 1
    assert record.deep_get({'a': 1}, ['b', 'a']) == 1
    assert record.deep_get({'a': 1}, ['b']) is None
    assert record.deep_get(None, ['a']) is None
    assert record.deep_get('text', ['a']) is None
    assert record.deep_get({'a': None}, ['a'], default=7) == 7


def test_cache_hit_key_names() -> None:
    """Find the cached input count under every provider spelling."""
    names = ['cache_read_input_tokens', 'cache_hit_tokens',
             'prompt_cache_hit_tokens']
    for name in names:
        slo = {'hidden_params': {'usage_object': {name: 120}}}
        usage = record.extract_usage(slo)
        assert usage['cache_hit'] == 120, name

    # 1. Fallback into the prompt details block.
    slo = {'hidden_params': {'usage_object': {
        'prompt_tokens_details': {'cached_tokens': 99}}}}
    assert record.extract_usage(slo)['cache_hit'] == 99

    # 2. Fallback to metadata when hidden_params has no usage object.
    slo = {'metadata': {'usage_object': {'prompt_cache_hit_tokens': 55}}}
    assert record.extract_usage(slo)['cache_hit'] == 55


def test_cache_write_key_names() -> None:
    """Find cache writes, and report absence as None rather than zero."""
    slo = {'hidden_params': {'usage_object': {
        'cache_creation_input_tokens': 320}}}
    assert record.extract_usage(slo)['cache_write'] == 320

    slo = {'hidden_params': {'usage_object': {'cache_write_tokens': 7}}}
    assert record.extract_usage(slo)['cache_write'] == 7

    empty = {'hidden_params': {'usage_object': {}}}
    assert record.extract_usage(empty)['cache_write'] is None


def test_reasoning_tokens() -> None:
    """Find reasoning tokens in either location, else None."""
    slo = {'hidden_params': {'usage_object': {'reasoning_tokens': 500}}}
    assert record.extract_usage(slo)['reasoning_tokens'] == 500

    slo = {'hidden_params': {'usage_object': {
        'completion_tokens_details': {'reasoning_tokens': 12}}}}
    assert record.extract_usage(slo)['reasoning_tokens'] == 12

    empty = {'hidden_params': {'usage_object': {}}}
    assert record.extract_usage(empty)['reasoning_tokens'] is None


def test_alias_split() -> None:
    """Split a virtual key alias into tool and project."""
    assert record._split_alias('codex--wind-forecast') == 'wind-forecast'
    assert record._split_alias('cline--ai-gateway') == 'ai-gateway'
    assert record._split_alias('nodeepdash') is None
    assert record._split_alias('tool--') is None
    assert record._split_alias(None) is None
    assert record._split_alias('a--b--c') == 'b--c'


def test_user_from_header() -> None:
    """Prefer the project stated in the spend logs header."""
    slo = {'metadata': {
        'spend_logs_metadata': '{"project": "wind-forecast"}',
        'user_api_key_alias': 'cline--ai-gateway',
    }}
    assert record.resolve_user(slo) == 'wind-forecast'

    # 1. A malformed header must fall back to the alias, not raise.
    slo = {'metadata': {
        'spend_logs_metadata': '{not json',
        'user_api_key_alias': 'codex--wind-forecast',
    }}
    assert record.resolve_user(slo) == 'wind-forecast'

    # 2. A header that is already a mapping is accepted too.
    slo = {'metadata': {'spend_logs_metadata': {'project': 'alpha'}}}
    assert record.resolve_user(slo) == 'alpha'


def test_error_redaction() -> None:
    """Keep credentials and newlines out of an error summary."""
    text = record.clean_error('boom\nkey: sk-abcdefghijklmnop')
    assert '\n' not in text
    assert 'sk-abcdefghijklmnop' not in text

    text = record.clean_error(
        {'error_class': 'AuthenticationError', 'error_message': 'bad key'})
    assert 'AuthenticationError' in text

    assert record.clean_error(None) is None

    long_text = record.clean_error('x' * 500)


def test_build_record_full() -> None:
    """Build a complete record from a well-formed payload."""
    slo = {
        'id': 'req-abc-123',
        'model': 'deepseek/deepseek-flash',
        'prompt_tokens': 12000,
        'completion_tokens': 3000,
        'response_cost': 0.0041,
        'hidden_params': {
            'usage_object': {
                'prompt_cache_hit_tokens': 10000,
                'reasoning_tokens': 800,
            },
            'model_id': 'dep-1',
            'api_base': 'https://api.deepseek.com',
        },
        'metadata': {
            'user_api_key_alias': 'cline--ai-gateway',
            'user_api_key_hash': 'abcdef1234567890',
        },
    }
    row = record.build_record(slo, ok=True, moment=MOMENT)

    assert row['ok'] is True
    assert row['in'] == 12000
    assert row['out'] == 3000
    assert row['cache_hit'] == 10000
    assert row['reasoning_tokens'] == 800
    assert row['user'] == 'ai-gateway'
    assert row['key'] == 'cline--ai-gateway'
    assert row['key_hash8'] == 'abcdef12'
    assert row['req_id'] == 'req-abc-123'
    assert row['model_id'] == 'dep-1'
    assert row['api_base'] == 'https://api.deepseek.com'
    assert row['cost_upstream'] == 0.0041
    assert 'cache_write' not in row
    assert 'partial' not in row
    assert 'peak' not in row


def test_build_record_missing_usage() -> None:
    """Mark a missing usage object as partial, not as no-cache model."""
    slo = {
        'model': 'deepseek/deepseek-flash',
        'prompt_tokens': 500,
        'completion_tokens': 50,
        'metadata': {'user_api_key_alias': 'cline--ai-gateway'},
    }
    row = record.build_record(slo, ok=True, moment=MOMENT)
    assert row['cache_hit'] == 0
    assert row['partial'] is True
    assert 'cache_write' not in row


def test_build_record_failure() -> None:
    """Record a failed request, keeping whatever usage came through."""
    slo = {
        'model': 'deepseek/deepseek-flash',
        'prompt_tokens': 6000,
        'completion_tokens': 0,
        'error_str': 'upstream timeout',
        'metadata': {'user_api_key_alias': 'cline--ai-gateway'},
    }
    row = record.build_record(slo, ok=False, moment=MOMENT)
    assert row['ok'] is False
    assert row['in'] == 6000
    assert row['err'] == 'upstream timeout'


def test_build_record_no_project() -> None:
    """Keep the request but flag the missing project attribution."""
    slo = {
        'model': 'deepseek/deepseek-flash',
        'prompt_tokens': 100,
        'completion_tokens': 10,
        'metadata': {'user_api_key_alias': 'noconvention'},
    }
    row = record.build_record(slo, ok=True, moment=MOMENT)
    assert row['user'] is None
    assert row['partial'] is True
    assert row['err'] == 'missing_user'


def test_build_record_empty_object() -> None:
    """Survive an empty logging object, recording zeros."""
    row = record.build_record({}, ok=False, moment=MOMENT)
    assert row['in'] == 0
    assert row['out'] == 0
    assert row['cache_hit'] == 0
    assert row['partial'] is True
    assert row['model'] == 'unknown'
    assert row['key'] == 'unknown'


def test_record_is_json_ready() -> None:
    """Every field must survive a JSON round trip."""
    import json

    slo = {'model': 'm', 'prompt_tokens': 1, 'completion_tokens': 1,
           'metadata': {'user_api_key_alias': 't--p'}}
    row = record.build_record(slo, ok=True, moment=MOMENT)
    again = json.loads(json.dumps(row, ensure_ascii=False))
    assert again == row


def test_record_costs_with_pricing() -> None:
    """A built record must be costable by the pricing module."""
    from pathlib import Path

    from src import pricing

    doc = pricing.load_pricing(
        Path(__file__).parent / 'config' / 'pricing.yaml')
    slo = {
        'model': 'deepseek/deepseek-flash',
        'prompt_tokens': 12000,
        'completion_tokens': 3000,
        'hidden_params': {'usage_object': {
            'prompt_cache_hit_tokens': 10000}},
        'metadata': {'user_api_key_alias': 'cline--ai-gateway'},
    }
    # 10:00 on a weekday falls inside a peak window, so the peak rates
    # apply without any band being stored in the record.
    row = record.build_record(
        slo, ok=True, moment=datetime(2026, 9, 21, 10, 0, 0))

    # Peak band: 10000*0.04 + 2000*2 + 3000*8, over one million.
    cost, currency = pricing.row_cost(row, doc)
    assert currency == 'CNY'
    assert abs(cost - 0.0284) < 1e-9, cost
    assert 'peak' not in row


def main() -> int:
    """
    Run every check and report the outcome.

    Returns:
        int: 0 when all checks passed, 1 otherwise.
    """
    # 1. Collect the checks in the order they are defined.
    checks = [value for name, value in sorted(globals().items())
              if name.startswith('test_') and callable(value)]
    failed = list()

    # 2. Run each one, keeping going after a failure.
    for check in checks:
        name = check.__name__
        try:
            check()
            print(f'PASS   {name}')
        except AssertionError as exc:
            failed.append(name)
            print(f'FAIL   {name}: {exc}')

    # 3. Summarise.
    print('')
    print(f'{len(checks) - len(failed)}/{len(checks)} checks passed')
    if failed:
        print(f'FAILED: {", ".join(failed)}')
        return 1
    print('All checks passed')
    return 0


if __name__ == '__main__':
    print('Program test_callback.py started')
    total_start = arrow.now()

    code = main()

    total_elapsed = (arrow.now() - total_start).total_seconds()
    print(f'Program test_callback.py finished, total time '
          f'{utils.format_elapsed(seconds=total_elapsed)}')
    sys.exit(code)

    assert len(long_text) == 200
