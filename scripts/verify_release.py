from __future__ import annotations

import os
import sys


def main() -> int:
    environment = os.getenv("BIBLE_ENVIRONMENT", "local").lower()
    if environment not in {"production", "staging"}:
        print("BIBLE_RELEASE_CHECK_LOCAL_OK")
        return 0
    required = {
        "DATABASE_URL": os.getenv("DATABASE_URL"),
        "BIBLE_RESTRICTED_DATA_KEY": os.getenv("BIBLE_RESTRICTED_DATA_KEY"),
        "BIBLE_OIDC_ISSUER": os.getenv("BIBLE_OIDC_ISSUER"),
        "BIBLE_OIDC_AUDIENCE": os.getenv("BIBLE_OIDC_AUDIENCE"),
        "BIBLE_OIDC_INTROSPECTION_ENDPOINT": os.getenv("BIBLE_OIDC_INTROSPECTION_ENDPOINT"),
        "BIBLE_OIDC_CLIENT_ID": os.getenv("BIBLE_OIDC_CLIENT_ID"),
        "BIBLE_OIDC_CLIENT_SECRET": os.getenv("BIBLE_OIDC_CLIENT_SECRET"),
    }
    missing = [key for key, value in required.items() if not value]
    if os.getenv("BIBLE_AUTH_MODE", "") != "oidc":
        missing.append("BIBLE_AUTH_MODE=oidc")
    if missing:
        print("BIBLE_RELEASE_CHECK_FAILED=" + ",".join(missing), file=sys.stderr)
        return 2
    from bible_platform.config import get_settings
    from bible_platform.infrastructure.store import store
    settings = get_settings()
    if store.backend_name == "memory" or not store.schema_ready():
        print("BIBLE_RELEASE_CHECK_FAILED=postgres_schema_not_ready", file=sys.stderr)
        return 3
    print(f"BIBLE_RELEASE_CHECK_OK environment={settings.environment} backend={store.backend_name} release={settings.release_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
