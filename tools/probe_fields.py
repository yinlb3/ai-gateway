"""
Dump what a live LiteLLM instance actually sends to a callback.

The design document leaves three questions open, and none of them can be
answered from documentation alone:

    - Where does hidden_params.usage_object really sit, and how deep?
    - Which key names does DeepSeek use for cached input?
    - What exactly does the model field contain, so the price table can
      be keyed correctly?

This script answers them by capturing one real request. Run it once,
read the dump, then point the gateway back at the real callback.

Usage:

    python tools/probe_fields.py <dump.json>

To capture a dump, point LiteLLM at this file instead of the real one:

    litellm_settings:
      callbacks: tools.probe_fields.probe_handler_instance

Then send one request and read logs/probe/.
"""

# -*- coding: utf-8 -*-
# ==========================================================================
# Founded: 2026-09-21
# Modified: 2026-09-21
# @author yinlb, deepseek-flash
# ==========================================================================

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import arrow

# Dumps land beside the project rather than in the raw log directory, so
# a probe can never be mistaken for a real record.
BASE_DIR = Path(__file__).resolve().parent.parent
PROBE_DIR = Path(os.environ.get('AI_GATEWAY_PROBE_DIR')
                 or (BASE_DIR / 'logs' / 'probe'))

# Candidate paths the design document mentions, checked in order.
PATHS_TO_REPORT = [
    ('model', ['model']),
    ('id', ['id']),
    ('prompt_tokens', ['prompt_tokens']),
    ('completion_tokens', ['completion_tokens']),
    ('response_cost', ['response_cost']),
    ('model_id', ['model_id']),
    ('api_base', ['api_base']),
    ('error_str', ['error_str']),
    ('hidden_params', ['hidden_params']),
    ('metadata', ['metadata']),
    ('hidden.usage_object', ['hidden_params', 'usage_object']),
    ('meta.usage_object', ['metadata', 'usage_object']),
    ('hidden.model_id', ['hidden_params', 'model_id']),
    ('hidden.api_base', ['hidden_params', 'api_base']),
    ('meta.key_alias', ['metadata', 'user_api_key_alias']),
    ('meta.key_hash', ['metadata', 'user_api_key_hash']),
    ('meta.spend_logs', ['metadata', 'spend_logs_metadata']),
]


def deep_find(source: dict, keys: list) -> object:
    """
    Walk a nested mapping, returning None when any step is missing.

    Args:
        source (dict): Mapping to walk.
        keys (list): Key path to follow.

    Returns:
        object: Value at the end of the path, or None.
    """
    node = source
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def summary(slo: dict) -> dict:
    """
    Report which of the documented paths are present, and their values.

    A missing path is itself the answer to an open question, so it is
    recorded as absent rather than skipped.

    Args:
        slo (dict): The standard logging object from LiteLLM.

    Returns:
        dict: One entry per documented path, plus the usage key names.
    """
    found = dict()
    for label, keys in PATHS_TO_REPORT:
        node = deep_find(slo, keys)
        if isinstance(node, dict):
            found[label] = {'present': True, 'keys': sorted(node.keys())}
        elif node is None:
            found[label] = {'present': False}
        else:
            found[label] = {'present': True, 'value': node}

    # The cache counters are the whole point of the probe: the report
    # lists every key name the provider actually used.
    usage = deep_find(slo, ['hidden_params', 'usage_object'])
    if not isinstance(usage, dict):
        usage = deep_find(slo, ['metadata', 'usage_object'])
    found['usage_key_names'] = sorted(usage.keys()) \
        if isinstance(usage, dict) else []
    return _finish_report(found, usage)


def _finish_report(found: dict, usage: object) -> dict:
    """
    Add the prompt detail keys and return the finished report.

    Kept separate so that the name report inside summary cannot shadow
    this module's report function.

    Args:
        found (dict): Findings collected so far.
        usage (object): The usage object, if one was located.

    Returns:
        dict: The finished findings.
    """
    details = usage.get('prompt_tokens_details') if \
        isinstance(usage, dict) else None
    found['prompt_details_keys'] = sorted(details.keys()) \
        if isinstance(details, dict) else []
    return found



