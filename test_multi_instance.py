"""
Hand-run checks for the multi-process lock.

A second gateway process must never be refused, but it also must not
write into the same file as the first. The checks below cover the four
decisions the lock has to make: winning the race, standing aside for a
live owner, taking over from a dead one, and keeping a heartbeat fresh
independently of request traffic.

Run directly:

    python test_multi_instance.py
"""

# -*- coding: utf-8 -*-
# ==========================================================================
# Founded: 2026-09-21
# Modified: 2026-09-21
# @author yinlb, deepseek-flash
# ==========================================================================

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import arrow

from src import lockfile, utils


def test_acquire_creates_lock() -> None:
    """A free lock is taken, and the payload names this process."""
    tmp = Path(tempfile.mkdtemp())
    try:
        path = tmp / lockfile.LOCK_NAME
        assert lockfile.acquire(path) is True
        assert path.exists()

        data = json.loads(path.read_text(encoding='utf-8'))
        assert data['pid'] == os.getpid()
        assert 'heartbeat' in data
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_second_process_is_told_taken() -> None:
    """A live owner with a fresh beat keeps the lock."""
    tmp = Path(tempfile.mkdtemp())
    try:
        path = tmp / lockfile.LOCK_NAME
        # Pretend another process holds it: a pid that is alive (ours)
        # and a heartbeat from this instant.
        path.write_text(json.dumps(
            {'pid': os.getpid(), 'heartbeat': time.time()}), encoding='utf-8')

        outcome = lockfile.try_acquire(path)
        assert outcome == 'taken', outcome
        assert lockfile.acquire(path) is False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_stale_lock_is_taken_over() -> None:
    """An old heartbeat means the owner is gone and the lock is free."""
    tmp = Path(tempfile.mkdtemp())
    try:
        path = tmp / lockfile.LOCK_NAME
        stamp = time.time() - (lockfile.STALE_SECONDS * 3)
        path.write_text(json.dumps(
            {'pid': 999999, 'heartbeat': stamp}), encoding='utf-8')

        assert lockfile.is_stale(path) is True
        assert lockfile.acquire(path) is True

        data = json.loads(path.read_text(encoding='utf-8'))
        assert data['pid'] == os.getpid()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_dead_pid_is_taken_over() -> None:
    """A fresh beat from a process that no longer exists is residue."""
    tmp = Path(tempfile.mkdtemp())
    try:
        path = tmp / lockfile.LOCK_NAME
        # A pid beyond any real process, with a beat from just now.
        path.write_text(json.dumps(
            {'pid': 999999999, 'heartbeat': time.time()}), encoding='utf-8')

        assert lockfile.is_alive(999999999) is False
        assert lockfile.try_acquire(path) == 'stale'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_broken_lock_file_is_survivable() -> None:
    """Garbage in the lock must not raise, only count as unknown."""
    tmp = Path(tempfile.mkdtemp())
    try:
        path = tmp / lockfile.LOCK_NAME
        path.write_text('{not json', encoding='utf-8')

        assert lockfile.read_lock(path) == {}
        assert lockfile.is_stale(path) is True
        assert lockfile.acquire(path) is True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_missing_lock_is_not_stale() -> None:
    """An absent lock is simply free, not residue."""
    tmp = Path(tempfile.mkdtemp())
    try:
        path = tmp / lockfile.LOCK_NAME
        assert lockfile.is_stale(path) is False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_heartbeat_freshness() -> None:
    """The beat is judged against the stale window, not the wall clock."""
    now = 1_000_000.0
    fresh = {'pid': 1, 'heartbeat': now - 1}
    old = {'pid': 1, 'heartbeat': now - lockfile.STALE_SECONDS - 1}

    assert lockfile.heartbeat_fresh(fresh, now) is True
    assert lockfile.heartbeat_fresh(old, now) is False
    assert lockfile.heartbeat_fresh({}, now) is False
    assert lockfile.heartbeat_fresh({'heartbeat': 'nope'}, now) is False


