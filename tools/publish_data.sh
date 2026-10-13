#!/usr/bin/env bash
# Publish a data update for the site.
#
# The site's CSVs aren't in git (they're built from ~12 GB of local raw inputs). Each data
# snapshot is uploaded as the assets of a GitHub release instead, and DATA_VERSION names the
# release the site serves; .github/workflows/pages.yml deploys index.html + assets/ + that
# release's CSVs whenever main changes.
#
#   tools/publish_data.sh                        # regenerate (cache-aware), publish, commit DATA_VERSION
#   tools/publish_data.sh --force lyme,wildfire  # extra args are passed to generate_candidate_data.py
#   tools/publish_data.sh --skip-generate        # publish what's already in output/
#
# DATA_VERSION is committed on the current branch but not pushed: push main (or merge the
# branch) to deploy. When an index.html change depends on new data columns, publish from
# that change's branch so the page and its data go live in the same merge.
set -euo pipefail
cd "$(dirname "$0")/.."

skip_generate=0
gen_args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-generate) skip_generate=1 ;;
    --sample|--anchors-only|--seed-cache)
      echo "error: $1 doesn't produce publishable output" >&2; exit 2 ;;
    *) gen_args+=("$1") ;;
  esac
  shift
done
if [[ $skip_generate == 1 && ${#gen_args[@]} -gt 0 ]]; then
  echo "error: --skip-generate can't be combined with generator args" >&2; exit 2
fi

command -v gh >/dev/null || { echo "error: needs the GitHub CLI (gh)" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "error: not logged in; run 'gh auth login'" >&2; exit 1; }

if [[ $skip_generate == 0 ]]; then
  [[ -x .venv/bin/python ]] || { echo "Creating .venv"; python3 -m venv .venv; }
  # reinstall only when requirements.txt changes
  req_hash=$(shasum -a 256 requirements.txt | cut -d' ' -f1)
  if [[ "$(cat .venv/.requirements.sha256 2>/dev/null)" != "$req_hash" ]]; then
    echo "Installing requirements"
    .venv/bin/python -m pip install -q -r requirements.txt
    echo "$req_hash" > .venv/.requirements.sha256
  fi
  # exits non-zero (stopping here) if the smoke tests fail
  .venv/bin/python tools/generate_candidate_data.py ${gen_args[@]+"${gen_args[@]}"}
fi

files=(output/candidate_scores.csv output/column_metadata.csv output/candidate_scores.parquet)
for f in "${files[@]}"; do
  [[ -s $f ]] || { echo "error: $f is missing or empty" >&2; exit 1; }
done

csv_hash=$(shasum -a 256 output/candidate_scores.csv | cut -d' ' -f1)
current=""
[[ -f DATA_VERSION ]] && current=$(tr -d '[:space:]' < DATA_VERSION)
if [[ -n $current ]] && gh release view "$current" --json body -q .body 2>/dev/null | grep -q "$csv_hash"; then
  echo "Data is unchanged from $current; nothing to publish."
  exit 0
fi

source_note="Generated from $(git rev-parse --short HEAD)"
[[ $skip_generate == 1 ]] && source_note="Published from existing output/ at $(git rev-parse --short HEAD) (not regenerated)"
if [[ -n $(git status --porcelain -- data_pipeline tools requirements.txt) ]]; then
  source_note+=" with uncommitted pipeline changes"
fi
rows=$(( $(wc -l < output/candidate_scores.csv) - 1 ))

tag="data-$(date +%Y%m%d-%H%M)"
gh release create "$tag" "${files[@]}" --title "Data $tag" --latest=false --notes "$source_note.
$rows places.
candidate_scores.csv sha256: $csv_hash"

echo "$tag" > DATA_VERSION
git add DATA_VERSION
git commit -q -m "Data update: $tag" -- DATA_VERSION
echo
echo "Published release $tag and committed DATA_VERSION on $(git branch --show-current)."
echo "Push main (or merge this branch into main) to deploy."
