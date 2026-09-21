/** Barcode-Scanner über die Handykamera.
 *  Nutzt BarcodeDetector, wo der Browser ihn hat (Android Chrome), sonst die mitgelieferte
 *  ZXing-Bibliothek (iPhone/Safari). Man kann den Code immer auch von Hand eintippen.
 *  Liefert den Code oder null (abgebrochen). */
import { icon } from "./util.js";

let zxingLoading = null;

function loadZxing() {
  if (window.ZXing) return Promise.resolve(window.ZXing);
  zxingLoading ||= new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = "/vendor/zxing.min.js";
    s.onload = () => resolve(window.ZXing);
    s.onerror = () => { zxingLoading = null; reject(new Error("Scanner konnte nicht geladen werden.")); };
    document.head.appendChild(s);
  });
  return zxingLoading;
}

const FORMATS = ["ean_13", "ean_8", "upc_a", "upc_e"];

export function scanBarcode() {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "scanner";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-label", "Barcode scannen");
    const lastFocus = document.activeElement;
    overlay.innerHTML = `<video playsinline muted></video>
      <div class="scanner-frame" aria-hidden="true"></div>
      <div class="scanner-bar">
        <p data-msg>Kamera wird gestartet …</p>
        <form class="scanner-manual">
          <input inputmode="numeric" pattern="[0-9]{6,14}" placeholder="Barcode eintippen" aria-label="Barcode eintippen">
          <button type="submit" class="btn small">OK</button>
        </form>
        <button type="button" class="btn ghost" data-cancel>${icon("x")} Abbrechen</button>
      </div>`;
    document.body.appendChild(overlay);
    const video = overlay.querySelector("video");
    const msg = overlay.querySelector("[data-msg]");
    let stream = null;
    let reader = null;
    let raf = null;
    let done = false;

    function finish(code) {
      if (done) return;
      done = true;
      cancelAnimationFrame(raf);
      try { reader?.reset(); } catch { /* egal */ }
      stream?.getTracks().forEach((t) => t.stop());
      overlay.remove();
      document.removeEventListener("keydown", onKey, true);
      if (lastFocus?.isConnected) lastFocus.focus({ preventScroll: true });
      if (code) navigator.vibrate?.(80);
      resolve(code || null);
    }

    // Esc schliesst den Scanner, Tab bleibt in ihm (das Sheet darunter haelt still, util.js).
    function onKey(e) {
      // stopPropagation: sonst sieht der Sheet-Handler (util.js) den Scanner schon nicht
      // mehr und schliesst das Sheet darunter gleich mit.
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(null); return; }
      if (e.key !== "Tab") return;
      const items = [...overlay.querySelectorAll("button, input")];
      const [first, last] = [items[0], items[items.length - 1]];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
    document.addEventListener("keydown", onKey, true);
    overlay.addEventListener("scanner-close", () => finish(null));
    // Fokus auf Abbrechen, nicht ins Eingabefeld – die Tastatur wuerde die Kamera verdecken.
    overlay.querySelector("[data-cancel]").focus({ preventScroll: true });

    overlay.querySelector("[data-cancel]").addEventListener("click", () => finish(null));
    overlay.querySelector("form").addEventListener("submit", (ev) => {
      ev.preventDefault();
      const v = ev.target.querySelector("input").value.trim();
      if (/^\d{6,14}$/.test(v)) finish(v);
      else msg.textContent = "Bitte 6 bis 14 Ziffern eingeben.";
    });

    (async () => {
      if (!navigator.mediaDevices?.getUserMedia) {
        msg.textContent = "Kamera ist hier nicht verfügbar – bitte den Code eintippen.";
        return;
      }
      try {
        if ("BarcodeDetector" in window) {
          const supported = await window.BarcodeDetector.getSupportedFormats?.() || FORMATS;
          const detector = new window.BarcodeDetector({ formats: FORMATS.filter((f) => supported.includes(f)) });
          stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
          // Abgebrochen, waehrend die Kamera noch startete: sofort wieder aus (Kamera-LED).
          if (done) { stream.getTracks().forEach((t) => t.stop()); return; }
          video.srcObject = stream;
          await video.play();
          msg.textContent = "Barcode in den Rahmen halten.";
          const loop = async () => {
            if (done) return;
            try {
              const codes = await detector.detect(video);
              if (codes.length) return finish(codes[0].rawValue);
            } catch { /* nächster Versuch */ }
            raf = requestAnimationFrame(loop);
          };
          loop();
        } else {
          const ZXing = await loadZxing();
          if (done) return;
          const hints = new Map([[ZXing.DecodeHintType.POSSIBLE_FORMATS,
            [ZXing.BarcodeFormat.EAN_13, ZXing.BarcodeFormat.EAN_8, ZXing.BarcodeFormat.UPC_A, ZXing.BarcodeFormat.UPC_E]]]);
          reader = new ZXing.BrowserMultiFormatReader(hints);
          msg.textContent = "Barcode in den Rahmen halten.";
          await reader.decodeFromConstraints({ video: { facingMode: "environment" } }, video, (result) => {
            if (result) finish(result.getText());
          });
        }
      } catch (e) {
        msg.textContent = e?.name === "NotAllowedError"
          ? "Kein Zugriff auf die Kamera – bitte erlauben oder den Code eintippen."
          : `Kamera-Fehler: ${e?.message || e} – bitte den Code eintippen.`;
      }
    })();
  });
}
