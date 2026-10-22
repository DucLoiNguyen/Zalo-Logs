import fs from "node:fs";
import path from "node:path";
import ExcelJS from "exceljs";
import { describe, toExcelDateTime, toExcelDay } from "./format.js";

const MAX_CELL = 32000; // Excel giới hạn 32767 ký tự/ô
const HEADER_FILL = { type: "pattern", pattern: "solid", fgColor: { argb: "FFD9E1F2" } };
const BORDER = {
  top: { style: "thin" },
  left: { style: "thin" },
  bottom: { style: "thin" },
  right: { style: "thin" },
};

export const safeFileName = (s) =>
  String(s).replace(/[\\/:*?"<>|]/g, "_").replace(/\s+/g, " ").trim().slice(0, 100) || "nhom";

function styleSheet(ws, widths, wrapCols = []) {
  ws.columns = ws.columns.map((c, i) => ({ ...c, width: widths[i] }));
  const header = ws.getRow(1);
  header.font = { bold: true };
  header.fill = HEADER_FILL;
  header.alignment = { vertical: "middle", horizontal: "center" };
  ws.views = [{ state: "frozen", ySplit: 1 }];
  ws.autoFilter = { from: { row: 1, column: 1 }, to: { row: 1, column: ws.columns.length } };
  ws.eachRow((row) => {
    row.eachCell((cell, col) => {
      cell.border = BORDER;
      if (row.number > 1) {
        cell.alignment = { vertical: "top", wrapText: wrapCols.includes(col) };
      }
    });
  });
}

const LOG_COLUMNS = [
  { header: "Ngày", key: "day" },
  { header: "Thời gian", key: "time" },
  { header: "Người gửi", key: "sender" },
  { header: "Loại", key: "kind" },
  { header: "Nội dung", key: "text" },
];

/** Ghi các dòng tin nhắn vào ws (đã có LOG_COLUMNS), trả về danh sách file đính kèm. */
function fillLogSheet(ws, rows, tz) {
  const files = [];
  for (const r of rows) {
    const d = describe(r.msgType, r.content, r.recalled);
    const sender = r.senderName || r.senderId || "";
    ws.addRow({
      day: toExcelDay(r.ts, tz),
      time: toExcelDateTime(r.ts, tz),
      sender,
      kind: d.kind,
      text: d.text.slice(0, MAX_CELL),
    });
    if (d.kind === "Tập tin") files.push({ sender, name: d.fileName, time: toExcelDateTime(r.ts, tz) });
  }
  ws.getColumn("day").numFmt = "dd/mm/yyyy";
  ws.getColumn("time").numFmt = "dd/mm/yyyy hh:mm:ss";
  styleSheet(ws, [12, 20, 26, 13, 90], [5]);
  return files;
}

async function writeWorkbook(wb, outDir, name) {
  fs.mkdirSync(outDir, { recursive: true });
  const file = path.join(outDir, name);
  await wb.xlsx.writeFile(file);
  return file;
}

/**
 * Xuất 1 nhóm trong khoảng [fromMs, toMs) ra file .xlsx gồm 2 sheet:
 *  - "Tin nhắn": toàn bộ log
 *  - "Tổng hợp file đính kèm": STT | Người chat | Tên file | Thời gian (giống bảng trong file mẫu)
 * Trả về { file, count, files } hoặc null nếu không có tin nào.
 */
export async function exportGroup({ db, group, fromMs, toMs, tz, outDir, label }) {
  const rows = db.queryMessages(group.id, fromMs, toMs);
  if (rows.length === 0) return null;

  const wb = new ExcelJS.Workbook();
  wb.creator = "zalo-logger";
  wb.created = new Date();

  const logSheet = wb.addWorksheet("Tin nhắn");
  logSheet.columns = LOG_COLUMNS;
  const fileSheet = wb.addWorksheet("Tổng hợp file đính kèm");
  fileSheet.columns = [
    { header: "STT", key: "stt" },
    { header: "Người chat", key: "sender" },
    { header: "Tên file", key: "name" },
    { header: "Thời gian", key: "time" },
  ];

  const files = fillLogSheet(logSheet, rows, tz);
  files.forEach((f, i) => fileSheet.addRow({ stt: i + 1, ...f }));
  fileSheet.getColumn("time").numFmt = "dd/mm/yyyy hh:mm:ss";
  styleSheet(fileSheet, [7, 26, 60, 20], [3]);

  const file = await writeWorkbook(wb, outDir, `${safeFileName(group.name)}_${label}.xlsx`);
  return { file, count: rows.length, files: files.length };
}

/** Tên sheet Excel: tối đa 31 ký tự, không chứa \ / ? * [ ] :, không trùng nhau. */
function sheetName(name, used) {
  const base = String(name).replace(/[\\/?*[\]:]/g, "_").trim().slice(0, 31) || "Nhom";
  let out = base;
  for (let i = 2; used.has(out.toLowerCase()); i++) out = `${base.slice(0, 31 - String(i).length - 1)}_${i}`;
  used.add(out.toLowerCase());
  return out;
}

/**
 * Xuất nhiều nhóm vào MỘT file .xlsx: mỗi nhóm có tin nhắn một sheet riêng,
 * cuối cùng là sheet "File đính kèm" gộp (có cột Nhóm). Nhóm không có tin sẽ bị bỏ qua.
 * Trả về { file, count, files, groups } hoặc null nếu không nhóm nào có tin.
 */
export async function exportCombined({ db, groups, fromMs, toMs, tz, outDir, label }) {
  const wb = new ExcelJS.Workbook();
  wb.creator = "zalo-logger";
  wb.created = new Date();
  const used = new Set(["file đính kèm"]);

  const attach = [];
  let count = 0;
  let nGroups = 0;
  for (const g of groups) {
    const rows = db.queryMessages(g.id, fromMs, toMs);
    if (rows.length === 0) continue;
    const ws = wb.addWorksheet(sheetName(g.name, used));
    ws.columns = LOG_COLUMNS;
    for (const f of fillLogSheet(ws, rows, tz)) attach.push({ group: g.name, ...f });
    count += rows.length;
    nGroups += 1;
  }
  if (count === 0) return null;

  const fileSheet = wb.addWorksheet("File đính kèm");
  fileSheet.columns = [
    { header: "STT", key: "stt" },
    { header: "Nhóm", key: "group" },
    { header: "Người chat", key: "sender" },
    { header: "Tên file", key: "name" },
    { header: "Thời gian", key: "time" },
  ];
  attach.forEach((f, i) => fileSheet.addRow({ stt: i + 1, ...f }));
  fileSheet.getColumn("time").numFmt = "dd/mm/yyyy hh:mm:ss";
  styleSheet(fileSheet, [7, 28, 26, 60, 20], [2, 4]);

  const file = await writeWorkbook(wb, outDir, `Zalo_${label}.xlsx`);
  return { file, count, files: attach.length, groups: nGroups };
}
