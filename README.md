# EFATÀ 777 — Bible API RC014 native auth candidate
**Standalone Bible identity. AO-01 review and PostgreSQL staging tests required.**

`BIBLE_AUTH_MODE=native_session` in staging/production, with the new
`009_bible_native_auth` Alembic migration applied **in an independent gate**
using a migration DB role. The public registration flag is OFF by default.
There is no default admin account or OIDC integration.

Native endpoints: `/api/auth/register`, `/login`, `/session`, `/logout`,
`/password-reset/request`, `/password-reset/confirm`, `/me`.
All authenticated writes require `X-CSRF-Token` from `/api/auth/session`
and an exact `Origin` matching `BIBLE_NATIVE_PUBLIC_ORIGIN`.

The frontend is the only public browser entry point; the Bible API should be
Railway-private when platform requirements allow. The user database and
migrations are completely separate from ORKIO Premium.

Not yet approved: billing commercial, PWA1, public registration,
PostgreSQL RLS adversarial validation, SMTP recovery and runtime deployment.
