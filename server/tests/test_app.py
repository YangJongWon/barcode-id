import pytest
from fastapi.testclient import TestClient

from barcode_id.app import create_app
from barcode_id.providers.base import ProviderError
from barcode_id.schemas import ProductMatch

TOKEN = "t0ken"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeProvider:
    def __init__(self, name, supports_jan, matches=None, error=None, currency="JPY"):
        self.name = name
        self.currency = currency
        self.supports_jan = supports_jan
        self.matches = matches or []
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def lookup_jan(self, jan, limit=5):
        self.calls.append(("jan", jan))
        if self.error:
            raise self.error
        return self.matches

    async def search(self, text, limit=5):
        self.calls.append(("search", text))
        if self.error:
            raise self.error
        return self.matches


def match(name, price, source="yahoo", currency="JPY"):
    return ProductMatch(name=name, price=price, currency=currency, source=source)


def client_with(jp=None, kr=None):
    return TestClient(create_app(providers={"jp": jp or [], "kr": kr or []}, token=TOKEN))


def test_requires_token():
    client = client_with(jp=[FakeProvider("yahoo", True)])
    assert client.get("/health").status_code == 401
    assert client.get("/health", headers=AUTH).json() == {"ok": True}


def test_barcode_lookup_returns_cheapest_first():
    yahoo = FakeProvider("yahoo", True, [match("포테토칩 60g", 168), match("포테토칩 60g", 138)])
    client = client_with(jp=[yahoo])

    body = client.get("/product", params={"jan": "4901330500009"}, headers=AUTH).json()

    assert yahoo.calls == [("jan", "4901330500009")]
    assert [m["price"] for m in body["matches"]] == [138, 168]
    assert body["region"] == "jp"


def test_keyword_search_when_no_barcode():
    yahoo = FakeProvider("yahoo", True, [match("녹차", 150)])
    client = client_with(jp=[yahoo])

    body = client.get("/product", params={"q": "お～いお茶"}, headers=AUTH).json()

    assert yahoo.calls == [("search", "お～いお茶")]
    assert body["matches"][0]["name"] == "녹차"


def test_keyword_only_provider_is_skipped_when_barcode_already_matched():
    yahoo = FakeProvider("yahoo", True, [match("포테토칩", 138)])
    rakuten = FakeProvider("rakuten", False, [match("포테토칩 12봉", 1980, source="rakuten")])
    client = client_with(jp=[yahoo, rakuten])

    client.get("/product", params={"jan": "4901330500009"}, headers=AUTH)

    # Searching the digits as a keyword returns noise, so only do it if nothing matched.
    assert rakuten.calls == []


def test_keyword_only_provider_still_tried_when_barcode_found_nothing():
    yahoo = FakeProvider("yahoo", True, [])
    rakuten = FakeProvider("rakuten", False, [match("포테토칩 12봉", 1980, source="rakuten")])
    client = client_with(jp=[yahoo, rakuten])

    body = client.get("/product", params={"jan": "4901330500009"}, headers=AUTH).json()

    assert rakuten.calls == [("jan", "4901330500009")]
    assert body["matches"][0]["source"] == "rakuten"


def test_one_failing_provider_does_not_sink_the_answer():
    broken = FakeProvider("yahoo", True, error=ProviderError("yahoo: HTTP 500"))
    working = FakeProvider("rakuten", False, [match("포테토칩", 1980, source="rakuten")])
    client = client_with(jp=[broken, working])

    body = client.get("/product", params={"jan": "4901330500009"}, headers=AUTH).json()

    assert body["matches"][0]["source"] == "rakuten"


def test_all_providers_failing_is_a_bad_gateway():
    broken = FakeProvider("yahoo", True, error=ProviderError("yahoo: HTTP 500"))
    client = client_with(jp=[broken])

    response = client.get("/product", params={"jan": "4901330500009"}, headers=AUTH)

    assert response.status_code == 502


def test_region_without_keys_names_the_missing_env_var():
    client = client_with(jp=[FakeProvider("yahoo", True)])

    response = client.get("/product", params={"q": "우유", "region": "kr"}, headers=AUTH)

    assert response.status_code == 503
    assert "NAVER_CLIENT_ID" in response.json()["detail"]


