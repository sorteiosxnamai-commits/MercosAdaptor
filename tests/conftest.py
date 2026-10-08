import pytest

from app.quota import reset_gates


@pytest.fixture(autouse=True)
def _fresh_quota_gate():
    """O gate guarda o deadline da conta; cada teste começa sem cooldown herdado."""
    reset_gates()
    yield
    reset_gates()
