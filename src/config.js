import fs from "node:fs";
import { CONFIG_FILE } from "./paths.js";

export function loadConfig() {
  let raw;
  try {
    raw = JSON.parse(fs.readFileSync(CONFIG_FILE, "utf8"));
  } catch (e) {
    throw new Error(`Không đọc được ${CONFIG_FILE}: ${e.message}`);
  }
  const timezone = raw.timezone || "Asia/Ho_Chi_Minh";
  try {
    new Intl.DateTimeFormat("en-GB", { timeZone: timezone });
  } catch {
    throw new Error(`Múi giờ không hợp lệ trong config: "${timezone}"`);
  }
  const groups = Array.isArray(raw.groups) ? raw.groups.map(String).filter((s) => s.trim()) : [];
  // reportTo: nơi nhận báo cáo ngày, vd { "type": "user" | "group", "id": "..." }. Bỏ trống = không gửi.
  const r = raw.reportTo;
  const reportTo = r?.id ? { id: String(r.id), type: r.type === "group" ? "group" : "user" } : null;
  // exportCopyDir: thư mục phụ (vd OneDrive) để chép thêm file báo cáo ngày vào. Bỏ trống = không chép.
  const exportCopyDir = typeof raw.exportCopyDir === "string" && raw.exportCopyDir.trim() ? raw.exportCopyDir.trim() : null;
  // downloadMedia: tải ảnh/file/video về exports/media (mặc định bật); mediaMaxMB: bỏ qua tệp lớn hơn mức này.
  const downloadMedia = raw.downloadMedia !== false;
  const mediaMaxMB = Number(raw.mediaMaxMB) > 0 ? Number(raw.mediaMaxMB) : 100;
  return { timezone, groups, reportTo, exportCopyDir, downloadMedia, mediaMaxMB };
}
