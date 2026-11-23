#!/usr/bin/env python3
"""Plot an existing Twinkly snapshot without contacting any devices.

Requires matplotlib: pip install matplotlib
"""
import argparse
import json
from pathlib import Path
import sys


def load_snapshot(snapshot):
    root = Path(snapshot)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    rows = []
    for item in manifest["controllers"]:
        ip = item["ip"]
        path = root / "controllers" / ip.replace(".", "_") / "responses" / "led_layout_full.json"
        if not path.is_file():
            continue
        result = json.loads(path.read_text(encoding="utf-8"))
        data = result.get("data", {})
        coordinates = data.get("coordinates", []) if isinstance(data, dict) else []
        coords = [(float(p["x"]), float(p["y"])) for p in coordinates
                  if isinstance(p, dict) and isinstance(p.get("x"), (int, float))
                  and isinstance(p.get("y"), (int, float))]
        if not coords:
            continue
        group = None
        group_path = path.with_name("led_movie_config.json")
        if group_path.is_file():
            raw = json.loads(group_path.read_text(encoding="utf-8"))
            sync = (raw.get("data") or {}).get("sync") or {}
            if isinstance(sync, dict):
                group = sync.get("master_id") or sync.get("slave_id")
        rows.append({"ip": ip, "name": item.get("name", ip),
                     "coordinates": coords, "group": group})
    return rows


def select_rows(rows, names, group):
    if names:
        requested = {n.casefold() for n in names}
        selected = [row for row in rows if row["name"].casefold() in requested or row["ip"] in names]
        missing = requested - {row["name"].casefold() for row in selected} - {row["ip"].casefold() for row in selected}
        if missing:
            raise ValueError("Unknown controller name/IP: " + ", ".join(sorted(missing)))
        return selected
    if group:
        matching = [row for row in rows if row["group"] and row["group"].casefold().startswith(group.casefold())]
        if not matching:
            raise ValueError("No controllers matched group ID/prefix: " + group)
        return matching
    return rows


def plot_snapshot(snapshot, output, names=None, group=None, point_size=7):
    rows = select_rows(load_snapshot(snapshot), names, group)
    if not rows:
        raise ValueError("No mapped LED coordinates found for selection")
    # Delayed import gives a clear install hint for users without matplotlib.
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("Matplotlib required: python -m pip install matplotlib") from exc

    fig, ax = plt.subplots(figsize=(11, 9))
    cmap = plt.get_cmap("tab20")
    for i, row in enumerate(rows):
        xs, ys = zip(*row["coordinates"])
        ax.scatter(xs, ys, s=point_size, alpha=0.78, color=cmap(i % 20),
                   edgecolors="none", label=f'{row["name"]} ({len(xs)} LEDs)')
    ax.set_title(f"Twinkly layout | {len(rows)} sets | {sum(len(r['coordinates']) for r in rows):,} LEDs")
    ax.set_xlabel("X (API coordinates)")
    ax.set_ylabel("Y (API coordinates)")
    # Preserve spatial proportions; do not independently stretch X and Y.
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlim(-1.08, 1.08)
    ax.set_ylim(-0.08, 1.08)
    ax.grid(alpha=0.15)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.10), ncol=2, fontsize=9)
    fig.tight_layout()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return output, len(rows), sum(len(r["coordinates"]) for r in rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", help="Snapshot folder containing manifest.json and controllers/")
    parser.add_argument("--output", help="PNG destination; defaults to snapshot/layout.png")
    parser.add_argument("--names", nargs="+", help="Filter by exact configured names or IPs")
    parser.add_argument("--group", help="Filter by Twinkly master/slave group UUID prefix")
    parser.add_argument("--point-size", type=float, default=7, help="LED point area (default 7)")
    args = parser.parse_args()
    try:
        if not 0.2 <= args.point_size <= 200:
            parser.error("--point-size must be between 0.2 and 200")
        output = args.output or str(Path(args.snapshot) / "layout.png")
        path, count, leds = plot_snapshot(args.snapshot, output, args.names, args.group, args.point_size)
        print(f"Saved {path} ({count} controllers, {leds} LED coordinates)")
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
