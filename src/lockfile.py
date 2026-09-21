"""
Decide which file a process may write when several may run at once.

LiteLLM runs one process with many coroutines, and one asyncio lock
covers every coroutine inside that process. It does not cover a second
process: two gateways started by accident would each open the same file
and interleave their lines.

This module settles that without ever refusing to start. The first
process to create the lock file owns the shared log; any later process
detects the owner and falls back to a file named after its own pid. The
reporter merges those files by request id afterwards, so nothing is lost
and no request is ever held up by an accounting concern.

The lock is created with O_CREAT | O_EXCL, which the file system
guarantees to be atomic. Testing whether the file exists and then
creating it would leave a window where both processes see it absent.
"""

# -*- coding: utf-8 -*-
# ==========================================================================
# Founded: 2026-09-21
# Modified: 2026-09-21
# @author yinlb, deepseek-flash
# ==========================================================================

import json
import os
import time
from pathlib import Path

# Name of the file that records who owns the shared raw log.
LOCK_NAME = '.lock'

# How often a live process refreshes its heartbeat.
HEARTBEAT_SECONDS = 5

# A beat older than this belongs to a process that is gone. Six times
# the refresh interval, so six missed beats are tolerated.
STALE_SECONDS = 30


def read_lock(lock_path: Path) -> dict:
    """
    Read a lock file, returning an empty mapping when it is unusable.

    A half-written or unreadable lock is treated as no information
    rather than as an error: the caller has to decide what to do, and
    failing here would defeat the purpose of the check.

    Args:
        lock_path (Path): Path of the lock file.

    Returns:
        dict: Mapping with pid and heartbeat, or an empty mapping.
    """
    try:
        data = json.loads(lock_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def is_alive(pid: object) -> bool:
    """
    Report whether a process id belongs to a running process.

    Windows is probed through the kernel handle; POSIX uses the usual
    signal 0. An unreadable pid counts as dead so that a damaged lock
    can be taken over instead of blocking writes forever.

    Args:
        pid (object): Process id read from a lock file.

    Returns:
        bool: True when the process appears to be running.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False

    if os.name == 'nt':
        return _alive_on_windows(pid)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _alive_on_windows(pid: int) -> bool:
    """
    Probe a process id on Windows through the kernel handle.

    Args:
        pid (int): Process id to test.

    Returns:
        bool: True when the process is running.
    """
    import ctypes

    # 1. PROCESS_QUERY_LIMITED_INFORMATION is enough to ask the state.
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def heartbeat_fresh(data: dict, now: float = None) -> bool:
    """
    Report whether a heartbeat is recent enough to mean a live owner.

    Args:
        data (dict): Lock payload read from disk.
        now (float): Current epoch seconds. Defaults to the real clock.

    Returns:
        bool: True when the beat falls inside the stale window.
    """
    if now is None:
        now = time.time()
    try:
        beat = float(data.get('heartbeat'))
    except (TypeError, ValueError):
        return False
    return (now - beat) < STALE_SECONDS



def _inspect_existing(lock_path: Path, now: float = None) -> str:
    """
    Judge an existing lock and clear it when its owner is gone.

    A fresh heartbeat with a live process means the lock stands. A stale
    heartbeat or a dead process means the lock is residue from a crash,
    and removing it lets the next attempt succeed.

    Args:
        lock_path (Path): Path of the lock file.
        now (float): Current epoch seconds. Defaults to the real clock.

    Returns:
        str: 'taken' or 'stale'.
    """
    data = read_lock(lock_path)
    if heartbeat_fresh(data, now) and is_alive(data.get('pid')):
        return 'taken'

    # 1. The owner is gone, so drop the residue for the next attempt.
    try:
        lock_path.unlink()
    except OSError:
        pass
    return 'stale'


def try_acquire(lock_path: Path, now: float = None) -> str:
    """
    Attempt to become the owner of the shared raw log.

    Three answers are possible. An empty string means ownership was won.
    'taken' means a live process owns it and this one must fall back.
    'stale' means the previous owner was gone and its lock removed, so a
    retry may now succeed.

    Args:
        lock_path (Path): Path of the lock file.
        now (float): Current epoch seconds. Defaults to the real clock.

    Returns:
        str: '', 'taken' or 'stale'.
    """
    stamp = now if now is not None else time.time()
    payload = {'pid': os.getpid(), 'heartbeat': stamp}
    try:
        handle = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return _inspect_existing(lock_path, now)
    except OSError:
        return 'taken'

    # 1. The race was won, so record who we are and start beating.
    try:
        os.write(handle, json.dumps(payload).encode('utf-8'))
    finally:
        os.close(handle)
    return ''


def acquire(lock_path: Path, retries: int = 3) -> bool:
    """
    Try to take the lock, clearing a stale one and retrying.

    Args:
        lock_path (Path): Path of the lock file.
        retries (int): Number of attempts made in total.

    Returns:
        bool: True when this process owns the lock.
    """
    for _ in range(max(1, retries)):
        outcome = try_acquire(lock_path)
        if outcome == '':
            return True
        if outcome == 'taken':
            return False
    return False


def refresh(lock_path: Path, now: float = None) -> bool:
    """
    Rewrite the heartbeat while keeping the same owner.

    A process that no longer owns the lock must not refresh it, so the
    recorded pid is checked first.

    Args:
        lock_path (Path): Path of the lock file.
        now (float): Current epoch seconds. Defaults to the real clock.

    Returns:
        bool: True when the heartbeat was written.
    """
    data = read_lock(lock_path)
    if data.get('pid') != os.getpid():
        return False
    payload = {'pid': os.getpid(),
               'heartbeat': now if now is not None else time.time()}
    try:
        lock_path.write_text(json.dumps(payload), encoding='utf-8')
    except OSError:
        return False
    return True


def release(lock_path: Path) -> bool:
    """
    Drop the lock at exit, but only when this process still owns it.

    Args:
        lock_path (Path): Path of the lock file.

    Returns:
        bool: True when the file was removed.
    """
    data = read_lock(lock_path)
    if data.get('pid') != os.getpid():
        return False
    try:
        lock_path.unlink()
    except OSError:
        return False
    return True


def is_stale(lock_path: Path, now: float = None) -> bool:
    """
    Report whether an existing lock belongs to a process that is gone.

    Args:
        lock_path (Path): Path of the lock file.
        now (float): Current epoch seconds. Defaults to the real clock.

    Returns:
        bool: True when the lock exists but its owner cannot be alive.
    """
    if not lock_path.exists():
        return False
    data = read_lock(lock_path)
    if not data:
        return True
    return not (heartbeat_fresh(data, now) and is_alive(data.get('pid')))
