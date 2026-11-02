// Gửi thử/gửi tay báo cáo 1 ngày tới reportTo (không bật listener).
//   npm run send-report                      -> hôm qua
//   npm run send-report -- --date today
import { parseArgs } from "node:util";
import { loadConfig } from "./config.js";
import { openStore } from "./store.js";
import { loginSaved, fetchAllGroups } from "./zaloClient.js";
import { matchGroups } from "./groupmatch.js";
import { parseDay } from "./format.js";
import { sendDayReport } from "./daily.js";

const log = (...a) => console.log(...a);
let db;
try {
  const { values } = parseArgs({ options: { date: { type: "string" } } });
  const config = loadConfig();
  db = await openStore(log);
  const api = await loginSaved();
  const { matched } = matchGroups(config.groups, [...(await fetchAllGroups(api)).values()]);
  const day = parseDay(values.date ?? "yesterday", config.timezone);
  await sendDayReport({ api, db, groups: matched, tz: config.timezone, day, reportTo: config.reportTo, copyTo: config.exportCopyDir, log });
  await db.close();
  process.exit(0);
} catch (e) {
  console.error(e.message);
  await db?.close();
  process.exit(1);
}
