// Cầu nối sang Hermes: đọc SQLite (CHỈ ĐỌC) và nối tin mới vào <hermesHistoryDir>/<group_id>.jsonl.
//   npm run export-hermes                  -> xuất tin mới + sự kiện thu hồi / tệp tải xong
//   npm run export-hermes -- --dry-run     -> chỉ đếm, không ghi gì
//   npm run export-hermes -- --quiet       -> chỉ in một dòng tổng kết
//   npm run export-hermes -- --reset --yes -> xoá checkpoint, đổi tên thư mục JSONL cũ, xuất lại từ đầu
// Checkpoint là rowid của bảng messages (không phải ts): tin được bù (backfill) có ts cũ nhưng được ghi sau,
// lọc theo ts sẽ bị sót. Không bao giờ ghi vào zalo-logs.db.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { parseArgs } from "node:util";
import Database from "better-sqlite3";
import { loadConfig } from "./config.js";
import { describe } from "./format.js";
import { DB_FILE, HERMES_HISTORY_DIR, HERMES_STATE_FILE } from "./paths.js";

const SKIP_TYPES = ["chat.undo", "chat.delete"]; // tin hệ thống của thao tác thu hồi; trạng thái đã nằm ở cột recalled
const MEDIA_TYPES = ["chat.photo", "share.file", "chat.video.msg"];
const EVENT_WINDOW_MS = 7 * 86400_000; // chỉ quét sự kiện đến sau (thu hồi / tệp tải xong) của tin trong 7 ngày gần nhất

export class ExportError extends Error {
  constructor(message, code = 1) {
    super(message);
    this.exitCode = code;
  }
}

const keyOf = (groupId, msgId) => `${groupId}:${msgId}`;

function readState(file) {
  try {
    const s = JSON.parse(fs.readFileSync(file, "utf8"));
    return {
      lastRowid: Number(s.lastRowid) || 0,
      recalledSent: new Set(s.recalledSent ?? []),
      mediaSent: new Set(s.mediaSent ?? []),
    };
  } catch (e) {
    if (e.code === "ENOENT") return { lastRowid: 0, recalledSent: new Set(), mediaSent: new Set() };
    throw new ExportError(`Không đọc được ${file}: ${e.message}`);
  }
}

