#!/usr/bin/env bash
# Render thumbnails for every model in catalog.json using headless Blender.
# Usage: scripts/generate-thumbnails.sh [--force] [--only ID ...] [--size N] [--samples N] [--cpu]
# Set BLENDER=/path/to/blender to use a specific Blender binary.
set -euo pipefail

cd "$(dirname "$0")/.."
BLENDER="${BLENDER:-blender}"

if ! command -v "$BLENDER" >/dev/null 2>&1; then
  echo "Blender not found. Install Blender 4.2+ or set BLENDER=/path/to/blender." >&2
  exit 1
fi

exec "$BLENDER" --background --factory-startup --python-exit-code 1 \
  --python scripts/render_thumbnails.py -- "$@"
