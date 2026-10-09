import argparse
import os
import re
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone

import requests
from google.cloud import bigquery

# API interne de la plateforme de la SGI SBIF Trade (lecture seule, sans
# connexion). Les transactions de la séance d'une action sont servies par
# mode=4 ; le tableau des actions (mode=3) sert de référence pour les contrôles.
#
# SBIF ne sert que la séance en cours, et la recharge pendant la nuit avec
# d'éventuelles corrections. D'où deux collectes :
#   soir       séance du jour, après la clôture : provisoire ;
#   lendemain  séance de la veille, que SBIF sert encore le matin avant de
#              passer à la séance suivante : fait foi, remplace le soir et
#              alimente stg_cours_journaliers_sbif.
URL_SBIF = "https://www.sbiftrade.bf/SBIFTradeServer/MarketDetails.aspx"
EN_TETE_NAVIGATEUR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0",
    "Referer": "https://www.sbiftrade.bf/sbiftrade/",
}
GROUPE_CONTINU = "4"
PAUSE_ENTRE_ACTIONS_S = 3

# Pas d'identifiant de transaction, et le cumul SBIF d'une même transaction
# change au rechargement : chaque collecte remplace donc les transactions de
# la séance pour les actions lues. Le soir ne remplace jamais le lendemain.
REQUETE_REMPLACEMENT = """
BEGIN TRANSACTION;
DELETE FROM `{table}`
WHERE date_seance = @date_seance AND code IN UNNEST(@codes)
  AND (@source_collecte = 'lendemain' OR source_collecte = 'soir');
INSERT INTO `{table}` (code, date_seance, horodatage, quantite, cours, montant, cumul_sbif, sequence,
                       source_collecte, coherente, maj_le)
SELECT S.code, S.date_seance, S.horodatage, S.quantite, S.cours, S.montant, S.cumul_sbif, S.sequence,
       S.source_collecte, S.coherente, CURRENT_TIMESTAMP()
FROM `{table_temporaire}` S
WHERE @source_collecte = 'lendemain' OR NOT EXISTS (
  SELECT 1 FROM `{table}` T
  WHERE T.date_seance = S.date_seance AND T.code = S.code AND T.source_collecte = 'lendemain');
COMMIT TRANSACTION;
"""

REQUETE_FUSION_CONTROLES = """
MERGE `{table}` T
USING `{table_temporaire}` S
ON T.date_seance = S.date_seance AND T.code = S.code AND T.source_collecte = S.source_collecte
WHEN MATCHED THEN
  UPDATE SET statut = S.statut, nombre_transactions = S.nombre_transactions,
             premiere_transaction = S.premiere_transaction, somme_quantites = S.somme_quantites,
             cumul_echange = S.cumul_echange, controle_cumul = S.controle_cumul,
             controle_somme = S.controle_somme, controle_dernier_cours = S.controle_dernier_cours,
             controle_haut_bas = S.controle_haut_bas, controle_date = S.controle_date,
             controle_doublons = S.controle_doublons, coherente = S.coherente, ecarts = S.ecarts,
             maj_le = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN
  INSERT (code, date_seance, source_collecte, statut, nombre_transactions, premiere_transaction, somme_quantites,
          cumul_echange, controle_cumul, controle_somme, controle_dernier_cours, controle_haut_bas, controle_date,
          controle_doublons, coherente, ecarts, maj_le)
  VALUES (S.code, S.date_seance, S.source_collecte, S.statut, S.nombre_transactions, S.premiere_transaction,
          S.somme_quantites, S.cumul_echange, S.controle_cumul, S.controle_somme, S.controle_dernier_cours,
          S.controle_haut_bas, S.controle_date, S.controle_doublons, S.coherente, S.ecarts, CURRENT_TIMESTAMP())
"""

REQUETE_FUSION_COURS = """
MERGE `{table}` T
USING `{table_temporaire}` S
ON T.date = S.date AND T.code = S.code
WHEN MATCHED THEN
  UPDATE SET ouverture = S.ouverture, haut = S.haut, bas = S.bas, cloture = S.cloture,
             volume_titres = S.volume_titres, volume_xof = S.volume_xof, cmp = S.cmp, maj_le = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN
  INSERT (code, date, ouverture, haut, bas, cloture, volume_titres, volume_xof, cmp, maj_le)
  VALUES (S.code, S.date, S.ouverture, S.haut, S.bas, S.cloture, S.volume_titres, S.volume_xof, S.cmp,
          CURRENT_TIMESTAMP())
"""