function writeState(file, state, now) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.tmp`;
  fs.writeFileSync(
    tmp,
    JSON.stringify(
      {
        lastRowid: state.lastRowid,
        recalledSent: [...state.recalledSent],
        mediaSent: [...state.mediaSent],
        updatedAt: new Date(now).toISOString(),
      },
      null,
      2
    )
  );
  fs.renameSync(tmp, file);
}

function parseContent(json) {
  try {
    return JSON.parse(json);
  } catch {
    return null;
  }
}

/** Tệp đính kèm của tin ảnh/file/video; path = null nếu chưa tải xong. */
function attachmentOf(msgType, content, relPath, mediaStatus) {
  if (!MEDIA_TYPES.includes(msgType)) return null;
  const ok = mediaStatus === "ok" && relPath;
  const title = typeof content?.title === "string" ? content.title.trim() : "";
  // Tin ảnh: title là lời nhắn kèm theo chứ không phải tên tệp, nên ưu tiên tên tệp đã tải.
  const name = describe(msgType, content, false).fileName || (ok ? path.posix.basename(relPath) : "") || title;
  return { name, path: ok ? `media/${relPath}` : null };
}

/** Đổi tên thư mục JSONL cũ và xoá checkpoint để xuất lại từ đầu. */
export function resetExport({ outDir = HERMES_HISTORY_DIR, stateFile = HERMES_STATE_FILE, now = Date.now() } = {}) {
  const stamp = new Date(now).toISOString().replace(/[-:]/g, "").replace(/\..*$/, "").replace("T", "-");
  let backup = null;
  if (fs.existsSync(outDir)) {
    backup = `${outDir}.bak-${stamp}`;
    fs.renameSync(outDir, backup);
  }
  fs.rmSync(stateFile, { force: true });
  return { backup };
}

/**
 * Xuất tin mới. Trả về { messages, recalls, attachments, groups, dryRun }.
 * Ghi các file JSONL xong mới ghi state: chết giữa chừng thì lần sau có thể ghi trùng vài dòng (Hermes tự chống trùng),
 * còn mất tin thì không chấp nhận được.
 */
export function runExport({
  dbFile = DB_FILE,
  outDir = HERMES_HISTORY_DIR,
  stateFile = HERMES_STATE_FILE,
  dryRun = false,
  now = Date.now(),
} = {}) {
  let db;
  try {
    db = new Database(dbFile, { readonly: true, fileMustExist: true });
  } catch (e) {
    throw new ExportError(`Không mở được ${dbFile} (chỉ đọc): ${e.message}`);
  }
  try {
    const state = readState(stateFile);
    const skipSql = SKIP_TYPES.map((t) => `'${t}'`).join(",");
    const hasMedia = !!db.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'media'").get();
    const cutoff = now - EVENT_WINDOW_MS;

    const newSql = `
      SELECT m.rowid AS rid, m.group_id AS groupId, m.msg_id AS msgId, m.sender_id AS senderId,
             m.sender_name AS senderName, m.msg_type AS msgType, m.content_json AS contentJson,
             m.ts, m.recalled, g.name AS groupName,
             ${hasMedia ? "d.rel_path" : "NULL"} AS relPath, ${hasMedia ? "d.status" : "NULL"} AS mediaStatus
        FROM messages m
        LEFT JOIN groups g ON g.group_id = m.group_id
        ${hasMedia ? "LEFT JOIN media d ON d.group_id = m.group_id AND d.msg_id = m.msg_id" : ""}
       WHERE m.rowid > ? ORDER BY m.rowid`;
    const recallSql = `
      SELECT group_id AS groupId, msg_id AS msgId FROM messages
       WHERE rowid <= ? AND ts >= ? AND recalled = 1 AND msg_type NOT IN (${skipSql})`;
    const mediaSql = `
      SELECT m.group_id AS groupId, m.msg_id AS msgId, m.msg_type AS msgType, m.content_json AS contentJson,
             d.rel_path AS relPath, d.status AS mediaStatus
        FROM messages m JOIN media d ON d.group_id = m.group_id AND d.msg_id = m.msg_id
       WHERE m.rowid <= ? AND m.ts >= ? AND d.status = 'ok' AND m.msg_type IN ('chat.photo','share.file','chat.video.msg')`;

    // Một giao dịch đọc để mọi truy vấn thấy cùng một ảnh chụp của DB.
    const snapshot = db.transaction(() => {
      const maxRowid = db.prepare("SELECT COALESCE(MAX(rowid), 0) AS m FROM messages").get().m;
      if (maxRowid < state.lastRowid) {
        throw new ExportError(
          `⚠ MAX(rowid)=${maxRowid} nhỏ hơn checkpoint ${state.lastRowid}: DB có thể bị VACUUM/khôi phục. ` +
            "Không tự xuất lại; kiểm tra rồi dùng --reset --yes nếu muốn xuất lại từ đầu.",
          2
        );
      }
      const rows = db.prepare(newSql).all(state.lastRowid);
      const newLast = rows.length ? rows.at(-1).rid : state.lastRowid;
      const recalledRows = db.prepare(recallSql).all(newLast, cutoff);
      const mediaRows = hasMedia ? db.prepare(mediaSql).all(newLast, cutoff) : [];
      return { rows, newLast, recalledRows, mediaRows };
    });
    const { rows, newLast, recalledRows, mediaRows } = snapshot.deferred();

    const perGroup = new Map(); // group_id -> các dòng JSON sẽ nối vào file
    const push = (groupId, obj) => {
      if (!perGroup.has(groupId)) perGroup.set(groupId, []);
      perGroup.get(groupId).push(JSON.stringify(obj));
    };

    let messages = 0;
    for (const r of rows) {
      if (SKIP_TYPES.includes(r.msgType)) continue;
      const content = parseContent(r.contentJson);
      const d = describe(r.msgType, content, false); // luôn nội dung gốc; thu hồi chỉ nằm ở "recalled"
      const line = {
        message_id: r.msgId,
        from_id: r.senderId,
        from_name: r.senderName,
        text: d.text,
        kind: d.kind,
        timestamp: r.ts / 1000,
        type: r.msgType,
        group_name: r.groupName ?? null,
        recalled: !!r.recalled,
      };
      const att = attachmentOf(r.msgType, content, r.relPath, r.mediaStatus);
      if (att) line.attachment = att;
      push(r.groupId, line);
      messages += 1;
      // Đã mang sẵn trạng thái thu hồi / đường dẫn tệp trong dòng tin thì không cần phát thêm sự kiện.
      if (r.recalled) state.recalledSent.add(keyOf(r.groupId, r.msgId));
      if (att?.path) state.mediaSent.add(keyOf(r.groupId, r.msgId));
    }

    // Sự kiện đến sau: tin đã xuất trước đó, giờ mới bị thu hồi / tải xong tệp.
    let recalls = 0;
    let attachments = 0;
    const recallWindow = new Set();
    for (const r of recalledRows) {
      const k = keyOf(r.groupId, r.msgId);
      recallWindow.add(k);
      if (state.recalledSent.has(k)) continue;
      push(r.groupId, { type: "recall", message_id: r.msgId });
      state.recalledSent.add(k);
      recalls += 1;
    }
    const mediaWindow = new Set();
    for (const r of mediaRows) {
      const k = keyOf(r.groupId, r.msgId);
      mediaWindow.add(k);
      if (state.mediaSent.has(k)) continue;
      const att = attachmentOf(r.msgType, parseContent(r.contentJson), r.relPath, r.mediaStatus);
      push(r.groupId, { type: "attachment", message_id: r.msgId, attachment: att });
      state.mediaSent.add(k);
      attachments += 1;
    }

    // Dọn state: chỉ giữ khoá còn nằm trong cửa sổ 7 ngày (ngoài cửa sổ thì không bao giờ phát sự kiện nữa).
    state.recalledSent = new Set([...state.recalledSent].filter((k) => recallWindow.has(k)));
    state.mediaSent = new Set([...state.mediaSent].filter((k) => mediaWindow.has(k)));
    state.lastRowid = newLast;

    const summary = {
      messages,
      recalls,
      attachments,
      groups: perGroup.size,
      lines: Object.fromEntries([...perGroup].map(([g, l]) => [g, l.length])),
      dryRun,
      outDir,
    };
    if (dryRun) return summary;

    if (perGroup.size) fs.mkdirSync(outDir, { recursive: true });
    for (const [groupId, lines] of perGroup) {
      fs.appendFileSync(path.join(outDir, `${groupId}.jsonl`), lines.join("\n") + "\n", "utf8");
    }
    writeState(stateFile, state, now);
    return summary;
  } finally {
    db.close();
  }
}

function main() {
  const { values } = parseArgs({
    options: {
      "dry-run": { type: "boolean" },
      reset: { type: "boolean" },
      yes: { type: "boolean" },
      quiet: { type: "boolean" },
    },
  });
  const { hermesHistoryDir } = loadConfig();
  if (values.reset) {
    if (!values.yes) throw new ExportError("--reset xoá checkpoint và đổi tên thư mục JSONL cũ, phải kèm --yes để xác nhận.");
    if (values["dry-run"]) {
      console.log(`export-hermes (dry-run): sẽ đổi tên ${hermesHistoryDir} thành .bak-<thời gian>, xoá ${HERMES_STATE_FILE} rồi xuất lại.`);
      return;
    }
    const { backup } = resetExport({ outDir: hermesHistoryDir });
    console.log(backup ? `Đã đổi tên thư mục cũ -> ${backup}` : "Chưa có thư mục JSONL cũ.");
  }
  const s = runExport({ outDir: hermesHistoryDir, dryRun: !!values["dry-run"] });
  console.log(
    `export-hermes${s.dryRun ? " (dry-run, không ghi gì)" : ""}: ${s.messages} tin mới, ${s.recalls} thu hồi, ` +
      `${s.attachments} tệp, ${s.groups} nhóm → ${s.outDir}`
  );
  if (!values.quiet) for (const [g, n] of Object.entries(s.lines)) console.log(`  ${g}: +${n} dòng`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  try {
    main();
  } catch (e) {
    console.error(e.message);
    process.exit(e.exitCode ?? 1);
  }
}
