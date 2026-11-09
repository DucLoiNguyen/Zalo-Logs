// Dọn exports/: chỉ giữ `keepDays` ngày gần nhất (tính cả hôm nay) ở máy, phần cũ hơn đã có bản sao ở thư mục sao lưu
// (exportCopyDir, vd OneDrive) thì xoá. An toàn: một tệp chỉ bị xoá khi bản sao cùng đường dẫn tương đối, cùng kích thước
// đã nằm ở thư mục sao lưu; tệp chưa có bản sao (hoặc khác kích thước) được giữ lại, không bao giờ ghi đè bản ở sao lưu và đếm vào `kept`.
import fs from "node:fs";
import path from "node:path";
import { addDays, dayLabel, parseDay } from "./format.js";

const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
const REPORT_RE = /^Zalo_(\d{4}-\d{2}-\d{2})\.xlsx$/;

const sameSize = (a, b) => {
  try {
    return fs.statSync(a).size === fs.statSync(b).size;
  } catch {
    return false;
  }
};

/**
 * @param {{exportDir:string, mediaDir:string, copyTo:string|null, tz:string, keepDays?:number, dryRun?:boolean, backupFirst?:boolean, now?:number}} o
 * @returns {{cutoff:string, deleted:number, freedBytes:number, kept:number, backedUp:number}}
 */
export function pruneExports({ exportDir, mediaDir, copyTo, tz, keepDays = 7, dryRun = false, backupFirst = false, now = Date.now() }) {
  const res = { cutoff: "", deleted: 0, freedBytes: 0, kept: 0, backedUp: 0 };
  if (!copyTo) return res; // chưa có nơi sao lưu thì không xoá gì
  const today = parseDay("today", tz, now);
  res.cutoff = dayLabel(addDays(today, -(Math.max(1, keepDays) - 1))); // ngày < cutoff là cũ
  const rm = (file, backup) => {
    const size = fs.statSync(file).size;
    if (!sameSize(file, backup)) {
      // khác kích thước nhưng bản sao đã tồn tại: không ghi đè (không biết bản nào mới/đúng), giữ lại để người dùng xem
      if (!backupFirst || fs.existsSync(backup)) {
        res.kept += 1;
        return;
      }
      if (!dryRun) {
        fs.mkdirSync(path.dirname(backup), { recursive: true });
        fs.copyFileSync(file, backup);
      }
      res.backedUp += 1;
    }
    if (!dryRun) fs.rmSync(file);
    res.deleted += 1;
    res.freedBytes += size;
  };
  const rmEmpty = (dir) => {
    if (dryRun) return;
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) if (e.isDirectory()) rmEmpty(path.join(dir, e.name));
    if (!fs.readdirSync(dir).length) fs.rmdirSync(dir);
  };
  const walk = (dir, destDir) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const f = path.join(dir, e.name);
      if (e.isDirectory()) walk(f, path.join(destDir, e.name));
      else if (e.name.endsWith(".part")) res.kept += 1; // đang tải dở
      else rm(f, path.join(destDir, e.name));
    }
  };

  if (fs.existsSync(exportDir)) {
    for (const name of fs.readdirSync(exportDir)) {
      const m = REPORT_RE.exec(name);
      if (m && m[1] < res.cutoff) rm(path.join(exportDir, name), path.join(copyTo, name));
    }
  }
  if (fs.existsSync(mediaDir)) {
    for (const name of fs.readdirSync(mediaDir)) {
      if (!DAY_RE.test(name) || name >= res.cutoff) continue;
      const dir = path.join(mediaDir, name);
      if (!fs.statSync(dir).isDirectory()) continue;
      walk(dir, path.join(copyTo, "media", name));
      rmEmpty(dir);
    }
  }
  return res;
}
