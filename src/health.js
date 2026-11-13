// Kiểm tra sức khoẻ cron của Hermes, chạy phía WINDOWS (task riêng mỗi 5 phút, xem setup-health.ps1) nên vẫn chạy khi WSL/gateway chết.
//   node src/health.js [--dry-run] [--onedrive-dir <thư mục>] [--distro Ubuntu-24.04] [--profile ticket]
//   node src/health.js --daily [--date YYYY-MM-DD]     # báo cáo độ trễ cron của một ngày (mặc định hôm qua), chỉ đọc
// Mỗi lượt: hỏi `hermes cron list` + `hermes cron runs` (qua wsl.exe) và GET http://localhost:20128/v1/models (9router).
// Job nào quá ngưỡng kể từ lần hoàn tất cuối (triage: 40 phút khi chạy 30 phút, 30 phút khi chạy 15 phút; job khác: max(20, 4 × chu kỳ)) hoặc
// 9router/WSL không phản hồi thì là SỰ CỐ. Báo MỘT lần mỗi đợt (toast Windows + HEALTH.txt) rồi báo phục hồi; KHÔNG tự khởi động lại gì.
// Ghi: data/health/{state.json,health.log,HEALTH.txt,cron-latency-<ngày>.md}; chép HEALTH.txt sang thư mục OneDrive nếu có --onedrive-dir
// (hoặc biến HERMES_HEALTH_ONEDRIVE). Không đọc/ghi bí mật: chỉ tên job, mã job, giờ chạy.
import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { DATA_DIR } from "./paths.js";

export const HEALTH_DIR = path.join(DATA_DIR, "health");
const TZ_OFFSET_MIN = 7 * 60; // giờ Việt Nam

// ---------------------------------------------------------------- phân tích đầu ra của hermes (hàm thuần)
/** `hermes cron list` -> [{id, status, name, schedule}] */
export function parseJobs(text) {
  const jobs = [];
  let cur = null;
  for (const line of String(text).split(/\r?\n/)) {
    const head = line.match(/^\s*([0-9a-f]{12})\s+\[(\w+)\]/);
    if (head) {
      cur = { id: head[1], status: head[2], name: "", schedule: "" };
      jobs.push(cur);
      continue;
    }
    if (!cur) continue;
    const kv = line.match(/^\s+(Name|Schedule):\s+(.*?)\s*$/);
    if (kv) cur[kv[1] === "Name" ? "name" : "schedule"] = kv[2];
  }
  return jobs;
}

/** `hermes cron runs` -> [{runId, status, job, ts(ms)}] */
export function parseRuns(text) {
  const runs = [];
  for (const line of String(text).split(/\r?\n/)) {
    const m = line.match(/^\s*(\w+)\s+(\w+)\s+job=([0-9a-f]+)\s+source=\S+\s+(\d{4}-\d{2}-\d{2}T\S+)/);
    if (!m) continue;
    const ts = Date.parse(m[4]);
    if (Number.isFinite(ts)) runs.push({ runId: m[1], status: m[2], job: m[3], ts });
  }
  return runs;
}

/** "every 30m" | "every 2h" | "30m" -> phút; biểu thức cron hoặc không rõ -> null */
export function intervalMinutes(schedule) {
  const m = String(schedule || "").trim().match(/^(?:every\s+)?(\d+)\s*(m|min|h|hr|d)\w*$/i);
  if (!m) return null;
  const n = Number(m[1]);
  const u = m[2].toLowerCase();
  return u.startsWith("m") ? n : u.startsWith("h") ? n * 60 : n * 1440;
}

/** Ngưỡng (phút) kể từ lần hoàn tất cuối mà job bị coi là chậm/chết. */
export function thresholdMinutes(job) {
  const iv = intervalMinutes(job.schedule);
  if (/triage/i.test(job.name)) {
    const m = iv ?? 30; // lịch kiểu cron của job shadow: coi như 30 phút
    return m <= 15 ? 30 : m <= 30 ? 40 : m + 15;
  }
  return Math.max(20, (iv ?? 5) * 4);
}

const fmtVN = (ms) => new Date(ms + TZ_OFFSET_MIN * 60000).toISOString().replace("T", " ").slice(0, 16);