def write_dump(slo: dict, kwargs: dict) -> Path:
    """
    Save the raw object and the path report to disk.

    Args:
        slo (dict): The standard logging object from LiteLLM.
        kwargs (dict): The full callback arguments, for the key list.

    Returns:
        Path: File holding the dump.
    """
    os.makedirs(PROBE_DIR, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    path = PROBE_DIR / f'probe-{stamp}.json'

    payload = {
        'captured_at': datetime.now().isoformat(timespec='seconds'),
        'summary': summary(slo),
        'callback_keys': sorted(kwargs.keys()) if isinstance(kwargs, dict)
        else [],
        'raw_slo': slo,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False,
                               default=str), encoding='utf-8')
    return path


def report(slo: dict) -> None:
    """
    Print the findings in a form that is easy to read at a glance.

    Args:
        slo (dict): The standard logging object from LiteLLM.

    Returns:
        None.
    """
    print('')
    print('=== Probe findings ===')
    findings = summary(slo)
    for label, _ in PATHS_TO_REPORT:
        entry = findings.get(label, {})
        if entry.get('present'):
            detail = entry.get('keys') or entry.get('value')
            print(f'PRESENT  {label:<22} {detail}')
        else:
            print(f'MISSING  {label}')

    print('')
    print(f'usage keys       {findings.get("usage_key_names")}')
    print(f'prompt details   {findings.get("prompt_details_keys")}')
    print('')
    print('Check the usage keys against src/record.py CACHE_HIT_KEYS.')
    print('If a name is absent there, add it before relying on it.')


try:
    from litellm.integrations.custom_logger import CustomLogger as _Base
except ImportError:                                   # pragma: no cover
    _Base = object


class ProbeCallback(_Base):
    """
    Capture one real payload without writing a raw record.

    The real callback is left out of the gateway configuration while
    probing, so nothing lands in the accounting logs.
    """

    async def async_log_success_event(self, kwargs: dict,
                                      response_obj: object,
                                      start_time: object,
                                      end_time: object) -> None:
        """
        Capture a successful request.

        Args:
            kwargs (dict): LiteLLM call details.
            response_obj (object): Raw provider response, unused.
            start_time (object): Request start, unused.
            end_time (object): Request end, unused.

        Returns:
            None.
        """
        self._capture(kwargs)

    async def async_log_failure_event(self, kwargs: dict,
                                      response_obj: object,
                                      start_time: object,
                                      end_time: object) -> None:
        """
        Capture a failed request.

        Args:
            kwargs (dict): LiteLLM call details.
            response_obj (object): Raw provider response, unused.
            start_time (object): Request start, unused.
            end_time (object): Request end, unused.

        Returns:
            None.
        """
        self._capture(kwargs)

    def _capture(self, kwargs: dict) -> None:
        """
        Write the dump and print a short report.

        Args:
            kwargs (dict): LiteLLM call details.

        Returns:
            None.
        """
        try:
            slo = kwargs.get('standard_logging_object') if \
                isinstance(kwargs, dict) else None
            if not isinstance(slo, dict):
                print('probe: no standard_logging_object in this event')
                return
            path = write_dump(slo, kwargs)
            print(f'probe: dumped {path}')
            report(slo)
        except Exception as exc:                      # noqa: BLE001
            print(f'probe: capture failed: {exc}')


# The instance the gateway is pointed at while probing.
probe_handler_instance = ProbeCallback()


def main(argv: list) -> int:
    """
    Inspect a saved dump, or explain how to capture one.

    Args:
        argv (list): Command line arguments after the script name.

    Returns:
        int: 0 on success, 1 otherwise.
    """
    if not argv:
        print('No dump given.')
        print('')
        print('To capture one, point LiteLLM at this file:')
        print('  litellm_settings:')
        print('    callbacks: tools.probe_fields.probe_handler_instance')
        print('')
        print('Then send one request and read logs/probe/.')
        print('')
        print('To inspect an existing dump:')
        print('  python tools/probe_fields.py <dump.json>')
        return 1

    path = Path(argv[0])
    if not path.is_file():
        print(f'ERROR: file not found: {path}')
        return 1

    # A dump may have been saved by an editor that adds a byte order
    # mark, so the text is read with utf-8-sig and any stray mark is
    # tolerated rather than reported as a broken file.
    text = path.read_text(encoding='utf-8-sig')
    payload = json.loads(text)
    slo = payload.get('raw_slo') or {}
    report(slo)
    return 0


if __name__ == '__main__':
    print('Program probe_fields.py started')
    total_start = arrow.now()

    code = main(sys.argv[1:])

    total_elapsed = (arrow.now() - total_start).total_seconds()
    print(f'Program probe_fields.py finished, total time '
          f'{total_elapsed:.2f}s')
    sys.exit(code)
