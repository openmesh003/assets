"""Keep each catalog entry's "formats" map in sync with the files on disk.

    python3 scripts/sync_catalog.py          # update catalog.json
    python3 scripts/sync_catalog.py --check  # exit 1 if catalog.json is out of date (for CI)

A model's formats are the files next to its `asset` that share its base name,
e.g. models/coffee-mug.glb + models/coffee-mug.blend. Entries whose file no
longer exists are dropped. Plain Python, no Blender needed; render_thumbnails.py
also runs this before rendering.
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(ROOT, "catalog.json")

# File extensions recognised as model formats. Blender backups (.blend1) are not listed on purpose.
MODEL_EXTENSIONS = ("glb", "gltf", "blend", "fbx", "obj", "usdz", "usd", "usda", "usdc", "stl", "ply", "dae", "3mf")


def detect_formats(entry):
    """Return the formats map for one entry: the asset's own format first, then the rest alphabetically."""
    asset = entry["asset"]
    folder, name = os.path.split(asset)
    stem, ext = os.path.splitext(name)
    primary = ext.lstrip(".").lower()

    found = {}
    abs_folder = os.path.join(ROOT, folder)
    if os.path.isdir(abs_folder):
        for filename in os.listdir(abs_folder):
            f_stem, f_ext = os.path.splitext(filename)
            fmt = f_ext.lstrip(".").lower()
            if f_stem == stem and fmt in MODEL_EXTENSIONS:
                found[fmt] = f"{folder}/{filename}" if folder else filename

    # Always list the asset itself, even if it is missing, so the problem stays visible.
    found[primary] = asset
    return {primary: asset, **{k: found[k] for k in sorted(found) if k != primary}}


def sync_formats(catalog):
    """Update every entry's "formats" in place. Returns a list of human-readable changes."""
    changes = []
    for i, entry in enumerate(catalog["models"]):
        old = entry.get("formats", {})
        new = detect_formats(entry)
        if old == new:
            continue
        added = sorted(set(new) - set(old))
        removed = sorted(set(old) - set(new))
        parts = [f"+{f}" for f in added] + [f"-{f}" for f in removed]
        changes.append(f"{entry['id']}: {' '.join(parts) or 'reordered'}")
        # Rebuild the entry so "formats" sits right after "asset".
        rebuilt = {}
        for key, value in entry.items():
            if key == "formats":
                continue
            rebuilt[key] = value
            if key == "asset":
                rebuilt["formats"] = new
        catalog["models"][i] = rebuilt
    return changes


def load_catalog():
    with open(CATALOG, encoding="utf-8") as f:
        text = f.read()
    return json.loads(text), text


def dump_catalog(catalog, original_text):
    text = json.dumps(catalog, indent=2, ensure_ascii=False)
    return text + "\n" if original_text.endswith("\n") else text


def save_catalog(catalog, original_text):
    """Write catalog.json if it changed, keeping its formatting. Returns True if written."""
    text = dump_catalog(catalog, original_text)
    if text == original_text:
        return False
    with open(CATALOG, "w", encoding="utf-8") as f:
        f.write(text)
    return True


def main():
    p = argparse.ArgumentParser(prog="sync_catalog.py")
    p.add_argument("--check", action="store_true", help="don't write; exit 1 if catalog.json is out of date")
    args = p.parse_args()

    catalog, original = load_catalog()
    changes = sync_formats(catalog)
    for change in changes:
        print(f"[formats] {change}")

    if args.check:
        if changes:
            print("[formats] catalog.json is out of date; run scripts/sync_catalog.py", file=sys.stderr)
            sys.exit(1)
        print("[formats] catalog.json is up to date")
        return

    if save_catalog(catalog, original):
        print("[formats] updated catalog.json")
    else:
        print("[formats] catalog.json is up to date")


if __name__ == "__main__":
    main()
