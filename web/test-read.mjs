// Reading tests: barcodes rendered with bwip-js, read back with Quagga2 in Node.
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import bwipjs from "bwip-js";
import Quagga from "@ericblade/quagga2";
import decodeQR from "./vendor/qr/decode.js";
import { readFileSync } from "node:fs";
import { PNG } from "pngjs";
import { decodeImage, isValidGtin, upcEtoA, productCode, Confirmer, parseGs1, interpret } from "./barcode-id.js";

let failed = 0;
const check = (name, ok, detail = "") => {
  console.log(`${ok ? "ok  " : "FAIL"} ${name}${detail ? "  " + detail : ""}`);
  if (!ok) failed++;
};

// --- pure parts
for (const c of ["4901330500009", "8801791000055", "3017620422003", "96385074", "036000291452"]) check(`valid ${c}`, isValidGtin(c));
for (const c of ["4901330500008", "490133050000", "abc", "123"]) check(`invalid ${c}`, !isValidGtin(c));
check("UPC-E -> UPC-A", upcEtoA("04252614") === "042100005264", upcEtoA("04252614"));
check("UPC-E read is looked up as GTIN-13", productCode("04252614", "upc_e") === "0042100005264");
check("UPC-A read is looked up as GTIN-13", productCode("036000291452", "upc_a") === "0036000291452");
check("misread refused", productCode("4901330500008", "ean_13") === null);
{
  const c = new Confirmer(2);
  const seq = ["4901330500009", "4901330500009", "4901330500009", "8801791000055", "8801791000055"].map((x) => c.push(x));
  check("confirmed once per run, on the 2nd frame", JSON.stringify(seq) === JSON.stringify([null, "4901330500009", null, null, "8801791000055"]), JSON.stringify(seq));
}

// --- GS1 data in QR / DataMatrix
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
{
  const g = parseGs1("https://id.gs1.org/01/04901330500009/10/LOT7?17=261231");
  check("Digital Link: GTIN, lot, expiry", g?.gtin === "4901330500009" && g.lot === "LOT7" && g.expiry === "2026-12-31", JSON.stringify(g));
  const e = parseGs1("]d201049013305000091726040010A12B21SN99".replace(/\x1d/g, ""));
  check("element string: DD 00 = last day of the month", e?.expiry === "2026-04-30" && e.lot === "A12B" && e.serial === "SN99", JSON.stringify(e));
  const h = parseGs1("(01)08801791000055(17)270115(10)K9");
  check("human-readable (01)(17)(10)", h?.gtin === "8801791000055" && h.expiry === "2027-01-15" && h.lot === "K9", JSON.stringify(h));
  check("GTIN with a wrong check digit is not a product", parseGs1("(01)04901330500008(17)261231") === null);
  check("a plain URL is not GS1", parseGs1("https://example.com/campaign") === null);
}
check("QR with a URL", eq(interpret("https://example.com/x", "qr_code"), { kind: "url", code: null, format: "qr_code", text: "https://example.com/x" }));
check("QR with GS1 -> product code", interpret("(01)04901330500009(17)261231", "qr_code")?.code === "4901330500009");
check("1D misread -> nothing", interpret("4901330500008", "ean_13") === null);
{
  const c = new Confirmer(2);
  check("2D read counts at once", c.push("https://example.com/x", true) === "https://example.com/x");
  check("...and is not repeated while in view", c.push("https://example.com/x", true) === null);
}

// --- reading rendered barcodes
const dir = mkdtempSync(join(tmpdir(), "barcode-id-"));
const cases = [
  ["JAN (Japan)", "ean13", "4901330500009", "4901330500009", {}],
  ["KAN (Korea)", "ean13", "8801791000055", "8801791000055", {}],
  ["EAN-8", "ean8", "96385074", "96385074", {}],
  ["UPC-A", "upca", "036000291452", "0036000291452", {}],
  ["UPC-E", "upce", "04252614", "0042100005264", {}],
  ["turned 90°", "ean13", "4901330500009", "4901330500009", { rotate: "R" }],
  ["small in a wide view", "ean13", "8801043014809", "8801043014809", { scale: 2, paddingwidth: 300, paddingheight: 200 }],
];
for (const [name, bcid, text, want, extra] of cases) {
  const png = await bwipjs.toBuffer({ bcid, text, scale: 3, height: 15, includetext: true, backgroundcolor: "FFFFFF", paddingwidth: 20, paddingheight: 10, ...extra });
  const file = join(dir, `${bcid}-${text}-${extra.rotate ?? ""}.png`);
  writeFileSync(file, png);
  const r = await decodeImage(file, { quagga: Quagga });
  check(`read ${name}`, r?.code === want, `got ${r?.code ?? "-"} (${r?.format ?? "-"})`);
}
rmSync(dir, { recursive: true, force: true });

// --- reading rendered QR codes (paulmillr/qr), as RGBA pixels
const qrCases = [
  ["URL", "qrcode", "https://example.com/campaign?id=7", { kind: "url", code: null }, {}],
  ["GS1 Digital Link -> GTIN + expiry", "qrcode", "https://id.gs1.org/01/04901330500009/10/LOT7?17=261231",
    { kind: "gs1", code: "4901330500009", expiry: "2026-12-31", lot: "LOT7" }, {}],
  ["Korean text (UTF-8)", "qrcode", "유통기한 2026.04.23", { kind: "text", code: null }, {}],
  ["turned 90°", "qrcode", "https://example.com/r", { kind: "url" }, { rotate: "R" }],
  ["small in a wide view", "qrcode", "https://example.com/s", { kind: "url" }, { scale: 2, paddingwidth: 400, paddingheight: 300 }],
  // FNC1 mode: paulmillr/qr 0.7.2 does not read it; vendor/qr/barcode-id.patch adds it
  ["GS1 element-string QR (FNC1)", "gs1qrcode", "(01)04901330500009(17)261231(10)LOT7",
    { kind: "gs1", code: "4901330500009", expiry: "2026-12-31", lot: "LOT7" }, {}],
  ["GS1 QR, '%' in the lot", "gs1qrcode", "(01)08801791000055(10)A%B(17)270100",
    { kind: "gs1", code: "8801791000055", expiry: "2027-01-31", lot: "A%B" }, {}],
];
for (const [name, bcid, text, want, extra] of qrCases) {
  const png = PNG.sync.read(await bwipjs.toBuffer({ bcid, text, scale: 4, backgroundcolor: "FFFFFF", paddingwidth: 16, paddingheight: 16, ...extra }));
  const r = await decodeImage({ width: png.width, height: png.height, data: png.data }, { decodeQR });
  const ok = want === null ? r === null
    : r && Object.entries(want).every(([k, v]) => r[k] === v) && (want.kind !== "url" && want.kind !== "text" || r.text === text);
  check(`QR ${name}`, ok, r ? `${r.kind} ${r.code ?? ""} ${r.expiry ?? ""} "${r.text}"` : "-");
}
// Japanese QR made with segno (web/fixtures): kanji mode, and Shift JIS bytes with no ECI
for (const [file, want] of [["kanji-mode.png", "賞味期限"], ["kanji-mixed.png", "賞味期限2026年4月23日"]]) {
  const png = PNG.sync.read(readFileSync(new URL(`./fixtures/${file}`, import.meta.url)));
  const r = await decodeImage({ width: png.width, height: png.height, data: png.data }, { decodeQR });
  check(`QR ${file}`, r?.text === want, r ? `"${r.text}"` : "-");
}

console.log(failed ? `${failed} failed` : "all passed");
process.exit(failed ? 1 : 0);
