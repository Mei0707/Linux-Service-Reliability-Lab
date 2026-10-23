"""Build tables and charts from measured runs, never from target numbers."""

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path
from statistics import median


def summarize(runs):
    signatures = {
        (
            run.get("profile_sha256"),
            run.get("load_source_sha256"),
            run.get("users"),
            run.get("warmup_seconds"),
            run.get("measurement_target_seconds"),
        )
        for run in runs
        if "experiment" in run
    }
    if len(signatures) > 1:
        raise ValueError("Do not combine runs with different workload sources, users, or timing")
    groups = defaultdict(list)
    for run in runs:
        if "experiment" in run:
            groups[run["experiment"]].append(run)
    result = {}
    for name, cases in groups.items():
        configurations = {json.dumps(case.get("configuration"), sort_keys=True) for case in cases}
        if len(configurations) != 1:
            raise ValueError(f"Experiment {name} contains different configurations")
        read = [
            next((row for row in case["endpoints"] if row["name"] == "/links/[code]"), None)
            for case in cases
        ]

        def med(values):
            values = [value for value in values if value is not None]
            return median(values) if values else None

        connections = [
            sample["dependencies"].get("database_connections")
            for case in cases
            for sample in case.get("samples", [])
        ]
        sockets = [
            sample["established_client_connections"]
            for case in cases
            for sample in case.get("server_samples", [])
            if sample.get("established_client_connections") is not None
        ]
        result[name] = {
            "repetitions": len(cases),
            "users": min(case["users"] for case in cases),
            "rps": med([case["total"]["rps"] for case in cases]),
            "successful_rps": med(
                [case["total"]["rps"] * (1 - case["error_rate"]) for case in cases]
            ),
            "p50_ms": med([case["total"]["p50_ms"] for case in cases]),
            "p95_ms": med([case["total"]["p95_ms"] for case in cases]),
            "p99_ms": med([case["total"]["p99_ms"] for case in cases]),
            "read_p95_ms": med([row["p95_ms"] if row else None for row in read]),
            "error_rate": med([case["error_rate"] for case in cases]),
            "cache_hit_rate": med(
                [
                    case.get("cache_hit_rate") if case.get("schema_version", 1) >= 2 else None
                    for case in cases
                ]
            ),
            "database_connections_median": med(connections),
            "client_connections_min": min(sockets) if sockets else None,
            "client_connections_max": max(sockets) if sockets else None,
            "profile_hashes": sorted(set(case["profile_sha256"] for case in cases)),
        }
    return result