SCHEMA_TRANSACTIONS = [
    bigquery.SchemaField("code", "STRING"),
    bigquery.SchemaField("date_seance", "DATE"),
    bigquery.SchemaField("horodatage", "DATETIME"),
    bigquery.SchemaField("quantite", "INTEGER"),
    bigquery.SchemaField("cours", "NUMERIC"),
    bigquery.SchemaField("montant", "NUMERIC"),
    bigquery.SchemaField("cumul_sbif", "INTEGER"),
    bigquery.SchemaField("sequence", "INTEGER"),
    bigquery.SchemaField("source_collecte", "STRING"),
    bigquery.SchemaField("coherente", "BOOLEAN"),
]
SCHEMA_CONTROLES = [
    bigquery.SchemaField("code", "STRING"),
    bigquery.SchemaField("date_seance", "DATE"),
    bigquery.SchemaField("source_collecte", "STRING"),
    bigquery.SchemaField("statut", "STRING"),
    bigquery.SchemaField("nombre_transactions", "INTEGER"),
    bigquery.SchemaField("premiere_transaction", "DATETIME"),
    bigquery.SchemaField("somme_quantites", "INTEGER"),
    bigquery.SchemaField("cumul_echange", "INTEGER"),
    bigquery.SchemaField("controle_cumul", "BOOLEAN"),
    bigquery.SchemaField("controle_somme", "BOOLEAN"),
    bigquery.SchemaField("controle_dernier_cours", "BOOLEAN"),
    bigquery.SchemaField("controle_haut_bas", "BOOLEAN"),
    bigquery.SchemaField("controle_date", "BOOLEAN"),
    bigquery.SchemaField("controle_doublons", "BOOLEAN"),
    bigquery.SchemaField("coherente", "BOOLEAN"),
    bigquery.SchemaField("ecarts", "STRING"),
]
SCHEMA_COURS = [
    bigquery.SchemaField("code", "STRING"),
    bigquery.SchemaField("date", "DATE"),
    bigquery.SchemaField("ouverture", "NUMERIC"),
    bigquery.SchemaField("haut", "NUMERIC"),
    bigquery.SchemaField("bas", "NUMERIC"),
    bigquery.SchemaField("cloture", "NUMERIC"),
    bigquery.SchemaField("volume_titres", "INTEGER"),
    bigquery.SchemaField("volume_xof", "NUMERIC"),
    bigquery.SchemaField("cmp", "NUMERIC"),
]


def interroger_racine(parametres, type_attendu):
    reponse = requests.post(
        URL_SBIF,
        params={**parametres, "seq_cache": int(time.time() * 1000)},
        headers=EN_TETE_NAVIGATEUR,
        timeout=60,
    )
    reponse.raise_for_status()
    # Réponse précédée d'un BOM UTF-8, sans jeu de caractères annoncé.
    racine = ET.fromstring(reponse.content.decode("utf-8-sig").strip())
    if (racine.findtext("TYPE") or "").strip() != type_attendu:
        raise ValueError(f"réponse de type {racine.findtext('TYPE')!r} au lieu de {type_attendu!r}")
    return racine


def interroger(parametres, type_attendu):
    racine = interroger_racine(parametres, type_attendu)
    return [(paquet.text or "").replace("\n", "").strip() for paquet in racine.iter("PAC_DET")]


def marche_annonce_ferme():
    champs = (interroger_racine({"mode": "1"}, "MKT_DATA").findtext("PACQ") or "").strip().split("|")
    return champs[-1].strip().upper() == "F"


def nettoyer_nombre(texte, entier=False):
    texte = (texte or "").strip()
    if texte == "":
        return None
    valeur = float(texte)
    return int(valeur) if entier else round(valeur, 6)


def nettoyer_cours(texte):
    valeur = nettoyer_nombre(texte)
    return None if not valeur else valeur


def nettoyer_horodatage(texte):
    texte = (texte or "").strip()
    if texte in ("", "0"):
        return None
    return datetime.strptime(texte, "%d/%m/%Y %H:%M:%S")


