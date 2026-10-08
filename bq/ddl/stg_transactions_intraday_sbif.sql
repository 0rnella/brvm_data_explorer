-- Source : SBIF Trade, transactions de la séance (MarketDetails.aspx?mode=4),
-- une ligne par transaction, toutes actions. Deux collectes par séance : le
-- soir (provisoire) puis le lendemain matin, qui remplace le soir et fait foi.
-- Chaque collecte remplace les lignes (date_seance, code) des actions lues.

CREATE TABLE IF NOT EXISTS `brvm.stg_transactions_intraday_sbif` (
  code             STRING    NOT NULL,  -- référence dim_instruments.code
  date_seance      DATE      NOT NULL,
  horodatage       DATETIME  NOT NULL,  -- heure du serveur SBIF (GMT)
  quantite         INT64     NOT NULL,
  cours            NUMERIC   NOT NULL,
  montant          NUMERIC   NOT NULL,  -- quantite × cours
  cumul_sbif       INT64     NOT NULL,  -- cumul de quantité affiché par SBIF ; change entre soir et lendemain
  sequence         INT64     NOT NULL,  -- rang chronologique dans la séance pour le code, 1 = première
  source_collecte  STRING    NOT NULL,  -- 'soir' | 'lendemain'
  coherente        BOOL      NOT NULL,  -- résultat des contrôles de l'action pour cette collecte
  maj_le           TIMESTAMP NOT NULL
)
PARTITION BY DATE_TRUNC(date_seance, YEAR)
CLUSTER BY code;
