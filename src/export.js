// Xuất Excel từ DB (không cần đăng nhập Zalo).
//   node src/export.js                       -> hôm nay, tất cả nhóm
//   node src/export.js --date 2026-10-20     -> 1 ngày
//   node src/export.js --from 2026-10-01 --to 2026-10-22
//   node src/export.js --date yesterday --group "Tên nhóm"
import { parseArgs } from "node:util";
import { loadConfig } from "./config.js";
import { openDb } from "./db.js";
import { exportGroup } from "./exporter.js";
import { matchGroups } from "./groupmatch.js";
import { addDays, dayLabel, parseDay, startOfDay } from "./format.js";
import { EXPORT_DIR } from "./paths.js";

try {
  const { values } = parseArgs({
    options: {
      date: { type: "string" },
      from: { type: "string" },
      to: { type: "string" },
      group: { type: "string", multiple: true },
      out: { type: "string" },
    },
  });

  const { timezone: tz } = loadConfig();
  let fromDay, toDay;
  if (values.from || values.to) {
    fromDay = parseDay(values.from ?? values.to, tz);
    toDay = parseDay(values.to ?? values.from, tz);
  } else {
    fromDay = toDay = parseDay(values.date, tz);
  }
  const fromMs = startOfDay(fromDay, tz);
  const toMs = startOfDay(addDays(toDay, 1), tz);
  if (toMs <= fromMs) throw new Error("--to phải bằng hoặc sau --from");
  const label =
    dayLabel(fromDay) === dayLabel(toDay) ? dayLabel(fromDay) : `${dayLabel(fromDay)}_${dayLabel(toDay)}`;

  const db = openDb();
  let groups = db.listGroups();
  if (values.group?.length) {
    const m = matchGroups(values.group, groups);
    for (const u of m.unmatched) console.warn(`Không có nhóm "${u}" trong DB`);
    groups = m.matched;
  }
  if (groups.length === 0) {
    console.error("Chưa có nhóm nào trong DB. Chạy `npm start` để bắt đầu log trước.");
    process.exit(1);
  }

  const outDir = values.out ?? EXPORT_DIR;
  let made = 0;
  for (const group of groups) {
    const res = await exportGroup({ db, group, fromMs, toMs, tz, outDir, label });
    if (!res) {
      console.log(`- ${group.name}: không có tin nhắn trong ${label}`);
      continue;
    }
    made += 1;
    console.log(`✔ ${group.name}: ${res.count} tin, ${res.files} file đính kèm -> ${res.file}`);
  }
  db.close();
  if (made === 0) console.log("Không có file nào được tạo.");
} catch (e) {
  if (e.code === "EBUSY" || e.code === "EPERM") {
    console.error("Không ghi được file: hãy đóng file Excel đang mở rồi chạy lại.");
  } else {
    console.error(e.message);
  }
  process.exit(1);
}
