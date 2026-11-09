// Dọn exports/: chỉ giữ N ngày gần nhất ở máy, phần cũ hơn đã có bản sao ở exportCopyDir (OneDrive) thì xoá.
//   npm run prune-exports -- --dry-run      xem trước, không xoá gì
//   npm run prune-exports                   xoá tệp cũ đã có bản sao (mặc định giữ 7 ngày)
//   npm run prune-exports -- --days 14
//   npm run prune-exports -- --backup       chép tệp cũ CHƯA có bản sao sang OneDrive rồi mới xoá
import { parseArgs } from "node:util";
import { loadConfig } from "./config.js";
import { EXPORT_DIR, MEDIA_DIR } from "./paths.js";
import { pruneExports } from "./prune.js";

const { values } = parseArgs({
  options: { days: { type: "string", default: "7" }, "dry-run": { type: "boolean" }, backup: { type: "boolean" } },
});
const config = loadConfig();
if (!config.exportCopyDir) {
  console.error('Chưa đặt "exportCopyDir" trong config/config.json: không có nơi sao lưu nên không xoá gì.');
  process.exit(1);
}
const keepDays = Number(values.days);
if (!Number.isInteger(keepDays) || keepDays < 1) {
  console.error("--days phải là số nguyên >= 1");
  process.exit(1);
}
const r = pruneExports({
  exportDir: EXPORT_DIR, mediaDir: MEDIA_DIR, copyTo: config.exportCopyDir, tz: config.timezone,
  keepDays, dryRun: values["dry-run"], backupFirst: values.backup,
});
const mb = (r.freedBytes / 1048576).toFixed(1);
console.log(
  `${values["dry-run"] ? "[thử] " : ""}Giữ ${keepDays} ngày (từ ${r.cutoff}): ${values["dry-run"] ? "sẽ xoá" : "đã xoá"} ${r.deleted} tệp (${mb} MB)` +
    (r.backedUp ? `, trong đó ${r.backedUp} tệp được chép sang sao lưu trước` : "") +
    `; giữ lại ${r.kept} tệp cũ chưa có bản sao ở sao lưu${r.kept ? " (dùng --backup để chép rồi xoá)" : ""}.`,
);
