"""
Compare local prices against the table shipped with LiteLLM.

The built-in LiteLLM table is community maintained. It may lag behind a
vendor price change, and it has been seen to get the ratio between input
and output wrong outright. A difference therefore proves nothing on its
own: it is a signal that the official page is worth a look.

The comparison is opt-in per tier, through a compare block:

    compare:
      mode: cost
      litellm_key: deepseek-chat
      warn_threshold: 0.05
      alarm_threshold: 0.5

Both thresholds are required. With only one, there is no way to tell a
small drift from a stale entry.

Only off-peak prices are compared, because LiteLLM has no idea which
hours this project treats as peak. A difference is reported as a note,
never as an error: the local entry may well be the correct one.
"""

# -*- coding: utf-8 -*-
# ==========================================================================
# Founded: 2026-09-21
# Modified: 2026-09-21
# @author yinlb, deepseek-flash
# ==========================================================================

from typing import Optional, Tuple

# Reference usage used for the comparison, in tokens. A single mid-sized
# request is enough to expose a price difference; the exact split does
# not matter as long as both sides price the same usage.
SAMPLE_CACHE_HIT = 10_000
SAMPLE_MISS = 2_000
SAMPLE_OUTPUT = 3_000

# Modes a tier may request.
VALID_MODES = ['none', 'cost']


def reference_usage() -> dict:
    """
    Return the fixed token counts the comparison is based on.

    Returns:
        dict: Keys in, cache_hit and out, in tokens.
    """
    return {
        'in': SAMPLE_CACHE_HIT + SAMPLE_MISS,
        'cache_hit': SAMPLE_CACHE_HIT,
        'out': SAMPLE_OUTPUT,
    }


def local_cost(tier: dict) -> Optional[float]:
    """
    Cost the reference usage with the prices in this project.

    Only flat or idle prices are used, so the result stays comparable
    with LiteLLM, which does not know the peak windows.

    Args:
        tier (dict): Tier block from pricing.yaml.

    Returns:
        Optional[float]: Cost in the tier's own currency, or None when
            the tier cannot be priced.
    """
    from src import pricing

    usage = reference_usage()
    cost = 0.0
    try:
        # 1. Cached input, priced at the idle band.
        cost += usage['cache_hit'] * pricing.pick_price(
            tier['cache_hit'], peak=False)

        # 2. Non-cached input.
        if 'cache_miss' in tier:
            cost += SAMPLE_MISS * pricing.pick_price(
                tier['cache_miss'], peak=False)
        else:
            cost += SAMPLE_MISS * pricing.pick_price(
                tier['input'], peak=False)

        # 3. Output.
        cost += usage['out'] * pricing.pick_price(
            tier['output'], peak=False)
    except (KeyError, ValueError, TypeError):
        return None
    return cost / 1_000_000


def upstream_cost(key: str, usage: dict) -> Optional[float]:
    """
    Cost the reference usage with the LiteLLM price table.

    The table comes from the installed package. When LiteLLM is absent,
    or the model is unknown to it, there is nothing to compare against
    and None is returned rather than an error.

    Args:
        key (str): Model name as LiteLLM knows it.
        usage (dict): Token counts to price.

    Returns:
        Optional[float]: Cost in USD, or None when unavailable.
    """
    try:
        from litellm import cost_per_token
    except ImportError:
        return None

    try:
        prompt_cost, completion_cost = cost_per_token(
            model=key,
            prompt_tokens=usage['in'],
            completion_tokens=usage['out'],
        )
    except Exception:                                 # noqa: BLE001
        return None
    if prompt_cost is None or completion_cost is None:
        return None
    return float(prompt_cost) + float(completion_cost)


def ratio(local: float, upstream: float) -> float:
    """
    Express the difference between two costs as a relative amount.

    Args:
        local (float): Cost from this project's table.
        upstream (float): Cost from the LiteLLM table.

    Returns:
        float: Relative difference, always non-negative.
    """
    if upstream == 0:
        return 0.0 if local == 0 else 1.0
    return abs(local - upstream) / abs(upstream)


def check_tier(name: str, tier: dict) -> Tuple[list, list]:
    """
    Compare one tier against the LiteLLM table.

    An error is raised only for a malformed compare block. A difference
    between prices is always a note, because the local entry may be the
    correct one and the LiteLLM table the stale one.

    Args:
        name (str): Tier name, used in the messages.
        tier (dict): Tier block from pricing.yaml.

    Returns:
        tuple: Two lists of English text, problems and notes.
    """
    problems = list()
    notes = list()

    block = tier.get('compare')
    if block is None:
        return problems, notes
    if not isinstance(block, dict):
        problems.append(f'{name}.compare: must be a mapping')
        return problems, notes

    mode = block.get('mode', 'none')
    if mode not in VALID_MODES:
        problems.append(f'{name}.compare.mode: must be one of '
                        f'{VALID_MODES}')
        return problems, notes
    if mode == 'none':
        return problems, notes

    # 1. The thresholds turn a number into an action, so both are
    #    needed; one alone cannot separate drift from decay.
    warn = block.get('warn_threshold')
    alarm = block.get('alarm_threshold')
    if warn is None or alarm is None:
        problems.append(f'{name}.compare: both warn_threshold and '
                        'alarm_threshold are required')
        return problems, notes
    if warn > alarm:
        problems.append(f'{name}.compare: warn_threshold must not exceed '
                        'alarm_threshold')
        return problems, notes

    key = block.get('litellm_key')
    if not key:
        problems.append(f'{name}.compare.litellm_key: required in cost '
                        'mode')
        return problems, notes

    # 2. Price both sides and measure the gap.
    ours = local_cost(tier)
    theirs = upstream_cost(key, reference_usage())
    if ours is None:
        notes.append(f'{name}: cannot be priced locally, comparison '
                     'skipped')
        return problems, notes
    if theirs is None:
        notes.append(f'{name}: LiteLLM has no usable price for {key}, '
                     'comparison skipped')
        return problems, notes

    gap = ratio(ours, theirs)
    percent = f'{gap * 100:.1f}%'

    # 3. Report the gap, without claiming which side is wrong.
    if gap > alarm:
        notes.append(f'{name}: cost differs from LiteLLM by {percent} '
                     f'({ours:.6f} local vs {theirs:.6f} USD), one side is'
                     ' likely out of date, check the official page')
    elif gap >= warn:
        notes.append(f'{name}: cost differs from LiteLLM by {percent}, '
                     'worth a look')
    return problems, notes


def run(doc: dict) -> Tuple[list, list]:
    """
    Compare every tier that asks for it.

    Args:
        doc (dict): Document returned by pricing.load_pricing.

    Returns:
        tuple: Two lists of English text, problems and notes.
    """
    problems = list()
    notes = list()
    for name, tier in doc['tiers'].items():
        if not isinstance(tier, dict):
            continue
        tier_problems, tier_notes = check_tier(name, tier)
        problems += tier_problems
        notes += tier_notes
    return problems, notes
