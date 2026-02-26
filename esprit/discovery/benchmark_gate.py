"""Benchmark gate utilities for discovery CI checks.

This module evaluates discovery metrics against explicit thresholds and
returns a non-zero status when thresholds are not met.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BenchmarkThresholds:
    min_hypothesis_conversion_rate: float = 0.15
    min_novelty_ratio: float = 0.60
    min_validated_finding_rate: float = 0.10
    min_validated_hypotheses: int = 1


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def evaluate_metrics(
    metrics: dict[str, Any],
    thresholds: BenchmarkThresholds,
) -> list[str]:
    failures: list[str] = []

    conversion = _as_float(metrics.get("hypothesis_conversion_rate"))
    novelty = _as_float(metrics.get("novelty_ratio"))
    validated_rate = _as_float(metrics.get("validated_finding_rate"))
    validated = _as_int(metrics.get("validated_hypotheses"))

    if conversion < thresholds.min_hypothesis_conversion_rate:
        failures.append(
            "hypothesis_conversion_rate "
            f"{conversion:.3f} < {thresholds.min_hypothesis_conversion_rate:.3f}"
        )

    if novelty < thresholds.min_novelty_ratio:
        failures.append(
            f"novelty_ratio {novelty:.3f} < {thresholds.min_novelty_ratio:.3f}"
        )

    if validated_rate < thresholds.min_validated_finding_rate:
        failures.append(
            "validated_finding_rate "
            f"{validated_rate:.3f} < {thresholds.min_validated_finding_rate:.3f}"
        )

    if validated < thresholds.min_validated_hypotheses:
        failures.append(
            f"validated_hypotheses {validated} < {thresholds.min_validated_hypotheses}"
        )

    return failures


def load_metrics(path: str | Path) -> dict[str, Any]:
    metrics_path = Path(path).expanduser().resolve()
    data = json.loads(metrics_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Metrics file must contain an object: {metrics_path}")
    return data


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate discovery benchmark metrics against CI thresholds."
    )
    parser.add_argument("--metrics", required=True, help="Path to discovery_metrics.json")
    parser.add_argument("--min-hypothesis-conversion-rate", type=float, default=0.15)
    parser.add_argument("--min-novelty-ratio", type=float, default=0.60)
    parser.add_argument("--min-validated-finding-rate", type=float, default=0.10)
    parser.add_argument("--min-validated-hypotheses", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    thresholds = BenchmarkThresholds(
        min_hypothesis_conversion_rate=args.min_hypothesis_conversion_rate,
        min_novelty_ratio=args.min_novelty_ratio,
        min_validated_finding_rate=args.min_validated_finding_rate,
        min_validated_hypotheses=args.min_validated_hypotheses,
    )

    metrics = load_metrics(args.metrics)
    failures = evaluate_metrics(metrics, thresholds)
    if failures:
        logger.error("Discovery benchmark gate failed:")
        for failure in failures:
            logger.error(" - %s", failure)
        return 1

    logger.info("Discovery benchmark gate passed.")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    raise SystemExit(main())
