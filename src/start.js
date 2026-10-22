// Chạy nền: lắng nghe tin nhắn và ghi vào SQLite, chỉ cho các nhóm trong config.
import { ThreadType, CloseReason } from "zca-js";
import { loadConfig } from "./config.js";
import { openDb } from "./db.js";
import { loginSaved, fetchAllGroups } from "./zaloClient.js";
import { matchGroups } from "./groupmatch.js";
import { describe, formatDateTime, normalizeTs } from "./format.js";

const BACKFILL_COUNT = 50;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log(`[${new Date().toLocaleString("vi-VN")}]`, ...a);

const config = loadConfig();
if (config.groups.length === 0) {
  console.error('config/config.json chưa có nhóm nào trong "groups".');
  process.exit(1);
}

const db = openDb();
const api = await loginSaved().catch((e) => {
  console.error(e.message);
  process.exit(1);
});

// 1) Xác định các nhóm cần log
const all = [...(await fetchAllGroups(api)).values()];
const { matched, unmatched, ambiguous } = matchGroups(config.groups, all);
for (const u of unmatched) log(`⚠ Không tìm thấy nhóm: "${u}" (kiểm tra lại bằng npm run groups)`);
for (const a of ambiguous) log(`⚠ Tên "${a.entry}" trùng nhiều nhóm, sẽ log tất cả: ${a.ids.join(", ")}`);
if (matched.length === 0) {
  console.error("Không có nhóm nào khớp config. Chạy `npm run groups` để xem tên/ID chính xác.");
  process.exit(1);
}
const allowed = new Map(matched.map((g) => [g.id, g.name]));
for (const g of matched) db.upsertGroup(g.id, g.name);
log(`Đang log ${matched.length} nhóm:`);
for (const g of matched) log(`  - ${g.name} (${g.id})`);

// 2) Ghi tin nhắn
function save(msg, { live }) {
  const d = msg.data;
  const ts = normalizeTs(d.ts);
  const added = db.insertMessage({
    groupId: msg.threadId,
    msgId: d.msgId,
    senderId: d.uidFrom,
    senderName: d.dName,
    msgType: d.msgType,
    content: d.content,
    ts,
  });
  if (added && live) {
    const text = describe(d.msgType, d.content).text.replace(/\s+/g, " ").slice(0, 80);
    log(`${allowed.get(msg.threadId)} | ${formatDateTime(ts, config.timezone)} ${d.dName}: ${text}`);
  }
  return added;
}

api.listener.on("message", (msg) => {
  if (msg.type !== ThreadType.Group || !allowed.has(msg.threadId)) return;
  try {
    save(msg, { live: true });
  } catch (e) {
    log("Lỗi ghi tin nhắn:", e.message);
  }
});

// Tin bị thu hồi -> đánh dấu (nội dung gốc vẫn được giữ trong DB)
api.listener.on("undo", (u) => {
  if (!u.isGroup || !allowed.has(u.threadId)) return;
  try {
    db.markRecalled(u.threadId, u.data.content.globalMsgId);
  } catch (e) {
    log("Lỗi đánh dấu thu hồi:", e.message);
  }
});

// 3) Bù tin bị lỡ mỗi lần (kết nối lại): listener của zca-js không tự phát lại tin cũ.
let backfilling = false;
async function backfill() {
  if (backfilling) return;
  backfilling = true;
  try {
    for (const [id, name] of allowed) {
      try {
        const res = await api.getGroupChatHistory(id, BACKFILL_COUNT);
        let added = 0;
        for (const m of res.groupMsgs ?? []) added += save(m, { live: false });
        if (added) log(`Bù ${added} tin còn thiếu cho nhóm "${name}"`);
      } catch (e) {
        log(`Không bù được tin cho "${name}": ${e.message}`);
      }
      await sleep(800);
    }
  } finally {
    backfilling = false;
  }
}

api.listener.on("connected", () => {
  log("Đã kết nối Zalo, đang lắng nghe...");
  backfill();
});
api.listener.on("closed", (code, reason) => {
  log(`Kết nối đóng (code ${code}${reason ? `, ${reason}` : ""})`);
  if (code === CloseReason.DuplicateConnection) {
    log("⚠ Có phiên Zalo Web khác đang đăng nhập cùng tài khoản. Đóng phiên đó rồi chạy lại.");
  }
});
api.listener.on("error", (e) => log("Lỗi listener:", e?.message ?? e));

api.listener.start({ retryOnClose: true });

function shutdown() {
  log("Đang dừng...");
  try {
    api.listener.stop();
  } catch {}
  db.close();
  process.exit(0);
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
