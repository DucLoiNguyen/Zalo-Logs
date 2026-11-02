// Báo cáo hằng ngày: sau 0h xuất 1 file Excel (nhiều sheet) của ngày hôm trước
// và gửi tới `reportTo` (nếu đã cấu hình) trên Zalo. Chỉ gửi file, không kèm tin nhắn.
import fs from "node:fs";
import path from "node:path";
import { ThreadType } from "zca-js";
import { exportCombined } from "./exporter.js";
import { addDays, dayLabel, parseDay, startOfDay } from "./format.js";
import { EXPORT_DIR, REPORT_STATE_FILE } from "./paths.js";

const MAX_CATCHUP_DAYS = 7; // máy tắt lâu thì chỉ bù tối đa 7 ngày gần nhất

function readLastSent() {
  try {
    return JSON.parse(fs.readFileSync(REPORT_STATE_FILE, "utf8")).lastSentDay ?? null;
  } catch {
    return null;
  }
}

function writeLastSent(day) {
  fs.writeFileSync(REPORT_STATE_FILE, JSON.stringify({ lastSentDay: day }, null, 2));
}

/** Xuất 1 ngày thành 1 file nhiều sheet và gửi tới reportTo (nếu có; không thì chỉ xuất file). Trả về kết quả xuất, hoặc null nếu ngày đó không có tin. */
export async function sendDayReport({ api, db, groups, tz, day, reportTo, copyTo, log }) {
  const label = dayLabel(day);
  const fromMs = startOfDay(day, tz);
  const toMs = startOfDay(addDays(day, 1), tz);
  const res = await exportCombined({ db: db.sqlite, groups, fromMs, toMs, tz, outDir: EXPORT_DIR, label });
  if (!res) {
    log(`Báo cáo ${label}: không có tin nào, bỏ qua.`);
    return null;
  }
  if (copyTo) {
    // Chép thêm sang thư mục phụ; lỗi ở đây chỉ cảnh báo, không chặn việc gửi Zalo.
    try {
      fs.mkdirSync(copyTo, { recursive: true });
      const dest = path.join(copyTo, path.basename(res.file));
      fs.copyFileSync(res.file, dest);
      log(`Đã chép báo cáo ${label} vào ${dest}`);
    } catch (e) {
      log(`⚠ Không chép được báo cáo ${label} vào ${copyTo}: ${e.message}`);
    }
  }
  if (!reportTo) {
    log(`Đã xuất báo cáo ${label}: ${res.count} tin, ${res.groups} nhóm, ${res.files} file đính kèm -> ${res.file}`);
    return res;
  }
  const type = reportTo.type === "group" ? ThreadType.Group : ThreadType.User;
  await api.sendMessage({ msg: "", attachments: [res.file] }, reportTo.id, type);
  log(`Đã gửi báo cáo ${label} tới ${reportTo.type} ${reportTo.id}: ${res.count} tin, ${res.groups} nhóm, ${res.files} file đính kèm.`);
  return res;
}

/**
 * Bắt đầu kiểm tra mỗi phút; mỗi ngày đã qua (chưa gửi) được xuất và gửi đúng 1 lần.
 * Ngày nào không có tin thì bỏ qua và đánh dấu đã xử lý.
 * @returns {() => Promise<void>} hàm chạy kiểm tra ngay (gọi khi vừa kết nối)
 */
export function startDailyReport({ api, db, groups, tz, reportTo, copyTo, log }) {
  if (!reportTo) log('Chưa đặt "reportTo": báo cáo ngày chỉ xuất file vào exports/, không gửi Zalo.');
  let running = false;

  async function check() {
    if (running) return;
    running = true;
    try {
      const today = parseDay("today", tz);
      const yesterday = addDays(today, -1);
      const last = readLastSent();
      let day = last ? addDays(parseDay(last, tz), 1) : yesterday;
      const earliest = addDays(yesterday, -(MAX_CATCHUP_DAYS - 1));
      if (dayLabel(day) < dayLabel(earliest)) day = earliest;

      for (; dayLabel(day) <= dayLabel(yesterday); day = addDays(day, 1)) {
        await sendDayReport({ api, db, groups, tz, day, reportTo, copyTo, log });
        writeLastSent(dayLabel(day));
      }
    } catch (e) {
      // Không đánh dấu đã gửi: phút sau (hoặc lần chạy sau) sẽ thử lại.
      log(`⚠ Gửi báo cáo ngày thất bại, sẽ thử lại: ${e.message}`);
    } finally {
      running = false;
    }
  }

  setInterval(check, 60_000).unref();
  return check;
}
