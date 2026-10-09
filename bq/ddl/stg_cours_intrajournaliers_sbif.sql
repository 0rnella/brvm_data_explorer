-- Source : SBIF Trade (SGI), tableau des actions (MarketDetails.aspx?mode=3) :
-- une ligne par action et par capture, carnet à 5 limites compris.

CREATE TABLE IF NOT EXISTS `brvm.stg_cours_intrajournaliers_sbif` (
  code                STRING    NOT NULL,  -- référence dim_instruments.code
  date_seance         DATE      NOT NULL,  -- séance servie par SBIF au moment de la capture
  capture_le          TIMESTAMP NOT NULL,
  rang                INT64,               -- ordre d'affichage sur le site
  isin                STRING,
  titre               STRING,
  statut              STRING,              -- 'actif' | 'suspendu'
  etat_marche_sbif    STRING,              -- état annoncé par le site : 'preouverture' | 'ouvert' | 'ferme'
  derniere_execution  DATETIME,
  quantite_achat      INT64,
  cours_achat         NUMERIC,
  cours_vente         NUMERIC,
  quantite_vente      INT64,
  dernier             NUMERIC,
  variation_pct       NUMERIC,             -- en points de pourcentage
  cumul_echange       INT64,               -- titres échangés depuis l'ouverture
  cours_veille        NUMERIC,
  cours_min           NUMERIC,
  cours_max           NUMERIC,
  ouverture           NUMERIC,
  seuil_bas           NUMERIC,
  seuil_haut          NUMERIC,
  volume_xof          NUMERIC,
  cmp                 NUMERIC,
  limites             STRING,              -- 5 meilleures limites, en JSON
  maj_le              TIMESTAMP NOT NULL,
  PRIMARY KEY (date_seance, capture_le, code) NOT ENFORCED
)
PARTITION BY DATE_TRUNC(date_seance, YEAR)
CLUSTER BY code;
