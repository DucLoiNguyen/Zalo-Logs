import path from "node:path";
import { fileURLToPath } from "node:url";

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const DATA_DIR = path.join(ROOT, "data");
export const DB_FILE = path.join(DATA_DIR, "zalo-logs.db");
export const CRED_FILE = path.join(DATA_DIR, "credentials.json");
export const QR_FILE = path.join(DATA_DIR, "qr.png");
export const CONFIG_FILE = path.join(ROOT, "config", "config.json");
export const EXPORT_DIR = path.join(ROOT, "exports");
// Ảnh/file/video tải về: exports/media/<ngày>/<nhóm>/... (nằm cạnh file báo cáo để link tương đối trong Excel dùng được
// cả ở exports/ lẫn ở thư mục OneDrive đã chép sang).
export const MEDIA_DIR = path.join(EXPORT_DIR, "media");
export const ENV_FILE = path.join(ROOT, ".env");
export const REPORT_STATE_FILE = path.join(DATA_DIR, "report-state.json");
export const LOG_FILE = path.join(DATA_DIR, "start.log");
