// Liệt kê các nhóm Zalo của tài khoản để copy tên/ID vào config/config.json
import { loginSaved, fetchAllGroups } from "./zaloClient.js";

const find = process.argv[2]?.toLowerCase();

try {
  const api = await loginSaved();
  const groups = [...(await fetchAllGroups(api)).values()]
    .filter((g) => !find || g.name.toLowerCase().includes(find))
    .sort((a, b) => a.name.localeCompare(b.name, "vi"));

  console.log(`\n${groups.length} nhóm${find ? ` khớp "${find}"` : ""}:\n`);
  for (const g of groups) {
    console.log(`${g.id.padEnd(22)} ${String(g.total).padStart(5)} tv   ${g.name}`);
  }
  console.log('\nDán tên (hoặc ID) nhóm cần log vào "groups" trong config/config.json');
  console.log("Lọc theo tên: npm run groups -- <từ khoá>");
  process.exit(0);
} catch (e) {
  console.error(e.message);
  process.exit(1);
}
