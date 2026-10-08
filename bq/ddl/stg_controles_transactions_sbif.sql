-- Contrôles des collectes de transactions SBIF : une ligne par action, par
-- séance et par collecte ('soir' | 'lendemain'). Une action suspendue ou sans
-- échange y figure même sans aucune transaction.
-- coherente = tous les contrôles passent : cumul enchaîné sans trou, somme
-- des quantités = cumul échangé du tableau, dernier cours, date, doublons.

CREATE TABLE IF NOT EXISTS `brvm.stg_controles_transactions_sbif` (
  code                    STRING    NOT NULL,
  date_seance             DATE      NOT NULL,
  source_collecte         STRING    NOT NULL,
  statut                  STRING,              -- 'actif' | 'suspendu'
  nombre_transactions     INT64,
  premiere_transaction    DATETIME,            -- une heure tardive trahit un début de séance manquant
  somme_quantites         INT64,
  cumul_echange           INT64,
  controle_cumul          BOOL,
  controle_somme          BOOL,
  controle_dernier_cours  BOOL,
  controle_date           BOOL,
  controle_doublons       BOOL,
  coherente               BOOL      NOT NULL,
  ecarts                  STRING,
  informations            STRING,              -- non bloquant : écarts entre cours min / max et transactions
  maj_le                  TIMESTAMP NOT NULL,
  PRIMARY KEY (date_seance, code, source_collecte) NOT ENFORCED
)
PARTITION BY DATE_TRUNC(date_seance, YEAR)
CLUSTER BY code;
