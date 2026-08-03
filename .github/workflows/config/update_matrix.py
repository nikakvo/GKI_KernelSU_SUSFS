#!/usr/bin/env python3
"""
update_matrix.py - refreshes matrix.json with the latest known respin
(sub_level + os_patch_level + kernel_tag) for each Android/kernel branch,
sourced directly from Google's kernel/common repo.

Technique: `git ls-remote --tags` (the same approach kernel_builder.py
already uses for deprecated-branch detection) to discover every respin
tag (e.g. android13-5.15-2025-12_r10), then one gitiles TEXT-format fetch
per *winning* tag to read the Makefile's VERSION/PATCHLEVEL/SUBLEVEL and
resolve the actual sub_level number (the tag name alone doesn't carry it).

matrix.json becomes a CACHE of what this script last found - Google's
repo remains the source of truth. This script does NOT touch the
`enabled` flag on existing entries, and NEVER deletes an existing entry
(even an old one). New patch-levels it discovers are added with
"enabled": false so nothing gets built without your explicit opt-in, and
only within the --months lookback window (default 24) to avoid the
matrix growing back to 2021.

Usage:
    python3 update_matrix.py                 # update in place, last 24 months
    python3 update_matrix.py --dry-run        # only print what would change
    python3 update_matrix.py --months 12      # narrower lookback window
"""
import subprocess
import re
import json
import sys
import time
import base64
import urllib.request
import urllib.error
from pathlib import Path
from datetime import date
from collections import defaultdict

REMOTE = "https://android.googlesource.com/kernel/common"
MATRIX_FILE = Path(__file__).resolve().parent / "matrix.json"

# Only the families this project builds (matches config.py's
# ANDROID_KERNEL_MAP, plus android16/17 for tracking ahead of time).
TRACKED = {
    "android12": "5.10",
    "android13": "5.15",
    "android14": "6.1",
    "android15": "6.6",
    "android16": "6.12",
    "android17": "6.18",
}

TAG_RE = re.compile(r'^(android1[2-7])-(5\.10|5\.15|6\.1|6\.6|6\.12|6\.18)-(\d{4}-\d{2})_r(\d+)$')

REQUEST_DELAY = 0.4          # seconds between Makefile fetches - avoids 429
MAX_RETRIES = 5
RETRY_BASE_DELAY = 2.0       # seconds, doubles each retry (2, 4, 8, 16, 32)


def fetch_tags() -> list[str]:
    print("Fetching tag list from kernel/common (git ls-remote)...")
    result = subprocess.run(
        ["git", "ls-remote", "--tags", REMOTE],
        capture_output=True, text=True, check=True, timeout=120,
    )
    tags = []
    for line in result.stdout.splitlines():
        if "refs/tags/" not in line:
            continue
        tag = line.split("refs/tags/", 1)[1]
        if tag.endswith("^{}"):
            continue
        tags.append(tag)
    print(f"Found {len(tags)} tags total.")
    return tags


def months_ago(patch: str, now: date) -> int:
    """How many months back a 'YYYY-MM' patch string is, relative to now."""
    y, m = (int(x) for x in patch.split("-"))
    return (now.year - y) * 12 + (now.month - m)


def find_latest_respins(tags: list[str], months: int) -> dict:
    """{(android, kernel): {os_patch_level: (respin, tag_name)}} - only
    patches within the lookback window."""
    now = date.today()
    latest = defaultdict(dict)
    for tag in tags:
        m = TAG_RE.match(tag)
        if not m:
            continue
        android, kernel, patch, respin = m.groups()
        if TRACKED.get(android) != kernel:
            continue
        if months_ago(patch, now) > months:
            continue
        respin = int(respin)
        key = (android, kernel)
        current = latest[key].get(patch)
        if current is None or respin > current[0]:
            latest[key][patch] = (respin, tag)
    return latest


def fetch_sub_level(tag: str) -> str | None:
    """Read VERSION/PATCHLEVEL/SUBLEVEL from the Makefile at this exact tag,
    with retry/backoff on HTTP 429 (gitiles rate limiting)."""
    url = f"{REMOTE}/+/refs/tags/{tag}/Makefile?format=TEXT"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Python"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                content = base64.b64decode(resp.read()).decode("utf-8", errors="ignore")
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                print(f"  429 for {tag}, waiting {delay:.0f}s and retrying "
                      f"({attempt}/{MAX_RETRIES})...")
                time.sleep(delay)
                continue
            print(f"  ! Failed to read Makefile for {tag}: {e}")
            return None
        except Exception as e:
            print(f"  ! Failed to read Makefile for {tag}: {e}")
            return None
    else:
        return None

    m = re.search(r'^SUBLEVEL\s*=\s*(\S+)', content, re.MULTILINE)
    if not m:
        print(f"  ! Could not find SUBLEVEL in the Makefile for {tag}")
        return None
    return m.group(1)


