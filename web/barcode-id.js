// Read product barcodes and QR codes in the browser, and identify products with the barcode-id server.
//
// Engines, best first:
//   1. the browser's own BarcodeDetector (Chrome / Edge on Android, macOS, ChromeOS): EAN / UPC, QR,
//      DataMatrix, no download. Not in Safari (iOS) or Firefox.
//   2. otherwise Quagga2 (MIT) for 1D product codes + paulmillr/qr (MIT) for QR, both on the same
//      camera stream. paulmillr/qr is vendored in vendor/qr/ with a small patch (barcode-id.patch):
//      FNC1 modes (GS1 element-string QR), kanji mode, and Shift JIS bytes without an ECI.
//
// Browser:
//   <script src="https://cdn.jsdelivr.net/npm/@ericblade/quagga2@1.13.0/dist/quagga.min.js"></script>
//   <script type="module">
//   import decodeQR from "./vendor/qr/decode.js";
//   import { startScanner, identify } from "./barcode-id.js";
//   const scanner = await startScanner(box, { decodeQR,
//     onCode: async (code, info) => show(await identify(code, server), info),   // a product (GTIN)
//     onText: (text, info) => showText(text) });                                // any other QR
//   </script>
// Node: pass opts.quagga (import Quagga from "@ericblade/quagga2") and opts.decodeQR
// (import decodeQR from "./vendor/qr/decode.js"); give QR stills as {width, height, data} RGBA.
//
// 1D readers misread now and then: a 1D code counts only when its check digit is right AND the same
// code was read on CONFIRM_FRAMES frames in a row. QR / DataMatrix carry error correction: one read.

export const READERS = ["ean_reader", "ean_8_reader", "upc_reader", "upc_e_reader"];
export const CONFIRM_FRAMES = 2;
const NATIVE_FORMATS = ["ean_13", "ean_8", "upc_a", "upc_e", "qr_code", "data_matrix"];
const TWO_D = new Set(["qr_code", "data_matrix"]);

// ------------------------------------------------------------------ codes

// GTIN check digit (EAN-13 / JAN, EAN-8, UPC-A, GTIN-14): mod 10, weights 3,1 from the right.
export function isValidGtin(code) {
  if (!/^\d+$/.test(code) || ![8, 12, 13, 14].includes(code.length)) return false;
  const d = [...code].map(Number), check = d.pop();
  let sum = 0;
  d.reverse().forEach((v, i) => { sum += v * (i % 2 === 0 ? 3 : 1); });
  return (10 - (sum % 10)) % 10 === check;
}

// UPC-E (8 digits, number system 0/1) -> UPC-A (12): shops index the long form.
export function upcEtoA(e) {
  if (!/^[01]\d{7}$/.test(e)) return null;
  const [ns, a, b, c, d, f, g, check] = [...e];
  let body;
  if ("012".includes(g)) body = `${a}${b}${g}0000${c}${d}${f}`;
  else if (g === "3") body = `${a}${b}${c}00000${d}${f}`;
  else if (g === "4") body = `${a}${b}${c}${d}00000${f}`;
  else body = `${a}${b}${c}${d}${f}0000${g}`;
  return `${ns}${body}${check}`;
}

// A 1D read -> the GTIN to look up: 13 digits (UPC-A gets its leading 0, UPC-E is expanded first; a
// GTIN-14 of a single item drops its leading 0), EAN-8 stays 8; null when not a valid product code.
export function productCode(code, format) {
  if (!code) return null;
  if (format === "upc_e") code = upcEtoA(code) ?? code;
  if (code.length === 12) code = "0" + code;
  if (code.length === 14 && code[0] === "0") code = code.slice(1);
  return isValidGtin(code) ? code : null;
}

