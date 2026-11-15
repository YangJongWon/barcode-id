import hmac
import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .providers import (
    REQUIRED_ENV,
    MissingCredentials,
    ProductProvider,
    ProviderError,
    providers_from_env,
)
from .naming import pick_identity, speakable
from .schemas import ProductLookup, ProductMatch, Region

log = logging.getLogger("barcode_id")


def is_valid_gtin(code: str) -> bool:
    """EAN-13 / JAN, EAN-8, UPC-A (12) and GTIN-14 carry a mod-10 check digit. A misread
    (1D readers do misread) fails it; refusing it here spends no API quota on noise."""
    if not code.isdigit() or len(code) not in (8, 12, 13, 14):
        return False
    digits = [int(c) for c in code]
    body = reversed(digits[:-1])
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(body))
    return (10 - total % 10) % 10 == digits[-1]


def create_app(
    providers: dict[Region, list[ProductProvider]] | None = None,
    token: str | None = None,
    cors_origins: list[str] | None = None,
) -> FastAPI:
    state: dict = {"providers": providers}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # One shared connection pool; providers are cheap wrappers around it.
        async with httpx.AsyncClient() as client:
            if state["providers"] is None:
                state["providers"] = providers_from_env(client)
            yield

    def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
        if token is None:
            return
        if authorization is None or not hmac.compare_digest(authorization, f"Bearer {token}"):
            raise HTTPException(status_code=401, detail="invalid token")

    app = FastAPI(title="barcode-id", lifespan=lifespan, dependencies=[Depends(require_token)])
    if cors_origins:
        # the browser reader (web/barcode-id.js) calls this from a page on another origin
        app.add_middleware(
            CORSMiddleware, allow_origins=cors_origins, allow_methods=["GET"], allow_headers=["Authorization"]
        )

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/product")
    async def product(
        region: Annotated[Region, Query()] = "jp",
        jan: Annotated[str | None, Query(pattern=r"^\d{8,14}$")] = None,
        q: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
        limit: Annotated[int, Query(ge=1, le=10)] = 5,
    ) -> ProductLookup:
        """Identify a product and what it costs online.

        Callers send the barcode they read on-device (web/barcode-id.js, iOS Vision ...):
        only the digits leave the device, never a photo.
        """
        if (jan is None) == (q is None):
            raise HTTPException(status_code=400, detail="pass exactly one of jan or q")
        if jan is not None and not is_valid_gtin(jan):
            raise HTTPException(status_code=422, detail="check digit does not match: not a product barcode")

        available = (state["providers"] or {}).get(region) or []
        if not available:
            raise MissingCredentials(REQUIRED_ENV[region])

        matches: list[ProductMatch] = []
        errors: list[str] = []

        async def ask(provider: ProductProvider, call) -> None:
            try:
                matches.extend(await call)
            except ProviderError as e:
                log.warning("provider failed: %s", e)
                errors.append(str(e))

        def identity_only(p: ProductProvider) -> bool:
            return getattr(p, "identity_only", False)

        if jan is None:
            for provider in available:
                if not identity_only(provider):
                    await ask(provider, provider.search(q or "", limit))
        else:
            # 1. Whoever resolves a barcode directly: shops (Yahoo) and registries (식품안전나라)
            #    in configured order; fallbacks (Open Food Facts) only while nothing is found.
            for provider in available:
                if not provider.supports_jan or (getattr(provider, "fallback", False) and matches):
                    continue
                await ask(provider, provider.lookup_jan(jan, limit))
            # 2. Shops that only take keywords, for a price: by the product's name once a
            #    registry or shop named it (the digits as a keyword return noise), by the
            #    digits only when nothing at all was found.
            if not any(m.price is not None for m in matches):
                named = pick_identity(matches)
                keyword = speakable(named.name) if named else None
                for provider in available:
                    if provider.supports_jan or identity_only(provider):
                        continue
                    await ask(provider, provider.search(keyword, limit) if keyword else provider.lookup_jan(jan, limit))

        if not matches and errors:
            raise HTTPException(status_code=502, detail="; ".join(errors))

        matches.sort(key=lambda m: (m.price is None, m.price or 0))
        # A registry's name (식품안전나라, Open Food Facts) is the product's own name; shop
        # titles are written for search engines, and a name search may return look-alikes.
        registry = {p.name for p in available if identity_only(p)}
        identity = pick_identity([m for m in matches if m.source in registry]) or pick_identity(matches)
        priced = [m for m in matches if m.price is not None]
        return ProductLookup(
            query=jan or q or "",
            region=region,
            identity=identity,
            cheapest=priced[0] if priced else None,
            spoken_name=speakable(identity.name) if identity else None,
            matches=matches[:limit],
        )

    @app.exception_handler(MissingCredentials)
    async def missing_credentials(_request, exc: MissingCredentials):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=503, content={"detail": f"설정되지 않은 키: {exc}"})

    return app


def app_from_env() -> FastAPI:
    origins = [o.strip() for o in os.environ.get("BARCODE_ID_CORS_ORIGINS", "").split(",") if o.strip()]
    return create_app(token=os.environ.get("BARCODE_ID_TOKEN"), cors_origins=origins)
