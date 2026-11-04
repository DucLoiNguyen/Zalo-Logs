// Tải bù ảnh/file/video của các tin ĐÃ có trong SQLite (không cần đăng nhập Zalo). Tuỳ chọn, tool không tự chạy lệnh này:
// khi chạy `npm start` chỉ tự tải tin mới và tin 3 ngày gần nhất.
//   npm run download-media               -> tất cả tin chưa tải
//   npm run download-media -- --days 7   -> chỉ 7 ngày gần nhất
import { parseArgs } from "node:util";
import { loadConfig } from "./config.js";
import { openDb } from "./db.js";
import { createMediaQueue } from "./media.js";

const log = (...a) => console.log(...a);
const db = openDb();
try {
  const { values } = parseArgs({ options: { days: { type: "string" } } });
  const days = values.days == null ? null : Number(values.days);
  if (days != null && !(days > 0)) throw new Error(`--days phải là số dương, nhận được "${values.days}"`);
  const config = loadConfig();
  const queue = createMediaQueue({ db, tz: config.timezone, maxBytes: config.mediaMaxMB * 1048576, log });
  const n = queue.addPending(days == null ? 0 : Date.now() - days * 86400_000);
  log(`Có ${n} tin cần tải${days == null ? "" : ` trong ${days} ngày gần nhất`}.`);
  await queue.idle();
  log("Xong. Tệp lưu ở exports/media/<ngày>/<nhóm>/. Tin lỗi sẽ được thử lại ở lần chạy sau (tối đa 5 lần).");
} catch (e) {
  console.error(e.message);
  process.exitCode = 1;
} finally {
  db.close();
}
