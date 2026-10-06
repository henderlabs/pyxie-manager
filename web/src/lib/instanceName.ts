/** Which PyXie this is, from the address it is served on, so a tab or window says CRE-PyXie or
 * ST-PyXie instead of just "PyXie" when two installs are open side by side.
 *
 *   cre-pyxie.slc.crengland.com  ->  CRE-PyXie        st-pyxie.example.com  ->  ST-PyXie
 *   pyxie.example.com            ->  PyXie            ops-console.example   ->  PyXie (ops-console)
 *   an IP address or localhost   ->  PyXie
 *
 * PYXIE_INSTANCE_NAME, when set on the web container, wins over all of this. */
export function instanceNameFromHost(host: string | null | undefined, override?: string | null): string {
  const o = (override || "").trim();
  if (o) return o;
  const h = (host || "").split(",")[0].trim().toLowerCase().replace(/:\d+$/, "");
  if (!h || h === "localhost" || h.includes(":") || /^[\d.]+$/.test(h)) return "PyXie";
  const label = h.split(".")[0];
  if (!label.includes("pyxie")) return `PyXie (${label})`;
  return label
    .split("-")
    .filter(Boolean)
    .map((part) => (part === "pyxie" ? "PyXie" : part.toUpperCase()))
    .join("-");
}
