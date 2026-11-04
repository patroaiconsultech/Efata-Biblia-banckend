import pytest

from bible_platform.integrations.orkio.adapter import OrkioAdapter, OrkioExecutionRequest, OrkioUnavailable


def test_adapter_is_disabled_without_external_configuration():
    adapter = OrkioAdapter()
    assert adapter.enabled() is False
    with pytest.raises(OrkioUnavailable):
        adapter.request_execution(OrkioExecutionRequest(tenant_id="t", user_id="u", thread_id="th"))


def test_adapter_rejects_non_bible_owner_before_network():
    adapter = OrkioAdapter("https://example.invalid", "service-token")
    with pytest.raises(OrkioUnavailable, match="OWNERSHIP"):
        adapter.request_execution(OrkioExecutionRequest(tenant_id="t", user_id="u", thread_id="th", agent_id="other"))
