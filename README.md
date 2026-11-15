# barcode-id

Read a product barcode with the camera and say what it is: name, maker, and the cheapest online price. The parts are free: [Quagga2](https://github.com/ericblade/quagga2) (MIT) reads the code in the browser, and free shop and registry APIs identify it.

상품 바코드를 카메라로 읽고, 무슨 상품인지(이름·제조사)와 온라인 최저가를 알려줍니다.

```
web/barcode-id.js   browser: camera -> barcode (Quagga2) -> check digit + 2 frames agree -> server
server/             Python: barcode -> Yahoo!ショッピング / 식품안전나라 / Open Food Facts -> name, maker
                            -> no price yet: 楽天 / 네이버 searched by that name
```

Only the digits leave the device, never a photo.

## Reading (web/)

Engines, best first:

1. **The browser's own `BarcodeDetector`** (Chrome / Edge on Android, macOS and ChromeOS) reads EAN / UPC, QR and DataMatrix. Nothing is downloaded and there is no license to carry.
2. **Otherwise [Quagga2](https://github.com/ericblade/quagga2) (MIT)** reads 1D product codes, and **[paulmillr/qr](https://github.com/paulmillr/qr) (MIT)** reads QR on the same camera stream. This covers Safari on iOS and Firefox. paulmillr/qr 0.7.2 is vendored in `web/vendor/qr/` with a small patch ([barcode-id.patch](web/vendor/qr/barcode-id.patch)) that adds what it did not read:
   - FNC1 modes, i.e. GS1 element-string QR. The output starts with the AIM `]Q3`, and fields are separated by GS.
   - Kanji mode.
   - Shift JIS bytes written without an ECI, which is common in Japan. These used to come out garbled.

```html
<script src="https://cdn.jsdelivr.net/npm/@ericblade/quagga2@1.13.0/dist/quagga.min.js"></script>
<script type="module">
import decodeQR from "./vendor/qr/decode.js";
import { startScanner, identify } from "./barcode-id.js";
const server = { url: "http://localhost:8010", token: "..." };
const scanner = await startScanner(document.querySelector("#camera"), {
  decodeQR,
  onCode: async (code, info) => console.log(await identify(code, server), info.expiry, info.lot),
  onText: (text, info) => console.log("QR:", text),
});
</script>
```

- `onCode(code, info)` reports a product: a 1D code, or a QR / DataMatrix carrying GS1 data. `code` is the GTIN to look up.
  - A 1D code counts only when its check digit is right and the same code was read on 2 frames in a row. 1D readers misread now and then.
  - QR and DataMatrix carry error correction, so one read is enough.
- **GS1 in QR / DataMatrix** is read in all three forms: Digital Link `https://id.gs1.org/01/…/10/…?17=…`, the element string `]d2 01… 17… 10…`, and `(01)…(17)…`. You get `code` (GTIN), `info.expiry` (17), `info.bestBefore` (15), `info.lot` (10) and `info.serial` (21). Medicine packs carry their expiry date this way, no OCR needed.
- `onText(text, info)` reports any other QR: a URL (`info.kind` `"url"`) or text.
- `decodeImage(src)` reads one still with the same engines. Quagga2's locator misses short codes (EAN-8, UPC-E) and small codes in a wide view, so the middle lines and the centre half are also scanned.
- `identify(code, { url, token, region })` asks the server. The region defaults to `kr` for 880… and `jp` otherwise.
- Codes come out as 13-digit GTIN: UPC-A gets a leading 0, UPC-E is expanded, and a GTIN-14 of a single item drops its leading 0. EAN-8 stays at 8 digits.

`web/index.html` is a demo: camera, or type a number. Serve the folder over HTTPS or `localhost`; browsers allow the camera only there.

| | built-in BarcodeDetector | Quagga2 + paulmillr/qr |
|---|---|---|
| EAN-13 / JAN, EAN-8, UPC-A/E | ✅ | ✅ |
| QR: URL, text, GS1 Digital Link | ✅ | ✅ |
| QR: GS1 element string (FNC1 mode), kanji mode, Shift JIS | ✅ | ✅ (vendored patch) |
| DataMatrix (GS1, medicine) | ✅ | ❌ |
| GS1 DataBar, PDF417 | ❌ | ❌ |

paulmillr/qr is dual-licensed MIT or Apache-2.0 (used here under MIT, licence files kept in `web/vendor/qr/`). It has no dependencies, is ~7 KB, and runs on the main thread. Each live try is capped at about one frame's time, so 1D scanning keeps its pace.

## Identifying (server/)

```sh
cd server
uv sync
uv run pytest
export BARCODE_ID_TOKEN=$(openssl rand -hex 24)
uv run barcode-id          # http://0.0.0.0:8010/product?jan=4901330500009&region=jp
```

| env | |
|---|---|
| `BARCODE_ID_TOKEN` | required; callers send `Authorization: Bearer <token>`. Every request spends someone's API quota |
| `YAHOO_CLIENT_ID` | Japan. Yahoo!ショッピング, the only Japanese source that looks a JAN up directly |
| `RAKUTEN_APP_ID`, `RAKUTEN_ACCESS_KEY` | Japan, price (keyword only) |
| `FOODSAFETY_KEY` | Korea. [식품안전나라](https://www.foodsafetykorea.go.kr/api/openApiInfo.do?svc_no=C005) 바코드연계제품정보 (C005), free key: Korean food barcode -> name, maker, shelf life |
| `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` | Korea, price. No barcode lookup, so it is searched by the name found above |
| `BARCODE_ID_OPENFOODFACTS` | on by default (`0` turns it off). [Open Food Facts](https://world.openfoodfacts.org), no key; asked only when nothing else knew the code. Few Japanese and Korean products |
| `OPENFOODFACTS_USER_AGENT` | default `barcode-id/0.1`; nothing about the user is sent |
| `BARCODE_ID_CORS_ORIGINS` | comma-separated origins allowed to call from a browser (the page serving `web/`) |
| `BARCODE_ID_HOST`, `BARCODE_ID_PORT` | default `0.0.0.0`, `8010` |

How a code is looked up:
1. A code with a wrong check digit is refused (422) before any API is called.
2. Sources that look a barcode up directly, in order: Yahoo, 식품안전나라.
3. Open Food Facts, only if those found nothing.
4. If there is no price yet, the keyword-only shops (楽天, 네이버) are searched by the product's name. The digits are used as the keyword only when no name was found.

The name to show or speak comes from a registry (식품안전나라, Open Food Facts) before shop titles. Shop titles are written for search engines.

Response: `{ identity, cheapest, spoken_name, matches }`. `identity` answers "what is this?" and `cheapest` answers "what does it cost?".

## Tests

```sh
npm install && npm test        # reading: barcodes and QR rendered with bwip-js, read back with Quagga2 / paulmillr/qr
cd server && uv run pytest     # identifying: providers against recorded responses, no keys needed
```

## Built on and inspired by

| project | license | how it is used |
|---|---|---|
| [Quagga2](https://github.com/ericblade/quagga2) | MIT | reads 1D barcodes (dependency) |
| [paulmillr/qr](https://github.com/paulmillr/qr) | MIT or Apache-2.0 | reads QR; 0.7.2 vendored in `web/vendor/qr/` and patched (FNC1, kanji mode, Shift JIS) |
| [ZXing](https://github.com/zxing/zxing) | Apache-2.0 | paulmillr/qr contains code inspired by it. The Shift JIS guess for byte segments without an ECI follows ZXing's behaviour. No ZXing code is copied here |
| [Shape Detection API](https://wicg.github.io/shape-detection-api/) (`BarcodeDetector`) | — | the browser's own reader, used first when present |
| [GS1 General Specifications](https://www.gs1.org/standards/barcodes-epcrfid-id-keys/gs1-general-specifications), [GS1 Digital Link](https://www.gs1.org/standards/gs1-digital-link) | — | GTIN check digit, application identifiers (01, 10, 11, 13, 15, 17, 21), Digital Link URLs |
| [nunkil](https://github.com/YangJongWon/nunkil) | Apache-2.0 (same author) | the shop providers and naming in `server/` started there and are relicensed MIT here |
| [Yahoo!ショッピング](https://developer.yahoo.co.jp/webapi/shopping/), [楽天市場](https://webservice.rakuten.co.jp/), [네이버 쇼핑](https://developers.naver.com/docs/serviceapi/search/shopping/shopping.md), [식품안전나라 C005](https://www.foodsafetykorea.go.kr/api/openApiInfo.do?svc_no=C005), [Open Food Facts](https://world.openfoodfacts.org) | each service's terms | product data. Open Food Facts data is under the ODbL |
| [bwip-js](https://github.com/metafloor/bwip-js), [pngjs](https://github.com/pngjs/pngjs), [segno](https://github.com/heuer/segno) | MIT, MIT, BSD-3-Clause | tests only: rendering barcodes and QR. segno made `web/fixtures/kanji-*.png` |

## License

MIT, see [LICENSE](LICENSE). The vendored paulmillr/qr keeps its own license files in `web/vendor/qr/`.
