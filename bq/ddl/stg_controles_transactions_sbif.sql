-- Contrôles des collectes de transactions SBIF : une ligne par action, par
-- séance et par collecte, y compris pour une action sans transaction.

CREATE TABLE IF NOT EXISTS `brvm.stg_controles_transactions_sbif` (
  code                    STRING    NOT NULL,
  date_seance             DATE      NOT NULL,
  source_collecte         STRING    NOT NULL,  -- 'soir' | 'lendemain'
  statut                  STRING,              -- 'actif' | 'suspendu'
  nombre_transactions     INT64,
  premiere_transaction    DATETIME,
  somme_quantites         INT64,
  cumul_echange           INT64,
  controle_cumul          BOOL,                -- cumul SBIF enchaîné sans trou
  controle_somme          BOOL,                -- somme des quantités = cumul échangé du tableau
  controle_dernier_cours  BOOL,                -- dernière transaction = dernier cours du tableau
  controle_haut_bas       BOOL,                -- plus haut / plus bas des transactions = cours max / min du tableau
  controle_date           BOOL,
  controle_doublons       BOOL,
  coherente               BOOL      NOT NULL,  -- tous les contrôles passent
  ecarts                  STRING,
  maj_le                  TIMESTAMP NOT NULL,
  PRIMARY KEY (date_seance, code, source_collecte) NOT ENFORCED
)
PARTITION BY DATE_TRUNC(date_seance, YEAR)
CLUSTER BY code;
