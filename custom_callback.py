"""
Record every LiteLLM request as one line of JSON.

This callback writes facts only: when a request happened, which tool and
project it came from, which model answered, and how many tokens it used.
Cost is never written here, because the price table can change and the
report must be recomputed from facts plus rules.

Two rules govern everything in this file:

1. A failure here must never break a request. LiteLLM runs callbacks
   inside the request path, so an exception escaping this module could
   turn a working request into a 500. Every entry point catches its own
   errors and prints instead of raising.

2. Importing this module must be harmless. No directory is created, no
   file is opened and no configuration is read at import time, because
   a broken accounting setup must not stop the gateway from starting.

Register it with LiteLLM as:

    litellm_settings:
      callbacks: custom_callback.proxy_handler_instance
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
import threading
import time
from datetime import datetime
from pathlib import Path

from src import lockfile, record

# The log directory can be redirected by an environment variable. The
# default sits beside this module, which is independent of the working
# directory the gateway was started from.
BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = Path(os.environ.get('AI_GATEWAY_LOG_DIR') or (BASE_DIR / 'logs'))


class UsageLogger:
    """
    Append one JSON line per request to a per-day file.

    The class is deliberately free of LiteLLM imports so that it can be
    exercised from a test without a gateway running. A thin subclass at
    the end of this module connects it to LiteLLM.
    """

    def __init__(self, log_dir: Path = None) -> None:
        """
        Prepare the logger without touching the disk.

        Args:
            log_dir (Path): Directory for the raw files. Defaults to the
                module-level LOG_DIR.
        """
        self._dir = Path(log_dir) if log_dir else LOG_DIR
        self._lock = asyncio.Lock()
        self._ready = False
        self._written = 0
        self._owns_file = True
        self._heartbeat = None
        self._claimed = False

    def claim(self) -> bool:
        """
        Decide whether this process writes the shared file or its own.

        Called once when the first record is written. Losing the race is
        not an error: the process keeps running and writes a file named
        after its pid, which the report merges afterwards. Refusing to
        start would let an accounting concern stop the gateway, which
        this project never allows.

        Returns:
            bool: True when this process owns the shared file.
        """
        lock_path = self._dir / lockfile.LOCK_NAME
        self._owns_file = lockfile.acquire(lock_path)

        # 1. Warn loudly when degraded, so the duplicate files are not a
        #    surprise later.
        if not self._owns_file:
            owner = lockfile.read_lock(lock_path)
            print(f'WARN   another gateway process owns the raw log '
                  f'(pid {owner.get("pid")}); writing pid-specific files')
        return self._owns_file

    def _ensure_dir(self) -> None:
        """
        Create the log directory on first use.

        Doing this here rather than in the constructor keeps import and
        instantiation free of side effects, so a missing directory can
        never stop the gateway from starting.

        Returns:
            None.
        """
        if not self._ready:
            os.makedirs(self._dir, exist_ok=True)
            self._ready = True

    def path_for(self, moment: datetime) -> Path:
        """
        Build the file name for a timestamp.

        A process that owns the shared file writes the plain daily name.
        A process that lost the race appends its pid, so that two
        gateways never write the same file.

        Args:
            moment (datetime): Timestamp of the request.

        Returns:
            Path: File holding that day's records.
        """
        stamp = moment.strftime('%Y-%m-%d')
        if self._owns_file:
            return self._dir / f'raw_{stamp}.jsonl'
        return self._dir / f'raw_{stamp}_{os.getpid()}.jsonl'

    def start_heartbeat(self) -> None:
        """
        Begin refreshing the lock on a timer of its own.

        The beat must not depend on request traffic, or a single slow
        request would make the owner look dead and trigger a takeover.

        Returns:
            None.
        """
        if self._heartbeat is not None or not self._owns_file:
            return
        self._heartbeat = threading.Thread(
            target=self._beat_forever, name='ai-gateway-heartbeat',
            daemon=True)
        self._heartbeat.start()

    def _beat_forever(self) -> None:
        """
        Refresh the lock until the process exits.

        Returns:
            None.
        """
        lock_path = self._dir / lockfile.LOCK_NAME
        while True:
            time.sleep(lockfile.HEARTBEAT_SECONDS)
            if not lockfile.refresh(lock_path):
                return

    def release(self) -> None:
        """
        Drop the lock so the next start does not see residue.

        Returns:
            None.
        """
        if self._owns_file:
            lockfile.release(self._dir / lockfile.LOCK_NAME)


    async def append(self, row: dict) -> None:
        """
        Append one record, serialised against other writers.

        The file name is computed inside the lock on purpose. A request
        that starts just before midnight and finishes just after could
        otherwise have two coroutines disagree about which day the row
        belongs to.

        Args:
            row (dict): Record produced by record.build_record.

        Returns:
            None.
        """
        line = json.dumps(row, ensure_ascii=False) + '\n'
        moment = _row_moment(row)

        # 1. Hold the lock across both the name lookup and the write.
        async with self._lock:
            self._ensure_dir()

            # 2. Decide once, on the first record, whether this process
            #    owns the shared file or must write its own.
            if not self._claimed:
                self.claim()
                self.start_heartbeat()
                self._claimed = True

            path = self.path_for(moment)
            with path.open('a', encoding='utf-8') as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            self._written += 1

    async def log(self, slo: dict, ok: bool) -> bool:
        """
        Turn a logging object into a record and store it.

        Any error is reported and swallowed, because a broken accounting
        path must not fail a user's request.

        Args:
            slo (dict): The standard logging object from LiteLLM.
            ok (bool): Whether the request succeeded.

        Returns:
            bool: True when a line was written.
        """
        try:
            row = record.build_record(slo, ok=ok)
            await self.append(row)
            if row.get('partial'):
                print(f'WARN   partial record written for '
                      f'{row.get("model")}')
            return True
        except Exception as exc:                      # noqa: BLE001
            print(f'ERROR  callback failed to record a request: {exc}')
            return False


def _row_moment(row: dict) -> datetime:
    """
    Read the timestamp back out of a finished record.

    The record stores its time as an ISO string, and that value decides
    which daily file the row lands in. An unreadable timestamp falls back
    to the current time rather than losing the row.

    Args:
        row (dict): Record produced by record.build_record.

    Returns:
        datetime: Timestamp to file the row under.
    """
    raw = row.get('ts')
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
    return datetime.now()


def log_success(slo: dict) -> bool:
    """
    Record a successful request through the shared logger.

    Args:
        slo (dict): The standard logging object from LiteLLM.

    Returns:
        bool: True when a line was written.
    """
    return _run_logger(slo, ok=True)


def log_failure(slo: dict) -> bool:
    """
    Record a failed request through the shared logger.

    Failed requests are written too: the provider has already consumed
    the prompt tokens, so omitting them would understate the bill.

    Args:
        slo (dict): The standard logging object from LiteLLM.

    Returns:
        bool: True when a line was written.
    """
    return _run_logger(slo, ok=False)


def _run_logger(slo: dict, ok: bool) -> bool:
    """
    Drive the shared logger from synchronous or async callers.

    LiteLLM may invoke a callback from inside a running event loop. In
    that case the coroutine cannot be awaited from here, so the record is
    built and stored on a fresh loop in a worker thread to avoid
    interfering with the caller's loop.

    Args:
        slo (dict): The standard logging object from LiteLLM.
        ok (bool): Whether the request succeeded.

    Returns:
        bool: True when a line was written.
    """
    logger = get_logger()

    # 1. Inside a running loop: hand the write to a helper thread.
    try:
        asyncio.get_running_loop()
        running = True
    except RuntimeError:
        running = False

    if running:
        return _write_in_thread(logger, slo, ok)
    return asyncio.run(logger.log(slo, ok=ok))


def _write_in_thread(logger: 'UsageLogger', slo: dict, ok: bool) -> bool:
    """
    Perform the write on a private event loop in another thread.

    Args:
        logger (UsageLogger): Logger to write with.
        slo (dict): The standard logging object from LiteLLM.
        ok (bool): Whether the request succeeded.

    Returns:
        bool: True when a line was written.
    """
    from concurrent.futures import ThreadPoolExecutor

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                lambda: asyncio.run(logger.log(slo, ok=ok))).result()
    except Exception as exc:                          # noqa: BLE001
        print(f'ERROR  callback thread write failed: {exc}')
        return False




# The shared instance, created on first use rather than at import.
_LOGGER = None

# Bridge to LiteLLM when it is installed. The accounting modules stay
# importable without LiteLLM, which keeps the tests independent of a
# gateway and lets this file be validated on its own.
try:
    from litellm.integrations.custom_logger import CustomLogger as _Base
except ImportError:                                   # pragma: no cover
    _Base = object


def _slo_from(kwargs: dict) -> dict:
    """
    Pull the standard logging object out of the callback arguments.

    LiteLLM has used more than one key for this over time, and a stream
    that died early may carry no object at all, so every candidate is
    tried and an empty mapping is an acceptable answer.

    Args:
        kwargs (dict): Arguments LiteLLM passed to the callback.

    Returns:
        dict: The logging object, or an empty mapping.
    """
    if not isinstance(kwargs, dict):
        return {}
    for key in ('standard_logging_object', 'litellm_params',
                'response_obj'):
        value = kwargs.get(key)
        if isinstance(value, dict) and value:
            return value
    return {}


class UsageCallback(_Base):
    """
    LiteLLM adapter for UsageLogger.

    Every method swallows its own errors: an accounting problem must
    never surface as a failed request.
    """

    async def async_log_success_event(self, kwargs: dict,
                                      response_obj: object,
                                      start_time: object,
                                      end_time: object) -> None:
        """
        Record a successful request.

        Args:
            kwargs (dict): LiteLLM call details, holding the standard
                logging object.
            response_obj (object): Raw provider response, unused.
            start_time (object): Request start, unused.
            end_time (object): Request end, unused.

        Returns:
            None.
        """
        try:
            slo = _slo_from(kwargs)
            await get_logger().log(slo, ok=True)
        except Exception as exc:                      # noqa: BLE001
            print(f'ERROR  success callback failed: {exc}')

    async def async_log_failure_event(self, kwargs: dict,
                                      response_obj: object,
                                      start_time: object,
                                      end_time: object) -> None:
        """
        Record a failed request.

        Args:
            kwargs (dict): LiteLLM call details, holding the standard
                logging object.
            response_obj (object): Raw provider response, unused.
            start_time (object): Request start, unused.
            end_time (object): Request end, unused.

        Returns:
            None.
        """
        try:
            slo = _slo_from(kwargs)
            await get_logger().log(slo, ok=False)
        except Exception as exc:                      # noqa: BLE001
            print(f'ERROR  failure callback failed: {exc}')


# The instance LiteLLM is configured with.
proxy_handler_instance = UsageCallback()

def get_logger() -> UsageLogger:
    """
    Return the process-wide logger, creating it on first call.

    Returns:
        UsageLogger: Shared logger instance.
    """
    global _LOGGER
    if _LOGGER is None:
        _LOGGER = UsageLogger()
    return _LOGGER