def recuperer_tableau():
    actions, sequence, heure_serveur = [], "0", None
    for paquet in interroger({"mode": "3"}, "MKT"):
        if paquet.startswith("~finresm/"):
            sequence = paquet[len("~finresm/"):]
        elif paquet.startswith("~CT/"):
            heure_serveur = nettoyer_horodatage(paquet[len("~CT/"):])
        elif paquet.startswith("~Re/!"):
            v = paquet[len("~Re/!"):].split("|")
            if len(v) == 52 and v[2].strip() == GROUPE_CONTINU:
                actions.append({
                    "code": v[0].strip(),
                    "statut": "suspendu" if v[50].strip() == "0" else "actif",
                    "ouverture": nettoyer_cours(v[14]),
                    "dernier": nettoyer_cours(v[6]),
                    "cours_min": nettoyer_cours(v[4]),
                    "cours_max": nettoyer_cours(v[5]),
                    "cumul_echange": nettoyer_nombre(v[8], entier=True) or 0,
                    "volume_xof": nettoyer_nombre(v[45]),
                    "cmp": nettoyer_cours(v[46]),
                })
    if heure_serveur is None:
        raise ValueError("heure du serveur (~CT/) absente de la réponse")
    return actions, sequence, heure_serveur


def recuperer_transactions(code, sequence):
    transactions = []
    for paquet in interroger({"mode": "4", "seq": sequence, "m_seq": "0", "list_exec": f"{code},0|"}, "MKT_MAJ"):
        for texte in re.split(r"~(?:Ex|CE)/", paquet)[1:]:
            champs = [champ.strip() for champ in texte.split("|")]
            if champs[0] != code or set(champs[2:]) == {"--"}:
                continue
            transactions.append({
                "horodatage": nettoyer_horodatage(champs[2]),
                "quantite": nettoyer_nombre(champs[3], entier=True),
                "cours": nettoyer_cours(champs[4]),
                "cumul_sbif": nettoyer_nombre(champs[5], entier=True),
                "sequence_sbif": int(re.match(r"\d+", champs[1]).group()),
            })
    return sorted(transactions, key=lambda t: (t["horodatage"], t["sequence_sbif"]))


def controler(transactions, action, date_seance):
    ecarts = {"cumul": [], "somme": [], "dernier_cours": [], "haut_bas": [], "date": [], "doublons": []}
    precedent = 0
    for transaction in transactions:
        if transaction["cumul_sbif"] != precedent + transaction["quantite"]:
            manque = transaction["cumul_sbif"] - precedent - transaction["quantite"]
            ecarts["cumul"].append(f"{manque:+d} titres avant {transaction['horodatage']:%H:%M:%S}")
        precedent = transaction["cumul_sbif"]
    somme = sum(t["quantite"] for t in transactions)
    if somme != action["cumul_echange"]:
        ecarts["somme"].append(f"somme {somme} ≠ cumul échangé {action['cumul_echange']}")
    if transactions and transactions[-1]["cours"] != action["dernier"]:
        ecarts["dernier_cours"].append(f"dernière transaction {transactions[-1]['cours']} ≠ dernier {action['dernier']}")
    if transactions:
        observes = (max(t["cours"] for t in transactions), min(t["cours"] for t in transactions))
        if observes != (action["cours_max"], action["cours_min"]):
            ecarts["haut_bas"].append(f"transactions {observes[0]}/{observes[1]} ≠ tableau "
                                      f"{action['cours_max']}/{action['cours_min']}")
    autres_jours = sorted({t["horodatage"].date() for t in transactions} - {date_seance})
    ecarts["date"] += [f"transactions du {jour}" for jour in autres_jours]
    occurrences = Counter((t["horodatage"], t["quantite"], t["cours"], t["cumul_sbif"]) for t in transactions)
    ecarts["doublons"] += [f"{h:%H:%M:%S} × {n}" for (h, _, _, _), n in occurrences.items() if n > 1]
    return ecarts


def charger(client, table, schema, lignes, requete, parametres=None):
    table_temporaire = f"{table}_tmp_{uuid.uuid4().hex[:8]}"
    config_chargement = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(lignes, table_temporaire, job_config=config_chargement).result()
    try:
        config_requete = bigquery.QueryJobConfig(query_parameters=parametres or [])
        client.query(requete.format(table=table, table_temporaire=table_temporaire), job_config=config_requete).result()
    finally:
        client.delete_table(table_temporaire, not_found_ok=True)


def analyser_arguments():
    analyseur = argparse.ArgumentParser()
    analyseur.add_argument("--collecte", choices=("soir", "lendemain"), required=True,
                           help="soir : séance du jour, provisoire ; lendemain : séance précédente, qui fait foi")
    return analyseur.parse_args()


