// Ghi mọi output console (log/warn/error, kể cả của zca-js) vào file UTF-8, song song với việc in ra màn hình.
import fs from "node:fs";
import path from "node:path";
import { format } from "node:util";
import { LOG_FILE } from "./paths.js";

const MAX_BYTES = 5 * 1024 * 1024; // quá 5 MB thì đổi tên thành .1 (giữ 1 bản cũ)
const ANSI = /\x1b\[[0-9;]*m/g;

export function installFileLog(file = LOG_FILE) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  try {
    if (fs.statSync(file).size > MAX_BYTES) fs.renameSync(file, `${file}.1`);
  } catch {}

  let failed = false;
  for (const method of ["log", "info", "warn", "error"]) {
    const orig = console[method].bind(console);
    console[method] = (...args) => {
      orig(...args);
      if (failed) return;
      try {
        fs.appendFileSync(file, format(...args).replace(ANSI, "") + "\n", "utf8");
      } catch (e) {
        failed = true; // lỗi ghi file không được làm sập tool
        orig(`⚠ Không ghi được ${file}: ${e.message}`);
      }
    };
  }
}
