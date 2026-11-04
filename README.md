# Bíblia Sagrada Digital — Bible API

API independente para leitura, estudo e jornada bíblica. O domínio utiliza o namespace `bible_platform` e não importa módulos internos do ORKIO.

## Princípios

A leitura básica não depende do ORKIO ou de um LLM. Dados de diário, oração e memória são restritos e devem ser cifrados. Em produção, a autenticação é fail-closed, o PostgreSQL é obrigatório e migrations devem ser executadas separadamente do boot da aplicação.

## Desenvolvimento local

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
uvicorn bible_platform.main:app --reload --host 0.0.0.0 --port 8080
```

O modo `demo` é permitido somente fora de produção. Ele exige tenant e usuário presentes nas allowlists. Para produção, configure OIDC/introspection e não use headers de identidade controlados pelo browser.

## Testes

```bash
pytest -q
```

## Container

```bash
docker build -t bible-api:local .
docker run --rm -p 8080:8080 --env-file .env bible-api:local
```

## Endpoints iniciais

`/api/health`, `/api/readiness`, `/api/auth/status`, `/api/auth/me`, `/api/works`, `/api/translations`, `/api/works/{work_id}/chapters/{chapter}`, `/api/search`, `/api/reading-plans`, `/api/progress`, `/api/journal` e `/api/conversations/{thread_id}/stream`.

O conteúdo textual só deve ser carregado por ingestão versionada com `SourceRegistry` e `RightsRecord`. O seed inicial não deve incluir tradução moderna sem revisão de direitos.
\n> RC 0.12-FP1 (local, proposta): cadastro opcional de tradição de fé com consentimento e exclusão; o corpus atual ainda não cobre todos os cânones. Veja `RC012_FAITH_PROFILE_STATUS.md` na raiz do pacote. Sem deploy ou cobrança.\n

## RC 0.14 — Visão administrativa local (somente leitura)

`GET /api/admin/overview`: requer role `admin` validado pelo backend.
Opera somente com `BIBLE_ENVIRONMENT=local` e um `DATABASE_URL=sqlite:///...` próprio.
Sem SQLite, retorna HTTP 503; em PostgreSQL/staging/produção, também é bloqueado,
pois o papel técnico e RLS para agregação multiusuário ainda não foram homologados.

O retorno agrega conversas, mensagens, leituras, favoritos e status de billing de teste,
sempre limitados ao tenant do principal. **Não exibe** perfis religiosos, orações,
diários, texto das conversas, dados pessoais ou IDs de usuários. Não tenta calcular
receita recorrente, custo de tokens, DAU/MAU ou cadastros sem instrumentação.
O endpoint é GET somente leitura, sem migrations adicionais e com `Cache-Control: no-store`.