def main():
    jeu_de_donnees = os.environ.get("BQ_DATASET", "brvm")
    arguments = analyser_arguments()
    aujourdhui = datetime.now(timezone.utc).date()

    actions, sequence, heure_serveur = recuperer_tableau()
    date_seance = heure_serveur.date()
    if arguments.collecte == "soir" and date_seance != aujourdhui:
        print(f"SBIF sert la séance du {date_seance} : pas de séance aujourd'hui, rien à collecter.")
        return
    if arguments.collecte == "lendemain" and (date_seance >= aujourdhui or not marche_annonce_ferme()):
        print(f"ALERTE : SBIF sert la séance du {date_seance} avec un marché non fermé ; la relecture de la veille "
              f"n'est plus possible.", file=sys.stderr)
        sys.exit(1)

    transactions, controles, echecs, incoherentes = [], [], [], []
    for position, action in enumerate(actions, start=1):
        code = action["code"]
        if position > 1:
            time.sleep(PAUSE_ENTRE_ACTIONS_S)
        try:
            lues = recuperer_transactions(code, sequence)
        except (requests.RequestException, ValueError) as erreur:
            echecs.append(code)
            print(f"[{position}/{len(actions)}] {code} : échec ({erreur})", file=sys.stderr)
            continue

        ecarts = controler(lues, action, date_seance)
        coherente = not any(ecarts.values())
        if not coherente:
            incoherentes.append(code)
        for rang, transaction in enumerate(lues, start=1):
            transactions.append({
                "code": code,
                "date_seance": date_seance.isoformat(),
                "horodatage": transaction["horodatage"].isoformat(),
                "quantite": transaction["quantite"],
                "cours": transaction["cours"],
                "montant": round(transaction["cours"] * transaction["quantite"], 6),
                "cumul_sbif": transaction["cumul_sbif"],
                "sequence": rang,
                "source_collecte": arguments.collecte,
                "coherente": coherente,
            })
        controles.append({
            "code": code,
            "date_seance": date_seance.isoformat(),
            "source_collecte": arguments.collecte,
            "statut": action["statut"],
            "nombre_transactions": len(lues),
            "premiere_transaction": lues[0]["horodatage"].isoformat() if lues else None,
            "somme_quantites": sum(t["quantite"] for t in lues),
            "cumul_echange": action["cumul_echange"],
            "controle_cumul": not ecarts["cumul"],
            "controle_somme": not ecarts["somme"],
            "controle_dernier_cours": not ecarts["dernier_cours"],
            "controle_haut_bas": not ecarts["haut_bas"],
            "controle_date": not ecarts["date"],
            "controle_doublons": not ecarts["doublons"],
            "coherente": coherente,
            "ecarts": " | ".join(f"{nom} : {e}" for nom, liste in ecarts.items() for e in liste) or None,
        })
        print(f"[{position}/{len(actions)}] {code} : {len(lues)} transactions{'' if coherente else ' — incohérente'}")

    client = bigquery.Client(project=os.environ.get("GCP_PROJECT"))
    codes_lus = [c["code"] for c in controles]
    if transactions:
        charger(client, f"{jeu_de_donnees}.stg_transactions_sbif", SCHEMA_TRANSACTIONS, transactions,
                REQUETE_REMPLACEMENT, [
                    bigquery.ScalarQueryParameter("date_seance", "DATE", date_seance),
                    bigquery.ArrayQueryParameter("codes", "STRING", codes_lus),
                    bigquery.ScalarQueryParameter("source_collecte", "STRING", arguments.collecte),
                ])
    if controles:
        charger(client, f"{jeu_de_donnees}.stg_controles_transactions_sbif", SCHEMA_CONTROLES, controles,
                REQUETE_FUSION_CONTROLES)
    if arguments.collecte == "lendemain":
        cours = [{"code": a["code"], "date": date_seance.isoformat(), "ouverture": a["ouverture"],
                  "haut": a["cours_max"], "bas": a["cours_min"], "cloture": a["dernier"], "volume_titres": a["cumul_echange"], "volume_xof": a["volume_xof"],
                  "cmp": a["cmp"]} for a in actions]
        charger(client, f"{jeu_de_donnees}.stg_cours_journaliers_sbif", SCHEMA_COURS, cours, REQUETE_FUSION_COURS)

    print(f"Séance du {date_seance} ({arguments.collecte}) : {len(transactions)} transactions, "
          f"{len(controles)} actions lues, {len(incoherentes)} incohérente(s), {len(echecs)} échec(s) de lecture.")
    if incoherentes and arguments.collecte == "lendemain":
        print(f"ALERTE : actions incohérentes dans la collecte du lendemain : {', '.join(incoherentes)}", file=sys.stderr)
    if echecs:
        print(f"ALERTE : lecture impossible pour {', '.join(echecs)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