/** Đánh giá: {ok, reasons[], rows[]}. `wslOk`/`httpOk` = false khi không hỏi được. */
export function evaluate({ jobs, runs, now, wslOk = true, httpOk = true }) {
  const reasons = [];
  const rows = [];
  if (!wslOk) reasons.push("WSL/hermes không phản hồi (không đọc được danh sách job)");
  if (!httpOk) reasons.push("9router (localhost:20128) không phản hồi");
  if (wslOk) {
    for (const job of jobs.filter((j) => j.status === "active")) {
      const done = runs.filter((r) => r.job === job.id && r.status === "completed").map((r) => r.ts);
      const last = done.length ? Math.max(...done) : null;
      const thr = thresholdMinutes(job);
      const age = last == null ? null : Math.round((now - last) / 60000);
      const late = age != null && age > thr;
      rows.push({ ...job, last, age, thr, late });
      if (late) reasons.push(`${job.name}: lần hoàn tất cuối ${fmtVN(last)} (${age} phút trước, ngưỡng ${thr} phút)`);
    }
  }
  return { ok: reasons.length === 0, reasons, rows };
}

/** Máy trạng thái "một lần mỗi đợt": trả {state, notify: 'alert' | 'recovered' | null}. */
export function nextState(prev, result, now) {
  const p = prev && typeof prev === "object" ? prev : { alert: false };
  if (!result.ok) {
    if (!p.alert) return { state: { alert: true, since: now, reasons: result.reasons }, notify: "alert" };
    return { state: { ...p, reasons: result.reasons }, notify: null };
  }
  if (p.alert) return { state: { alert: false, since: null, recoveredAt: now }, notify: "recovered" };
  return { state: { ...p, alert: false }, notify: null };
}

export function renderHealthText({ now, result, state }) {
  const lines = [`TRẠNG THÁI HERMES: ${result.ok ? "BÌNH THƯỜNG" : "SỰ CỐ"}`, `Kiểm tra lúc: ${fmtVN(now)} (giờ VN)`];
  if (!result.ok) {
    lines.push(state?.since ? `Bắt đầu: ${fmtVN(state.since)}` : "", "Lý do:", ...result.reasons.map((r) => `- ${r}`));
    lines.push("Gợi ý: xem Task Scheduler (9router, HermesGateway) và `hermes -p ticket cron status` trong WSL; script này không tự khởi động lại gì.");
  }
  lines.push("", "Job:");
  for (const r of result.rows) {
    lines.push(`- ${r.name} (${r.schedule || "?"}): ${r.last == null ? "chưa có lượt nào" : `hoàn tất cuối ${fmtVN(r.last)}, ${r.age} phút trước`}${r.late ? "  <-- QUÁ NGƯỠNG" : ""}`);
  }
  return lines.filter((l, i, a) => !(l === "" && a[i - 1] === "")).join("\n") + "\n";
}

// ---------------------------------------------------------------- báo cáo độ trễ hằng ngày (hàm thuần)
const pct = (sorted, p) => (sorted.length ? sorted[Math.min(sorted.length - 1, Math.ceil((p / 100) * sorted.length) - 1)] : 0);

/** Giờ VN: [bắt đầu, hết) của ngày dateStr (YYYY-MM-DD) theo ms UTC. */
export function dayRange(dateStr) {
  const start = Date.parse(`${dateStr}T00:00:00Z`) - TZ_OFFSET_MIN * 60000;
  return [start, start + 86400000];
}

