import argparse
import os

import google.auth
from google.auth.transport.requests import AuthorizedSession

# Alertes e-mail du projet, via l'API Cloud Monitoring (gratuite). Rejouable :
# les canaux existants sont réutilisés, les règles du même nom remplacées.
#   - tout job Cloud Run du projet terminé en échec (y compris les futurs) ;
#   - toute ligne « ALERTE » écrite par un job (ex. SBIF annonce le marché
#     fermé en pleine séance, actions incohérentes, relecture impossible).
#
# Usage : python surveillance/alertes.py adresse@exemple.com [autre@exemple.com ...]
API = "https://monitoring.googleapis.com/v3/projects/{projet}"

REGLES = [
    {
        "displayName": "Job Cloud Run en échec",
        "combiner": "OR",
        "conditions": [{
            "displayName": "Exécution terminée en échec",
            "conditionThreshold": {
                "filter": 'resource.type = "cloud_run_job" AND '
                          'metric.type = "run.googleapis.com/job/completed_execution_count" AND '
                          'metric.labels.result = "failed"',
                "aggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_SUM"}],
                "comparison": "COMPARISON_GT",
                "thresholdValue": 0,
                "duration": "0s",
            },
        }],
        "alertStrategy": {"autoClose": "86400s"},
    },
    {
        "displayName": "Ligne ALERTE dans les journaux d'un job",
        "combiner": "OR",
        "conditions": [{
            "displayName": "Message ALERTE",
            "conditionMatchedLog": {"filter": 'resource.type = "cloud_run_job" AND textPayload:"ALERTE"'},
        }],
        "alertStrategy": {"notificationRateLimit": {"period": "3600s"}, "autoClose": "86400s"},
    },
]


def lister(session, url, cle):
    elements, page = [], None
    while True:
        reponse = session.get(url, params={"pageToken": page} if page else None)
        reponse.raise_for_status()
        corps = reponse.json()
        elements += corps.get(cle, [])
        page = corps.get("nextPageToken")
        if not page:
            return elements


def canal_email(session, base, adresse):
    for canal in lister(session, f"{base}/notificationChannels", "notificationChannels"):
        if canal.get("type") == "email" and canal.get("labels", {}).get("email_address") == adresse:
            return canal["name"]
    reponse = session.post(f"{base}/notificationChannels", json={
        "type": "email", "displayName": f"E-mail {adresse}", "labels": {"email_address": adresse}})
    reponse.raise_for_status()
    return reponse.json()["name"]


def main():
    analyseur = argparse.ArgumentParser()
    analyseur.add_argument("adresses", nargs="+", help="adresses e-mail destinataires des alertes")
    arguments = analyseur.parse_args()

    credentials, projet_par_defaut = google.auth.default(scopes=["https://www.googleapis.com/auth/monitoring"])
    projet = os.environ.get("GCP_PROJECT", projet_par_defaut)
    session = AuthorizedSession(credentials)
    base = API.format(projet=projet)

    canaux = [canal_email(session, base, adresse) for adresse in arguments.adresses]
    existantes = lister(session, f"{base}/alertPolicies", "alertPolicies")
    for regle in REGLES:
        for ancienne in existantes:
            if ancienne["displayName"] == regle["displayName"]:
                session.delete(f"https://monitoring.googleapis.com/v3/{ancienne['name']}").raise_for_status()
        reponse = session.post(f"{base}/alertPolicies", json={**regle, "notificationChannels": canaux})
        reponse.raise_for_status()
        print(f"Règle « {regle['displayName']} » → {', '.join(arguments.adresses)}")


if __name__ == "__main__":
    main()
