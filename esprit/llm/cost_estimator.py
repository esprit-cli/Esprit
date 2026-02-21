"""Pre-scan cost estimation based on scan mode, target count, and model pricing."""

from __future__ import annotations

import logging
from typing import Any

from esprit.llm.pricing import get_pricing_db

logger = logging.getLogger(__name__)

# Rough token budget estimates per scan mode per target (midpoint of observed ranges)
_MODE_TOKEN_ESTIMATES: dict[str, dict[str, Any]] = {
    "quick": {
        "input_tokens_per_target": 100_000,
        "output_tokens_per_target": 30_000,
        "cached_ratio": 0.3,
    },
    "standard": {
        "input_tokens_per_target": 400_000,
        "output_tokens_per_target": 120_000,
        "cached_ratio": 0.4,
    },
    "deep": {
        "input_tokens_per_target": 1_200_000,
        "output_tokens_per_target": 350_000,
        "cached_ratio": 0.45,
    },
}

_WHITEBOX_MULTIPLIER = 1.5
_ADDITIONAL_TARGET_FACTOR = 0.7

_MODE_DURATION_ESTIMATES_MIN: dict[str, float] = {
    "quick": 8.0,
    "standard": 30.0,
    "deep": 75.0,
}

_MODEL_DURATION_MULTIPLIERS: list[tuple[str, float]] = [
    ("haiku", 0.75),
    ("mini", 0.80),
    ("flash", 0.80),
    ("sonnet", 1.00),
    ("gpt-5", 1.10),
    ("o4", 1.20),
    ("o3", 1.20),
    ("opus", 1.35),
]


def estimate_scan_cost(
    model_name: str,
    scan_mode: str,
    target_count: int = 1,
    is_whitebox: bool = False,
) -> dict[str, Any]:
    """Estimate the cost of a scan. Returns cost range (low/mid/high) in USD."""
    mode_config = _MODE_TOKEN_ESTIMATES.get(scan_mode, _MODE_TOKEN_ESTIMATES["deep"])

    base_input = mode_config["input_tokens_per_target"]
    base_output = mode_config["output_tokens_per_target"]
    cached_ratio = mode_config["cached_ratio"]

    if target_count <= 1:
        total_input = base_input
        total_output = base_output
    else:
        total_input = base_input + int(base_input * _ADDITIONAL_TARGET_FACTOR * (target_count - 1))
        total_output = base_output + int(base_output * _ADDITIONAL_TARGET_FACTOR * (target_count - 1))

    if is_whitebox:
        total_input = int(total_input * _WHITEBOX_MULTIPLIER)
        total_output = int(total_output * _WHITEBOX_MULTIPLIER)

    cached_tokens = int(total_input * cached_ratio)

    db = get_pricing_db()
    mid_cost = db.get_cost(model_name, total_input, total_output, cached_tokens)

    return {
        "estimated_cost_low": round(mid_cost * 0.5, 4),
        "estimated_cost_mid": round(mid_cost, 4),
        "estimated_cost_high": round(mid_cost * 2.0, 4),
        "model": model_name,
        "scan_mode": scan_mode,
    }


def estimate_scan_duration_minutes(
    model_name: str,
    scan_mode: str,
    target_count: int = 1,
    is_whitebox: bool = False,
) -> dict[str, float]:
    """Estimate scan duration range (minutes) for model/mode/target profile."""
    base_mid = _MODE_DURATION_ESTIMATES_MIN.get(scan_mode, _MODE_DURATION_ESTIMATES_MIN["deep"])

    if target_count > 1:
        base_mid = base_mid + (base_mid * _ADDITIONAL_TARGET_FACTOR * (target_count - 1))

    if is_whitebox:
        base_mid *= _WHITEBOX_MULTIPLIER

    model_lower = (model_name or "").lower()
    model_mult = 1.0
    for token, mult in _MODEL_DURATION_MULTIPLIERS:
        if token in model_lower:
            model_mult = mult
            break

    mid = base_mid * model_mult
    return {
        "estimated_time_low_min": round(max(1.0, mid * 0.6), 1),
        "estimated_time_mid_min": round(max(1.0, mid), 1),
        "estimated_time_high_min": round(max(1.0, mid * 1.8), 1),
    }


def estimate_scan_profile(
    model_name: str,
    scan_mode: str,
    target_count: int = 1,
    is_whitebox: bool = False,
) -> dict[str, Any]:
    """Estimate both cost and runtime for a scan profile."""
    result: dict[str, Any] = {}
    result.update(
        estimate_scan_cost(
            model_name=model_name,
            scan_mode=scan_mode,
            target_count=target_count,
            is_whitebox=is_whitebox,
        )
    )
    result.update(
        estimate_scan_duration_minutes(
            model_name=model_name,
            scan_mode=scan_mode,
            target_count=target_count,
            is_whitebox=is_whitebox,
        )
    )
    return result
