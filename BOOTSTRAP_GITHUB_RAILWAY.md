# Bible API — bootstrap de novo repositório (staging)

**Fonte:** RC 0.14 Admin Analytics, SHA-256 `64513d1ddc51c049fef86e47c5f952303cbf627af08d15876ed97678534ea888`.
Novo repositório **independente** da ORKIO Premium. Este ZIP NÃO contém histórico Git. O primeiro commit será um commit novo.
Status: pronto para revisão AO-01 e posterior bootstrap Git; **não homologado para produção**.

## Configuração e gates

- Python 3.12, Dockerfile raiz, `railway.toml` com healthcheck `/api/health`.
- `BIBLE_ENVIRONMENT=staging`, `BIBLE_AUTH_MODE=oidc`, `DATABASE_URL` PostgreSQL usando role de app sem `SUPERUSER` nem `BYPASSRLS`, segredo `BIBLE_RESTRICTED_DATA_KEY` e OIDC real obrigatório.
- **Nunca** executar migration no boot ou build. Alembic heads e SQL upgrade/downgrade devem ser auditados, depois migration explícita com credencial migradora separada.
- **Nunca** usar URL do superuser PostgreSQL como `DATABASE_URL` da aplicação. Separar dono/migrator e role de runtime.
- Backend local `133/133` pytest PASS. Não prova PostgreSQL, RLS, OIDC nem Railway.
- Admin Analytics permanece 503 em staging/PostgreSQL por desenho. Billing comercial e recorrência Asaas não homologados.
- A publicação do conteúdo depende de revisão de direitos por arquivo e dos requisitos do registro de direitos.
- `BIBLE_BILLING_PLANS_JSON={}`, checkout sandbox/webhook desligados e nenhuma cobrança real em staging.
- Nenhum GitHub Action faz deploy ou migration.

Ver instruções completas no README_OPERACIONAL do pacote-mestre.
