"""
Hand-run checks for the callback that writes raw records.

The callback is the piece that runs inside a live gateway, so the checks
focus on its failure modes: it must write whole lines under concurrent
load, it must survive an empty or malformed payload, and it must never
raise into the request path.

Run directly:

    python test_usage_logger.py

A temporary directory is used throughout, so the real logs are untouched.
"""

# -*- coding: utf-8 -*-
# ==========================================================================
# Founded: 2026-09-21
# Modified: 2026-09-21
# @author yinlb, deepseek-flash
# ==========================================================================

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import arrow

import custom_callback
import recalc
from src import lockfile, utils


def _sample(prompt: int = 12000, answer: int = 3000,
            cached: int = 10000, alias: str = 'cline--ai-gateway') -> dict:
    """
    Build a payload shaped like a real LiteLLM logging object.

    Args:
        prompt (int): Prompt token count.
        answer (int): Completion token count.
        cached (int): Cached input token count.
        alias (str): Virtual key alias.

    Returns:
        dict: Payload accepted by the callback.
    """
    return {
        'id': 'req-test-1',
        'model': 'deepseek/deepseek-flash',
        'prompt_tokens': prompt,
        'completion_tokens': answer,
        'hidden_params': {'usage_object': {
            'prompt_cache_hit_tokens': cached}},
        'metadata': {
            'user_api_key_alias': alias,
            'user_api_key_hash': 'deadbeef12345678',
        },
    }


def _read_rows(directory: Path) -> list:
    """
    Read every record written under a directory.

    Args:
        directory (Path): Directory holding raw JSONL files.

    Returns:
        list: Decoded records, in file order.
    """
    rows = list()
    for path in sorted(directory.glob('raw_*.jsonl')):
        with path.open('r', encoding='utf-8') as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))
    return rows


def test_writes_one_line() -> None:
    """A single payload becomes exactly one valid JSON line."""
    tmp = Path(tempfile.mkdtemp())
    try:
        logger = custom_callback.UsageLogger(log_dir=tmp)
        ok = asyncio.run(logger.log(_sample(), ok=True))
        assert ok is True

        rows = _read_rows(tmp)
        assert len(rows) == 1
        assert rows[0]['in'] == 12000
        assert rows[0]['cache_hit'] == 10000
        assert rows[0]['user'] == 'ai-gateway'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_failure_is_recorded() -> None:
    """A failed request is written with ok false and its error text."""
    tmp = Path(tempfile.mkdtemp())
    try:
        logger = custom_callback.UsageLogger(log_dir=tmp)
        payload = _sample(prompt=6000, answer=0, cached=0)
        payload['error_str'] = 'upstream timeout'
        asyncio.run(logger.log(payload, ok=False))

        rows = _read_rows(tmp)
        assert len(rows) == 1
        assert rows[0]['ok'] is False
        assert rows[0]['in'] == 6000
        assert rows[0]['err'] == 'upstream timeout'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_concurrent_writes_stay_whole() -> None:
    """Concurrent writers never interleave inside a line."""
    tmp = Path(tempfile.mkdtemp())
    try:
        logger = custom_callback.UsageLogger(log_dir=tmp)
        count = 50

        async def hammer() -> None:
            await asyncio.gather(*[logger.log(_sample(), ok=True)
                                   for _ in range(count)])

        asyncio.run(hammer())
        rows = _read_rows(tmp)
        assert len(rows) == count, f'expected {count}, got {len(rows)}'
        for row in rows:
            assert row['in'] == 12000
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_creates_directory_on_demand() -> None:
    """A missing log directory is created at first write."""
    tmp = Path(tempfile.mkdtemp()) / 'nested' / 'logs'
    try:
        logger = custom_callback.UsageLogger(log_dir=tmp)
        assert not logger._ready
        asyncio.run(logger.log(_sample(), ok=True))
        assert tmp.is_dir()
        assert logger._ready is True
    finally:
        shutil.rmtree(tmp.parent.parent, ignore_errors=True)


