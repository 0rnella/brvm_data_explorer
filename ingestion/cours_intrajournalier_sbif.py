import argparse
import json
import os
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, time as heure, timezone

import requests
from google.cloud import bigquery

# API interne de la plateforme de la SGI SBIF Trade, utilisée par sa propre
# page « Résumé du Marché ». Elle répond sans session : aucune connexion,
# aucun identifiant, lecture seule. Une photo du tableau des actions
# (cotation en continu) à chaque passage, pour suivre la séance en direct.
URL_SBIF = "https://www.sbiftrade.bf/SBIFTradeServer/MarketDetails.aspx"
EN_TETE_NAVIGATEUR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0",
    "Referer": "https://www.sbiftrade.bf/sbiftrade/",
}
GROUPE_CONTINU = "4"
NOMBRE_CHAMPS = 52
ETATS_MARCHE = {"P": "preouverture", "O": "ouvert", "F": "ferme"}

# Séance continue de la BRVM, en GMT. Le planificateur lance toutes les
# 5 minutes de 9 h à 15 h ; hors de cette fenêtre le script ne fait rien.
DEBUT_SEANCE = heure(9, 45)
FIN_SEANCE = heure(15, 0)
# Le 07/10/2026, SBIF a annoncé « fermé » toute la séance et n'a servi les
# transactions d'avant 10:37 que le lendemain : on alerte si cela se reproduit.
OUVERTURE_ATTENDUE_AVANT = heure(9, 50)

REQUETE_FUSION = """
MERGE `{table}` T
USING `{table_temporaire}` S
ON T.date_seance = S.date_seance AND T.capture_le = S.capture_le AND T.code = S.code
WHEN NOT MATCHED THEN
  INSERT (code, date_seance, capture_le, rang, isin, titre, statut, etat_marche_sbif, derniere_execution,
          quantite_achat, cours_achat, cours_vente, quantite_vente, dernier, variation_pct, cumul_echange,
          cours_veille, cours_min, cours_max, ouverture, seuil_bas, seuil_haut, volume_xof, cmp, limites, maj_le)
  VALUES (S.code, S.date_seance, S.capture_le, S.rang, S.isin, S.titre, S.statut, S.etat_marche_sbif,
          S.derniere_execution, S.quantite_achat, S.cours_achat, S.cours_vente, S.quantite_vente, S.dernier,
          S.variation_pct, S.cumul_echange, S.cours_veille, S.cours_min, S.cours_max, S.ouverture, S.seuil_bas,
          S.seuil_haut, S.volume_xof, S.cmp, S.limites, CURRENT_TIMESTAMP())
"""


def interroger(mode, type_attendu):
    reponse = requests.post(
        URL_SBIF,
        params={"mode": mode, "seq_cache": int(time.time() * 1000)},
        headers=EN_TETE_NAVIGATEUR,
        timeout=60,
    )
    reponse.raise_for_status()
    # Réponse précédée d'un BOM UTF-8, sans jeu de caractères annoncé.
    racine = ET.fromstring(reponse.content.decode("utf-8-sig").strip())
    if (racine.findtext("TYPE") or "").strip() != type_attendu:
        raise ValueError(f"réponse de type {racine.findtext('TYPE')!r} au lieu de {type_attendu!r}")
    return racine


def nettoyer_nombre(texte, entier=False):
    texte = (texte or "").strip()
    if texte == "":
        return None
    valeur = float(texte)
    return int(valeur) if entier else round(valeur, 6)


def nettoyer_cours(texte):
    try:
        valeur = nettoyer_nombre(texte)
    except ValueError:
        return None
    return None if not valeur else valeur


def nettoyer_horodatage(texte):
    texte = (texte or "").strip()
    if texte in ("", "0"):
        return None
    return datetime.strptime(texte, "%d/%m/%Y %H:%M:%S")


def recuperer_etat_marche():
    champs = (interroger("1", "MKT_DATA").findtext("PACQ") or "").strip().split("|")
    return ETATS_MARCHE.get(champs[-1].strip().upper(), champs[-1].strip())


def recuperer_tableau():
    lignes, anomalies, heure_serveur = [], [], None
    for paquet in interroger("3", "MKT").iter("PAC_DET"):
        texte = (paquet.text or "").replace("\n", "").strip()
        if texte.startswith("~CT/"):
            heure_serveur = nettoyer_horodatage(texte[len("~CT/"):])
        if not texte.startswith("~Re/!"):
            continue
        v = texte[len("~Re/!"):].split("|")
        if len(v) < 3 or v[2].strip() != GROUPE_CONTINU:
            continue
        if len(v) != NOMBRE_CHAMPS:
            anomalies.append(f"{v[0]} : {len(v)} champs au lieu de {NOMBRE_CHAMPS}")
            continue
        limites = [{"quantite_achat": nettoyer_nombre(v[16 + 6 * n], entier=True), "cours_achat": v[17 + 6 * n].strip(),
                    "cours_vente": v[18 + 6 * n].strip(), "quantite_vente": nettoyer_nombre(v[19 + 6 * n], entier=True)}
                   for n in range(5)]
        derniere_execution = nettoyer_horodatage(v[51])
        lignes.append({
            "code": v[0].strip(),
            "rang": len(lignes) + 1,
            "isin": v[1].strip(),
            "titre": v[47].strip(),
            "statut": "suspendu" if v[50].strip() == "0" else "actif",
            "derniere_execution": derniere_execution.isoformat() if derniere_execution else None,
            "quantite_achat": limites[0]["quantite_achat"],
            "cours_achat": nettoyer_cours(v[17]),
            "cours_vente": nettoyer_cours(v[18]),
            "quantite_vente": limites[0]["quantite_vente"],
            "dernier": nettoyer_cours(v[6]),
            "variation_pct": nettoyer_nombre(v[7]),
            "cumul_echange": nettoyer_nombre(v[8], entier=True) or 0,
            "cours_veille": nettoyer_cours(v[3]),
            "cours_min": nettoyer_cours(v[4]),
            "cours_max": nettoyer_cours(v[5]),
            "ouverture": nettoyer_cours(v[14]),
            "seuil_bas": nettoyer_cours(v[9]),
            "seuil_haut": nettoyer_cours(v[10]),
            "volume_xof": nettoyer_nombre(v[45]),
            "cmp": nettoyer_cours(v[46]),
            "limites": json.dumps(limites, ensure_ascii=False),
        })
    if heure_serveur is None:
        raise ValueError("heure du serveur (~CT/) absente de la réponse")
    return lignes, heure_serveur, anomalies


