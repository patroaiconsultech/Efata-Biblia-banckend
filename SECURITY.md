# Security model — Bíblia Sagrada Digital

## Classificação

Conteúdo bíblico publicado é `PUBLIC` somente quando possui provenance e rights aprovados. Configuração operacional é `INTERNAL`. Memberships, configuração de organização e dados de integração são `CONFIDENTIAL`. Diário, oração, reflexão e memória consentida são `RESTRICTED`.

## Controles obrigatórios

O backend aceita bearer token validado por OIDC/introspection em staging/produção. Headers demo de tenant/usuário só são aceitos fora de produção e por allowlist. A aplicação exige tenant e user no principal, aplica filtros compostos e prepara contexto PostgreSQL para RLS. O PostgreSQL de produção deve executar as migrations `001_bible_foundation`, `002_bible_rls` e `003_bible_content` com uma role de aplicação que não seja superuser/bypassrls.

Dados restritos são cifrados antes de persistir usando uma chave carregada de secret manager/Railway, versionada como `v1`. Em produção `BIBLE_RESTRICTED_DATA_KEY` é obrigatório. A chave não é enviada ao frontend, não aparece em logs e não deve ser armazenada junto ao ciphertext. A implementação atual é a fundação; rotação de chave deve ser realizada com dual-read/new-write e migração controlada antes de produção.

Todos os serviços devem usar HTTPS/TLS, CORS allowlist, CSP, HSTS em produção, headers de proteção, limites de body, timeouts, rate limit, request IDs e erros sem traceback. O frontend nunca recebe API keys, client secrets, DATABASE_URL ou chaves de criptografia.

## Incidentes

Em caso de suspeita de vazamento: revogar tokens e secrets, congelar deploy, abrir incidente com timestamp/request IDs, preservar logs mínimos, rotacionar chaves, avaliar dados afetados, invalidar sessões, corrigir a causa, executar testes de regressão, restaurar a partir de backup se necessário e registrar a decisão de retorno.

Em caso de cross-tenant, citação falsa, rights inválido ou exposição de dados restritos, o release é imediatamente `NO-GO`. Não mascarar um incidente por deletar logs; manter evidências técnicas minimizadas e acessíveis a pessoas autorizadas.

## Release gate

O release candidate não é produção-ready enquanto a autenticação OIDC real, PostgreSQL/RLS, KMS/secret manager, rotação de chave, backup/restore, scanner de secrets, SAST/SCA, testes de isolamento e revisão de direitos não estiverem comprovados no ambiente de staging.
