import httpx

from ..schemas import ProductMatch
from .base import ProviderError, get_json

# 식품안전나라 OpenAPI, service C005 (바코드연계제품정보): a Korean food barcode -> the product
# as registered with the Ministry of Food and Drug Safety. Free with a key (인증키).
# http://openapi.foodsafetykorea.go.kr/api/{key}/C005/json/{start}/{end}/BAR_CD={barcode}
ENDPOINT = "http://openapi.foodsafetykorea.go.kr/api"
SERVICE = "C005"


class FoodSafetyKorea:
    """식품안전나라 바코드연계제품정보. Resolves a Korean food barcode (880...) to its registered
    name and maker — the identity, no price. Shops (네이버) are then searched by that name."""

    name = "foodsafety"
    currency = "KRW"
    supports_jan = True
    identity_only = True

    def __init__(self, key: str, client: httpx.AsyncClient) -> None:
        self._key = key
        self._client = client

    async def lookup_jan(self, jan: str, limit: int = 5) -> list[ProductMatch]:
        url = f"{ENDPOINT}/{self._key}/{SERVICE}/json/1/{max(1, limit)}/BAR_CD={jan}"
        payload = await get_json(self._client, url, {}, self.name)
        body = payload.get(SERVICE) or payload  # errors can come back without the service key
        code = (body.get("RESULT") or {}).get("CODE", "")
        if code == "INFO-200":  # 해당하는 데이터가 없습니다
            return []
        if code != "INFO-000":
            raise ProviderError(f"{self.name}: {code} {(body.get('RESULT') or {}).get('MSG', '')}".strip())
        rows = body.get("row") or []
        # A barcode can carry rows of a discontinued product (END_DT, 생산중단) or a closed maker
        # (CLSBIZ_DT, 폐업): what is on the shelf now comes first.
        rows.sort(key=lambda r: (bool(r.get("END_DT")) or bool(r.get("CLSBIZ_DT"))))
        out: list[ProductMatch] = []
        for row in rows:
            if not row.get("PRDLST_NM"):
                continue
            m = self._to_match(row)
            if not any((o.name, o.brand) == (m.name, m.brand) for o in out):  # rows repeat
                out.append(m)
        return out

    async def search(self, text: str, limit: int = 5) -> list[ProductMatch]:
        return []  # barcode only

    def _to_match(self, row: dict) -> ProductMatch:
        return ProductMatch(
            name=row["PRDLST_NM"].strip(),
            price=None,
            currency="KRW",
            source=self.name,
            jan=row.get("BAR_CD") or None,
            brand=(row.get("BSSH_NM") or "").strip() or None,
            shelf_life=(row.get("POG_DAYCNT") or "").strip() or None,
        )
