from typing import Literal

import httpx

from ..schemas import ProductMatch
from .base import ProviderError

ENDPOINT = "https://world.openfoodfacts.org/api/v2/product/{code}.json"
FIELDS = "code,product_name,product_name_ja,product_name_ko,brands,quantity"


class OpenFoodFacts:
    """Open Food Facts: an open, crowd-sourced food database, no key. The last resort for
    'what is this?' when no shop or registry knows the barcode — few Japanese and Korean
    products are in it. Identity only, no price.

    Its API asks clients to send a User-Agent naming the app; this sends a generic one
    (OPENFOODFACTS_USER_AGENT overrides it) and nothing else about the user."""

    name = "openfoodfacts"
    supports_jan = True
    identity_only = True
    fallback = True  # asked only when nothing else knew the barcode

    def __init__(self, client: httpx.AsyncClient, currency: Literal["JPY", "KRW"], user_agent: str = "barcode-id/0.1") -> None:
        self._client = client
        self.currency = currency
        self._headers = {"User-Agent": user_agent}
        # The product's name in the wearer's region first.
        self._name_keys = ("product_name_ja",) if currency == "JPY" else ("product_name_ko",)

    async def lookup_jan(self, jan: str, limit: int = 5) -> list[ProductMatch]:
        try:
            response = await self._client.get(
                ENDPOINT.format(code=jan), params={"fields": FIELDS}, headers=self._headers, timeout=5
            )
        except httpx.HTTPError as e:
            raise ProviderError(f"{self.name}: {e}") from e
        if response.status_code == 404:  # how it says "product not found"
            return []
        if response.status_code != 200:
            raise ProviderError(f"{self.name}: HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as e:
            raise ProviderError(f"{self.name}: invalid JSON") from e
        product = payload.get("product") or {}
        if payload.get("status") != 1 or not product:
            return []
        name = next((product[k] for k in (*self._name_keys, "product_name") if product.get(k)), "").strip()
        if not name:
            return []
        quantity = (product.get("quantity") or "").strip()
        brand = (product.get("brands") or "").split(",")[0].strip() or None
        return [
            ProductMatch(
                name=f"{name} {quantity}".strip() if quantity and quantity not in name else name,
                price=None,
                currency=self.currency,
                source=self.name,
                jan=product.get("code") or jan,
                brand=brand,
            )
        ]

    async def search(self, text: str, limit: int = 5) -> list[ProductMatch]:
        return []  # barcode only
