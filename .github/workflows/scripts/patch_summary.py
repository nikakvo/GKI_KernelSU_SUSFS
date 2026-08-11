#!/usr/bin/env python3
"""Aggregates the PATCH_STATUS.json produced by every matrix build
(kernel_builder.py's _write_patch_status()) into a single Markdown table
showing, per build, which patches/features applied, were skipped
(not requested / not applicable to that branch), or failed to apply.

Usage: patch_summary.py <dir-with-*.patches.json> [output.md]
If output.md is omitted, prints the table to stdout only.
"""
import json
import sys
from pathlib import Path

STATUS_ICON = {"applied": "OK", "skipped": "-", "failed": "FAIL"}

# Friendly display names, in a fixed preferred column order. Any patch
# name found in the JSON but not listed here is appended at the end
# (alphabetically) so a newly-added patch never gets silently dropped
# from the table.
DISPLAY_NAMES = {
    "ptrace_leak_fix": "Ptrace Leak Fix",
    "ntsync": "NTSync",
    "susfs": "SUSFS",
    "sukisu_hide_stuff": "SukiSU Hide Stuff",
    "zram_lz4kd": "ZRAM (LZ4KD)",
    "task_mmu_fixes": "task_mmu.c Fixes",
    "safemode_disable": "Safe Mode Disabled",
    "baseband_guard": "Baseband-guard",
}
PREFERRED_ORDER = list(DISPLAY_NAMES.keys())


def load_reports(results_dir: Path) -> list:
    reports = []
    for f in sorted(results_dir.glob("*.patches.json")):
        try:
            reports.append(json.loads(f.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as e:
            print(f"::warning::Could not read {f}: {e}", file=sys.stderr)
    return reports


def load_lts_map(repo_root: Path) -> dict:
    """Cross-references (android, kernel, sub_level) -> is this build from
    an LTS-style respin tag (e.g. android13-5.15.209_r00, merged from the
    upstream Linux -stable tree into the android*-lts branch), so the
    summary table can flag it. Purely cosmetic - has no effect on the
    build itself."""
    matrix_path = repo_root / ".github" / "workflows" / "config" / "matrix.json"
    lts_map = {}
    try:
        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
        for key, configs in matrix.items():
            android, kernel = key.split("-", 1)
            for cfg in configs:
                if cfg.get("lts"):
                    lts_map[(android, kernel, str(cfg.get("sub_level")))] = True
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return lts_map


def build_table(reports: list, lts_map: dict = None) -> str:
    lts_map = lts_map or {}
    if not reports:
        return "_No patch status data available for this run._\n"

    # Union of all patch keys seen across every build, preferred ones
    # first, anything unexpected appended after.
    all_keys = set()
    for r in reports:
        all_keys.update(r.get("patches", {}).keys())
    ordered_keys = [k for k in PREFERRED_ORDER if k in all_keys]
    ordered_keys += sorted(k for k in all_keys if k not in PREFERRED_ORDER)

    headers = ["Build"] + [DISPLAY_NAMES.get(k, k) for k in ordered_keys]
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]

    def sort_key(r):
        return (r.get("android_version", ""), r.get("kernel_version", ""), r.get("sub_level", ""))

    for r in sorted(reports, key=sort_key):
        label = f"{r.get('android_version','?')}-{r.get('kernel_version','?')}.{r.get('sub_level','?')}"
        respin = r.get("kernel_respin")
        if respin:
            label += f" ({respin})"
        key = (r.get("android_version", ""), r.get("kernel_version", ""), str(r.get("sub_level", "")))
        if lts_map.get(key):
            label += " [LTS]"
        row = [label]
        patches = r.get("patches", {})
        for key in ordered_keys:
            entry = patches.get(key)
            if entry is None:
                row.append("n/a")
                continue
            status = entry.get("status", "?")
            icon = STATUS_ICON.get(status, status)
            detail = entry.get("detail", "")
            cell = icon
            if status == "failed" and detail:
                cell += f" ({detail})"
            row.append(cell)
        lines.append("| " + " | ".join(row) + " |")

    legend = "\n_OK = applied, - = skipped (not requested / not applicable to this branch), FAIL = attempted but did not apply cleanly, n/a = no data for this build_\n"
    return "\n".join(lines) + "\n" + legend


def main():
    if len(sys.argv) < 2:
        print("Usage: patch_summary.py <dir-with-*.patches.json> [output.md]", file=sys.stderr)
        return 1
    results_dir = Path(sys.argv[1])
    # Independent of cwd (this script is invoked from different working
    # directories across the two workflows) - locate the repo root from
    # this file's own path: <repo_root>/.github/workflows/scripts/patch_summary.py
    repo_root = Path(__file__).resolve().parents[3]
    lts_map = load_lts_map(repo_root)
    if not results_dir.exists():
        print(f"No such directory: {results_dir}", file=sys.stderr)
        table = build_table([], lts_map)
    else:
        table = build_table(load_reports(results_dir), lts_map)

    print("=== Patch Status ===")
    print(table)

    if len(sys.argv) > 2:
        out_path = Path(sys.argv[2])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "a", encoding="utf-8") as f:
            f.write("\n### Patch Status\n\n")
            f.write(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
