CREATE TABLE IF NOT EXISTS `brvm.stg_cours_journaliers_sikafinance` (
  code       STRING    NOT NULL,  -- référence dim_instruments.code
  date       DATE      NOT NULL,
  ouverture  NUMERIC,
  haut       NUMERIC,
  bas        NUMERIC,
  cloture    NUMERIC,
  volume     INT64,
  maj_le     TIMESTAMP NOT NULL,
  PRIMARY KEY (date, code) NOT ENFORCED
)
PARTITION BY date
CLUSTER BY code;
