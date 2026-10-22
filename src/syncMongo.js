// Đẩy toàn bộ dữ liệu SQLite lên MongoDB Atlas (an toàn khi chạy lại, tin trùng được bỏ qua).
//   npm run sync-mongo
import { openDb } from "./db.js";
import { openMongo } from "./mongo.js";

const BATCH = 1000;
const db = openDb();
try {
  const mongo = await openMongo();
  if (!mongo) {
    console.error("Chưa có MONGODB_URI. Tạo file .env từ .env.example rồi điền chuỗi kết nối Atlas.");
    process.exit(1);
  }
  const groups = db.listGroups();
  for (const g of groups) await mongo.upsertGroup(g.id, g.name);

  let total = 0;
  let inserted = 0;
  for (const g of groups) {
    const rows = db.queryMessages(g.id, 0, Number.MAX_SAFE_INTEGER).map((r) => ({ ...r, groupId: g.id }));
    for (let i = 0; i < rows.length; i += BATCH) {
      const res = await mongo.bulkMessages(rows.slice(i, i + BATCH));
      inserted += res?.upsertedCount ?? 0;
    }
    total += rows.length;
    console.log(`✔ ${g.name}: ${rows.length} tin`);
  }
  console.log(`Xong: ${groups.length} nhóm, ${total} tin đã quét, ${inserted} tin mới trên Atlas (db: ${mongo.name}).`);
  await mongo.close();
} catch (e) {
  console.error(`Lỗi: ${e.message}`);
  process.exitCode = 1;
} finally {
  db.close();
}
