import argparse
import os
import sys
import time
import uuid
from datetime import date, datetime, timedelta

import requests
from bs4 import BeautifulSoup
from google.cloud import bigquery

# Page HTML rendue côté serveur, par instrument. Contrairement à
# sikafinance, une vraie plage de dates est respectée pour de petites
# fenêtres, et un rattrapage complet est possible via pagination (?page=N)
# quand aucune date de début n'est fournie.
URL_BASE = "https://www.richbourse.com/common/variation/historique"
EN_TETE_NAVIGATEUR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0",
}
TAILLE_PAGE = 20

REQUETE_FUSION = """
MERGE `{table}` T
USING `{table_temporaire}` S
ON T.date = S.date AND T.code = S.code
WHEN MATCHED THEN
  UPDATE SET cloture_normale = S.cloture_normale, cloture_ajustee = S.cloture_ajustee,
             volume_titres_normal = S.volume_titres_normal, volume_titres_ajuste = S.volume_titres_ajuste,
             volume_xof = S.volume_xof, variation = S.variation, maj_le = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN
  INSERT (code, date, cloture_normale, cloture_ajustee, volume_titres_normal, volume_titres_ajuste, volume_xof, variation, maj_le)
  VALUES (S.code, S.date, S.cloture_normale, S.cloture_ajustee, S.volume_titres_normal, S.volume_titres_ajuste, S.volume_xof, S.variation, CURRENT_TIMESTAMP())
"""


def nettoyer_nombre(texte, entier=False):
    texte = texte.replace("\xa0", "").replace(" ", "").replace("%", "").strip()
    if texte in ("", "-"):
        return None
    valeur = float(texte)
    return int(valeur) if entier else round(valeur, 6)


def analyser_lignes(html):
    soupe = BeautifulSoup(html, "html.parser")
    tbody = soupe.find("tbody")
    if tbody is None:
        return []

    lignes = []
    for tr in tbody.find_all("tr"):
        cellules = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cellules) < 7:
            continue
        date_str, variation, valeur_xof, cloture_ajustee, volume_ajuste, cloture_normale, volume_normal = cellules[:7]
        lignes.append({
            "date": datetime.strptime(date_str, "%d/%m/%Y").date().isoformat(),
            "cloture_normale": nettoyer_nombre(cloture_normale),
            "cloture_ajustee": nettoyer_nombre(cloture_ajustee),
            "volume_titres_normal": nettoyer_nombre(volume_normal, entier=True),
            "volume_titres_ajuste": nettoyer_nombre(volume_ajuste, entier=True),
            "volume_xof": nettoyer_nombre(valeur_xof, entier=True),
            "variation": nettoyer_nombre(variation),
        })
    return lignes


def recuperer(code, segment, page=None):
    url = f"{URL_BASE}/{code}/jour/{segment}"
    parametres = {"page": page} if page else None
    reponse = requests.get(url, headers=EN_TETE_NAVIGATEUR, params=parametres, timeout=30)
    reponse.raise_for_status()
    return analyser_lignes(reponse.text)


def recuperer_plage(code, date_debut, date_fin):
    segment = f"{date_debut.strftime('%d-%m-%Y')}/{date_fin.strftime('%d-%m-%Y')}"
    return recuperer(code, segment)


def recuperer_historique_complet(code, date_fin, date_debut=None):
    borne = date_debut.isoformat() if date_debut else None
    segment = f"0/{date_fin.strftime('%d-%m-%Y')}"

    lignes = []
    page = 1
    while True:
        page_lignes = recuperer(code, segment, page=page)
        if not page_lignes:
            break
        lignes.extend(page_lignes)
        if borne and any(l["date"] <= borne for l in page_lignes):
            break
        if len(page_lignes) < TAILLE_PAGE:
            break
        page += 1
        time.sleep(0.15)

    if borne:
        lignes = [l for l in lignes if l["date"] >= borne]
    return lignes


def lister_instruments_actifs(client, jeu_de_donnees):
    requete = f"""
        SELECT code
        FROM `{jeu_de_donnees}.dim_instruments`
        WHERE date_radiation IS NULL AND type = 'action'
    """
    return list(client.query(requete).result())


def fusionner_dans_bigquery(client, jeu_de_donnees, lignes):
    table = f"{jeu_de_donnees}.stg_cours_journaliers_richbourse"
    table_temporaire = f"{jeu_de_donnees}.stg_cours_journaliers_richbourse_tmp_{uuid.uuid4().hex[:8]}"

    schema = [
        bigquery.SchemaField("code", "STRING"),
        bigquery.SchemaField("date", "DATE"),
        bigquery.SchemaField("cloture_normale", "NUMERIC"),
        bigquery.SchemaField("cloture_ajustee", "NUMERIC"),
        bigquery.SchemaField("volume_titres_normal", "INTEGER"),
        bigquery.SchemaField("volume_titres_ajuste", "INTEGER"),
        bigquery.SchemaField("volume_xof", "INTEGER"),
        bigquery.SchemaField("variation", "NUMERIC"),
    ]
    config_chargement = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(lignes, table_temporaire, job_config=config_chargement).result()

    try:
        client.query(REQUETE_FUSION.format(table=table, table_temporaire=table_temporaire)).result()
    finally:
        client.delete_table(table_temporaire, not_found_ok=True)


def analyser_arguments():
    analyseur = argparse.ArgumentParser()
    analyseur.add_argument("--date-debut", help="YYYY-MM-DD, optionnel, borne basse pour un rattrapage")
    analyseur.add_argument("--date-fin", help="YYYY-MM-DD ; si fourni sans --date-debut, rattrapage complet jusqu'au début de l'historique")
    return analyseur.parse_args()


def main():
    jeu_de_donnees = os.environ.get("BQ_DATASET", "brvm")
    arguments = analyser_arguments()

    if arguments.date_fin:
        date_fin = date.fromisoformat(arguments.date_fin)
        date_debut = date.fromisoformat(arguments.date_debut) if arguments.date_debut else None
        mode_rattrapage = True
    else:
        date_fin = date.today()
        date_debut = date_fin - timedelta(days=7)
        mode_rattrapage = False

    client = bigquery.Client(project=os.environ.get("GCP_PROJECT"))
    instruments = lister_instruments_actifs(client, jeu_de_donnees)

    lignes = []
    echecs = []
    for position, instrument in enumerate(instruments, start=1):
        code = instrument["code"]
        try:
            if mode_rattrapage:
                cours = recuperer_historique_complet(code, date_fin, date_debut)
            else:
                cours = recuperer_plage(code, date_debut, date_fin)
        except requests.RequestException as erreur:
            echecs.append(code)
            print(f"[{position}/{len(instruments)}] {code} : echec ({erreur})", file=sys.stderr)
            continue

        for ligne in cours:
            ligne["code"] = code
            lignes.append(ligne)
        print(f"[{position}/{len(instruments)}] {code} : {len(cours)} lignes")
        time.sleep(0.15)

    if lignes:
        fusionner_dans_bigquery(client, jeu_de_donnees, lignes)
    print(f"{len(lignes)} lignes synchronisees ({len(instruments)} instruments interrogés, {len(echecs)} échecs).")

    if echecs:
        sys.exit(1)


if __name__ == "__main__":
    main()
