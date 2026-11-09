// Ghi mọi output console (log/warn/error, kể cả của zca-js) vào file UTF-8, song song với việc in ra màn hình.
import fs from "node:fs";
import path from "node:path";
import { format } from "node:util";
import { LOG_FILE } from "./paths.js";

const MAX_BYTES = 5 * 1024 * 1024; // quá 5 MB thì đổi tên thành .1 (giữ 1 bản cũ)
const ANSI = /\x1b\[[0-9;]*m/g;

/**
 * @param {string} [file]
 * @param {number} [maxBytes] ngưỡng xoay vòng (tham số để test)
 * @returns {() => void} hàm gỡ, khôi phục console gốc
 */
export function installFileLog(file = LOG_FILE, maxBytes = MAX_BYTES) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  let size = 0;
  try {
    size = fs.statSync(file).size;
  } catch {}

  // Xoay vòng cả khi tool chạy nhiều ngày: đếm số byte đã ghi, vượt ngưỡng thì đổi tên thành .1 rồi ghi file mới.
  const rotate = () => {
    try {
      fs.renameSync(file, `${file}.1`);
      size = 0;
    } catch {
      size = 0; // không đổi tên được (file đang bị khoá): bỏ qua, thử lại sau một ngưỡng nữa
    }
  };
  if (size > maxBytes) rotate();

  let failed = false;
  const originals = {};
  for (const method of ["log", "info", "warn", "error"]) {
    const orig = (originals[method] = console[method]).bind(console);
    console[method] = (...args) => {
      orig(...args);
      if (failed) return;
      try {
        const line = format(...args).replace(ANSI, "") + "\n";
        if (size > 0 && size + Buffer.byteLength(line) > maxBytes) rotate();
        fs.appendFileSync(file, line, "utf8");
        size += Buffer.byteLength(line);
      } catch (e) {
        failed = true; // lỗi ghi file không được làm sập tool
        orig(`⚠ Không ghi được ${file}: ${e.message}`);
      }
    };
  }
  return () => Object.assign(console, originals);
}
