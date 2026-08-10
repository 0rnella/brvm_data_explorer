import os
import sys
import uuid

import requests
from bs4 import BeautifulSoup
from google.cloud import bigquery

# On scrape sikafinance en attendant que la BRVM nous donne un accès direct
# à ses données. Solution transitoire, pas destinée à durer.
URL_SIKAFINANCE = "https://www.sikafinance.com/"
EN_TETE_NAVIGATEUR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0"
}

REQUETE_FUSION = """
MERGE `{table}` T
USING `{table_temporaire}` S
ON T.code = S.code
WHEN MATCHED THEN
  UPDATE SET nom = S.nom, type = S.type, code_pays = S.code_pays,
             date_radiation = NULL, vu_le = CURRENT_TIMESTAMP(), maj_le = CURRENT_TIMESTAMP()
WHEN NOT MATCHED BY TARGET THEN
  INSERT (code, nom, type, code_pays, vu_le, maj_le)
  VALUES (S.code, S.nom, S.type, S.code_pays, CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP())
WHEN NOT MATCHED BY SOURCE THEN
  UPDATE SET date_radiation = COALESCE(date_radiation, CURRENT_DATE()), maj_le = CURRENT_TIMESTAMP()
"""


def recuperer_instruments():
    reponse = requests.get(URL_SIKAFINANCE, headers=EN_TETE_NAVIGATEUR, timeout=30)
    reponse.raise_for_status()
    soupe = BeautifulSoup(reponse.text, "html.parser")

    instruments = []
    for option in soupe.select("#dpShares option"):
        valeur = (option.get("value") or "").strip()
        if not valeur:
            continue
        nom = option.get_text(strip=True)
        if "." in valeur:
            code, code_pays = valeur.split(".", 1)
            type_instrument, code_pays = "action", code_pays.upper()
        else:
            code, code_pays, type_instrument = valeur, None, "indice"
        instruments.append({"code": code, "nom": nom, "type": type_instrument, "code_pays": code_pays})
    return instruments


def fusionner_dans_bigquery(client, jeu_de_donnees, instruments):
    table = f"{jeu_de_donnees}.dim_instruments"
    table_temporaire = f"{jeu_de_donnees}.dim_instruments_tmp_{uuid.uuid4().hex[:8]}"

    schema = [
        bigquery.SchemaField("code", "STRING"),
        bigquery.SchemaField("nom", "STRING"),
        bigquery.SchemaField("type", "STRING"),
        bigquery.SchemaField("code_pays", "STRING"),
    ]
    config_chargement = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(instruments, table_temporaire, job_config=config_chargement).result()

    try:
        client.query(REQUETE_FUSION.format(table=table, table_temporaire=table_temporaire)).result()
    finally:
        client.delete_table(table_temporaire, not_found_ok=True)


def main():
    jeu_de_donnees = os.environ.get("BQ_DATASET", "brvm")
    instruments = recuperer_instruments()
    if not instruments:
        print("Aucun instrument récupéré depuis sikafinance — abandon.", file=sys.stderr)
        sys.exit(1)

    client = bigquery.Client(project=os.environ.get("GCP_PROJECT"))
    fusionner_dans_bigquery(client, jeu_de_donnees, instruments)
    print(f"{len(instruments)} instruments synchronisés dans {jeu_de_donnees}.dim_instruments.")


if __name__ == "__main__":
    main()
