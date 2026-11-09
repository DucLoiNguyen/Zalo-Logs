import fs from "node:fs";
import path from "node:path";
import Database from "better-sqlite3";
import { MEDIA_TYPES_SQL } from "./media.js";
import { DB_FILE } from "./paths.js";

export function openDb(file = DB_FILE) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const db = new Database(file);
  db.pragma("journal_mode = WAL");
  db.exec(`
    CREATE TABLE IF NOT EXISTS groups (
      group_id   TEXT PRIMARY KEY,
      name       TEXT NOT NULL,
      updated_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS messages (
      group_id     TEXT NOT NULL,
      msg_id       TEXT NOT NULL,
      sender_id    TEXT,
      sender_name  TEXT,
      msg_type     TEXT,
      content_json TEXT,
      ts           INTEGER NOT NULL,
      recalled     INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY (group_id, msg_id)
    );
    CREATE INDEX IF NOT EXISTS idx_messages_group_ts ON messages (group_id, ts);
    -- Trạng thái tải ảnh/file/video của từng tin: ok | failed | skipped. rel_path tính từ exports/media, dùng dấu "/".
    CREATE TABLE IF NOT EXISTS media (
      group_id   TEXT NOT NULL,
      msg_id     TEXT NOT NULL,
      rel_path   TEXT,
      status     TEXT NOT NULL,
      size       INTEGER,
      error      TEXT,
      attempts   INTEGER NOT NULL DEFAULT 0,
      updated_at INTEGER NOT NULL,
      PRIMARY KEY (group_id, msg_id)
    );
  `);

  const upsertGroup = db.prepare(
    `INSERT INTO groups (group_id, name, updated_at) VALUES (?, ?, ?)
     ON CONFLICT(group_id) DO UPDATE SET name = excluded.name, updated_at = excluded.updated_at`
  );
  // INSERT OR IGNORE: tin realtime và tin backfill trùng nhau sẽ không bị ghi đôi.
  const insert = db.prepare(
    `INSERT OR IGNORE INTO messages
       (group_id, msg_id, sender_id, sender_name, msg_type, content_json, ts)
     VALUES (@groupId, @msgId, @senderId, @senderName, @msgType, @contentJson, @ts)`
  );
  const recall = db.prepare(`UPDATE messages SET recalled = 1 WHERE group_id = ? AND msg_id = ?`);
  const listGroups = db.prepare(`SELECT group_id AS id, name FROM groups ORDER BY name`);
  const range = db.prepare(
    `SELECT msg_id AS msgId, sender_id AS senderId, sender_name AS senderName,
            msg_type AS msgType, content_json AS contentJson, ts, recalled
       FROM messages WHERE group_id = ? AND ts >= ? AND ts < ? ORDER BY ts, msg_id`
  );

  const getMedia = db.prepare(`SELECT rel_path AS relPath, status, attempts FROM media WHERE group_id = ? AND msg_id = ?`);
  const setMedia = db.prepare(
    `INSERT INTO media (group_id, msg_id, rel_path, status, size, error, attempts, updated_at)
     VALUES (@groupId, @msgId, @relPath, @status, @size, @error, @attempts, @now)
     ON CONFLICT(group_id, msg_id) DO UPDATE SET
       rel_path = excluded.rel_path, status = excluded.status, size = excluded.size, error = excluded.error,
       attempts = media.attempts + excluded.attempts, updated_at = excluded.updated_at`
  );
  // Tin ảnh/file/video từ sinceMs chưa tải (chưa có dòng media) hoặc tải lỗi chưa quá maxAttempts lần.
  const pendingMedia = db.prepare(
    `SELECT m.group_id AS groupId, m.msg_id AS msgId, m.msg_type AS msgType, m.content_json AS contentJson,
            m.ts, g.name AS groupName
       FROM messages m
       LEFT JOIN groups g ON g.group_id = m.group_id
       LEFT JOIN media d ON d.group_id = m.group_id AND d.msg_id = m.msg_id
      WHERE m.msg_type IN (${MEDIA_TYPES_SQL}) AND m.ts >= ?
        AND (d.msg_id IS NULL OR (d.status = 'failed' AND d.attempts < ?))
      ORDER BY m.ts`
  );
  const mediaRange = db.prepare(
    `SELECT d.msg_id AS msgId, d.rel_path AS relPath
       FROM media d JOIN messages m ON m.group_id = d.group_id AND m.msg_id = d.msg_id
      WHERE m.group_id = ? AND m.ts >= ? AND m.ts < ? AND d.status = 'ok'`
  );

  return {
    getMedia: (groupId, msgId) => getMedia.get(String(groupId), String(msgId)),
    setMedia: (m) =>
      setMedia.run({
        groupId: String(m.groupId),
        msgId: String(m.msgId),
        relPath: m.relPath ?? null,
        status: m.status,
        size: m.size ?? null,
        error: m.error ?? null,
        attempts: m.status === "failed" ? 1 : 0,
        now: Date.now(),
      }),
    pendingMedia: (sinceMs, maxAttempts = 5) =>
      pendingMedia.all(sinceMs, maxAttempts).map((r) => ({ ...r, content: JSON.parse(r.contentJson) })),
    /** Map msgId -> rel_path của các tệp đã tải trong khoảng [fromMs, toMs). */
    queryMedia: (groupId, fromMs, toMs) =>
      new Map(mediaRange.all(String(groupId), fromMs, toMs).map((r) => [r.msgId, r.relPath])),
    upsertGroup: (id, name) => upsertGroup.run(String(id), String(name), Date.now()),
    insertMessage: (m) =>
      insert.run({
        groupId: String(m.groupId),
        msgId: String(m.msgId),
        senderId: m.senderId == null ? null : String(m.senderId),
        senderName: m.senderName ?? null,
        msgType: m.msgType ?? null,
        contentJson: JSON.stringify(m.content ?? null),
        ts: m.ts,
      }).changes,
    markRecalled: (groupId, msgId) => recall.run(String(groupId), String(msgId)).changes,
    listGroups: () => listGroups.all(),
    queryMessages: (groupId, fromMs, toMs) =>
      range.all(String(groupId), fromMs, toMs).map((r) => ({
        ...r,
        content: JSON.parse(r.contentJson),
        recalled: !!r.recalled,
      })),
    close: () => db.close(),
  };
}
