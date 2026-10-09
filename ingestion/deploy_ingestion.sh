#!/usr/bin/env bash
# Déploie tous les jobs d'ingestion (Cloud Run) et leurs déclenchements (Cloud Scheduler).
# Rejouable : l'image est reconstruite, puis chaque job et chaque déclenchement est créé s'il
# n'existe pas, mis à jour sinon. La construction tourne sous le compte de service
# infrastructure@, qui doit avoir le rôle roles/cloudbuild.builds.builder.
#
# Usage :
#   GCP_PROJECT=brvm-explorer ./ingestion/deploy_ingestion.sh
#
# Env vars :
#   GCP_PROJECT   GCP project ID. Defaults to "brvm-explorer".
#   BQ_DATASET    Dataset name. Defaults to "brvm".
#   REGION        Defaults to "us-central1".
#   IMAGE         Defaults to the "latest" tag of the brvm-ingestion repository.

set -euo pipefail

PROJECT="${GCP_PROJECT:-brvm-explorer}"
DATASET="${BQ_DATASET:-brvm}"
REGION="${REGION:-us-central1}"
IMAGE="${IMAGE:-us-docker.pkg.dev/${PROJECT}/brvm-ingestion/cours-journalier:latest}"
SERVICE_ACCOUNT="brvm-ingestion@${PROJECT}.iam.gserviceaccount.com"
BUILD_SERVICE_ACCOUNT="infrastructure@${PROJECT}.iam.gserviceaccount.com"

# job | script (et arguments, séparés par des virgules) | délai max (s) | variables en plus | déclenchement | horaire (Etc/UTC)
# Script vide : commande par défaut de l'image (Dockerfile).
JOBS=(
  "cours-journalier||600|JOURS_HISTORIQUE=7|cours-journalier-12h|0 6,18 * * *"
  "cours-journalier-richbourse|ingestion/cours_journalier_richbourse.py|1800||cours-journalier-richbourse-12h|0 6,18 * * *"
  "cours-journalier-brvm-scrape|ingestion/cours_journalier_brvm_scrape.py|600||cours-journalier-brvm-scrape-quotidien|0 18 * * 1-5"
  # Le script ne capture qu'entre 09:45 et 15:00 GMT.
  "cours-intrajournalier-sbif|ingestion/cours_intrajournalier_sbif.py,--seulement-en-seance|300||cours-intrajournalier-sbif-5min|*/5 9-15 * * 1-5"
  "transactions-sbif-soir|ingestion/transactions_sbif.py,--collecte,soir|1200||transactions-sbif-soir|5 15 * * 1-5"
  # Après le chargement sikafinance de 06:00, avant que SBIF ne passe à la séance suivante (vers 09:00).
  "transactions-sbif-lendemain|ingestion/transactions_sbif.py,--collecte,lendemain|1200||transactions-sbif-lendemain|30 6 * * 2-6"
)

echo "==> Image $IMAGE"
gcloud builds submit "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" --project "$PROJECT" --tag "$IMAGE" \
  --service-account "projects/${PROJECT}/serviceAccounts/${BUILD_SERVICE_ACCOUNT}" \
  --default-buckets-behavior regional-user-owned-bucket

for definition in "${JOBS[@]}"; do
  IFS='|' read -r job script delai variables declencheur horaire <<< "$definition"

  env_vars="GCP_PROJECT=${PROJECT},BQ_DATASET=${DATASET}${variables:+,$variables}"
  echo "==> Job $job"
  gcloud run jobs deploy "$job" --project "$PROJECT" --region "$REGION" --image "$IMAGE" \
    ${script:+--args "$script"} --service-account "$SERVICE_ACCOUNT" --set-env-vars "$env_vars" \
    --task-timeout "$delai" --max-retries 1 --quiet

  uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT}/jobs/${job}:run"
  if gcloud scheduler jobs describe "$declencheur" --project "$PROJECT" --location "$REGION" >/dev/null 2>&1; then
    action=update
  else
    action=create
  fi
  echo "    déclenchement $declencheur ($action) : $horaire"
  gcloud scheduler jobs "$action" http "$declencheur" --project "$PROJECT" --location "$REGION" \
    --schedule "$horaire" --time-zone Etc/UTC --http-method POST --uri "$uri" \
    --oauth-service-account-email "$SERVICE_ACCOUNT" --quiet
done

echo "==> Done."