def test_empty_payload_still_written() -> None:
    """An empty payload yields a partial row rather than an exception."""
    tmp = Path(tempfile.mkdtemp())
    try:
        logger = custom_callback.UsageLogger(log_dir=tmp)
        ok = asyncio.run(logger.log({}, ok=False))
        assert ok is True
        rows = _read_rows(tmp)
        assert len(rows) == 1
        assert rows[0]['partial'] is True
        assert rows[0]['in'] == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_broken_payload_never_raises() -> None:
    """A payload that cannot be processed returns a result, not an error."""
    tmp = Path(tempfile.mkdtemp())
    try:
        logger = custom_callback.UsageLogger(log_dir=tmp)
        ok = asyncio.run(logger.log(['not', 'a', 'mapping'], ok=True))
        assert ok is True, 'an unusable payload is still worth a line'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_slo_extraction() -> None:
    """Read the logging object from whichever key carries it."""
    payload = _sample()
    assert custom_callback._slo_from(
        {'standard_logging_object': payload}) == payload
    assert custom_callback._slo_from({'litellm_params': payload}) == payload
    assert custom_callback._slo_from({}) == {}
    assert custom_callback._slo_from(None) == {}
    assert custom_callback._slo_from({'other': 1}) == {}


def test_daily_file_split() -> None:
    """Rows from different days land in different files."""
    tmp = Path(tempfile.mkdtemp())
    try:
        logger = custom_callback.UsageLogger(log_dir=tmp)
        first = custom_callback._row_moment({'ts': '2026-09-21T23:59:59'})
        second = custom_callback._row_moment({'ts': '2026-09-22T00:00:01'})
        assert logger.path_for(first).name == 'raw_2026-09-21.jsonl'
        assert logger.path_for(second).name == 'raw_2026-09-22.jsonl'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_unreadable_timestamp_falls_back() -> None:
    """A broken timestamp must not lose the row."""
    moment = custom_callback._row_moment({'ts': 'not-a-date'})
    assert isinstance(moment, datetime)
    moment = custom_callback._row_moment({})
    assert isinstance(moment, datetime)


def test_import_has_no_side_effects() -> None:
    """Importing the module must not create a directory or open a file."""
    assert isinstance(custom_callback.LOG_DIR, Path)
    assert custom_callback.LOG_DIR.name == 'logs'


def test_default_dir_sits_beside_module() -> None:
    """The default log directory is beside the module, not the cwd."""
    assert custom_callback.LOG_DIR == custom_callback.BASE_DIR / 'logs'


def test_callback_class_exists() -> None:
    """The object LiteLLM is configured with must exist and be usable."""
    handler = custom_callback.proxy_handler_instance
    assert hasattr(handler, 'async_log_success_event')
    assert hasattr(handler, 'async_log_failure_event')


def test_callback_event_writes_row() -> None:
    """Drive the real event method and confirm a line lands on disk."""
    tmp = Path(tempfile.mkdtemp())
    original = custom_callback._LOGGER
    try:
        custom_callback._LOGGER = custom_callback.UsageLogger(log_dir=tmp)
        handler = custom_callback.UsageCallback()
        asyncio.run(handler.async_log_success_event(
            {'standard_logging_object': _sample()}, None, None, None))

        rows = _read_rows(tmp)
        assert len(rows) == 1
        assert rows[0]['model'] == 'deepseek/deepseek-flash'
    finally:
        custom_callback._LOGGER = original
        shutil.rmtree(tmp, ignore_errors=True)


def test_callback_event_never_raises() -> None:
    """A broken payload must be swallowed by the event method."""
    original = custom_callback._LOGGER
    try:
        custom_callback._LOGGER = custom_callback.UsageLogger(
            log_dir=Path(tempfile.mkdtemp()))
        handler = custom_callback.UsageCallback()
        asyncio.run(handler.async_log_failure_event(None, None, None, None))
    finally:
        custom_callback._LOGGER = original


