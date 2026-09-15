/** Compares dotted/hyphenated version-ish strings numerically, segment by
 * segment (e.g. "9.2.18" vs "9.2.3" -- plain string comparison gets this
 * backwards, since "9.2.18" < "9.2.3" lexicographically at the third
 * character). Extracts every run of digits in order and compares them as
 * numbers; a null/undefined/empty value always sorts last. */
export function compareVersions(a: string | null | undefined, b: string | null | undefined): number {
  if (!a && !b) return 0;
  if (!a) return 1;
  if (!b) return -1;
  const aParts = a.match(/\d+/g)?.map(Number) ?? [];
  const bParts = b.match(/\d+/g)?.map(Number) ?? [];
  const len = Math.max(aParts.length, bParts.length);
  for (let i = 0; i < len; i++) {
    const av = aParts[i] ?? 0;
    const bv = bParts[i] ?? 0;
    if (av !== bv) return av - bv;
  }
  return 0;
}
