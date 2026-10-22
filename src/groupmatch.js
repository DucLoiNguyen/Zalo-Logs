// Khớp danh sách nhóm trong config (tên hoặc ID) với danh sách nhóm thật của tài khoản.

export const normalize = (s) =>
  String(s ?? "")
    .normalize("NFC")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();

/**
 * @param {string[]} entries  các dòng trong config.groups (tên hoặc ID)
 * @param {{id:string,name:string}[]} allGroups  nhóm thật của tài khoản
 * @returns {{ matched: {id:string,name:string,entry:string}[], unmatched: string[], ambiguous: {entry:string, ids:string[]}[] }}
 */
export function matchGroups(entries, allGroups) {
  const matched = new Map();
  const unmatched = [];
  const ambiguous = [];

  for (const entry of entries) {
    const key = normalize(entry);
    if (!key) continue;
    const byId = allGroups.find((g) => String(g.id) === String(entry).trim());
    if (byId) {
      matched.set(byId.id, { ...byId, entry });
      continue;
    }
    const byName = allGroups.filter((g) => normalize(g.name) === key);
    if (byName.length === 0) {
      unmatched.push(entry);
      continue;
    }
    if (byName.length > 1) ambiguous.push({ entry, ids: byName.map((g) => g.id) });
    for (const g of byName) matched.set(g.id, { ...g, entry });
  }
  return { matched: [...matched.values()], unmatched, ambiguous };
}
