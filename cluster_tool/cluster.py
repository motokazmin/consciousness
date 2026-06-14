"""
Offline HDBSCAN-кластеризация накопленных HRV-данных.

Отдельный инструмент — не часть веб-приложения hrv_web.
Читает точки RMSSD из SQLite-базы HRV Monitor.

Usage:
    python -m cluster_tool --db hrv_data.sqlite
    python -m cluster_tool --db /path/to/hrv_data.sqlite --include-mock
    python -m cluster_tool --db hrv_data.sqlite --min-cluster-size 10
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
from sklearn.preprocessing import StandardScaler

from cluster_tool.markers import marker_for_tag

try:
    import hdbscan
except ImportError as e:
    raise SystemExit(
        "Установите зависимости cluster_tool: pip install -r cluster_tool/requirements.txt"
    ) from e


def load_points(db_path: Path, include_mock: bool) -> list[dict]:
    if not db_path.is_file():
        raise SystemExit(f"DB not found: {db_path}")

    conn = sqlite3.connect(db_path)
    source_filter = "" if include_mock else "AND s.source NOT LIKE 'mock%'"

    rows = conn.execute(f"""
        SELECT h.ts,
               h.rmssd,
               s.tag,
               CAST(strftime('%H', datetime(h.ts, 'unixepoch', 'localtime')) AS INTEGER) AS hour
        FROM hrv_points h
        JOIN sessions s ON h.session_id = s.id
        WHERE h.rmssd > 0
          {source_filter}
        ORDER BY h.ts
    """).fetchall()
    conn.close()

    return [{"ts": r[0], "rmssd": r[1], "tag": r[2], "hour": r[3]} for r in rows]


def run_clustering(points: list[dict], min_cluster_size: int):
    rmssd = np.array([p["rmssd"] for p in points]).reshape(-1, 1)
    X_scaled = StandardScaler().fit_transform(rmssd)

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=5,
        metric="euclidean",
    )
    labels = clusterer.fit_predict(X_scaled)
    probs = clusterer.probabilities_
    return labels, probs, rmssd


def print_summary(points, labels):
    nonempty = [c for c in set(labels) if c != -1]
    n_clusters = len(nonempty)
    print(f"\n{'─'*50}")
    print(f"Points total : {len(points)}")
    print(f"Clusters     : {n_clusters}  (noise: {sum(l == -1 for l in labels)})")
    print(f"{'─'*50}")

    for cid in sorted(set(labels)):
        mask = labels == cid
        pts = [p for p, m in zip(points, mask) if m]
        if not pts:
            continue

        rmssd_vals = [p["rmssd"] for p in pts]
        tags: dict[str, int] = {}
        for p in pts:
            tags[p["tag"]] = tags.get(p["tag"], 0) + 1
        dominant = max(tags, key=tags.get)
        label = "noise" if cid == -1 else f"cluster {cid}"

        print(f"\n{label}  ({len(pts)} points)")
        print(f"  RMSSD  mean={np.mean(rmssd_vals):.1f}  "
              f"min={np.min(rmssd_vals):.1f}  max={np.max(rmssd_vals):.1f} ms")
        print(f"  tags   {dict(sorted(tags.items(), key=lambda x: -x[1]))}")
        print(f"  dominant tag: {dominant}")

    print(f"{'─'*50}\n")


def plot_clusters(points, labels, probs):
    fig, (ax_main, ax_hour) = plt.subplots(1, 2, figsize=(14, 6))
    fig.patch.set_facecolor("#111")
    fig.suptitle("HRV Clustering", color="#ccc", fontsize=12)

    nonempty_c = sorted(c for c in set(labels) if c != -1)
    n_clusters = len(nonempty_c)
    cmap = plt.colormaps["tab10"].resampled(max(n_clusters, 1))

    ax_main.set_facecolor("#1a1a1a")
    ax_main.tick_params(colors="#777")
    ax_main.spines[:].set_color("#2a2a2a")
    ax_main.set_xlabel("Hour of day", color="#999")
    ax_main.set_ylabel("RMSSD (ms)", color="#999")
    ax_main.set_title("Clusters", color="#bbb", fontsize=10)

    for p, lbl, prob in zip(points, labels, probs):
        color = "#333" if lbl == -1 else cmap(lbl)
        alpha = max(0.15, float(prob)) if lbl != -1 else 0.2
        ax_main.scatter(p["hour"], p["rmssd"], c=[color], alpha=alpha, s=18, linewidths=0)

    legend_handles = []
    seen_tags: set[str] = set()
    for p, lbl in zip(points, labels):
        tag = p["tag"]
        if lbl == -1 or not tag or tag in seen_tags:
            continue
        seen_tags.add(tag)
        marker, size = marker_for_tag(tag)
        tagged = [pt for pt, l in zip(points, labels) if pt["tag"] == tag and l != -1]
        xs = [pt["hour"] for pt in tagged]
        ys = [pt["rmssd"] for pt in tagged]
        cs = [cmap(l) for pt, l in zip(points, labels) if pt["tag"] == tag and l != -1]
        ax_main.scatter(xs, ys, c=cs, marker=marker, s=size,
                        edgecolors="white", linewidths=0.5, zorder=5)
        legend_handles.append(mpatches.Patch(label=tag, facecolor="#888"))

    for cid in nonempty_c:
        legend_handles.append(
            mpatches.Patch(color=cmap(cid), label=f"cluster {cid}")
        )
    legend_handles.append(mpatches.Patch(color="#333", label="noise"))
    ax_main.legend(handles=legend_handles, fontsize=8,
                   facecolor="#222", labelcolor="#bbb", framealpha=0.8)

    ax_hour.set_facecolor("#1a1a1a")
    ax_hour.tick_params(colors="#777")
    ax_hour.spines[:].set_color("#2a2a2a")
    ax_hour.set_xlabel("Cluster", color="#999")
    ax_hour.set_ylabel("RMSSD (ms)", color="#999")
    ax_hour.set_title("RMSSD per cluster", color="#bbb", fontsize=10)

    cluster_ids = sorted(c for c in set(labels) if c != -1)
    data_by_cluster = [
        [p["rmssd"] for p, lbl in zip(points, labels) if lbl == cid]
        for cid in cluster_ids
    ]
    if data_by_cluster:
        bp = ax_hour.boxplot(data_by_cluster, patch_artist=True, notch=False)
        for patch, cid in zip(bp["boxes"], cluster_ids):
            patch.set_facecolor(cmap(cid))
            patch.set_alpha(0.7)
        ax_hour.set_xticks(range(1, len(cluster_ids) + 1))
        ax_hour.set_xticklabels([f"c{c}" for c in cluster_ids], color="#999")

    plt.tight_layout()
    plt.show()


def main():
    parser = argparse.ArgumentParser(description="HRV HDBSCAN clustering (offline)")
    parser.add_argument(
        "--db",
        type=Path,
        required=True,
        metavar="PATH",
        help="Путь к SQLite-базе HRV Monitor (sessions, hrv_points)",
    )
    parser.add_argument("--include-mock", action="store_true")
    parser.add_argument("--min-cluster-size", type=int, default=15)
    args = parser.parse_args()

    db_path = args.db.expanduser().resolve()
    print(f"Database: {db_path}")

    points = load_points(db_path, args.include_mock)
    if len(points) < args.min_cluster_size * 2:
        raise SystemExit(
            f"Not enough data: {len(points)} points. "
            f"Need at least {args.min_cluster_size * 2}. Run more sessions."
        )

    print(f"Loaded {len(points)} points")
    labels, probs, _X = run_clustering(points, args.min_cluster_size)
    print_summary(points, labels)
    plot_clusters(points, labels, probs)


if __name__ == "__main__":
    main()
