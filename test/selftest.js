// Tự kiểm tra phần DB + xuất Excel + khớp nhóm bằng dữ liệu giả (không cần đăng nhập Zalo).
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import ExcelJS from "exceljs";
import { openDb } from "../src/db.js";
import { exportCombined, exportGroup } from "../src/exporter.js";
import { matchGroups } from "../src/groupmatch.js";
import { mediaHref, mediaOf } from "../src/media.js";
import { addDays, describe, formatDateTime, parseDay, startOfDay } from "../src/format.js";

const TZ = "Asia/Ho_Chi_Minh";
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "zalo-logger-test-"));
const db = openDb(path.join(tmp, "test.db"));
// ngày/giờ địa phương (UTC+7) -> ms
const at = (d, mo, h, mi, s) => Date.UTC(2026, mo - 1, d, h - 7, mi, s);

db.upsertGroup("g1", "Test Clause Zalo");
let n = 0;
const add = (ts, sender, msgType, content) =>
  db.insertMessage({ groupId: "g1", msgId: `m${++n}`, senderId: `u-${sender}`, senderName: sender, msgType, content, ts });

add(at(20, 10, 10, 25, 26), "Hoang Le", "share.file", { title: "AICongVu_TaiKhoan_20260701_20261019 (HCM).xlsx" });
add(at(20, 10, 10, 25, 26), "Hoang Le", "webchat", "cái này bên Misa xuát cho HCM, anh em tham khảo");
add(at(20, 10, 14, 28, 44), "Hoang Le", "chat.photo", { title: "" });
add(at(20, 10, 15, 31, 37), "Tạ Thanh Hoàng", "share.file", { title: "PHU_LUC_DANH_SACH_DANG_KY_TAI_KHOAN_AI Cập nhật.xls" });
add(at(20, 10, 21, 31, 10), "Hoang Le", "chat.recommended", { action: "recommened.user", title: "Thien Huong", description: "0987704710" });
add(at(20, 10, 23, 59, 59), "Nkd", "webchat", "=1+1 (không được thành công thức)");
const recalledId = n + 1;
add(at(20, 10, 22, 0, 0), "Hthanhthanh", "webchat", "tin sẽ bị thu hồi");
add(at(21, 10, 0, 0, 0), "Đức Lợi Nguyễn", "webchat", "tin ngày 21, không được lọt vào file ngày 20");

// 1) Chống ghi trùng + thu hồi
assert.equal(db.insertMessage({ groupId: "g1", msgId: "m1", ts: 1, content: "x" }), 0, "trùng msgId phải bị bỏ qua");
assert.equal(db.markRecalled("g1", `m${recalledId}`), 1);

// 2) Ranh giới ngày theo múi giờ
const day = parseDay("2026-10-20", TZ);
const from = startOfDay(day, TZ);
const to = startOfDay(addDays(day, 1), TZ);
assert.equal(from, at(20, 10, 0, 0, 0));
assert.equal(formatDateTime(at(20, 10, 10, 25, 26), TZ), "20/10/2026 10:25:26");
assert.throws(() => parseDay("2026-02-31", TZ));

// 3) Xuất Excel
const res = await exportGroup({ db, group: { id: "g1", name: "Test Clause Zalo" }, fromMs: from, toMs: to, tz: TZ, outDir: tmp, label: "2026-10-20" });
assert.equal(res.count, 7, "7 tin trong ngày 20 (không gồm tin ngày 21)");
assert.equal(res.files, 2);

const wb = new ExcelJS.Workbook();
await wb.xlsx.readFile(res.file);
assert.deepEqual(wb.worksheets.map((w) => w.name), ["Tin nhắn", "Tổng hợp file đính kèm"]);

const logWs = wb.getWorksheet("Tin nhắn");
assert.equal(logWs.rowCount, 8);
assert.equal(logWs.getRow(2).getCell(3).value, "Hoang Le");
assert.equal(logWs.getRow(2).getCell(5).value, "[Tập tin: AICongVu_TaiKhoan_20260701_20261019 (HCM).xlsx]");
assert.equal(logWs.getRow(4).getCell(5).value, "[Hình ảnh]");
assert.equal(logWs.getRow(6).getCell(5).value, "[Danh thiếp] Thien Huong 0987704710");
assert.equal(logWs.getRow(7).getCell(5).value, "[Tin nhắn đã thu hồi]");
assert.equal(logWs.getRow(8).getCell(5).value, "=1+1 (không được thành công thức)");
assert.equal(typeof logWs.getRow(8).getCell(5).value, "string", "nội dung bắt đầu bằng = phải là chuỗi, không phải công thức");
// Giờ hiển thị trong Excel phải đúng giờ địa phương (10:25:26)
const t = logWs.getRow(2).getCell(2).value;
assert.equal(`${t.getUTCHours()}:${t.getUTCMinutes()}:${t.getUTCSeconds()}`, "10:25:26");

const fileWs = wb.getWorksheet("Tổng hợp file đính kèm");
assert.equal(fileWs.rowCount, 3);
assert.deepEqual(
  [1, 2, 3].map((c) => (c === 4 ? null : fileWs.getRow(3).getCell(c).value)),
  [2, "Tạ Thanh Hoàng", "PHU_LUC_DANH_SACH_DANG_KY_TAI_KHOAN_AI Cập nhật.xls"]
);

