import fs from "node:fs";
import path from "node:path";
import Database from "better-sqlite3";
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

  return {
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