def fusionner_dans_bigquery(client, jeu_de_donnees, lignes):
    table = f"{jeu_de_donnees}.stg_cours_intrajournaliers_sbif"
    table_temporaire = f"{jeu_de_donnees}.stg_cours_intrajournaliers_sbif_tmp_{uuid.uuid4().hex[:8]}"

    schema = [
        bigquery.SchemaField("code", "STRING"),
        bigquery.SchemaField("date_seance", "DATE"),
        bigquery.SchemaField("capture_le", "TIMESTAMP"),
        bigquery.SchemaField("rang", "INTEGER"),
        bigquery.SchemaField("isin", "STRING"),
        bigquery.SchemaField("titre", "STRING"),
        bigquery.SchemaField("statut", "STRING"),
        bigquery.SchemaField("etat_marche_sbif", "STRING"),
        bigquery.SchemaField("derniere_execution", "DATETIME"),
        bigquery.SchemaField("quantite_achat", "INTEGER"),
        bigquery.SchemaField("cours_achat", "NUMERIC"),
        bigquery.SchemaField("cours_vente", "NUMERIC"),
        bigquery.SchemaField("quantite_vente", "INTEGER"),
        bigquery.SchemaField("dernier", "NUMERIC"),
        bigquery.SchemaField("variation_pct", "NUMERIC"),
        bigquery.SchemaField("cumul_echange", "INTEGER"),
        bigquery.SchemaField("cours_veille", "NUMERIC"),
        bigquery.SchemaField("cours_min", "NUMERIC"),
        bigquery.SchemaField("cours_max", "NUMERIC"),
        bigquery.SchemaField("ouverture", "NUMERIC"),
        bigquery.SchemaField("seuil_bas", "NUMERIC"),
        bigquery.SchemaField("seuil_haut", "NUMERIC"),
        bigquery.SchemaField("volume_xof", "NUMERIC"),
        bigquery.SchemaField("cmp", "NUMERIC"),
        bigquery.SchemaField("limites", "STRING"),
    ]
    config_chargement = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(lignes, table_temporaire, job_config=config_chargement).result()

    try:
        client.query(REQUETE_FUSION.format(table=table, table_temporaire=table_temporaire)).result()
    finally:
        client.delete_table(table_temporaire, not_found_ok=True)


def en_seance(moment):
    return moment.weekday() < 5 and DEBUT_SEANCE <= moment.time() <= FIN_SEANCE


def analyser_arguments():
    analyseur = argparse.ArgumentParser()
    analyseur.add_argument("--seulement-en-seance", action="store_true",
                           help="ne rien faire hors 09:45–15:00 GMT du lundi au vendredi (lancements planifiés)")
    return analyseur.parse_args()


def main():
    jeu_de_donnees = os.environ.get("BQ_DATASET", "brvm")
    arguments = analyser_arguments()
    capture_le = datetime.now(timezone.utc).replace(microsecond=0)
    if arguments.seulement_en_seance and not en_seance(capture_le):
        print(f"Hors séance ({capture_le:%H:%M} GMT) : pas de capture.")
        return

    etat_marche = recuperer_etat_marche()
    lignes, heure_serveur, anomalies = recuperer_tableau()
    for ligne in lignes:
        ligne["date_seance"] = heure_serveur.date().isoformat()
        ligne["capture_le"] = capture_le.isoformat()
        ligne["etat_marche_sbif"] = etat_marche

    client = bigquery.Client(project=os.environ.get("GCP_PROJECT"))
    fusionner_dans_bigquery(client, jeu_de_donnees, lignes)
    print(f"{len(lignes)} cours capturés (site « {etat_marche} », heure serveur {heure_serveur:%Y-%m-%d %H:%M:%S}) "
          f"dans {jeu_de_donnees}.stg_cours_intrajournaliers_sbif.")

    if en_seance(capture_le) and capture_le.time() >= OUVERTURE_ATTENDUE_AVANT and etat_marche != "ouvert":
        print(f"ALERTE : SBIF annonce le marché « {etat_marche} » à {capture_le:%H:%M} GMT, en pleine séance.",
              file=sys.stderr)
    for anomalie in anomalies:
        print(f"ALERTE : ligne du tableau illisible — {anomalie}", file=sys.stderr)
    if anomalies:
        sys.exit(1)


if __name__ == "__main__":
    main()
