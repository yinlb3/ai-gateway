"""
Extract one raw record from a LiteLLM logging object.

This module answers a single question: given the object LiteLLM hands to
a callback, what does one raw line of our own log look like?

The difficulty is that the provider hides cache and reasoning counters in
different places with different names, so every lookup here tries several
paths and key names. A missing value is never an error: it may simply
mean the model does not report that counter.

Nothing in this module writes files or reads configuration, so it is
safe to import anywhere.
"""

# -*- coding: utf-8 -*-
# ==========================================================================
# Founded: 2026-09-21
# Modified: 2026-09-21
# @author yinlb, deepseek-flash
# ==========================================================================

from datetime import datetime
import json
import re
from typing import Dict, List, Optional

# Candidate key names for cached input, in priority order.
CACHE_HIT_KEYS = [
    'cache_read_input_tokens',
    'cache_hit_tokens',
    'prompt_cache_hit_tokens',
]

# Candidate key names for cache writes, in priority order.
CACHE_WRITE_KEYS = [
    'cache_creation_input_tokens',
    'cache_write_tokens',
    'cache_creation_tokens',
]

# Candidate key names for reasoning tokens.
REASONING_KEYS = [
    'reasoning_tokens',
]


def deep_get(source: object, keys: List[str],
             default: Optional[object] = None) -> Optional[object]:
    """
    Read the first present key from a mapping.

    Providers name the same counter differently, so callers pass every
    candidate name and take whichever exists. A non-mapping source is
    treated as absent rather than raising.

    Args:
        source (object): Value to read from, expected to be a mapping.
        keys (List[str]): Candidate key names, in priority order.
        default (Optional[object]): Value returned when none is present.
            Defaults to None.

    Returns:
        Optional[object]: The first value found, or default.
    """
    if not isinstance(source, dict):
        return default
    for key in keys:
        value = source.get(key)
        if value is not None:
            return value
    return default


def extract_usage(slo: dict) -> dict:
    """
    Extract cache and reasoning counters from a logging object.

    LiteLLM buries these in hidden_params.usage_object, with
    metadata.usage_object as a fallback, and the key names differ per
    provider. A missing counter is reported as None here; the caller
    decides whether that means zero or means the field is omitted.

    Args:
        slo (dict): The standard logging object from LiteLLM.

    Returns:
        dict: Keys cache_hit, cache_write and reasoning_tokens, each
            either an int or None.
    """
    hidden = deep_get(slo, ['hidden_params'], default={})
    meta = deep_get(slo, ['metadata'], default={})

    # 1. Locate the usage object, whichever place the provider used.
    usage = deep_get(hidden, ['usage_object'])
    if usage is None:
        usage = deep_get(meta, ['usage_object'])

    # 2. Counters may also sit in the per-direction detail blocks.
    prompt_block = deep_get(usage, ['prompt_tokens_details'], default={})
    answer_block = deep_get(usage, ['completion_tokens_details'],
                            default={})

    # 3. Cached input: try the usage object, then the prompt details.
    cache_hit = deep_get(usage, CACHE_HIT_KEYS)
    if cache_hit is None:
        cache_hit = deep_get(prompt_block,
                             ['cached_tokens', 'cache_hit_tokens'])

    # 4. Cache writes exist only for providers that bill them.
    cache_write = deep_get(usage, CACHE_WRITE_KEYS)

    # 5. Reasoning tokens sit in the usage object or the answer block.
    reasoning = deep_get(usage, REASONING_KEYS)
    if reasoning is None:
        reasoning = deep_get(answer_block, REASONING_KEYS)

    return {
        'cache_hit': _as_int(cache_hit),
        'cache_write': _as_int(cache_write),
        'reasoning_tokens': _as_int(reasoning),
    }