@pytest.mark.parametrize(
    "params",
    [{}, {"jan": "4901330500009", "q": "포테토칩"}, {"jan": "abc"}, {"q": ""}],
)
def test_rejects_bad_queries(params):
    client = client_with(jp=[FakeProvider("yahoo", True)])
    assert client.get("/product", params=params, headers=AUTH).status_code in (400, 422)


class FakeRegistry(FakeProvider):
    """식품안전나라 / Open Food Facts: names a barcode, no price."""

    def __init__(self, name, matches=None, fallback=False, currency="KRW"):
        super().__init__(name, True, matches, currency=currency)
        self.identity_only = True
        self.fallback = fallback


def test_registry_names_the_product_and_the_shop_is_searched_by_name():
    registry = FakeRegistry("foodsafety", [match("매일맛있는진간장골드", None, source="foodsafety", currency="KRW")])
    naver = FakeProvider("naver", False, [match("[무료배송] 매일 진간장 골드 900ml 2개", 7900, source="naver", currency="KRW")], currency="KRW")
    client = client_with(kr=[registry, naver])

    body = client.get("/product", params={"jan": "8801791000055", "region": "kr"}, headers=AUTH).json()

    # The digits as a keyword return noise; the registered name finds the listing.
    assert naver.calls == [("search", "매일맛있는진간장골드")]
    assert body["spoken_name"] == "매일맛있는진간장골드"
    assert body["cheapest"]["price"] == 7900


def test_fallback_registry_is_asked_only_when_nothing_else_knew_the_barcode():
    yahoo = FakeProvider("yahoo", True, [match("ポテトチップス うすしお 60g", 138)])
    off = FakeRegistry("openfoodfacts", [match("Potato chips", None, source="openfoodfacts")], fallback=True, currency="JPY")
    client = client_with(jp=[yahoo, off])

    client.get("/product", params={"jan": "4901330500009"}, headers=AUTH)

    assert off.calls == []


def test_fallback_registry_answers_what_it_is_when_shops_do_not_know():
    yahoo = FakeProvider("yahoo", True, [])
    off = FakeRegistry("openfoodfacts", [match("Nutella 400 g", None, source="openfoodfacts")], fallback=True, currency="JPY")
    client = client_with(jp=[yahoo, off])

    body = client.get("/product", params={"jan": "3017620422003"}, headers=AUTH).json()

    assert off.calls == [("jan", "3017620422003")]
    assert body["spoken_name"] == "Nutella 400 g"
    assert body["cheapest"] is None


def test_keyword_search_skips_registries():
    registry = FakeRegistry("foodsafety")
    naver = FakeProvider("naver", False, [match("우유", 2500, source="naver", currency="KRW")], currency="KRW")
    client = client_with(kr=[registry, naver])

    client.get("/product", params={"q": "우유", "region": "kr"}, headers=AUTH)

    assert registry.calls == []


def test_misread_barcode_is_refused_before_any_provider_is_asked():
    yahoo = FakeProvider("yahoo", True, [match("포테토칩", 138)])
    client = client_with(jp=[yahoo])

    # 4901330500009 with its last digit changed: a 1D misread.
    response = client.get("/product", params={"jan": "4901330500008"}, headers=AUTH)

    assert response.status_code == 422
    assert yahoo.calls == []


@pytest.mark.parametrize("code", ["4901330500009", "8801791000055", "3017620422003", "96385074", "036000291452"])
def test_valid_gtins(code):
    from barcode_id.app import is_valid_gtin

    assert is_valid_gtin(code)


def test_browser_origin_is_allowed_when_configured():
    from barcode_id.app import create_app

    client = TestClient(create_app(providers={"jp": [], "kr": []}, token=TOKEN, cors_origins=["https://scan.example"]))
    response = client.options(
        "/product",
        headers={"Origin": "https://scan.example", "Access-Control-Request-Method": "GET",
                 "Access-Control-Request-Headers": "Authorization"},
    )
    assert response.headers.get("access-control-allow-origin") == "https://scan.example"