export function latencyReport({ jobs, runs, dateStr }) {
  const [from, to] = dayRange(dateStr);
  const out = [`# Độ trễ cron Hermes ngày ${dateStr} (giờ VN)`, "",
    "Chỉ đọc từ `hermes cron runs`. Khoảng cách = thời gian giữa hai lượt hoàn tất liên tiếp (lượt đầu tính từ lượt cuối của ngày trước nếu có). " +
    "Gián đoạn = tổng phần vượt quá chu kỳ.", "",
    "| Job | Chu kỳ | Số lượt | Khoảng cách trung vị | p95 | Lớn nhất | Số khoảng > 2× chu kỳ | Tổng gián đoạn |", "|---|---|---|---|---|---|---|---|"];
  for (const job of jobs) {
    const times = runs.filter((r) => r.job === job.id && r.status === "completed").map((r) => r.ts).sort((a, b) => a - b);
    const inDay = times.filter((t) => t >= from && t < to);
    const before = times.filter((t) => t < from).pop();
    const seq = before != null && inDay.length ? [before, ...inDay] : inDay;
    const gaps = [];
    for (let i = 1; i < seq.length; i++) gaps.push((seq[i] - seq[i - 1]) / 60000);
    const sorted = [...gaps].sort((a, b) => a - b);
    const iv = intervalMinutes(job.schedule);
    const late = iv ? gaps.filter((g) => g > 2 * iv).length : null;
    const down = iv ? gaps.reduce((s, g) => s + Math.max(0, g - iv), 0) : null;
    const f = (x) => (x == null ? "-" : `${Math.round(x)} phút`);
    out.push(`| ${job.name} | ${job.schedule || "?"} | ${inDay.length} | ${gaps.length ? f(pct(sorted, 50)) : "-"} | ${gaps.length ? f(pct(sorted, 95)) : "-"} | ` +
      `${gaps.length ? f(sorted[sorted.length - 1]) : "-"} | ${late ?? "-"} | ${f(down)} |`);
  }
  return out.join("\n") + "\n";
}

// ---------------------------------------------------------------- phần chạy thật
function runWsl(distro, profile, limit, timeoutMs = 90000) {
  const script = `export PATH=$HOME/.local/bin:$PATH; L=$(hermes -p ${profile} cron list 2>&1); echo "$L"; echo '###RUNS'; ` +
    `for id in $(echo "$L" | grep -oE '^ +[0-9a-f]{12} ' ); do hermes -p ${profile} cron runs --limit ${limit} $id 2>&1; done`;
  return new Promise((resolve) => {
    const p = spawn("wsl.exe", ["-d", distro, "-e", "bash", "-lc", script], { windowsHide: true });
    let out = "";
    const timer = setTimeout(() => { p.kill(); resolve({ ok: false, out }); }, timeoutMs);
    p.stdout.on("data", (d) => (out += d.toString("utf8")));
    p.stderr.on("data", () => {});
    p.on("error", () => { clearTimeout(timer); resolve({ ok: false, out }); });
    p.on("close", () => {
      clearTimeout(timer);
      const [list, runs = ""] = out.split("###RUNS");
      resolve({ ok: parseJobs(list).length > 0, list, runs });
    });
  });
}

const checkHttp = (url = "http://localhost:20128/v1/models") =>
  new Promise((resolve) => {
    const req = http.get(url, { timeout: 5000 }, (res) => { res.resume(); resolve(res.statusCode === 200); });
    req.on("timeout", () => { req.destroy(); resolve(false); });
    req.on("error", () => resolve(false));
  });

export function toast(title, body) {
  const esc = (s) => String(s).replace(/'/g, "''").replace(/[\r\n]+/g, " ");
  const ps = `[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime];` +
    `$x=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);` +
    `$t=$x.GetElementsByTagName('text');$t.Item(0).AppendChild($x.CreateTextNode('${esc(title)}'))|Out-Null;$t.Item(1).AppendChild($x.CreateTextNode('${esc(body)}'))|Out-Null;` +
    `[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe').Show([Windows.UI.Notifications.ToastNotification]::new($x))`;
  return new Promise((resolve) => {
    const p = spawn("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", ps], { windowsHide: true, stdio: "ignore" });
    p.on("error", () => resolve(false));
    p.on("close", (c) => resolve(c === 0));
  });
}

const readJson = (f, d) => { try { return JSON.parse(fs.readFileSync(f, "utf8")); } catch { return d; } };
function appendLog(line, now) {
  fs.mkdirSync(HEALTH_DIR, { recursive: true });
  const f = path.join(HEALTH_DIR, "health.log");
  try { if (fs.statSync(f).size > 512 * 1024) fs.renameSync(f, f + ".1"); } catch { /* chưa có */ }
  fs.appendFileSync(f, `${fmtVN(now)} ${line}\n`, "utf8");
}

function arg(argv, name, def) {
  const i = argv.indexOf(name);
  return i >= 0 && argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[i + 1] : def;
}

