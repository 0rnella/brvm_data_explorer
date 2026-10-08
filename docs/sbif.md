# Source SBIF Trade

Transactions de séance, cours intrajournaliers (toutes les 5 minutes) et cours journaliers, depuis la
plateforme de la SGI SBIF Trade.

## Intérêt

La plateforme de la SGI **SBIF Trade** expose une API interne (`MarketDetails.aspx`), utilisée par sa propre
page « Résumé du Marché ». Elle répond **sans connexion** : aucun identifiant, et lecture seule. Elle apporte
ce qu'aucune source actuelle n'a :

- **toutes les transactions de la séance** (heure, quantité, cours), pour toutes les actions ;
- **le tableau du marché toutes les 5 minutes** pendant la séance : dernier cours, volume, carnet à 5 limites ;
- **un cours journalier** de plus, identique à brvm.org le 07/10/2026 (ouverture, clôture, volume, 48/48
  actions).

## Fichiers

| Fichier | Rôle |
|---|---|
| `ingestion/cours_intrajournalier_sbif.py` | photo du tableau → `stg_cours_intrajournaliers_sbif` |
| `ingestion/transactions_sbif.py` | transactions + contrôles → `stg_transactions_sbif`, `stg_controles_transactions_sbif` ; avec `--collecte lendemain`, aussi `stg_cours_journaliers_sbif` |
| `bq/ddl/stg_*_sbif.sql` | 4 tables, ajoutées à `DDL_FILES` dans `deploy_ddl.sh` |
| `surveillance/alertes.py` | alertes e-mail : tout job en échec, toute ligne « ALERTE » dans les journaux |

Le code suit les conventions du dépôt : deux scripts autonomes, `requests`, MERGE via table temporaire, tables
`stg_<sujet>_<source>`, partition à l'année, `GCP_PROJECT` / `BQ_DATASET`. Rien ne change dans le `Dockerfile`
ni dans `requirements.txt`.

**Deux exceptions, justifiées :**

- **`stg_cours_intrajournaliers_sbif` est partitionnée au jour.** Elle reçoit environ 3 300 lignes par séance
  et 830 000 par an. Au jour, chaque MERGE ne relit que la séance en cours, au lieu de toute l'année.
- **Les transactions sont remplacées, pas fusionnées.** Chaque collecte remplace les lignes
  `(date_seance, code)` des actions qu'elle a lues : le cumul SBIF d'une même transaction change entre le soir
  et le lendemain, il ne peut donc pas servir de clé. La collecte du soir ne remplace jamais celle du
  lendemain.

## Pourquoi deux collectes par séance

Le 07/10/2026, le flux de SBIF n'a démarré que vers 10:37 : le site a annoncé « fermé » toute la journée, et le
soir il ne servait que 4 460 transactions, sans la matinée. Le lendemain matin, avant de passer à la séance
suivante, il servait la séance complète, rechargée pendant la nuit : 6 698 transactions dès 09:44:15, avec des
volumes égaux à sikafinance et brvm.org. D'où :

- **`soir`** à 15:05 : provisoire, disponible le jour même ;
- **`lendemain`** à 06:30 : fait foi, remplace le soir et alimente `stg_cours_journaliers_sbif`. Si SBIF sert
  déjà la séance suivante, le job écrit une ALERTE et échoue.

## Contrôles (`stg_controles_transactions_sbif`)

Une ligne par action, séance et collecte. Les contrôles :

- cumul SBIF enchaîné sans trou ;
- somme des quantités = cumul échangé du tableau ;
- dernière transaction = dernier cours ;
- date ;
- doublons.

`premiere_transaction` signale un début de séance manquant. Les écarts entre cours min / max et transactions
sont seulement informatifs. **Toutes les actions sont chargées** ; chaque transaction porte `coherente`. La
manière d'exploiter les incohérences reste à décider (vue des transactions validées ? exclusion ?).

## Déploiement

```bash
GCP_PROJECT=brvm-explorer ./bq/deploy_ddl.sh

gcloud builds submit --project brvm-explorer \
  --tag us-docker.pkg.dev/brvm-explorer/brvm-ingestion/cours-journalier:latest

IMAGE=us-docker.pkg.dev/brvm-explorer/brvm-ingestion/cours-journalier:latest
SA=brvm-ingestion@brvm-explorer.iam.gserviceaccount.com
ENV=GCP_PROJECT=brvm-explorer,BQ_DATASET=brvm

gcloud run jobs deploy cours-intrajournalier-sbif --project brvm-explorer --region us-central1 --image $IMAGE \
  --args ingestion/cours_intrajournalier_sbif.py,--seulement-en-seance \
  --service-account $SA --set-env-vars $ENV --task-timeout 300 --max-retries 1
gcloud run jobs deploy transactions-sbif-soir --project brvm-explorer --region us-central1 --image $IMAGE \
  --args ingestion/transactions_sbif.py,--collecte,soir \
  --service-account $SA --set-env-vars $ENV --task-timeout 1200 --max-retries 1
gcloud run jobs deploy transactions-sbif-lendemain --project brvm-explorer --region us-central1 --image $IMAGE \
  --args ingestion/transactions_sbif.py,--collecte,lendemain \
  --service-account $SA --set-env-vars $ENV --task-timeout 1200 --max-retries 1
```

Déclenchements Cloud Scheduler, en `Etc/UTC`, même modèle que `cours-journalier-12h` (cible HTTP `…/jobs/<job>:run`,
OAuth `brvm-ingestion@`) :

| Déclenchement | Job | Horaire |
|---|---|---|
| `cours-intrajournalier-sbif-5min` | `cours-intrajournalier-sbif` | `*/5 9-15 * * 1-5` (le script ignore avant 09:45 et après 15:00) |
| `transactions-sbif-soir` | `transactions-sbif-soir` | `5 15 * * 1-5` |
| `transactions-sbif-lendemain` | `transactions-sbif-lendemain` | `30 6 * * 2-6` (après le chargement sikafinance de 06:00, avant le changement de séance SBIF vers 09:00) |

```bash
gcloud scheduler jobs create http transactions-sbif-lendemain --project brvm-explorer --location us-central1 \
  --schedule "30 6 * * 2-6" --time-zone Etc/UTC --http-method POST \
  --uri https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/brvm-explorer/jobs/transactions-sbif-lendemain:run \
  --oauth-service-account-email $SA
```

Alertes, une seule fois :

```bash
GCP_PROJECT=brvm-explorer python surveillance/alertes.py adresse@exemple.com
```

Coût : quelques dizaines de centimes par mois au plus. Cloud Scheduler facture 0,10 $ par déclenchement et
par mois au-delà des 3 gratuits ; Cloud Run et BigQuery restent dans le palier gratuit.

## Points ouverts

- **Exploitation des transactions incohérentes** : vue des seules transactions validées, ou exclusion ? À
  décider.
- **Planifier `cours-journalier-brvm-scrape`.** C'est la source officielle ; le job n'a jamais tourné dans
  Cloud Run. brvm.org ne montre que la dernière séance, il faut donc le lancer chaque jour, par exemple à
  `0 18 * * 1-5`.
- **Tests sans réseau** (décodage, contrôles) : disponibles hors du dépôt, à ajouter si on le souhaite.