def build_report(directory):
    matrix = json.loads(
        (Path(__file__).resolve().parents[1] / "experiments/matrix.json").read_text()
    )
    order = {name: index for index, name in enumerate(matrix)}
    runs = []
    for path in directory.glob("*.json"):
        data = json.loads(path.read_text())
        if isinstance(data, dict) and "experiment" in data and "total" in data:
            runs.append(data)
    runs.sort(key=lambda run: (order.get(run["experiment"], len(order)), run.get("repeat", 0)))
    summaries = summarize(runs)
    if not summaries:
        raise ValueError("No measured benchmark runs found")
    (directory / "summary.json").write_text(json.dumps(summaries, indent=2))
    lines = [
        "# Measured benchmark report",
        "",
        "Medians of repeated runs; latency units are milliseconds. These are measured outcomes, not promises.",
        "",
        f"Warmup: {runs[0].get('warmup_seconds', 'unknown')} s after spawning; "
        f"measurement: {runs[0].get('measurement_target_seconds', round(runs[0]['measurement_seconds']))} s; "
        f"client: {runs[0].get('http_client', 'HttpUser')}. One repetition is a smoke check, not a repeated study.",
        "",
        "| Experiment | Repeats | Users | All RPS | Successful RPS | p50 | p95 | p99 | Read p95 | Errors | Cache hit | DB connections |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    def fmt(value, percent=False):
        return "n/a" if value is None else f"{value * 100:.2f}%" if percent else f"{value:.2f}"

    for name, summary in summaries.items():
        lines.append(
            f"| {name} | {summary['repetitions']} | {summary['users']} | "
            + " | ".join(
                [
                    fmt(summary[key])
                    for key in [
                        "rps",
                        "successful_rps",
                        "p50_ms",
                        "p95_ms",
                        "p99_ms",
                        "read_p95_ms",
                    ]
                ]
                + [
                    fmt(summary["error_rate"], True),
                    fmt(summary["cache_hit_rate"], True),
                    fmt(summary["database_connections_median"]),
                ]
            )
            + " |"
        )
    lines += [
        "",
        "## Attribution",
        "",
        "Each comparison below changes the versioned variable relative to its parent.",
        "",
    ]
    for name, summary in summaries.items():
        parent_name = matrix.get(name, {}).get("parent")
        parent = summaries.get(parent_name)
        if parent and parent["successful_rps"] and parent["p95_ms"]:
            lines.append(
                f"- {parent_name} → {name} ({matrix[name]['variable']}): "
                f"successful throughput {(summary['successful_rps'] / parent['successful_rps'] - 1) * 100:+.2f}%; "
                f"p95 reduction {(1 - summary['p95_ms'] / parent['p95_ms']) * 100:+.2f}%."
            )
    if "baseline" in summaries and "tuned" in summaries:
        baseline, tuned = summaries["baseline"], summaries["tuned"]
        if baseline["successful_rps"] and baseline["p95_ms"]:
            lines += [
                "",
                f"Overall baseline → tuned: successful throughput {(tuned['successful_rps'] / baseline['successful_rps'] - 1) * 100:+.2f}%; "
                f"p95 reduction {(1 - tuned['p95_ms'] / baseline['p95_ms']) * 100:+.2f}%. "
                "This combined comparison does not attribute causality to individual changes.",
            ]
    lines += [
        "",
        "## Interpretation",
        "",
        "- Virtual users, established TCP connections, and simultaneous in-flight requests are different quantities.",
        "- Nginx socket samples are saved in each run. A user count alone does not prove 1,000 concurrent connections.",
        "- Cache hit rate uses Prometheus counter deltas; scrape boundaries can differ by up to one scrape interval.",
        "- Cache ratios from schema v1 are withheld because those snapshots did not preserve individual counter identities. Schema v2 rejects counter resets and replica churn.",
        "- A short local smoke run cannot establish the proposal's target or a resume claim.",
        "- Use three 120-second measurements after 30-second warmups on a separate load host; inspect CPU, errors, socket samples, and per-endpoint results.",
        "- Missing dependency metrics are reported as n/a; they are never replaced with zero.",
        "- All RPS includes failed requests. Throughput comparisons and charts use successful RPS; inspect latency alongside errors.",
        "",
    ]
    for name, summary in summaries.items():
        lines.append(
            f"{name}: sampled established client connections min={summary['client_connections_min']}, "
            f"max={summary['client_connections_max']}."
        )
    lines += ["", "![Measured throughput and latency](benchmark.svg)", ""]
    (directory / "report.md").write_text("\n".join(lines))
    os.environ.setdefault("MPLCONFIGDIR", str(directory.resolve() / ".matplotlib"))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    names = list(summaries)
    fig, axes = plt.subplots(2, 1, figsize=(max(7, len(names) * 0.7), 7), layout="constrained")
    for ax, key, label in zip(
        axes, ["successful_rps", "read_p95_ms"], ["Successful requests / second", "Read p95 (ms)"]
    ):
        ax.bar(
            names,
            [
                float("nan") if summaries[name][key] is None else summaries[name][key]
                for name in names
            ],
        )
        ax.set_ylabel(label)
        ax.tick_params(axis="x", rotation=60)
        ax.grid(axis="y", alpha=0.2)
    axes[1].axhline(120, color="red", linestyle="--", label="120 ms target")
    axes[1].legend()
    fig.suptitle(f"Measured reliability lab results ({runs[0]['users']} virtual users)")
    fig.savefig(directory / "benchmark.svg")
    fig.savefig(directory / "benchmark.png", dpi=140)
    plt.close(fig)
    print(directory / "report.md")
    return summaries


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    build_report(parser.parse_args().directory)
