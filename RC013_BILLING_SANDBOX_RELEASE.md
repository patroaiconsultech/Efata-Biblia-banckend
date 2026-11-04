# RC 0.13 — Billing sandbox local, não produção
`POST /api/billing/checkouts` é 503 por padrão. Exige `BIBLE_ENVIRONMENT=local`,
flag explícita, SQLite, chave de teste e callback HTTPS.
Persistência de intenção antes da chamada; idempotência e preço definido pelo servidor.
`chargeType=DETACHED`: não é assinatura renovável. Nenhuma concessão premium.
Sem migration nova. Tests 7/7 específicos; 16/16 catálogo/checkout; 49/49 verificação/reconciliação.
Não ativar cobranças reais. Referência técnica: relatório AO-01.
