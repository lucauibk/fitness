/** Tab mit Unterseiten (z. B. #training/plan). Jede Unterseite ist ein Modul mit
 *  mount(el) und load() – genau wie die Haupt-Tabs – und bekommt ein eigenes Element,
 *  das beim Wechsel nur versteckt wird. So bleiben Eingaben und Scroll-Zustand erhalten. */
import { $, prefs } from "./util.js";

export function subTabs(name, subs, fallback) {
  let root = null;
  let current = null;
  const mounted = new Set();

  const valid = (s) => (subs[s] ? s : null);

  function paint() {
    root.querySelectorAll("[data-sub]").forEach((a) => {
      const on = a.dataset.sub === current;
      a.classList.toggle("active", on);
      a.setAttribute("aria-current", on ? "page" : "false");
    });
    root.querySelectorAll("[data-panel]").forEach((el) => { el.hidden = el.dataset.panel !== current; });
  }

  return {
    title: (sub) => subs[valid(sub) || current || fallback]?.[0],
    setSub(sub) {
      current = valid(sub) || valid(prefs.get(`${name}.sub`)) || fallback;
      prefs.set(`${name}.sub`, current);
      if (root) paint();
      return current;
    },
    current: () => current,
    async mount(el) {
      root = el;
      // Neu einhaengen (Profilwechsel ersetzt die Ansicht): die Unterseiten muessen in die
      // neuen Panels, sonst zeichnen sie weiter in die alten, ausgehaengten Elemente.
      mounted.clear();
      el.innerHTML = `<nav class="subnav" aria-label="Bereich">
          ${Object.entries(subs).map(([k, [label]]) => `<a href="#${name}/${k}" data-sub="${k}">${label}</a>`).join("")}
        </nav>
        ${Object.keys(subs).map((k) => `<div data-panel="${k}" hidden></div>`).join("")}`;
      if (!current) this.setSub();
      paint();
    },
    async load() {
      if (!current) this.setSub();
      paint();
      const panel = $(`[data-panel="${current}"]`, root);
      const mod = subs[current][1];
      if (!mounted.has(current)) {
        await mod.mount(panel);
        mounted.add(current);
      }
      await mod.load();
    },
  };
}
