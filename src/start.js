// Chạy nền: lắng nghe tin nhắn và ghi vào SQLite, chỉ cho các nhóm trong config.
import { ThreadType, CloseReason } from "zca-js";
import { printBanner } from "./banner.js";
import { installFileLog } from "./filelog.js";
import { loadConfig } from "./config.js";
import { openStore } from "./store.js";
import { loginSaved, fetchAllGroups } from "./zaloClient.js";
import { matchGroups } from "./groupmatch.js";
import { startDailyReport } from "./daily.js";
import { describe, formatDateTime, normalizeTs } from "./format.js";

installFileLog();
printBanner();
const log = (...a) => console.log(`[${new Date().toLocaleString("vi-VN")}]`, ...a);

const config = loadConfig();
if (config.groups.length === 0) {
  console.error('config/config.json chưa có nhóm nào trong "groups".');
  process.exit(1);
}

const db = await openStore(log);
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

// 3) Bù tin bị lỡ mỗi lần kết nối lại. API HTTP getGroupChatHistory đã bị Zalo gỡ (404), nên dùng
// kênh WebSocket: yêu cầu tin cũ (mọi nhóm, theo trang, cũ -> mới) cho tới khi hết trang.
const BACKFILL_MAX_PAGES = 200;
const BACKFILL_PAGE_TIMEOUT_MS = 15000;
let backfillState = null; // { done, added, pages, timer } khi đang bù

function backfill() {
  if (backfillState) return Promise.resolve();
  return new Promise((resolve) => {
    const st = (backfillState = { added: 0, pages: 0, timer: null });
    const finish = (note) => {
      clearTimeout(st.timer);
      backfillState = null;
      if (note) log(note);
      if (st.added) log(`Bù ${st.added} tin còn thiếu`);
      resolve();
    };
    const request = (lastId) => {
      clearTimeout(st.timer);
      st.timer = setTimeout(() => finish("⚠ Không bù được tin: Zalo không trả lời yêu cầu tin cũ"), BACKFILL_PAGE_TIMEOUT_MS);
      api.listener.requestOldMessages(ThreadType.Group, lastId);
    };
    st.onPage = (msgs) => {
      st.pages += 1;
      for (const m of msgs) {
        if (!allowed.has(m.threadId)) continue;
        try {
          st.added += save(m, { live: false });
        } catch (e) {
          log("Lỗi ghi tin bù:", e.message);
        }
      }
      if (msgs.length === 0) return finish();
      if (st.pages >= BACKFILL_MAX_PAGES) return finish("⚠ Dừng bù tin: quá nhiều trang");
      request(msgs.at(-1).data.msgId);
    };
    request(null);
  });
}

api.listener.on("old_messages", (msgs, type) => {
  if (type === ThreadType.Group) backfillState?.onPage(msgs);
});

// 4) Mỗi ngày sau 0h: xuất 1 file Excel của ngày trước và gửi tới reportTo.
const checkDailyReport = startDailyReport({ api, db, groups: matched, tz: config.timezone, reportTo: config.reportTo, copyTo: config.exportCopyDir, log });

// Watchdog: kết nối đóng mà không nối lại được trong WATCHDOG_MS thì thoát với mã lỗi,
// để Task Scheduler (restart on failure) khởi động lại tiến trình thay vì "sống mà không nhận tin".
// Không áp dụng khi bị phiên Zalo Web khác chiếm (DuplicateConnection): khởi động lại sẽ đá phiên đó ra, gây vòng lặp.
const WATCHDOG_MS = 2 * 60 * 1000;
let watchdog = null;
function armWatchdog(reason) {
  clearTimeout(watchdog);
  watchdog = setTimeout(async () => {
    log(`⚠ ${reason}, quá ${WATCHDOG_MS / 60000} phút chưa nối lại. Thoát để khởi động lại tiến trình.`);
    try {
      api.listener.stop();
    } catch {}
    await Promise.race([db.close(), sleep(5000)]);
    process.exit(1);
  }, WATCHDOG_MS);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

api.listener.on("connected", async () => {
  clearTimeout(watchdog);
  log("Đã kết nối Zalo, đang lắng nghe...");
  await backfill();
  checkDailyReport(); // bù báo cáo các ngày máy đã tắt lúc 0h
});
api.listener.on("closed", (code, reason) => {
  log(`Kết nối đóng (code ${code}${reason ? `, ${reason}` : ""})`);
  if (code === CloseReason.DuplicateConnection) {
    log("⚠ Có phiên Zalo Web khác đang đăng nhập cùng tài khoản. Đóng phiên đó rồi chạy lại.");
    clearTimeout(watchdog);
  } else {
    armWatchdog("Kết nối Zalo đã đóng");
  }
});
api.listener.on("error", (e) => log("Lỗi listener:", e?.message ?? e));

api.listener.start({ retryOnClose: true });
armWatchdog("Chưa kết nối được Zalo"); // cũng canh lần kết nối đầu tiên

async function shutdown() {
  log("Đang dừng...");
  try {
    api.listener.stop();
  } catch {}
  await db.close();
  process.exit(0);
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