export async function main(argv = process.argv.slice(2)) {
  const distro = arg(argv, "--distro", "Ubuntu-24.04");
  const profile = arg(argv, "--profile", "ticket");
  const dry = argv.includes("--dry-run");
  const oneDrive = arg(argv, "--onedrive-dir", process.env.HERMES_HEALTH_ONEDRIVE || "");
  const now = Date.now();

  if (argv.includes("--daily")) {
    const dateStr = arg(argv, "--date", fmtVN(now - 86400000).slice(0, 10));
    const r = await runWsl(distro, profile, 500);
    if (!r.ok) { console.error("Không đọc được hermes cron (WSL/gateway không phản hồi)."); return 1; }
    const md = latencyReport({ jobs: parseJobs(r.list), runs: parseRuns(r.runs), dateStr });
    if (dry) { console.log(md); return 0; }
    fs.mkdirSync(HEALTH_DIR, { recursive: true });
    const f = path.join(HEALTH_DIR, `cron-latency-${dateStr}.md`);
    fs.writeFileSync(f, md, "utf8");
    console.log(`Đã ghi ${f}`);
    return 0;
  }

  const [r, httpOk] = await Promise.all([runWsl(distro, profile, 3), checkHttp()]);
  const result = evaluate({ jobs: r.ok ? parseJobs(r.list) : [], runs: r.ok ? parseRuns(r.runs) : [], now, wslOk: r.ok, httpOk });
  const stateFile = path.join(HEALTH_DIR, "state.json");
  const { state, notify } = nextState(readJson(stateFile, { alert: false }), result, now);
  const text = renderHealthText({ now, result, state });
  if (dry) { console.log(text); console.log(`(dry-run) sẽ báo: ${notify || "không"}`); return result.ok ? 0 : 2; }

  fs.mkdirSync(HEALTH_DIR, { recursive: true });
  fs.writeFileSync(path.join(HEALTH_DIR, "HEALTH.txt"), text, "utf8");
  fs.writeFileSync(stateFile, JSON.stringify({ ...state, lastCheck: now, lastCopy: state.lastCopy }), "utf8");
  if (notify) {
    appendLog(notify === "alert" ? `SỰ CỐ: ${result.reasons.join(" | ")}` : "ĐÃ PHỤC HỒI", now);
    const shown = await toast(notify === "alert" ? "Hermes: SỰ CỐ" : "Hermes: đã phục hồi", notify === "alert" ? result.reasons[0] : "Cron chạy lại bình thường.");
    if (!shown) appendLog("Không hiện được toast Windows (HEALTH.txt vẫn đã ghi)", now);
  }
  // chép sang OneDrive khi đổi trạng thái, hoặc mỗi giờ một lần (để thấy "kiểm tra lúc" còn mới)
  const prevCopy = readJson(stateFile, {}).lastCopy ?? 0;
  if (oneDrive && (notify || now - prevCopy > 3600000)) {
    try {
      fs.mkdirSync(oneDrive, { recursive: true });
      fs.copyFileSync(path.join(HEALTH_DIR, "HEALTH.txt"), path.join(oneDrive, "HEALTH.txt"));
      fs.writeFileSync(stateFile, JSON.stringify({ ...state, lastCheck: now, lastCopy: now }), "utf8");
    } catch (e) {
      appendLog(`Không chép được HEALTH.txt sang OneDrive: ${e.code || e.message}`, now);
    }
  }
  // báo cáo độ trễ của hôm qua (một lần mỗi ngày, chỉ đọc)
  const yesterday = fmtVN(now - 86400000).slice(0, 10);
  const latFile = path.join(HEALTH_DIR, `cron-latency-${yesterday}.md`);
  if (result.ok && !fs.existsSync(latFile)) {
    const full = await runWsl(distro, profile, 500);
    if (full.ok) fs.writeFileSync(latFile, latencyReport({ jobs: parseJobs(full.list), runs: parseRuns(full.runs), dateStr: yesterday }), "utf8");
  }
  return 0; // sự cố đã được báo bằng toast/HEALTH.txt; không trả mã lỗi để Task Scheduler không coi là tác vụ hỏng
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  main().then((c) => process.exit(c), (e) => { console.error(e.message); process.exit(1); });
}
