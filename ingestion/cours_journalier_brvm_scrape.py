import os
import sys
import uuid
from datetime import date

import requests
from bs4 import BeautifulSoup
from google.cloud import bigquery

# Page officielle, rendue côté serveur, aucune API découverte derrière.
# Un seul instantané du jour courant pour tout le marché en une requête —
# contrairement à richbourse, pas de boucle par instrument ici.
URL_COURS = "https://www.brvm.org/fr/cours-actions/0"
EN_TETE_NAVIGATEUR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0",
}

REQUETE_FUSION = """
MERGE `{table}` T
USING `{table_temporaire}` S
ON T.date = S.date AND T.code = S.code
WHEN MATCHED THEN
  UPDATE SET ouverture = S.ouverture, cloture = S.cloture, cloture_veille = S.cloture_veille,
             volume_titres = S.volume_titres, variation = S.variation, maj_le = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN
  INSERT (code, date, ouverture, cloture, cloture_veille, volume_titres, variation, maj_le)
  VALUES (S.code, S.date, S.ouverture, S.cloture, S.cloture_veille, S.volume_titres, S.variation, CURRENT_TIMESTAMP())
"""


def nettoyer_nombre(texte, entier=False):
    # Décimales à la française (virgule), contrairement aux autres sources.
    texte = texte.replace("\xa0", "").replace(" ", "").replace("%", "").replace(",", ".").strip()
    if texte in ("", "-"):
        return None
    valeur = float(texte)
    return int(valeur) if entier else round(valeur, 6)


def recuperer_cotations():
    reponse = requests.get(URL_COURS, headers=EN_TETE_NAVIGATEUR, timeout=30)
    reponse.raise_for_status()
    soupe = BeautifulSoup(reponse.text, "html.parser")

    table_cible = None
    for table in soupe.find_all("table"):
        entetes = [th.get_text(strip=True) for th in table.select("thead th")]
        if any("Symbole" in e for e in entetes):
            table_cible = table
            break
    if table_cible is None:
        return []

    lignes = []
    for tr in table_cible.select("tbody tr"):
        cellules = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cellules) < 7:
            continue
        code, _nom, volume, cloture_veille, ouverture, cloture, variation = cellules[:7]
        lignes.append({
            "code": code,
            "ouverture": nettoyer_nombre(ouverture),
            "cloture": nettoyer_nombre(cloture),
            "cloture_veille": nettoyer_nombre(cloture_veille),
            "volume_titres": nettoyer_nombre(volume, entier=True),
            "variation": nettoyer_nombre(variation),
        })
    return lignes


def fusionner_dans_bigquery(client, jeu_de_donnees, lignes):
    table = f"{jeu_de_donnees}.stg_cours_journaliers_brvm_scrape"
    table_temporaire = f"{jeu_de_donnees}.stg_cours_journaliers_brvm_scrape_tmp_{uuid.uuid4().hex[:8]}"

    schema = [
        bigquery.SchemaField("code", "STRING"),
        bigquery.SchemaField("date", "DATE"),
        bigquery.SchemaField("ouverture", "NUMERIC"),
        bigquery.SchemaField("cloture", "NUMERIC"),
        bigquery.SchemaField("cloture_veille", "NUMERIC"),
        bigquery.SchemaField("volume_titres", "INTEGER"),
        bigquery.SchemaField("variation", "NUMERIC"),
    ]
    config_chargement = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(lignes, table_temporaire, job_config=config_chargement).result()

    try:
        client.query(REQUETE_FUSION.format(table=table, table_temporaire=table_temporaire)).result()
    finally:
        client.delete_table(table_temporaire, not_found_ok=True)


def main():
    jeu_de_donnees = os.environ.get("BQ_DATASET", "brvm")
    lignes = recuperer_cotations()
    if not lignes:
        print("Aucune cotation recuperee sur brvm.org.", file=sys.stderr)
        sys.exit(1)

    aujourdhui = date.today().isoformat()
    for ligne in lignes:
        ligne["date"] = aujourdhui

    client = bigquery.Client(project=os.environ.get("GCP_PROJECT"))
    fusionner_dans_bigquery(client, jeu_de_donnees, lignes)
    print(f"{len(lignes)} cotations synchronisees dans {jeu_de_donnees}.stg_cours_journaliers_brvm_scrape.")


if __name__ == "__main__":
    main()
