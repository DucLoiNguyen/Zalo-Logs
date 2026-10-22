import fs from "node:fs";
import { MongoClient } from "mongodb";
import { ENV_FILE } from "./paths.js";

/** Nạp .env (nếu có) vào process.env. Biến đã đặt sẵn trong môi trường được ưu tiên. */
export function loadEnv() {
  if (fs.existsSync(ENV_FILE)) process.loadEnvFile(ENV_FILE);
}

/**
 * Kết nối Atlas và đảm bảo index. Trả về null nếu chưa cấu hình MONGODB_URI.
 * Ném lỗi nếu có URI nhưng kết nối thất bại.
 */
export async function openMongo() {
  loadEnv();
  const uri = process.env.MONGODB_URI?.trim();
  if (!uri) return null;

  const client = new MongoClient(uri, { serverSelectionTimeoutMS: 10000 });
  await client.connect();
  const mdb = client.db(process.env.MONGODB_DB || "zalo_logger");
  const groups = mdb.collection("groups");
  const messages = mdb.collection("messages");
  await messages.createIndex({ groupId: 1, msgId: 1 }, { unique: true });
  await messages.createIndex({ groupId: 1, ts: 1 });

  return {
    name: mdb.databaseName,
    upsertGroup: (id, name) =>
      groups.updateOne(
        { _id: String(id) },
        { $set: { name: String(name), updatedAt: new Date() } },
        { upsert: true }
      ),
    // $setOnInsert: tin trùng không bị ghi đè, giống INSERT OR IGNORE bên SQLite.
    insertMessage: (m) =>
      messages.updateOne(
        { groupId: String(m.groupId), msgId: String(m.msgId) },
        {
          $setOnInsert: {
            senderId: m.senderId == null ? null : String(m.senderId),
            senderName: m.senderName ?? null,
            msgType: m.msgType ?? null,
            content: m.content ?? null,
            ts: m.ts,
            recalled: false,
          },
        },
        { upsert: true }
      ),
    markRecalled: (groupId, msgId) =>
      messages.updateOne({ groupId: String(groupId), msgId: String(msgId) }, { $set: { recalled: true } }),
    // Đồng bộ hàng loạt: vừa chèn tin thiếu, vừa cập nhật cờ thu hồi.
    bulkMessages: (rows) =>
      rows.length
        ? messages.bulkWrite(
            rows.map((m) => ({
              updateOne: {
                filter: { groupId: String(m.groupId), msgId: String(m.msgId) },
                update: {
                  $setOnInsert: {
                    senderId: m.senderId == null ? null : String(m.senderId),
                    senderName: m.senderName ?? null,
                    msgType: m.msgType ?? null,
                    content: m.content ?? null,
                    ts: m.ts,
                    ...(m.recalled ? {} : { recalled: false }),
                  },
                  ...(m.recalled ? { $set: { recalled: true } } : {}),
                },
                upsert: true,
              },
            })),
            { ordered: false }
          )
        : null,
    close: () => client.close(),
  };
}
