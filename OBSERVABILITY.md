# Observabilidade — Bíblia Sagrada Digital

## Correlation

Toda requisição deve receber `X-Request-ID` e toda execução generativa deve associar `request_id`, `execution_id`, `thread_id`, `tenant_id` e `agent_id`. Esses identificadores podem aparecer em logs; conteúdo privado, tokens, headers completos e ciphertext não.

## Métricas

Monitorar disponibilidade e latência de `/api/health`, `/api/readiness`, leitura, busca, autenticação, journal, SSE e workers. Acompanhar HTTP 5xx, 4xx por categoria, rate limit, conexão ao PostgreSQL, pool exhaustion, migrations, outbox, timeout de provider, circuit breaker, reconnect, streams sem `done`, citation validation e rights blocks.

## Alertas

Criar alertas para backend indisponível, readiness not ready, aumento sustentado de 5xx, latência p95/p99, DB sem conexão, falha de backup, restore não testado, erro de descriptografia, cross-tenant detectado, secret scanning, token validation failure anormal, SSE terminal ausente e deployment sem commit/health correlacionados.

## Logs

Usar JSON estruturado com timestamp UTC, service, environment, release_sha, request_id, status_code, route_family, duration_ms, tenant_hash ou tenant_id conforme política, e outcome. Remover Authorization, cookies, conteúdo de diário/oração, prompts completos, respostas completas e dados de saúde/trauma. O modo de desenvolvimento pode emitir logs mais detalhados somente no processo local e nunca em produção.

## Railway

Ler Deploy Logs e Build Logs antes de redeploy. Associar o deployment ID ao commit e ao digest da imagem. Depois do deploy, executar health, readiness, CORS/preflight, auth, leitura, busca, journal cifrado, SSE e rollback. O runtime não deve ser considerado saudável apenas porque o Edge Proxy retorna uma página.
