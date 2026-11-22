/** QR-Code vom mescan-Gerät im Studio (ViCoach).
 *  Der Code enthält nur einen Link, der die Messung dem ViCoach-Konto zuordnet:
 *    https://vicoach.io/assign-measurement/mescan?scopeToken=<JWT>&measurementId=<24 hex>
 *  Die Messwerte selbst stehen nicht darin. Wir merken uns die measurementId (kein doppelter
 *  Import) und reichen den Link an ViCoach weiter; die Werte kommen per Screenshot. */

const HOSTS = new Set(["vicoach.io", "www.vicoach.io"]);
const ID = /^[0-9a-f]{24}$/i;

/** Liefert { measurementId, url } oder null, wenn es kein ViCoach-Messungs-Link ist. */
export function parseVicoachLink(text) {
  let u;
  try { u = new URL(String(text || "").trim()); } catch { return null; }
  if (u.protocol !== "https:" || !HOSTS.has(u.hostname.toLowerCase())) return null;
  if (!u.pathname.startsWith("/assign-measurement/")) return null;
  const id = u.searchParams.get("measurementId") || "";
  if (!ID.test(id)) return null;
  return { measurementId: id.toLowerCase(), url: u.href };
}

/** client_id der Messung: macht den Import derselben Studio-Messung idempotent. */
export const vicoachClientId = (measurementId) => `vicoach:${measurementId.toLowerCase()}`;
