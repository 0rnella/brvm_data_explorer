-- Source : brvm.org, page officielle des cours (/fr/cours-actions/0).
-- Rendue côté serveur, aucune API découverte. Seulement l'instantané du
-- jour courant, pas d'historique interrogeable — alimentée jour après
-- jour, en avançant. Pas de Haut/Bas ni de volume en XOF sur cette page.

CREATE TABLE IF NOT EXISTS `brvm.stg_cours_journaliers_brvm_scrape` (
  code            STRING    NOT NULL,  -- Symbole
  date            DATE      NOT NULL,
  ouverture       NUMERIC,             -- Cours Ouverture
  cloture         NUMERIC,             -- Cours Clôture
  cloture_veille  NUMERIC,             -- Cours veille (clôture de la séance précédente)
  volume_titres   INT64,
  variation       NUMERIC,             -- en points de pourcentage
  maj_le          TIMESTAMP NOT NULL,
  PRIMARY KEY (date, code) NOT ENFORCED
)
PARTITION BY DATE_TRUNC(date, YEAR)
CLUSTER BY code;