def load_matrix() -> dict:
    if MATRIX_FILE.exists():
        with open(MATRIX_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def reorder_entry(entry: dict) -> dict:
    """Canonical key order for readability: sub_level, os_patch_level,
    kernel_tag, revision (if present), then 'enabled' always LAST so it's
    easy to scan/toggle without reading through everything else first."""
    order = ["sub_level", "os_patch_level", "kernel_tag", "revision"]
    reordered = {}
    for k in order:
        if k in entry:
            reordered[k] = entry[k]
    for k, v in entry.items():
        if k not in reordered and k != "enabled":
            reordered[k] = v
    if "enabled" in entry:
        reordered["enabled"] = entry["enabled"]
    return reordered


def write_matrix_compact(matrix: dict, path: Path):
    """Write matrix.json with each entry on a single line (matches the
    original hand-edited style) instead of json.dump's one-key-per-line."""
    lines = ["{"]
    top_keys = list(matrix.keys())
    for ki, key in enumerate(top_keys):
        entries = matrix[key]
        lines.append(f'    "{key}": [')
        for ei, entry in enumerate(entries):
            comma = "," if ei < len(entries) - 1 else ""
            lines.append(f"        {json.dumps(reorder_entry(entry), ensure_ascii=False)}{comma}")
        comma = "," if ki < len(top_keys) - 1 else ""
        lines.append(f"    ]{comma}")
    lines.append("}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    dry_run = "--dry-run" in sys.argv
    months = 24
    if "--months" in sys.argv:
        idx = sys.argv.index("--months")
        months = int(sys.argv[idx + 1])

    tags = fetch_tags()
    latest = find_latest_respins(tags, months)
    matrix = load_matrix()

    changes = []
    warnings = []

    for android, kernel in TRACKED.items():
        matrix_key = f"{android}-{kernel}"
        entries = matrix.setdefault(matrix_key, [])
        by_patch = {e["os_patch_level"]: e for e in entries}

        for patch, (respin, tag) in sorted(latest.get((android, kernel), {}).items()):
            print(f"{matrix_key} / {patch}: latest respin is r{respin} ({tag})")
            sub_level = fetch_sub_level(tag)
            time.sleep(REQUEST_DELAY)
            if sub_level is None:
                warnings.append(f"{matrix_key} {patch}: skipped (could not determine sub_level)")
                continue

            existing = by_patch.get(patch)
            if existing is None:
                new_entry = {
                    "sub_level": sub_level,
                    "os_patch_level": patch,
                    "kernel_tag": tag,
                    "enabled": False,
                }
                entries.append(new_entry)
                by_patch[patch] = new_entry
                changes.append(f"+ NEW: {matrix_key} {patch} -> sub_level {sub_level}, {tag} (enabled: false)")
            else:
                old_tag = existing.get("kernel_tag")
                old_sub = existing.get("sub_level")
                if old_tag != tag or old_sub != sub_level:
                    changes.append(
                        f"~ UPDATED: {matrix_key} {patch}: "
                        f"sub_level {old_sub}->{sub_level}, tag {old_tag}->{tag} "
                        f"(enabled stays: {existing.get('enabled', True)})"
                    )
                    existing["sub_level"] = sub_level
                    existing["kernel_tag"] = tag

        entries.sort(key=lambda e: e["os_patch_level"])

        active_patches = set(latest.get((android, kernel), {}).keys())
        for e in entries:
            if e.get("enabled") and e["os_patch_level"] not in active_patches:
                warnings.append(
                    f"⚠ {matrix_key} {e['os_patch_level']} (sub_level {e['sub_level']}) "
                    f"is enabled, but the branch no longer appears among current tags "
                    f"(deprecated/removed, or outside the {months}-month window) - check manually"
                )

    print("\n" + "=" * 60)
    if changes:
        print(f"Changes ({len(changes)}):")
        for c in changes:
            print(f"  {c}")
    else:
        print("No changes - matrix.json is already up to date.")

    if warnings:
        print(f"\nWarnings ({len(warnings)}):")
        for w in warnings:
            print(f"  {w}")
    print("=" * 60)

    if dry_run:
        print("\n--dry-run: matrix.json was NOT written.")
        return

    if changes:
        write_matrix_compact(matrix, MATRIX_FILE)
        print(f"\nmatrix.json updated: {MATRIX_FILE}")
    else:
        print("\nNothing to write.")


if __name__ == "__main__":
    main()
