import os

import httpx

from ..schemas import Region
from .base import ProductProvider, ProviderError
from .foodsafety import FoodSafetyKorea
from .naver import NaverShopping
from .openfoodfacts import OpenFoodFacts
from .rakuten import RakutenIchiba
from .yahoo import YahooShopping

__all__ = [
    "ProductProvider",
    "ProviderError",
    "YahooShopping",
    "RakutenIchiba",
    "NaverShopping",
    "FoodSafetyKorea",
    "OpenFoodFacts",
    "providers_from_env",
    "MissingCredentials",
]


class MissingCredentials(Exception):
    """No provider is configured for a region. The message names the env vars,
    because that is the only thing the operator can act on."""


def providers_from_env(client: httpx.AsyncClient) -> dict[Region, list[ProductProvider]]:
    """Builds whatever the environment has keys for. Order matters: the first
    provider that can answer a JAN is the fast path."""
    jp: list[ProductProvider] = []
    # Yahoo calls it "Client ID（アプリケーションID）"; it goes in the appid parameter.
    if client_id := os.environ.get("YAHOO_CLIENT_ID"):
        jp.append(YahooShopping(client_id, client))
    if (rakuten_id := os.environ.get("RAKUTEN_APP_ID")) and (
        access_key := os.environ.get("RAKUTEN_ACCESS_KEY")
    ):
        jp.append(RakutenIchiba(rakuten_id, access_key, client))

    kr: list[ProductProvider] = []
    # 네이버 쇼핑 cannot look a barcode up; the registry names the product first.
    if food_key := os.environ.get("FOODSAFETY_KEY"):
        kr.append(FoodSafetyKorea(food_key, client))
    if (naver_id := os.environ.get("NAVER_CLIENT_ID")) and (
        naver_secret := os.environ.get("NAVER_CLIENT_SECRET")
    ):
        kr.append(NaverShopping(naver_id, naver_secret, client))

    # Open Food Facts needs no key: the last resort for "what is this?" in every region.
    if os.environ.get("BARCODE_ID_OPENFOODFACTS", "1") != "0":
        agent = os.environ.get("OPENFOODFACTS_USER_AGENT") or "barcode-id/0.1"
        jp.append(OpenFoodFacts(client, "JPY", agent))
        kr.append(OpenFoodFacts(client, "KRW", agent))

    return {"jp": jp, "kr": kr}


REQUIRED_ENV: dict[Region, str] = {
    "jp": "YAHOO_CLIENT_ID (또는 RAKUTEN_APP_ID + RAKUTEN_ACCESS_KEY)",
    "kr": "NAVER_CLIENT_ID + NAVER_CLIENT_SECRET (또는 FOODSAFETY_KEY)",
}
