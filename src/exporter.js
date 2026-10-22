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
  logSheet.columns = [
    { header: "Ngày", key: "day" },
    { header: "Thời gian", key: "time" },
    { header: "Người gửi", key: "sender" },
    { header: "Loại", key: "kind" },
    { header: "Nội dung", key: "text" },
  ];

  const fileSheet = wb.addWorksheet("Tổng hợp file đính kèm");
  fileSheet.columns = [
    { header: "STT", key: "stt" },
    { header: "Người chat", key: "sender" },
    { header: "Tên file", key: "name" },
    { header: "Thời gian", key: "time" },
  ];

  let fileCount = 0;
  for (const r of rows) {
    const d = describe(r.msgType, r.content, r.recalled);
    const sender = r.senderName || r.senderId || "";
    logSheet.addRow({
      day: toExcelDay(r.ts, tz),
      time: toExcelDateTime(r.ts, tz),
      sender,
      kind: d.kind,
      text: d.text.slice(0, MAX_CELL),
    });
    if (d.kind === "Tập tin") {
      fileCount += 1;
      fileSheet.addRow({
        stt: fileCount,
        sender,
        name: d.fileName,
        time: toExcelDateTime(r.ts, tz),
      });
    }
  }

  logSheet.getColumn("day").numFmt = "dd/mm/yyyy";
  logSheet.getColumn("time").numFmt = "dd/mm/yyyy hh:mm:ss";
  fileSheet.getColumn("time").numFmt = "dd/mm/yyyy hh:mm:ss";
  styleSheet(logSheet, [12, 20, 26, 13, 90], [5]);
  styleSheet(fileSheet, [7, 26, 60, 20], [3]);

  fs.mkdirSync(outDir, { recursive: true });
  const file = path.join(outDir, `${safeFileName(group.name)}_${label}.xlsx`);
  await wb.xlsx.writeFile(file);
  return { file, count: rows.length, files: fileCount };
}
