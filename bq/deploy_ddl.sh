#!/usr/bin/env bash
# Applies the DDL files listed in DDL_FILES below, in the order given.
#
# Order is spelled out explicitly (rather than inferred from filenames or
# directory listing order) so a dependency between two files — e.g. a view
# that references a table defined elsewhere — is a visible, commented fact
# here instead of something encoded in how the files happen to be named.
#
# Usage:
#   GCP_PROJECT=my-project ./bq/deploy_ddl.sh
#
# Env vars:
#   GCP_PROJECT   GCP project ID. If unset, uses whatever project is active
#                 in `gcloud config`.
#   BQ_DATASET    Dataset name. Defaults to "brvm".
#   BQ_LOCATION   Dataset location, only used if the dataset doesn't exist yet.
#                 Defaults to "US" — override if you care about data residency
#                 (e.g. "europe-west9" for Paris, closer to the source market).

set -euo pipefail

DATASET="${BQ_DATASET:-brvm}"
LOCATION="${BQ_LOCATION:-US}"

PROJECT_FLAG=()
if [[ -n "${GCP_PROJECT:-}" ]]; then
  PROJECT_FLAG=(--project_id="$GCP_PROJECT")
fi

echo "==> Ensuring dataset '${DATASET}' exists (location: ${LOCATION})"
bq --location="$LOCATION" mk --dataset "${PROJECT_FLAG[@]}" "${DATASET}" 2>/dev/null \
  && echo "    created" || echo "    already exists, skipping"

DDL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/ddl" && pwd)"

DDL_FILES=(
  dim_instruments.sql
  stg_cours_journaliers_sikafinance.sql
  stg_cours_journaliers_richbourse.sql
  stg_cours_journaliers_brvm_scrape.sql
)

for name in "${DDL_FILES[@]}"; do
  echo "==> Applying $name"
  bq query --use_legacy_sql=false "${PROJECT_FLAG[@]}" < "$DDL_DIR/$name"
done

echo "==> Done."
