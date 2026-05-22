#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "WARNING: This will permanently delete all wiki pages and pipeline state files under:"
echo "  $ROOT/wiki/"
echo "  $ROOT/.wiki_graph.json"
echo "  $ROOT/.wiki_embeddings.json"
echo "  $ROOT/.ingest_state.json"
echo ""
read -r -p "Type YES to proceed: " answer

if [ "$answer" != "YES" ]; then
    echo "Aborted."
    exit 1
fi

# Delete pipeline-generated files
rm -rf "$ROOT/wiki/"
rm -f "$ROOT/.wiki_graph.json" "$ROOT/.wiki_embeddings.json" "$ROOT/.ingest_state.json"

# Recreate the empty directory structure
mkdir -p "$ROOT/wiki/sources" "$ROOT/wiki/concepts" "$ROOT/wiki/entities" "$ROOT/wiki/analyses"

echo "Done. Wiki directories recreated."