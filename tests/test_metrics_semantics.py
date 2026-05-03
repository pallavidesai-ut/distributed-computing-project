from __future__ import annotations

from analyze_run import analyze_decision_quality
from clocksim import MetricsCollector


def test_missed_conflict_rate_counts_only_pairwise_clock_collapses() -> None:
    metrics = MetricsCollector()
    metrics.decisions.extend(
        [
            {
                "true_relation": "concurrent",
                "clock_relation": "concurrent",
                "final_action": "drop_incoming",
            },
            {
                "true_relation": "concurrent",
                "clock_relation": "dominated",
                "final_action": "drop_incoming",
            },
        ]
    )

    summary = metrics.summary(sim_time=1.0)

    assert summary["missed_conflict_rate"] == 0.5


def test_analyze_run_decision_quality_matches_metrics_summary(tmp_path) -> None:
    decisions = [
        {
            "t": "0.0",
            "true_relation": "concurrent",
            "clock_relation": "concurrent",
            "final_action": "drop_incoming",
        },
        {
            "t": "1.0",
            "true_relation": "concurrent",
            "clock_relation": "dominated",
            "final_action": "drop_incoming",
        },
        {
            "t": "2.0",
            "true_relation": "dominates",
            "clock_relation": "concurrent",
            "final_action": "keep_both",
        },
    ]
    metrics = MetricsCollector()
    metrics.decisions.extend(decisions)

    analyzed = analyze_decision_quality(decisions, window=10.0, output_dir=tmp_path)
    summary = metrics.summary(sim_time=10.0)

    assert analyzed["missed_conflict_rate"] == summary["missed_conflict_rate"]
    assert analyzed["stale_sibling_rate"] == summary["stale_sibling_rate"]
