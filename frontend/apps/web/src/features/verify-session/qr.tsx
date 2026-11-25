import QRCode from "qrcode";

/** The modules of a QR code for `text` (error correction M), as rows of booleans. */
export function qrMatrix(text: string): boolean[][] {
  const { modules } = QRCode.create(text, { errorCorrectionLevel: "M" });
  const rows: boolean[][] = [];
  for (let r = 0; r < modules.size; r++) {
    rows.push(Array.from({ length: modules.size }, (_, c) => Boolean(modules.get(r, c))));
  }
  return rows;
}

/** A QR code drawn as one SVG path (no image URL, no inline HTML), with the quiet zone the standard asks for. The link it carries is one-use and holds no personal data (WF-01 S06). */
export function QrCode({ text, label, size = 200 }: { text: string; label: string; size?: number }) {
  const m = qrMatrix(text);
  const quiet = 4;
  const n = m.length + quiet * 2;
  let d = "";
  m.forEach((row, r) => row.forEach((on, c) => (d += on ? `M${c + quiet} ${r + quiet}h1v1h-1z` : "")));
  return (
    <svg role="img" aria-label={label} width={size} height={size} viewBox={`0 0 ${n} ${n}`} shapeRendering="crispEdges" className="rounded bg-white">
      <rect width={n} height={n} fill="#fff" />
      <path d={d} fill="#000" />
    </svg>
  );
}
