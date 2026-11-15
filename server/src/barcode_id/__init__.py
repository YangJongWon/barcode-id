import os


def main() -> None:
    import uvicorn

    # The server listens on the LAN so the phone can reach it; a shared token is
    # mandatory because every request spends someone's API quota.
    if not os.environ.get("BARCODE_ID_TOKEN"):
        raise SystemExit("BARCODE_ID_TOKEN is not set. Generate one with: openssl rand -hex 24")

    uvicorn.run(
        "barcode_id.app:app_from_env",
        factory=True,
        host=os.environ.get("BARCODE_ID_HOST", "0.0.0.0"),
        port=int(os.environ.get("BARCODE_ID_PORT", "8010")),
    )