def _as_int(value: object) -> Optional[int]:
    """
    Convert a counter to int, or None when it is absent or unusable.

    Args:
        value (object): Raw counter value.

    Returns:
        Optional[int]: Integer value, or None when it cannot be read.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None



def _split_alias(alias: Optional[str]) -> Optional[str]:
    """
    Read the project name out of a virtual key alias.

    The alias carries two facts, tool and project, joined by a double
    hyphen: codex--wind-forecast. This is the main path for tools that
    cannot send a custom header.

    Args:
        alias (Optional[str]): Value of metadata.user_api_key_alias.

    Returns:
        Optional[str]: The project part, or None when the alias is
            absent or does not follow the convention.
    """
    if not alias or '--' not in alias:
        return None
    parts = alias.split('--', 1)
    project = parts[1].strip()
    return project or None


def resolve_user(slo: dict) -> Optional[str]:
    """
    Work out which project a request belongs to.

    Tried in order: the spend logs header, then the virtual key alias.
    A request with no project is still forwarded and still recorded; only
    the attribution is missing.

    Args:
        slo (dict): The standard logging object from LiteLLM.

    Returns:
        Optional[str]: Project name, or None when nothing identified it.
    """
    meta = deep_get(slo, ['metadata'], default={})

    # 1. Preferred: the project stated explicitly in a request header.
    spend = deep_get(meta, ['spend_logs_metadata'])
    if isinstance(spend, str):
        spend = parse_json(spend)
    project = deep_get(spend, ['project'])
    if project:
        return str(project)

    # 2. Fallback: split the virtual key alias.
    alias = deep_get(meta, ['user_api_key_alias'])
    return _split_alias(alias)


def parse_json(text: str) -> object:
    """
    Decode a JSON string, returning an empty mapping on failure.

    The spend logs header carries its value as a JSON string, and a
    malformed one must not raise here.

    Args:
        text (str): Text that should contain JSON.

    Returns:
        object: Decoded value, or an empty dict when undecodable.
    """
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return {}


def redact(text: str) -> str:
    """
    Remove anything that looks like a credential from a string.

    Args:
        text (str): Text that may embed a key.

    Returns:
        str: Text with key-shaped runs replaced by a placeholder.
    """
    # 1. Bearer tokens.
    text = re.sub(r'(?i)bearer\s+[A-Za-z0-9._\-]+',
                  'Bearer <redacted>', text)

    # 2. sk- style secrets.
    text = re.sub(r'sk-[A-Za-z0-9._\-]{8,}', '<redacted>', text)

    # 3. key: value pairs that name an api key.
    text = re.sub(
        r'(?i)(api[_-]?key"?\s*[:=]\s*"?)[A-Za-z0-9._\-]{8,}',
        r'\1<redacted>', text)
    return text


def clean_error(raw: object, limit: int = 200) -> Optional[str]:
    """
    Render an error summary, shortened and free of credentials.

    A failure record must not carry an API key into the log, and a long
    traceback would drown the useful part.

    Args:
        raw (object): Error text, or a structured error mapping.
        limit (int): Maximum length kept. Defaults to 200.

    Returns:
        Optional[str]: Short error text, or None when there is none.
    """
    if not raw:
        return None
    if isinstance(raw, dict):
        kind = raw.get('error_class') or ''
        message = raw.get('error_message') or raw.get('message') or ''
        text = f'{kind}: {message}'.strip(': ')
    else:
        text = str(raw)
    text = redact(text).replace('\n', ' ').strip()
    return text[:limit] or None


def build_record(slo: dict, ok: bool,
                 moment: Optional[datetime] = None) -> dict:
    """
    Assemble one raw record from a logging object.

    The record holds only facts: when, which tool, which project, which
    model and how many tokens. Cost is deliberately absent, because it is
    derived later from the price table.

    Args:
        slo (dict): The standard logging object from LiteLLM. May be
            empty when the payload could not be built.
        ok (bool): Whether the request succeeded.
        moment (Optional[datetime]): Timestamp to record. Defaults to the
            current local time.

    Returns:
        dict: One raw record, ready to be written as a JSON line.
    """
    if not isinstance(slo, dict):
        slo = {}
    if moment is None:
        moment = datetime.now()

    meta = deep_get(slo, ['metadata'], default={})
    usage = extract_usage(slo)
    record = dict()
    partial = False

    # 1. Facts that are always available, even for a failed request.
    record['ts'] = moment.isoformat(timespec='seconds')
    record['ok'] = bool(ok)
    record['key'] = str(deep_get(meta, ['user_api_key_alias'],
                                 default='unknown'))
    record['model'] = str(deep_get(slo, ['model'], default='unknown'))

    # 2. Token counts come from the top level, which LiteLLM normalises.
    total_in = _as_int(deep_get(slo, ['prompt_tokens']))
    total_out = _as_int(deep_get(slo, ['completion_tokens']))
    if total_in is None or total_out is None:
        partial = True
    record['in'] = total_in or 0
    record['out'] = total_out or 0

    # 3. Cached input is required; a model without the concept reports 0.
    if usage['cache_hit'] is None:
        record['cache_hit'] = 0
    else:
        record['cache_hit'] = usage['cache_hit']

    # 4. Optional counters are written only when the provider has them.
    if usage['cache_write'] is not None:
        record['cache_write'] = usage['cache_write']
    if usage['reasoning_tokens'] is not None:
        record['reasoning_tokens'] = usage['reasoning_tokens']

    # 5. Identifiers used for merging and for stable attribution.
    req_id = deep_get(slo, ['id'])
    if req_id:
        record['req_id'] = str(req_id)
    key_hash = deep_get(meta, ['user_api_key_hash'])
    if key_hash:
        record['key_hash8'] = str(key_hash)[:8]

    # 6. The project a request belongs to, or nothing when unknown.
    user = resolve_user(slo)
    if user:
        record['user'] = user
    else:
        record['user'] = None
        partial = True
        record.setdefault('err', 'missing_user')

    # 7. Model deployment details, for troubleshooting only. LiteLLM
    #    puts them at the top level, with hidden_params as a fallback.
    hidden = deep_get(slo, ['hidden_params'], default={})
    for field in ('model_id', 'api_base'):
        value = deep_get(slo, [field])
        if value is None:
            value = deep_get(hidden, [field])
        if value:
            record[field] = str(value)

    # 8. The upstream cost, kept only as a staleness hint.
    upstream = deep_get(slo, ['response_cost'])
    if upstream:
        record['cost_upstream'] = upstream

    # 9. Failure details, and the completeness marker.
    if not ok:
        record['err'] = clean_error(deep_get(slo, ['error_str'])) or \
            record.get('err') or 'request_failed'
    if not deep_get(slo, ['hidden_params']):
        partial = True
    if partial:
        record['partial'] = True
    return record

