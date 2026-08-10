import os
import sys
import time
import uuid
from datetime import date, datetime, timedelta

import requests
from google.cloud import bigquery

# API interne (non documentée) utilisée par le site sikafinance pour son
# propre historique de cours. Solution transitoire, en attendant les
# données de la BRVM.
URL_GETHISTOS = "https://www.sikafinance.com/api/general/GetHistos"
EN_TETE_NAVIGATEUR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0",
    "Accept": "*/*",
    "Content-Type": "application/json",
    "Origin": "https://www.sikafinance.com",
}

REQUETE_FUSION = """
MERGE `{table}` T
USING `{table_temporaire}` S
ON T.date = S.date AND T.code = S.code
WHEN MATCHED THEN
  UPDATE SET ouverture = S.ouverture, haut = S.haut, bas = S.bas, cloture = S.cloture,
             volume = S.volume, maj_le = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN
  INSERT (code, date, ouverture, haut, bas, cloture, volume, maj_le)
  VALUES (S.code, S.date, S.ouverture, S.haut, S.bas, S.cloture, S.volume, CURRENT_TIMESTAMP())
"""


def calculer_code_sikafinance(code, type_instrument, code_pays):
    if type_instrument == "indice":
        return code
    return f"{code}.{code_pays.lower()}"


def lister_instruments_actifs(client, jeu_de_donnees):
    requete = f"""
        SELECT code, type, code_pays
        FROM `{jeu_de_donnees}.dim_instruments`
        WHERE date_radiation IS NULL
    """
    return list(client.query(requete).result())


def recuperer_cours(code_sikafinance, date_debut, date_fin):
    reponse = requests.post(
        URL_GETHISTOS,
        headers={**EN_TETE_NAVIGATEUR, "Referer": f"https://www.sikafinance.com/marches/historiques/{code_sikafinance}"},
        json={
            "ticker": code_sikafinance,
            "datedeb": date_debut.isoformat(),
            "datefin": date_fin.isoformat(),
            "xperiod": "0",
        },
        timeout=30,
    )
    reponse.raise_for_status()
    corps = reponse.json()
    lignes_brutes = corps.get("lst")
    if not isinstance(lignes_brutes, list):
        return []

    cours = []
    for ligne in lignes_brutes:
        cours.append({
            "date": datetime.strptime(ligne["Date"], "%d/%m/%Y").date().isoformat(),
            "ouverture": ligne["Open"],
            "haut": ligne["High"],
            "bas": ligne["Low"],
            "cloture": ligne["Close"],
            "volume": ligne["Volume"],
        })
    return cours


def recuperer_cours_avec_reprises(code_sikafinance, date_debut, date_fin, tentatives=3):
    for tentative in range(1, tentatives + 1):
        try:
            return recuperer_cours(code_sikafinance, date_debut, date_fin)
        except requests.RequestException:
            if tentative == tentatives:
                raise
            time.sleep(2 * tentative)


def fusionner_dans_bigquery(client, jeu_de_donnees, lignes):
    table = f"{jeu_de_donnees}.stg_cours_journaliers_sikafinance"
    table_temporaire = f"{jeu_de_donnees}.stg_cours_journaliers_sikafinance_tmp_{uuid.uuid4().hex[:8]}"

    schema = [
        bigquery.SchemaField("code", "STRING"),
        bigquery.SchemaField("date", "DATE"),
        bigquery.SchemaField("ouverture", "NUMERIC"),
        bigquery.SchemaField("haut", "NUMERIC"),
        bigquery.SchemaField("bas", "NUMERIC"),
        bigquery.SchemaField("cloture", "NUMERIC"),
        bigquery.SchemaField("volume", "INTEGER"),
    ]
    config_chargement = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(lignes, table_temporaire, job_config=config_chargement).result()

    try:
        client.query(REQUETE_FUSION.format(table=table, table_temporaire=table_temporaire)).result()
    finally:
        client.delete_table(table_temporaire, not_found_ok=True)


def main():
    jeu_de_donnees = os.environ.get("BQ_DATASET", "brvm")
    jours_historique = int(os.environ.get("JOURS_HISTORIQUE", "7"))

    client = bigquery.Client(project=os.environ.get("GCP_PROJECT"))
    instruments = lister_instruments_actifs(client, jeu_de_donnees)

    date_fin = date.today()
    date_debut = date_fin - timedelta(days=jours_historique)

    lignes = []
    echecs = []
    for instrument in instruments:
        code_source = calculer_code_sikafinance(instrument["code"], instrument["type"], instrument["code_pays"])
        try:
            cours = recuperer_cours_avec_reprises(code_source, date_debut, date_fin)
        except requests.RequestException as erreur:
            echecs.append(instrument["code"])
            print(f"echec pour {instrument['code']}: {erreur}", file=sys.stderr)
            continue

        for ligne in cours:
            ligne["code"] = instrument["code"]
            lignes.append(ligne)
        time.sleep(0.15)

    if lignes:
        fusionner_dans_bigquery(client, jeu_de_donnees, lignes)
    print(f"{len(lignes)} lignes de cours synchronisées ({len(instruments)} instruments interrogés, {len(echecs)} échecs).")

    if echecs:
        sys.exit(1)


if __name__ == "__main__":
    main()
