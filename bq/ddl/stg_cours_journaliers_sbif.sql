-- Source : SBIF Trade (SGI), tableau des actions (MarketDetails.aspx?mode=3)
-- relu le lendemain matin, une fois la séance rechargée par SBIF. Le
-- 07/10/2026 : identique à brvm.org (ouverture, clôture, volume) pour les
-- 48 actions. Pas de cours veille ni de variation : au rechargement, SBIF y
-- met déjà la clôture de la séance relue. Pas de haut / bas : ceux de SBIF
-- incluent d'autres prix que les transactions.

CREATE TABLE IF NOT EXISTS `brvm.stg_cours_journaliers_sbif` (
  code           STRING    NOT NULL,  -- référence dim_instruments.code
  date           DATE      NOT NULL,
  ouverture      NUMERIC,
  cloture        NUMERIC,
  volume_titres  INT64,
  volume_xof     NUMERIC,
  cmp            NUMERIC,             -- cours moyen pondéré
  maj_le         TIMESTAMP NOT NULL,
  PRIMARY KEY (date, code) NOT ENFORCED
)
PARTITION BY DATE_TRUNC(date, YEAR)
CLUSTER BY code;