// GS1 YYMMDD -> "YYYY-MM-DD" (DD 00 = the month's last day); null when not a date.
function gs1Date(s) {
  const m = /^(\d\d)(\d\d)(\d\d)$/.exec(s ?? "");
  if (!m) return null;
  const y = 2000 + Number(m[1]), mo = Number(m[2]);
  let d = Number(m[3]);
  if (mo < 1 || mo > 12) return null;
  const last = new Date(Date.UTC(y, mo, 0)).getUTCDate();
  if (d === 0) d = last;
  if (d > last) return null;
  return `${y}-${String(mo).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

// AIs worth reading on retail and medicine packs: fixed lengths, or variable (ends at GS / end).
const AI_FIXED = { "01": 14, "11": 6, "13": 6, "15": 6, "17": 6 };
const AI_VAR = { "10": 20, "21": 20 };

// GS1 data in a QR / DataMatrix -> {gtin, expiry, bestBefore, lot, serial} or null. Reads:
//   GS1 Digital Link   https://id.gs1.org/01/04901330500009/10/LOT1?17=261231
//   element string     ]d2 0104901330500009 17261231 10LOT1 (FNC1 = \x1d), or "(01)...(17)...".
export function parseGs1(text) {
  if (!text) return null;
  const ai = {};
  let m;
  if ((m = /^https?:\/\/[^/]+(\/.*)$/i.exec(text.trim()))) { // Digital Link
    const url = new URL(text.trim());
    const parts = url.pathname.split("/").filter(Boolean);
    for (let i = parts.indexOf("01"); i >= 0 && i + 1 < parts.length; i += 2) ai[parts[i]] = decodeURIComponent(parts[i + 1]);
    for (const [k, v] of url.searchParams) if (/^\d{2,4}$/.test(k)) ai[k] = v;
    if (!ai["01"]) return null;
  } else {
    let s = text.replace(/^\][A-Za-z]\d/, ""); // symbology identifier
    if (/^\(\d{2,4}\)/.test(s)) s = s.replace(/\((\d{2,4})\)/g, "\x1d$1").replace(/^\x1d/, ""); // human-readable form
    let i = 0;
    while (i < s.length) {
      if (s[i] === "\x1d") { i++; continue; }
      const k = s.slice(i, i + 2);
      if (AI_FIXED[k]) { ai[k] = s.slice(i + 2, i + 2 + AI_FIXED[k]); i += 2 + AI_FIXED[k]; }
      else if (AI_VAR[k]) {
        const end = s.indexOf("\x1d", i + 2), stop = end < 0 ? s.length : Math.min(end, i + 2 + AI_VAR[k]);
        ai[k] = s.slice(i + 2, stop); i = stop;
      } else break; // an AI we do not read: what follows cannot be split safely
    }
    if (!ai["01"]) return null;
  }
  if (!/^\d{14}$/.test(ai["01"]) || !isValidGtin(ai["01"])) return null;
  return {
    gtin: ai["01"][0] === "0" ? ai["01"].slice(1) : ai["01"],
    expiry: gs1Date(ai["17"]), bestBefore: gs1Date(ai["15"]), lot: ai["10"] ?? null, serial: ai["21"] ?? null,
  };
}

// Any read -> what it means. kind: "product" (1D GTIN), "gs1" (2D with a GTIN, maybe an expiry date),
// "url" / "text" (other QR content), or null for a 1D read that fails its check digit.
export function interpret(text, format) {
  if (!TWO_D.has(format)) {
    const code = productCode(text, format);
    return code ? { kind: "product", code, format, text } : null;
  }
  const g = parseGs1(text);
  if (g) return { kind: "gs1", code: g.gtin, format, text, expiry: g.expiry, bestBefore: g.bestBefore, lot: g.lot, serial: g.serial };
  return { kind: /^https?:\/\//i.test(text) ? "url" : "text", code: null, format, text };
}

// The same 1D code on `frames` consecutive reads; 2D reads pass at once. Each code reported once
// per run (holding it in view does not repeat it).
export class Confirmer {
  constructor(frames = CONFIRM_FRAMES) { this.frames = frames; this.last = null; this.count = 0; }
  push(key, twoD = false) {
    if (!key) return null;
    this.count = key === this.last ? this.count + 1 : 1;
    this.last = key;
    return this.count === (twoD ? 1 : this.frames) ? key : null;
  }
  reset() { this.last = null; this.count = 0; }
}

// ------------------------------------------------------------------ engines

const quaggaOf = (opts) => opts.quagga ?? globalThis.Quagga ?? null;
const qrOf = (opts) => opts.decodeQR ?? globalThis.decodeQR ?? null;
const isPixels = (x) => x && typeof x === "object" && "data" in x && "width" in x && "height" in x;

// A video / image / bitmap / canvas -> RGBA pixels, the longer side at most maxSide (browser).
let scratch = null;
function pixelsOf(source, maxSide = 1280) {
  const w = source.videoWidth || source.naturalWidth || source.width, h = source.videoHeight || source.naturalHeight || source.height;
  if (!w || !h) return null;
  const s = Math.min(1, maxSide / Math.max(w, h)), cw = Math.round(w * s), ch = Math.round(h * s);
  scratch ??= document.createElement("canvas");
  if (scratch.width !== cw || scratch.height !== ch) { scratch.width = cw; scratch.height = ch; }
  const ctx = scratch.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(source, 0, 0, cw, ch);
  return ctx.getImageData(0, 0, cw, ch);
}

// QR from RGBA pixels -> text or null (paulmillr/qr throws when it finds none).
function readQR(decodeQR, pixels, still) {
  try {
    // a still gets more retries than a live frame (the next frame is the live retry)
    return decodeQR(pixels, still ? { effort: Infinity, timeLimit: 300 } : {}) || null;
  } catch {
    return null;
  }
}

// The browser's BarcodeDetector with the formats we want, or null (absent, or no product formats).
export async function nativeDetector() {
  if (typeof globalThis.BarcodeDetector !== "function") return null;
  try {
    const have = await globalThis.BarcodeDetector.getSupportedFormats();
    const formats = NATIVE_FORMATS.filter((f) => have.includes(f));
    if (!formats.includes("ean_13")) return null;
    return new globalThis.BarcodeDetector({ formats });
  } catch {
    return null;
  }
}

// One still image -> interpret() result or null.
// src: image URL / data URL / Blob / ImageBitmap / canvas (browser); in Node a file path (1D, Quagga2)
// or {width, height, data} RGBA (QR).
// Quagga2's locator finds tilted codes but misses short ones (EAN-8, UPC-E) and small ones in a
// wide view; scanning lines across the middle without it catches those.
const CENTRE = { top: "25%", right: "25%", bottom: "25%", left: "25%" };
export async function decodeImage(src, opts = {}) {
  const native = opts.native === false ? null : await nativeDetector();
  if (native) {
    const img = typeof src === "string" ? await createImageBitmap(await (await fetch(src)).blob())
      : src instanceof Blob ? await createImageBitmap(src) : src;
    for (const r of await native.detect(img)) {
      const hit = interpret(r.rawValue, r.format);
      if (hit) return { ...hit, engine: "native" };
    }
    return null;
  }
  const Quagga = quaggaOf(opts);
  if (Quagga && typeof src === "string") {
    const run = (locate, area) => new Promise((resolve) => {
      Quagga.decodeSingle({
        src, numOfWorkers: 0, locate,
        inputStream: { size: opts.size ?? 1280, ...(area ? { area } : {}) },
        decoder: { readers: opts.readers ?? READERS },
      }, (r) => resolve(r?.codeResult ? { code: r.codeResult.code, format: r.codeResult.format } : null));
    });
    for (const [locate, area] of [[true, null], [false, null], [false, CENTRE]]) {
      const r = await run(locate, area);
      const hit = interpret(r?.code, r?.format);
      if (hit) return { ...hit, engine: "quagga2" };
    }
  }
  const decodeQR = qrOf(opts);
  if (decodeQR) {
    let pixels = isPixels(src) ? src : null;
    if (!pixels && typeof document !== "undefined") {
      const img = typeof src === "string" ? await createImageBitmap(await (await fetch(src)).blob())
        : src instanceof Blob ? await createImageBitmap(src) : src;
      pixels = pixelsOf(img, opts.size ?? 1920);
    }
    const text = pixels && readQR(decodeQR, pixels, true);
    if (text) return { ...interpret(text, "qr_code"), engine: "paulmillr/qr" };
  }
  return null;
}

// Live camera scanning into `target` (an element; a <video> is put in it).
//   onCode(code, info)  a product: a confirmed 1D GTIN, or a GS1 QR / DataMatrix (info.expiry, .lot)
//   onText(text, info)  any other QR content (a URL, text)
// Returns {engine, stop(), reset()}. reset() lets the code in view be reported again.
export async function startScanner(target, opts = {}) {
  const confirmer = new Confirmer(opts.frames ?? CONFIRM_FRAMES);
  const emit = (hit, engine) => {
    if (!hit) return;
    const key = hit.code ?? hit.text;
    if (!confirmer.push(key, TWO_D.has(hit.format))) return;
    const info = { ...hit, engine };
    if (hit.code) opts.onCode?.(hit.code, info);
    else opts.onText?.(hit.text, info);
  };

  const native = opts.native === false ? null : await nativeDetector();
  if (native) {
    const video = document.createElement("video");
    video.muted = true; video.playsInline = true; video.setAttribute("playsinline", "");
    target.append(video);
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "environment", width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false,
    });
    video.srcObject = stream;
    await video.play();
    let running = true, busy = false;
    const timer = setInterval(async () => {
      if (!running || busy || video.readyState < 2) return;
      busy = true;
      try { for (const r of await native.detect(video)) emit(interpret(r.rawValue, r.format), "native"); }
      catch { /* a frame that could not be read */ }
      busy = false;
    }, opts.interval ?? 100);
    return {
      engine: "native",
      stop() { running = false; clearInterval(timer); stream.getTracks().forEach((t) => t.stop()); video.remove(); },
      reset() { confirmer.reset(); },
    };
  }

  const Quagga = quaggaOf(opts), decodeQR = qrOf(opts);
  if (!Quagga) throw new Error("No barcode engine: this browser has no BarcodeDetector and Quagga2 is not loaded");
  await new Promise((resolve, reject) => {
    Quagga.init({
      inputStream: {
        type: "LiveStream", target,
        constraints: { facingMode: "environment", width: { ideal: 1280 }, height: { ideal: 720 } },
      },
      locate: true, numOfWorkers: 0, frequency: opts.frequency ?? 10,
      decoder: { readers: opts.readers ?? READERS },
    }, (err) => (err ? reject(err) : resolve()));
  });
  const onDetected = (r) => emit(interpret(r?.codeResult?.code, r?.codeResult?.format), "quagga2");
  Quagga.onDetected(onDetected);
  Quagga.start();
  // QR on the same camera stream: paulmillr/qr reads Quagga2's <video> a few times a second.
  // Each try is capped at about one frame's time, so 1D scanning keeps its pace.
  let qrTimer = null;
  if (decodeQR) {
    const video = target.querySelector("video");
    qrTimer = setInterval(() => {
      if (!video || video.readyState < 2) return;
      const pixels = pixelsOf(video, opts.qrSize ?? 960);
      const text = pixels && readQR(decodeQR, pixels, false);
      if (text) emit(interpret(text, "qr_code"), "paulmillr/qr");
    }, opts.qrInterval ?? 150);
  }
  return {
    engine: decodeQR ? "quagga2+paulmillr/qr" : "quagga2",
    stop() { clearInterval(qrTimer); Quagga.offDetected(onDetected); Quagga.stop(); },
    reset() { confirmer.reset(); },
  };
}

// ------------------------------------------------------------------ identify

// code -> what it is and what it costs, from the barcode-id server (server/).
// server: {url, token, region: "jp" | "kr"}. Throws with the server's message on failure.
export async function identify(code, server) {
  const u = new URL("/product", server.url);
  u.searchParams.set("jan", code);
  u.searchParams.set("region", server.region ?? (code.startsWith("880") ? "kr" : "jp"));
  const res = await fetch(u, { headers: server.token ? { Authorization: `Bearer ${server.token}` } : {} });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
  return body; // {identity, cheapest, spoken_name, matches, ...}
}
