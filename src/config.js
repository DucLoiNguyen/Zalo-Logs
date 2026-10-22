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
  return { timezone, groups };
}
