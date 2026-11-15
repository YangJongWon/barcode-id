# vendored paulmillr/qr 0.7.2

`index.js`, `decode.js`, `LICENSE` and `LICENSE-MIT` come from [qr@0.7.2](https://github.com/paulmillr/qr) (MIT OR Apache-2.0, used under MIT). Upstream notes that the library contains code inspired by [ZXing](https://github.com/zxing/zxing) (Apache-2.0). `decode.js` is patched; [barcode-id.patch](barcode-id.patch) shows the whole change:

- FNC1 in first position (mode 5): the GS1 element string comes out with the AIM `]Q3` in front, and `%` in alphanumeric segments becomes GS (0x1D); `%%` becomes a literal `%`.
- FNC1 in second position (mode 9): the output starts with `]Q5` and the application indicator.
- Kanji mode (mode 8): 13-bit characters are turned back into Shift JIS and decoded.
- Byte segments without an ECI: decoded as UTF-8 when they are valid UTF-8, else as Shift JIS.

To update: copy the new release's files here, then apply the patch (`patch -p1 < barcode-id.patch`, run inside this folder) or port it by hand. The patch could also go upstream.
