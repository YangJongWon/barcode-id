import httpx
import pytest

from barcode_id.providers import providers_from_env

# A typo in an env var name fails silently — the region just looks unconfigured —
# so the exact names are pinned here.


KEYS = ("YAHOO_CLIENT_ID", "RAKUTEN_APP_ID", "RAKUTEN_ACCESS_KEY", "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET",
        "FOODSAFETY_KEY", "BARCODE_ID_OPENFOODFACTS", "OPENFOODFACTS_USER_AGENT")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    # Start from nothing; the keyless Open Food Facts fallback is off unless a test turns it on,
    # so the keyed providers' order is pinned on its own.
    for name in KEYS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BARCODE_ID_OPENFOODFACTS", "0")


@pytest.fixture
def client():
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))


def test_yahoo_client_id_enables_japan(monkeypatch, client):
    monkeypatch.setenv("YAHOO_CLIENT_ID", "client-id")
    built = providers_from_env(client)
    assert [p.name for p in built["jp"]] == ["yahoo"]
    assert built["kr"] == []


def test_rakuten_needs_both_keys(monkeypatch, client):
    monkeypatch.delenv("YAHOO_CLIENT_ID", raising=False)
    monkeypatch.setenv("RAKUTEN_APP_ID", "app")
    assert providers_from_env(client)["jp"] == []
    monkeypatch.setenv("RAKUTEN_ACCESS_KEY", "key")
    assert [p.name for p in providers_from_env(client)["jp"]] == ["rakuten"]


def test_naver_needs_both_keys(monkeypatch, client):
    monkeypatch.setenv("NAVER_CLIENT_ID", "id")
    assert providers_from_env(client)["kr"] == []
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")
    assert [p.name for p in providers_from_env(client)["kr"]] == ["naver"]


def test_yahoo_comes_before_rakuten(monkeypatch, client):
    monkeypatch.setenv("YAHOO_CLIENT_ID", "client-id")
    monkeypatch.setenv("RAKUTEN_APP_ID", "app")
    monkeypatch.setenv("RAKUTEN_ACCESS_KEY", "key")
    # Order matters: the barcode-capable provider must be asked first.
    assert [p.name for p in providers_from_env(client)["jp"]] == ["yahoo", "rakuten"]


def test_nothing_configured(monkeypatch, client):
    assert providers_from_env(client) == {"jp": [], "kr": []}


def test_foodsafety_key_names_korean_barcodes_before_naver(monkeypatch, client):
    monkeypatch.setenv("FOODSAFETY_KEY", "key")
    monkeypatch.setenv("NAVER_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")
    # 네이버 cannot look a barcode up: the registry must answer "what is this?" first.
    assert [p.name for p in providers_from_env(client)["kr"]] == ["foodsafety", "naver"]


def test_open_food_facts_is_on_by_default_and_last(monkeypatch, client):
    monkeypatch.delenv("BARCODE_ID_OPENFOODFACTS")
    monkeypatch.setenv("YAHOO_CLIENT_ID", "client-id")
    built = providers_from_env(client)
    assert [p.name for p in built["jp"]] == ["yahoo", "openfoodfacts"]
    assert [p.name for p in built["kr"]] == ["openfoodfacts"]
    assert (built["jp"][-1].currency, built["kr"][-1].currency) == ("JPY", "KRW")
