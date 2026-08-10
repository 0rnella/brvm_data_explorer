-- dim_instruments : liste de référence des instruments BRVM (actions + indices).
-- Source : liste déroulante <select id="dpShares"> de sikafinance.com.
-- Rafraîchie sur son propre cycle (peu fréquent), indépendamment de
-- l'ingestion quotidienne des cours — un instrument change rarement de
-- statut (nouvelle cotation / radiation), donc un échec de ce
-- rafraîchissement est volontairement traité comme peu sévère.
--
-- Le code attendu par sikafinance (ex. "SGBC.ci") n'est pas stocké ici :
-- il se reconstruit à partir de code/code_pays/type au moment de l'appel.

CREATE TABLE IF NOT EXISTS `brvm.dim_instruments` (
  code             STRING    NOT NULL,  -- code court, ex. "SGBC" — clé naturelle utilisée partout ailleurs
  nom              STRING,
  type             STRING    NOT NULL,  -- 'action' | 'indice'
  code_pays        STRING,              -- ex. 'CI', 'SN', 'BJ' — pays membre de l'UEMOA ; nul pour les indices
  secteur          STRING,              -- nul pour les indices
  date_radiation   DATE,                -- nul tant que l'instrument est coté
  vu_le            TIMESTAMP NOT NULL,  -- dernier rafraîchissement où cet instrument a été vu
  maj_le           TIMESTAMP NOT NULL,
  PRIMARY KEY (code) NOT ENFORCED
);
