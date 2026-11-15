import httpx
import pytest

from barcode_id.providers import NaverShopping, RakutenIchiba, YahooShopping
from barcode_id.providers.base import ProviderError

# Response shapes follow each provider's published documentation.
YAHOO_BODY = {
    "totalResultsAvailable": 1,
    "hits": [
        {
            "name": "カルビー ポテトチップス うすしお味 60g",
            "price": 138,
            "url": "https://store.example.jp/item/1",
            "janCode": "4901330500009",
            "brand": {"id": 1, "name": "カルビー"},
            "seller": {"sellerId": "s1", "name": "サンプル商店", "url": "https://store.example.jp"},
            "code": "abc",
            "inStock": True,
        }
    ],
}
RAKUTEN_BODY = {
    "items": [
        {
            "itemName": "カルビー ポテトチップス うすしお 60g×12袋",
            "itemPrice": 1980,
            "itemUrl": "https://item.rakuten.example/1",
            "shopName": "楽天サンプル店",
            "mediumImageUrls": ["https://img.example/1.jpg"],
        }
    ]
}
NAVER_BODY = {
    "items": [
        {
            "title": "<b>포카칩</b> 오리지널 &amp; 66g",
            "lprice": "1180",
            "link": "https://shopping.naver.example/1",
            "mallName": "네이버스토어",
            "brand": "오리온",
            "maker": "오리온",
        }
    ]
}


