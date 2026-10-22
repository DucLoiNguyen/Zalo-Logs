// Ghi song song: SQLite là bản gốc (đồng bộ, offline), MongoDB Atlas là bản sao (nếu có MONGODB_URI).
// Lỗi Atlas chỉ được ghi log, không làm mất tin; chạy `npm run sync-mongo` để đẩy bù.
import { openDb } from "./db.js";
import { openMongo } from "./mongo.js";

export async function openStore(log = console.log) {
  const sqlite = openDb();
  let mongo = null;
  try {
    mongo = await openMongo();
    if (mongo) log(`Đã kết nối MongoDB Atlas (db: ${mongo.name}), ghi song song cùng SQLite.`);
    else log("Chưa có MONGODB_URI, chỉ ghi vào SQLite.");
  } catch (e) {
    log(`⚠ Không kết nối được MongoDB Atlas, chỉ ghi SQLite: ${e.message}`);
  }

  // Bắn-và-quên: tránh chặn listener, lỗi chỉ in cảnh báo.
  const mirror = (what, p) => p?.catch((e) => log(`⚠ Atlas lỗi (${what}): ${e.message}`));

  return {
    sqlite,
    hasMongo: !!mongo,
    upsertGroup(id, name) {
      sqlite.upsertGroup(id, name);
      mirror("group", mongo?.upsertGroup(id, name));
    },
    insertMessage(m) {
      const added = sqlite.insertMessage(m);
      mirror("message", mongo?.insertMessage(m));
      return added;
    },
    markRecalled(groupId, msgId) {
      const n = sqlite.markRecalled(groupId, msgId);
      mirror("recall", mongo?.markRecalled(groupId, msgId));
      return n;
    },
    async close() {
      sqlite.close();
      await mongo?.close().catch(() => {});
    },
  };
}
