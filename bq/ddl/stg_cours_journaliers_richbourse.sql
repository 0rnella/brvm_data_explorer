-- Source : richbourse.com, page historique par instrument
-- (/common/variation/historique/<code>/jour/0/<date>). Contrairement à
-- sikafinance, la plage de dates y est réellement navigable (vérifié
-- jusqu'en 2020) et "Valeur (FCFA)" a l'air d'être une vraie valeur de
-- transaction, pas une approximation (recoupé avec les Haut/Bas
-- sikafinance). Pas de Haut/Bas ici en revanche — cette source ne donne
-- que des cours de clôture.

CREATE TABLE IF NOT EXISTS `brvm.stg_cours_journaliers_richbourse` (
  code                  STRING    NOT NULL,  -- référence dim_instruments.code
  date                  DATE      NOT NULL,
  cloture_normale       NUMERIC,
  cloture_ajustee       NUMERIC,  -- ajustée des opérations sur titres (splits, dividendes, ...)
  volume_titres_normal  INT64,
  volume_titres_ajuste  INT64,
  volume_xof            INT64,
  variation             NUMERIC,  -- en points de pourcentage, vs la séance précédente
  maj_le                TIMESTAMP NOT NULL,
  PRIMARY KEY (date, code) NOT ENFORCED
)
PARTITION BY DATE_TRUNC(date, YEAR)
CLUSTER BY code;