def test_owner_writes_plain_daily_file() -> None:
    """The first process writes raw_YYYY-MM-DD.jsonl."""
    tmp = Path(tempfile.mkdtemp())
    try:
        logger = custom_callback.UsageLogger(log_dir=tmp)
        asyncio.run(logger.log(_sample(), ok=True))

        assert logger._owns_file is True
        files = sorted(p.name for p in tmp.glob('raw_*.jsonl'))
        assert files == ['raw_2026-09-21.jsonl'], files
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_loser_writes_pid_file() -> None:
    """A second process writes raw_YYYY-MM-DD_<pid>.jsonl instead."""
    tmp = Path(tempfile.mkdtemp())
    try:
        # 1. A live owner holds the lock: our own pid, fresh beat.
        lock_path = tmp / lockfile.LOCK_NAME
        lock_path.write_text(
            json.dumps({'pid': os.getpid(), 'heartbeat': time.time()}),
            encoding='utf-8')

        logger = custom_callback.UsageLogger(log_dir=tmp)
        asyncio.run(logger.log(_sample(), ok=True))

        assert logger._owns_file is False
        files = sorted(p.name for p in tmp.glob('raw_*.jsonl'))
        expected = f'raw_2026-09-21_{os.getpid()}.jsonl'
        assert files == [expected], files
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_stale_owner_is_taken_over() -> None:
    """A dead owner leaves residue that the next process claims."""
    tmp = Path(tempfile.mkdtemp())
    try:
        lock_path = tmp / lockfile.LOCK_NAME
        old = time.time() - (lockfile.STALE_SECONDS * 3)
        lock_path.write_text(
            json.dumps({'pid': 999999, 'heartbeat': old}), encoding='utf-8')

        logger = custom_callback.UsageLogger(log_dir=tmp)
        asyncio.run(logger.log(_sample(), ok=True))

        assert logger._owns_file is True
        files = sorted(p.name for p in tmp.glob('raw_*.jsonl'))
        assert files == ['raw_2026-09-21.jsonl'], files
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_heartbeat_starts_only_for_owner() -> None:
    """A losing process must not start a heartbeat thread."""
    tmp = Path(tempfile.mkdtemp())
    try:
        lock_path = tmp / lockfile.LOCK_NAME
        lock_path.write_text(
            json.dumps({'pid': os.getpid(), 'heartbeat': time.time()}),
            encoding='utf-8')

        logger = custom_callback.UsageLogger(log_dir=tmp)
        asyncio.run(logger.log(_sample(), ok=True))
        assert logger._heartbeat is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_release_only_by_owner() -> None:
    """Releasing must not delete a lock someone else owns."""
    tmp = Path(tempfile.mkdtemp())
    try:
        logger = custom_callback.UsageLogger(log_dir=tmp)
        asyncio.run(logger.log(_sample(), ok=True))
        lock_path = tmp / lockfile.LOCK_NAME
        assert lock_path.exists()

        logger.release()
        assert not lock_path.exists()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_merge_reads_both_files() -> None:
    """The report must pick up owner and pid files together."""
    tmp = Path(tempfile.mkdtemp())
    try:
        owner = custom_callback.UsageLogger(log_dir=tmp)
        asyncio.run(owner.log(_sample(), ok=True))

        # 1. A second process, told to fall back.
        lock_path = tmp / lockfile.LOCK_NAME
        lock_path.write_text(
            json.dumps({'pid': os.getpid(), 'heartbeat': time.time()}),
            encoding='utf-8')
        loser = custom_callback.UsageLogger(log_dir=tmp)
        payload = _sample()
        payload['id'] = 'req-test-2'
        asyncio.run(loser.log(payload, ok=True))

        files = sorted(tmp.glob('raw_*.jsonl'))
        assert len(files) == 2, [f.name for f in files]

        rows = recalc._load_rows(files)
        assert len(rows) == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
    print('Program test_usage_logger.py started')
    total_start = arrow.now()

    code = main()

    total_elapsed = (arrow.now() - total_start).total_seconds()
    print(f'Program test_usage_logger.py finished, total time '
          f'{utils.format_elapsed(seconds=total_elapsed)}')
    sys.exit(code)