def client_returning(body: dict, status: int = 200, capture: dict | None = None) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture["url"] = str(request.url)
            capture["headers"] = request.headers
        return httpx.Response(status, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_yahoo_looks_up_a_barcode_directly():
    capture: dict = {}
    provider = YahooShopping("app-id", client_returning(YAHOO_BODY, capture=capture))

    matches = await provider.lookup_jan("4901330500009")

    assert "jan_code=4901330500009" in capture["url"]
    assert "appid=app-id" in capture["url"]
    assert provider.supports_jan
    match = matches[0]
    assert match.name.startswith("カルビー ポテトチップス")
    assert (match.price, match.currency, match.source) == (138, "JPY", "yahoo")
    assert (match.jan, match.brand, match.seller) == ("4901330500009", "カルビー", "サンプル商店")


async def test_rakuten_sends_flat_format_and_has_no_jan_lookup():
    capture: dict = {}
    provider = RakutenIchiba("app-id", "access-key", client_returning(RAKUTEN_BODY, capture=capture))

    matches = await provider.search("ポテトチップス")

    assert "formatVersion=2" in capture["url"]
    assert "accessKey=access-key" in capture["url"]
    assert not provider.supports_jan
    assert (matches[0].price, matches[0].seller) == (1980, "楽天サンプル店")


async def test_naver_strips_markup_and_sends_auth_headers():
    capture: dict = {}
    provider = NaverShopping("id", "secret", client_returning(NAVER_BODY, capture=capture))

    matches = await provider.search("포카칩")

    assert capture["headers"]["X-Naver-Client-Id"] == "id"
    assert capture["headers"]["X-Naver-Client-Secret"] == "secret"
    match = matches[0]
    assert match.name == "포카칩 오리지널 & 66g"
    assert (match.price, match.currency, match.brand) == (1180, "KRW", "오리온")


async def test_http_error_becomes_provider_error():
    provider = YahooShopping("app-id", client_returning({}, status=500))
    with pytest.raises(ProviderError):
        await provider.lookup_jan("4901330500009")


async def test_empty_result_is_not_an_error():
    provider = YahooShopping("app-id", client_returning({"hits": []}))
    assert await provider.lookup_jan("4901330500009") == []


# 식품안전나라 C005: shapes as returned by the live API (sample key, 2026-11).
FOODSAFETY_BODY = {
    "C005": {
        "total_count": "2",
        "row": [
            {"PRDLST_NM": "예전 간장", "BSSH_NM": "옛공장", "BAR_CD": "8801791000055", "POG_DAYCNT": "2년",
             "END_DT": "20161124", "CLSBIZ_DT": ""},
            {"PRDLST_NM": "매일맛있는진간장골드", "BSSH_NM": "매일식품주식회사", "BAR_CD": "8801791000055",
             "POG_DAYCNT": "실온보관 2년", "END_DT": "", "CLSBIZ_DT": ""},
        ],
        "RESULT": {"MSG": "정상처리되었습니다.", "CODE": "INFO-000"},
    }
}
FOODSAFETY_NONE = {"C005": {"total_count": "0", "RESULT": {"MSG": "해당하는 데이터가 없습니다.", "CODE": "INFO-200"}}}
OFF_BODY = {
    "code": "3017620422003",
    "product": {"brands": "Ferrero, Nutella", "code": "3017620422003", "product_name": "Nutella", "quantity": "400 g"},
    "status": 1,
    "status_verbose": "product found",
}
OFF_NONE = {"code": "4901330500009", "status": 0, "status_verbose": "product not found"}


async def test_foodsafety_names_a_korean_barcode_current_product_first():
    from barcode_id.providers import FoodSafetyKorea

    capture: dict = {}
    provider = FoodSafetyKorea("key", client_returning(FOODSAFETY_BODY, capture=capture))

    matches = await provider.lookup_jan("8801791000055")

    assert capture["url"].endswith("/api/key/C005/json/1/5/BAR_CD=8801791000055")
    # The discontinued row (END_DT set) goes last.
    assert [m.name for m in matches] == ["매일맛있는진간장골드", "예전 간장"]
    first = matches[0]
    assert (first.price, first.currency, first.source, first.brand) == (None, "KRW", "foodsafety", "매일식품주식회사")
    assert first.shelf_life == "실온보관 2년"


async def test_foodsafety_no_data_is_not_an_error():
    from barcode_id.providers import FoodSafetyKorea

    assert await FoodSafetyKorea("key", client_returning(FOODSAFETY_NONE)).lookup_jan("8800000000000") == []


async def test_foodsafety_bad_key_is_a_provider_error():
    from barcode_id.providers import FoodSafetyKorea

    body = {"RESULT": {"MSG": "인증키가 유효하지 않습니다.", "CODE": "INFO-100"}}
    with pytest.raises(ProviderError, match="INFO-100"):
        await FoodSafetyKorea("bad", client_returning(body)).lookup_jan("8801791000055")


async def test_open_food_facts_sends_a_generic_user_agent():
    from barcode_id.providers import OpenFoodFacts

    capture: dict = {}
    provider = OpenFoodFacts(client_returning(OFF_BODY, capture=capture), "KRW")

    matches = await provider.lookup_jan("3017620422003")

    assert capture["headers"]["user-agent"] == "barcode-id/0.1"
    assert "/api/v2/product/3017620422003.json" in capture["url"]
    assert [(m.name, m.brand, m.price, m.currency) for m in matches] == [("Nutella 400 g", "Ferrero", None, "KRW")]


async def test_open_food_facts_unknown_barcode_is_empty():
    from barcode_id.providers import OpenFoodFacts

    assert await OpenFoodFacts(client_returning(OFF_NONE), "JPY").lookup_jan("4901330500009") == []


async def test_open_food_facts_404_means_not_found():
    from barcode_id.providers import OpenFoodFacts

    # The live API answers an unknown barcode with HTTP 404 and {"status": 0}.
    assert await OpenFoodFacts(client_returning(OFF_NONE, status=404), "JPY").lookup_jan("4901330500009") == []


async def test_foodsafety_repeated_rows_count_once():
    from barcode_id.providers import FoodSafetyKorea

    row = {"PRDLST_NM": "쵸코카라멜", "BSSH_NM": "한양제과주식회사", "BAR_CD": "8801649120355", "END_DT": "", "CLSBIZ_DT": ""}
    body = {"C005": {"row": [row, dict(row)], "RESULT": {"CODE": "INFO-000"}}}
    assert len(await FoodSafetyKorea("key", client_returning(body)).lookup_jan("8801649120355")) == 1