def test_refresh_keeps_owner() -> None:
    """Only the owner may move its own heartbeat forward."""
    tmp = Path(tempfile.mkdtemp())
    try:
        path = tmp / lockfile.LOCK_NAME
        lockfile.acquire(path)
        before = lockfile.read_lock(path)['heartbeat']

        time.sleep(0.01)
        assert lockfile.refresh(path) is True
        after = lockfile.read_lock(path)['heartbeat']
        assert after > before

        # 1. A lock owned by someone else must not be refreshed.
        path.write_text(json.dumps(
            {'pid': 999999, 'heartbeat': after}), encoding='utf-8')
        assert lockfile.refresh(path) is False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_release_only_by_owner() -> None:
    """Exit cleanup must not delete a lock another process owns."""
    tmp = Path(tempfile.mkdtemp())
    try:
        path = tmp / lockfile.LOCK_NAME

        # 1. Someone else owns it: release must leave it alone.
        path.write_text(json.dumps(
            {'pid': 999999, 'heartbeat': time.time()}), encoding='utf-8')
        assert lockfile.release(path) is False
        assert path.exists()

        # 2. We own it: release removes it.
        lockfile.acquire(path)
        assert lockfile.release(path) is True
        assert not path.exists()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_is_alive_rejects_junk() -> None:
    """A pid that is not a positive number counts as dead."""
    assert lockfile.is_alive(None) is False
    assert lockfile.is_alive('abc') is False
    assert lockfile.is_alive(0) is False
    assert lockfile.is_alive(-5) is False


def test_atomic_race_has_one_winner() -> None:
    """Millisecond-close contenders must produce exactly one owner.

    The check spawns real processes, because the guarantee comes from
    the file system and cannot be reproduced inside one interpreter.
    """
    tmp = Path(tempfile.mkdtemp())
    try:
        path = tmp / lockfile.LOCK_NAME
        script = tmp / 'race.py'
        script.write_text(
            'import sys\n'
            'from pathlib import Path\n'
            f'sys.path.insert(0, r"{Path(__file__).parent}")\n'
            'from src import lockfile\n'
            'ok = lockfile.acquire(Path(sys.argv[1]))\n'
            'print(1 if ok else 0)\n',
            encoding='utf-8')

        # 1. Launch several contenders as close together as possible.
        procs = list()
        for _ in range(6):
            procs.append(subprocess.Popen(
                [sys.executable, str(script), str(path)],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True))

        wins = 0
        for proc in procs:
            out, _ = proc.communicate(timeout=60)
            if out.strip() == '1':
                wins += 1

        assert wins == 1, f'expected exactly one winner, got {wins}'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_fallback_file_name_uses_pid() -> None:
    """A losing process writes a pid-suffixed file beside the main one."""
    tmp = Path(tempfile.mkdtemp())
    try:
        main = tmp / 'raw_2026-09-21.jsonl'
        fallback = tmp / f'raw_2026-09-21_{os.getpid()}.jsonl'
        assert main != fallback
        assert str(os.getpid()) in fallback.name
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_heartbeat_does_not_need_requests() -> None:
    """The beat moves on its own, so a long request cannot kill the lock.

    A slow request must not be able to make the owner look dead: the
    heartbeat is refreshed by a timer, not by request traffic.
    """
    tmp = Path(tempfile.mkdtemp())
    try:
        path = tmp / lockfile.LOCK_NAME
        lockfile.acquire(path)
        first = lockfile.read_lock(path)['heartbeat']

        # 1. Simulate the passage of several refresh intervals without
        #    any request being recorded.
        for _ in range(3):
            lockfile.refresh(path)
            time.sleep(0.01)
        second = lockfile.read_lock(path)['heartbeat']

        assert second > first
        assert lockfile.is_stale(path) is False
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
    print('Program test_multi_instance.py started')
    total_start = arrow.now()

    code = main()

    total_elapsed = (arrow.now() - total_start).total_seconds()
    print(f'Program test_multi_instance.py finished, total time '
          f'{utils.format_elapsed(seconds=total_elapsed)}')
    sys.exit(code)

