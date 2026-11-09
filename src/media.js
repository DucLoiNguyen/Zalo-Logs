// Tải ảnh/file/video của tin nhắn về đĩa. Zalo không có API tải trong zca-js, nhưng nội dung tin đã chứa link
// (`href`) tải được bằng HTTP thường, không cần đăng nhập. Lưu: exports/media/<ngày>/<nhóm>/<HHmmss>_<tên>.
import fs from "node:fs";
import path from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import { dayLabel, zonedParts } from "./format.js";
import { MEDIA_DIR } from "./paths.js";

export const MEDIA_TYPES = ["chat.photo", "share.file", "chat.video.msg"];
/** Dạng dùng trong SQL `IN (...)`: hằng cố định của mã, không phải dữ liệu người dùng. */
export const MEDIA_TYPES_SQL = MEDIA_TYPES.map((t) => `'${t}'`).join(", ");

const DOWNLOAD_TIMEOUT_MS = 120_000;
// Windows giới hạn đường dẫn ~260 ký tự, mà thư mục OneDrive đã dài sẵn nên cắt ngắn tên nhóm/tệp.
const MAX_GROUP_DIR = 30;
const MAX_NAME_BASE = 60;

const safe = (s) =>
  String(s).replace(/[\\/:*?"<>|\x00-\x1f]/g, "_").replace(/\s+/g, " ").trim().replace(/[. ]+$/, "");

function parseParams(content) {
  try {
    return typeof content.params === "string" ? JSON.parse(content.params) : content.params ?? {};
  } catch {
    return {};
  }
}

/** Tên tệp an toàn trên Windows, giữ đuôi, cắt phần tên cho đủ ngắn. */
function safeName(name) {
  const ext = path.extname(name).slice(0, 12);
  const base = safe(path.basename(name, path.extname(name))).slice(0, MAX_NAME_BASE) || "tep";
  return base + safe(ext);
}

/** Từ (msgType, content) lấy { url, name, size } cần tải, hoặc null nếu tin không có tệp. */
export function mediaOf(msgType, content) {
  if (!MEDIA_TYPES.includes(msgType) || !content || typeof content !== "object") return null;
  const p = parseParams(content);
  const href = typeof content.href === "string" ? content.href.trim() : "";
  const url = (msgType === "chat.photo" && typeof p.hd === "string" && p.hd.trim()) || href;
  if (!/^https?:\/\//i.test(url)) return null;
  let urlExt = "";
  try {
    urlExt = path.extname(new URL(url).pathname);
  } catch {
    return null;
  }
  const title = typeof content.title === "string" ? content.title.trim() : "";
  let name;
  if (msgType === "share.file") name = title || "tep";
  else if (msgType === "chat.photo") name = `anh${urlExt || ".jpg"}`;
  else name = `video${urlExt || ".mp4"}`;
  return { url, name, size: Number(p.fileSize) || 0 };
}

/** Link tương đối (đã mã hoá URL) từ file báo cáo tới tệp, vd media/2026-11-03/Nhom/093022_a.xlsx */
export const mediaHref = (relPath) => "media/" + relPath.split("/").map(encodeURIComponent).join("/");

function chooseRelPath({ row, groupName, name, tz }) {
  const p = zonedParts(row.ts, tz);
  const day = dayLabel({ y: p.y, m: p.mo, d: p.d });
  const time = [p.h, p.mi, p.s].map((n) => String(n).padStart(2, "0")).join("");
  const dir = `${day}/${safe(groupName || row.groupId).slice(0, MAX_GROUP_DIR) || "nhom"}`;
  const file = `${time}_${safeName(name)}`;
  const ext = path.extname(file);
  const stem = file.slice(0, file.length - ext.length);
  fs.mkdirSync(path.join(MEDIA_DIR, dir), { recursive: true });
  // Trùng giờ + tên (vd 2 ảnh cùng giây): giữ chỗ bằng tệp rỗng tạo nguyên tử (wx) để 2 lượt tải song song
  // (hoặc npm start + npm run download-media) không ghi đè nhau; tải xong sẽ thay tệp rỗng này.
  for (let i = 1; ; i++) {
    const rel = i === 1 ? `${dir}/${file}` : `${dir}/${stem} (${i})${ext}`;
    try {
      fs.writeFileSync(path.join(MEDIA_DIR, rel), "", { flag: "wx" });
      return rel;
    } catch (e) {
      if (e.code !== "EEXIST") throw e;
    }
  }
}

/**
 * Tải tệp của 1 tin và ghi trạng thái vào bảng media. Trả về "ok" | "failed" | "skipped".
 * row: { groupId, groupName, msgId, msgType, content, ts }
 */
export async function downloadMedia({ db, row, tz, maxBytes, log }) {
  const rec = { groupId: row.groupId, msgId: row.msgId };
  const m = mediaOf(row.msgType, row.content);
  if (!m) {
    db.setMedia({ ...rec, status: "skipped", error: "không có link tải" });
    return "skipped";
  }
  if (m.size > maxBytes) {
    db.setMedia({ ...rec, status: "skipped", size: m.size, error: `quá lớn (${Math.round(m.size / 1048576)} MB)` });
    log(`Bỏ qua tệp quá lớn (${Math.round(m.size / 1048576)} MB): ${m.name}`);
    return "skipped";
  }

  const prev = db.getMedia(row.groupId, row.msgId);
  if (prev?.status === "ok" && prev.relPath && fs.existsSync(path.join(MEDIA_DIR, prev.relPath))) return "ok";
  let relPath;
  let dest;
  let part;
  try {
    relPath = chooseRelPath({ row, groupName: row.groupName, name: m.name, tz });
    dest = path.join(MEDIA_DIR, relPath);
    part = `${dest}.${process.pid}.part`;
    const res = await fetch(m.url, { signal: AbortSignal.timeout(DOWNLOAD_TIMEOUT_MS) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const len = Number(res.headers.get("content-length")) || 0;
    if (len > maxBytes) {
      db.setMedia({ ...rec, status: "skipped", size: len, error: `quá lớn (${Math.round(len / 1048576)} MB)` });
      await res.body?.cancel().catch(() => {});
      fs.rmSync(dest, { force: true });
      log(`Bỏ qua tệp quá lớn (${Math.round(len / 1048576)} MB): ${m.name}`);
      return "skipped";
    }
    await pipeline(Readable.fromWeb(res.body), fs.createWriteStream(part));
    const size = fs.statSync(part).size;
    if (m.size && size !== m.size) throw new Error(`sai dung lượng (${size}/${m.size} byte)`);
    fs.renameSync(part, dest);
    db.setMedia({ ...rec, relPath, status: "ok", size });
    log(`Đã tải ${relPath} (${Math.max(1, Math.round(size / 1024))} KB)`);
    return "ok";
  } catch (e) {
    if (part) fs.rmSync(part, { force: true });
    if (dest) fs.rmSync(dest, { force: true }); // bỏ tệp rỗng giữ chỗ
    db.setMedia({ ...rec, status: "failed", error: e.message });
    log(`⚠ Không tải được ${m.name}: ${e.message}`);
    return "failed";
  }
}

/** Hàng đợi tải nền (vài tệp cùng lúc) để không chặn việc nhận tin. */
export function createMediaQueue({ db, tz, maxBytes, log, concurrency = 2 }) {
  const queue = [];
  const queued = new Set();
  const waiters = [];
  let active = 0;
  const keyOf = (r) => `${r.groupId}/${r.msgId}`;

  function pump() {
    while (active < concurrency && queue.length) {
      const row = queue.shift();
      active += 1;
      downloadMedia({ db, row, tz, maxBytes, log })
        .catch((e) => log(`⚠ Lỗi tải tệp: ${e.message}`))
        .finally(() => {
          active -= 1;
          queued.delete(keyOf(row));
          pump();
        });
    }
    if (active === 0 && queue.length === 0) while (waiters.length) waiters.pop()();
  }

  return {
    add(row) {
      if (queued.has(keyOf(row))) return;
      queued.add(keyOf(row));
      queue.push(row);
      pump();
    },
    /** Đưa vào hàng đợi các tin từ sinceMs chưa tải / tải lỗi. Trả về số tin. */
    addPending(sinceMs) {
      const rows = db.pendingMedia(sinceMs);
      for (const r of rows) this.add(r);
      return rows.length;
    },
    idle: () => new Promise((resolve) => (active === 0 && queue.length === 0 ? resolve() : waiters.push(resolve))),
  };
}

/** Chép thư mục media của 1 ngày sang thư mục phụ (OneDrive), bỏ qua tệp đã có. Trả về số tệp mới. */
export function copyMediaDay(dayLabelStr, destRoot, srcRoot = MEDIA_DIR) {
  const src = path.join(srcRoot, dayLabelStr);
  if (!fs.existsSync(src)) return 0;
  const dest = path.join(destRoot, "media", dayLabelStr);
  let copied = 0;
  const walk = (from, to) => {
    fs.mkdirSync(to, { recursive: true });
    for (const e of fs.readdirSync(from, { withFileTypes: true })) {
      const f = path.join(from, e.name);
      const t = path.join(to, e.name);
      if (e.isDirectory()) walk(f, t);
      else if (!e.name.endsWith(".part")) {
        // Tệp 0 byte là chỗ giữ của lượt tải đang chạy (hoặc mồ côi): không chép, kẻo bản rỗng nằm lại ở đích vĩnh viễn.
        if (fs.statSync(f).size === 0) continue;
        // Đích đã có thì giữ nguyên (không ghi đè OneDrive), trừ khi đích là bản rỗng do lần chép lỗi trước.
        if (fs.existsSync(t) && fs.statSync(t).size > 0) continue;
        fs.copyFileSync(f, t);
        copied += 1;
      }
    }
  };
  walk(src, dest);
  return copied;
}
