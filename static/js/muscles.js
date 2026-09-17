/** Schlichte Muskel-Grafik (vorne/hinten). Hauptmuskeln kräftig, Hilfsmuskeln hell. */

// Formen je Muskel: [Ansicht, SVG-Element] – Koordinaten im 100×210-Raster je Figur
const SHAPES = {
  schulter_vorne: [["f", '<ellipse cx="31" cy="46" rx="7" ry="6"/>'], ["f", '<ellipse cx="69" cy="46" rx="7" ry="6"/>']],
  schulter_seite: [["f", '<ellipse cx="26" cy="49" rx="4" ry="6"/>'], ["f", '<ellipse cx="74" cy="49" rx="4" ry="6"/>'],
    ["b", '<ellipse cx="26" cy="49" rx="4" ry="6"/>'], ["b", '<ellipse cx="74" cy="49" rx="4" ry="6"/>']],
  schulter_hinten: [["b", '<ellipse cx="31" cy="46" rx="7" ry="6"/>'], ["b", '<ellipse cx="69" cy="46" rx="7" ry="6"/>']],
  brust: [["f", '<path d="M37 45 Q50 42 49 50 L49 62 Q40 66 35 60 Q33 52 37 45z"/>'],
    ["f", '<path d="M63 45 Q50 42 51 50 L51 62 Q60 66 65 60 Q67 52 63 45z"/>']],
  bizeps: [["f", '<rect x="21" y="56" width="8" height="20" rx="4"/>'], ["f", '<rect x="71" y="56" width="8" height="20" rx="4"/>']],
  trizeps: [["b", '<rect x="21" y="56" width="8" height="20" rx="4"/>'], ["b", '<rect x="71" y="56" width="8" height="20" rx="4"/>']],
  unterarm: [["f", '<rect x="18" y="79" width="7" height="22" rx="3.5"/>'], ["f", '<rect x="75" y="79" width="7" height="22" rx="3.5"/>'],
    ["b", '<rect x="18" y="79" width="7" height="22" rx="3.5"/>'], ["b", '<rect x="75" y="79" width="7" height="22" rx="3.5"/>']],
  bauch: [["f", '<rect x="42" y="65" width="16" height="32" rx="5"/>']],
  seitl_bauch: [["f", '<path d="M35 66 L40 67 L40 94 L36 92 Q33 80 35 66z"/>'], ["f", '<path d="M65 66 L60 67 L60 94 L64 92 Q67 80 65 66z"/>']],
  oberer_ruecken: [["b", '<path d="M40 38 L60 38 L66 45 L58 60 L42 60 L34 45z"/>']],
  latissimus: [["b", '<path d="M36 55 L44 62 L46 84 L39 80 Q34 68 36 55z"/>'], ["b", '<path d="M64 55 L56 62 L54 84 L61 80 Q66 68 64 55z"/>']],
  unterer_ruecken: [["b", '<rect x="44" y="72" width="12" height="24" rx="4"/>']],
  gesaess: [["b", '<ellipse cx="42" cy="108" rx="9" ry="10"/>'], ["b", '<ellipse cx="58" cy="108" rx="9" ry="10"/>']],
  quadrizeps: [["f", '<rect x="33" y="112" width="13" height="40" rx="6"/>'], ["f", '<rect x="54" y="112" width="13" height="40" rx="6"/>']],
  adduktoren: [["f", '<path d="M46 112 L50 112 L50 138 L47 136z"/>'], ["f", '<path d="M54 112 L50 112 L50 138 L53 136z"/>']],
  beinbeuger: [["b", '<rect x="33" y="120" width="13" height="34" rx="6"/>'], ["b", '<rect x="54" y="120" width="13" height="34" rx="6"/>']],
  waden: [["b", '<ellipse cx="40" cy="172" rx="6" ry="14"/>'], ["b", '<ellipse cx="60" cy="172" rx="6" ry="14"/>'],
    ["f", '<rect x="36" y="160" width="7" height="30" rx="3.5"/>'], ["f", '<rect x="57" y="160" width="7" height="30" rx="3.5"/>']],
};

const SILHOUETTE = `<g class="mm-body">
  <circle cx="50" cy="20" r="11"/>
  <path d="M44 30 h12 v8 h-12z"/>
  <path d="M32 38 Q50 34 68 38 L78 50 L84 100 L76 102 L72 64 L66 62 L68 104 L70 200 L56 200 L51 118 L49 118 L44 200 L30 200 L32 104 L34 62 L28 64 L24 102 L16 100 L22 50z"/>
</g>`;

function figure(view, primary, secondary, label) {
  const parts = [];
  for (const [muscle, shapes] of Object.entries(SHAPES)) {
    const cls = primary.includes(muscle) ? "mm-primary" : secondary.includes(muscle) ? "mm-secondary" : "mm-idle";
    for (const [v, svg] of shapes) {
      if (v === view) parts.push(svg.replace(/^<(\w+)/, `<$1 class="${cls}"`));
    }
  }
  return `<figure class="mm-fig"><svg viewBox="0 0 100 210" role="img" aria-label="${label}">
    ${SILHOUETTE}${parts.join("")}</svg><figcaption>${label}</figcaption></figure>`;
}

export function muscleMap(primary = [], secondary = []) {
  return `<div class="muscle-map">${figure("f", primary, secondary, "vorne")}${figure("b", primary, secondary, "hinten")}</div>`;
}
