# Deploy Railway — Bíblia Sagrada Digital

Este documento descreve o release candidate do Bible API. O deployment final só é aprovado quando todos os itens do checklist passam em staging.

## Topologia

```text
bible-app (Dockerfile) ──HTTPS──> bible-api (Dockerfile) ──private──> bible-postgres
                                      ├──> secret manager / OIDC provider
                                      ├──> optional ORKIO adapter
                                      └──> optional workers / storage
```

O PostgreSQL, storage, secrets e deployments Bible não devem ser compartilhados com ORKIO.

## Ordem de configuração

1. Criar o serviço PostgreSQL privado do Bible App.
2. Criar o serviço `bible-api` a partir do repositório `danielgraebin-hub/BIBLE-API-EFATA777`, branch `main`.
3. Confirmar que o builder usa `Dockerfile`, sem start command que o substitua.
4. Configurar as variáveis `BIBLE_ENVIRONMENT=staging`, `BIBLE_AUTH_MODE=oidc`, `DATABASE_URL`, `BIBLE_RESTRICTED_DATA_KEY`, issuer/audience/introspection OIDC, origins permitidas e release SHA.
5. Executar migrations em etapa controlada antes de liberar tráfego. Não executar `metadata.create_all` em produção.
6. Validar `/api/health` e `/api/readiness`; readiness precisa mostrar database, schema e persistent repository prontos.
7. Criar `bible-app` a partir do repositório `danielgraebin-hub/BIBLE-APP-EFATA777`, branch `main`.
8. Configurar `VITE_API_BASE_URL` no build para a URL HTTPS do Bible API e `BIBLE_API_ORIGIN` no runtime para o mesmo origin.
9. Validar login OIDC/PKCE, leitura, busca, progresso, bookmarks, highlights, diário cifrado, jornada e SSE.
10. Promover staging para production somente com approval registrado, backup e rollback testado.

## Variáveis do backend

Nunca registrar valores em ticket, commit, log ou screenshot. Configurar no painel Railway/secret manager:

```text
BIBLE_ENVIRONMENT=staging|production
BIBLE_RELEASE_SHA=<sha-do-commit>
BIBLE_AUTH_MODE=oidc
BIBLE_ALLOWED_ORIGINS=https://<dominio-frontend-bible>
DATABASE_URL=<private-postgres-bible>
BIBLE_RESTRICTED_DATA_KEY=<random-high-entropy-secret>
BIBLE_OIDC_ISSUER=https://<issuer>
BIBLE_OIDC_AUDIENCE=<audience>
BIBLE_OIDC_INTROSPECTION_ENDPOINT=https://<issuer>/introspect
BIBLE_OIDC_CLIENT_ID=<server-client-id>
BIBLE_OIDC_CLIENT_SECRET=<server-client-secret>
BIBLE_OIDC_USER_CLAIM=sub
BIBLE_OIDC_TENANT_CLAIM=tenant_id
BIBLE_OIDC_ROLES_CLAIM=roles
BIBLE_CONTENT_SOURCE_ID=bible-livre
BIBLE_CONTENT_SOURCE_VERSION=2025.1.0
BIBLE_DOCS_ENABLED=false
BIBLE_LOG_RESTRICTED_CONTENT=false
```

O segredo `BIBLE_RESTRICTED_DATA_KEY` deve ser gerado fora do repositório, armazenado no secret manager e rotacionado com plano de dual-read/new-write. A aplicação atual usa a versão `v1` como fundação.

## Variáveis do frontend

```text
VITE_API_BASE_URL=https://<dominio-api-bible>
VITE_OIDC_ISSUER=https://<issuer>
VITE_OIDC_CLIENT_ID=<public-client-id>
VITE_OIDC_REDIRECT_URI=https://<dominio-frontend-bible>/auth/callback
BIBLE_API_ORIGIN=https://<dominio-api-bible>
```

Nenhum `VITE_*` pode conter API key, client secret, `DATABASE_URL` ou chave criptográfica.

## Migrations

Aplicar a cadeia `001_bible_foundation`, `002_bible_rls`, `003_bible_content` e `004_bible_bookmarks_highlights`. Validar a única head e conferir policies RLS no PostgreSQL. A role de aplicação não deve ser superuser nem possuir `BYPASSRLS`. Backup deve preceder migration e restore deve ser testado em banco isolado.

## Smoke test pós-deploy

```bash
curl -fsS https://<dominio-api-bible>/api/health
curl -fsS https://<dominio-api-bible>/api/readiness
curl -fsS https://<dominio-frontend-bible>/
```

Depois dos endpoints públicos, executar testes autenticados de OIDC, tenant, leitura de Gênesis 1, busca, progresso, bookmark, highlight, journal, SSE terminal e reconnect. Confirmar `event: done`, `schema_version=bible.sse.v1`, `agent_id=turn_owner=bible_guide` e ausência de conteúdo privado nos logs.

## Runbook para 502

Se Railway mostrar `Application failed to respond`:

1. Registrar request ID, deployment ID e horário UTC.
2. Abrir Deploy Logs e Build Logs do mesmo deployment.
3. Confirmar source repository, branch, commit SHA, builder, Dockerfile e image digest.
4. Confirmar que Uvicorn iniciou `bible_platform.main:app` em `0.0.0.0:${PORT}`.
5. Procurar falha de import, configuração, OIDC, conexão de banco ou migration no startup.
6. Confirmar que o target port publicado é o mesmo port escutado pelo processo.
7. Confirmar healthcheck `/api/health` e não `/api/readiness` antes de migrations.
8. Corrigir uma causa por vez e registrar o patch.
9. Redeployar o commit aprovado; não usar redeploy vazio como diagnóstico.
10. Validar health, readiness, CORS, auth e smoke antes de promover.

Se o processo escuta em `127.0.0.1`, usa uma porta fixa diferente de `PORT`, ou morre durante import/configuração, o proxy pode devolver 502 mesmo com o build concluído. Não desabilitar autenticação, RLS ou criptografia como “correção” do 502.

## Rollback

Manter o último deployment saudável e seu image digest. Em falha crítica, reverter para o deployment anterior, restaurar schema somente se o rollback exigir compatibilidade de migration, invalidar caches e repetir smoke. Não executar downgrade destrutivo em produção sem backup e approval.

## GO final

O release é GO somente quando backend health/readiness, schema, OIDC, RLS, encryption, rights, CI, SAST/SCA, SBOM, smoke, observability, backup/restore, rollback e frontend E2E passam em staging. Ausência de credencial Railway, OIDC, PostgreSQL ou revisão jurídica de rights mantém o status `RELEASE_CANDIDATE / NO-GO`.
