-- Source : SBIF Trade (SGI), tableau des actions (MarketDetails.aspx?mode=3)
-- relu le lendemain matin, une fois la séance rechargée par SBIF.
-- Pas de cours veille ni de variation : au rechargement, SBIF y met déjà la clôture de la séance relue.

CREATE TABLE IF NOT EXISTS `brvm.stg_cours_journaliers_sbif` (
  code           STRING    NOT NULL,  -- référence dim_instruments.code
  date           DATE      NOT NULL,
  ouverture      NUMERIC,
  haut           NUMERIC,
  bas            NUMERIC,
  cloture        NUMERIC,
  volume_titres  INT64,
  volume_xof     NUMERIC,
  cmp            NUMERIC,             -- cours moyen pondéré
  maj_le         TIMESTAMP NOT NULL,
  PRIMARY KEY (date, code) NOT ENFORCED
)
PARTITION BY DATE_TRUNC(date, YEAR)
CLUSTER BY code;