// 4) Khoảng nhiều ngày + ngày không có tin
const wide = await exportGroup({ db, group: { id: "g1", name: "A/B: test?" }, fromMs: from, toMs: startOfDay(addDays(day, 2), TZ), tz: TZ, outDir: tmp, label: "range" });
assert.equal(wide.count, 8);
assert.ok(path.basename(wide.file).startsWith("A_B_ test_"), "tên file Windows-safe");
assert.equal(await exportGroup({ db, group: { id: "g1", name: "x" }, fromMs: at(1, 1, 0, 0, 0), toMs: at(2, 1, 0, 0, 0), tz: TZ, outDir: tmp, label: "empty" }), null);

// 5) Khớp nhóm theo tên/ID
const all = [
  { id: "111", name: "Test  Clause Zalo" },
  { id: "222", name: "Nhóm Trùng" },
  { id: "333", name: "nhóm trùng" },
];
const m = matchGroups(["test clause zalo", "222", "Không có", "Nhóm Trùng"], all);
assert.deepEqual(m.matched.map((g) => g.id).sort(), ["111", "222", "333"]);
assert.deepEqual(m.unmatched, ["Không có"]);
assert.equal(m.ambiguous.length, 1);

// 6) Loại tin chưa biết không làm hỏng xuất
assert.match(describe("chat.todo.xyz", { title: "Việc cần làm" }).text, /\[chat\.todo\.xyz\] Việc cần làm/);

// 7) Gộp nhiều nhóm vào 1 file nhiều sheet
db.upsertGroup("g2", "Nhóm hai: tên rất dài vượt quá ba mươi mốt ký tự");
db.insertMessage({ groupId: "g2", msgId: "x1", senderName: "An", msgType: "share.file", content: { title: "b.pdf" }, ts: at(20, 10, 9, 0, 0) });
const groupsAll = [{ id: "g1", name: "Test Clause Zalo" }, { id: "g2", name: "Nhóm hai: tên rất dài vượt quá ba mươi mốt ký tự" }, { id: "g3", name: "Rỗng" }];
const comb = await exportCombined({ db, groups: groupsAll, fromMs: from, toMs: to, tz: TZ, outDir: tmp, label: "2026-10-20" });
assert.equal(comb.groups, 2);
assert.equal(comb.count, 8);
assert.equal(comb.files, 3);
const wc = new ExcelJS.Workbook();
await wc.xlsx.readFile(comb.file);
assert.equal(wc.worksheets.length, 3, "2 nhóm có tin + sheet File đính kèm (nhóm rỗng bị bỏ)");
assert.ok(wc.worksheets.every((w) => w.name.length <= 31 && !/[:\/?*[\]]/.test(w.name)));
assert.equal(wc.worksheets.at(-1).name, "File đính kèm");
assert.equal(wc.worksheets.at(-1).rowCount, 4);
assert.equal(await exportCombined({ db, groups: groupsAll, fromMs: at(1, 1, 0, 0, 0), toMs: at(2, 1, 0, 0, 0), tz: TZ, outDir: tmp, label: "e" }), null);

// 8) Media: lấy link tải + link tương đối trong Excel
assert.deepEqual(mediaOf("share.file", { title: "a b.xlsx", href: "https://f.example/gr/x", params: '{"fileSize":"4222"}' }), {
  url: "https://f.example/gr/x", name: "a b.xlsx", size: 4222,
});
const ph = mediaOf("chat.photo", { href: "https://p.example/a/b.jpg", params: '{"hd":"https://p.example/a/hd.png"}' });
assert.equal(ph.url, "https://p.example/a/hd.png");
assert.equal(ph.name, "anh.png");
assert.equal(mediaOf("webchat", "xin chào"), null);
assert.equal(mediaOf("chat.photo", { href: "" }), null, "không có link thì bỏ qua");
assert.equal(mediaHref("2026-10-20/Nhóm A/102526_báo cáo (1).xlsx"), "media/2026-10-20/Nh%C3%B3m%20A/102526_b%C3%A1o%20c%C3%A1o%20(1).xlsx");

db.insertMessage({ groupId: "g1", msgId: "p1", senderName: "An", msgType: "chat.photo", content: { href: "https://p.example/a.jpg" }, ts: at(20, 10, 9, 30, 0) });
db.setMedia({ groupId: "g1", msgId: "p1", relPath: "2026-10-20/Test/093000_anh.jpg", status: "ok", size: 5 });
db.setMedia({ groupId: "g1", msgId: "m1", status: "failed", error: "HTTP 404" });
assert.equal(db.queryMedia("g1", from, to).get("p1"), "2026-10-20/Test/093000_anh.jpg");
assert.equal(db.queryMedia("g1", from, to).has("m1"), false, "tệp lỗi không có link");
assert.deepEqual(db.pendingMedia(0).map((r) => r.msgId), ["x1", "m1", "m3", "m4"], "chưa tải + lỗi; p1 đã ok nên không còn");
const withMedia = await exportGroup({ db, group: { id: "g1", name: "Test Clause Zalo" }, fromMs: from, toMs: to, tz: TZ, outDir: tmp, label: "media" });
const wm = new ExcelJS.Workbook();
await wm.xlsx.readFile(withMedia.file);
const photoRow = wm.getWorksheet("Tin nhắn").getRows(2, 20).find((r) => r.getCell(4).value === "Hình ảnh" && r.getCell(6).value);
assert.equal(photoRow.getCell(6).value.hyperlink, "media/2026-10-20/Test/093000_anh.jpg");

db.close();
fs.rmSync(tmp, { recursive: true, force: true });
console.log("✔ Tất cả kiểm tra đều đạt");
