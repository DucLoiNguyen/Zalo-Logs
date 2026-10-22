// Định dạng thời gian theo múi giờ + chuyển nội dung tin nhắn Zalo thành chữ dễ đọc.

const pad = (n) => String(n).padStart(2, "0");

export function zonedParts(ms, tz) {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: tz,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(ms));
  const o = {};
  for (const p of parts) o[p.type] = p.value;
  return { y: +o.year, mo: +o.month, d: +o.day, h: +o.hour, mi: +o.minute, s: +o.second };
}

/** dd/mm/yyyy hh:mm:ss (giống file mẫu) */
export function formatDateTime(ms, tz) {
  const p = zonedParts(ms, tz);
  return `${pad(p.d)}/${pad(p.mo)}/${p.y} ${pad(p.h)}:${pad(p.mi)}:${pad(p.s)}`;
}

/** Giờ địa phương -> Date "giả UTC" để Excel hiển thị đúng giờ địa phương. */
export function toExcelDateTime(ms, tz) {
  const p = zonedParts(ms, tz);
  return new Date(Date.UTC(p.y, p.mo - 1, p.d, p.h, p.mi, p.s));
}

export function toExcelDay(ms, tz) {
  const p = zonedParts(ms, tz);
  return new Date(Date.UTC(p.y, p.mo - 1, p.d));
}

function tzOffsetMs(ms, tz) {
  const p = zonedParts(ms, tz);
  return Date.UTC(p.y, p.mo - 1, p.d, p.h, p.mi, p.s) - Math.floor(ms / 1000) * 1000;
}

/** Mốc 00:00 của ngày (y, m, d) tại múi giờ tz, tính bằng ms UTC. */
export function startOfDay({ y, m, d }, tz) {
  const guess = Date.UTC(y, m - 1, d);
  let t = guess - tzOffsetMs(guess, tz);
  t = guess - tzOffsetMs(t, tz);
  return t;
}

export function addDays({ y, m, d }, n) {
  const dt = new Date(Date.UTC(y, m - 1, d + n));
  return { y: dt.getUTCFullYear(), m: dt.getUTCMonth() + 1, d: dt.getUTCDate() };
}

/** Nhận "today" | "yesterday" | "YYYY-MM-DD" -> {y, m, d} */
export function parseDay(input, tz, now = Date.now()) {
  const p = zonedParts(now, tz);
  const today = { y: p.y, m: p.mo, d: p.d };
  if (!input || input === "today") return today;
  if (input === "yesterday") return addDays(today, -1);
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(input);
  if (!m) throw new Error(`Ngày không hợp lệ: "${input}" (dùng YYYY-MM-DD, today hoặc yesterday)`);
  const day = { y: +m[1], m: +m[2], d: +m[3] };
  const check = new Date(Date.UTC(day.y, day.m - 1, day.d));
  if (check.getUTCMonth() + 1 !== day.m || check.getUTCDate() !== day.d) {
    throw new Error(`Ngày không tồn tại: "${input}"`);
  }
  return day;
}

export const dayLabel = ({ y, m, d }) => `${y}-${pad(m)}-${pad(d)}`;

/** Zalo trả ts là mili-giây (dạng chuỗi); phòng trường hợp là giây. */
export function normalizeTs(ts) {
  const n = Number(ts);
  if (!Number.isFinite(n) || n <= 0) return Date.now();
  return n < 1e12 ? n * 1000 : n;
}

const str = (v) => (typeof v === "string" ? v.trim() : "");

/**
 * Chuyển (msgType, content) của Zalo thành { kind, text, fileName? }.
 * Nhãn bám theo file mẫu: [Hình ảnh], [Tập tin: ...], [Danh thiếp], [Tin nhắn đã thu hồi].
 * Dữ liệu gốc luôn được lưu trong DB nên có thể chỉnh hàm này rồi xuất lại.
 */
export function describe(msgType, content, recalled = false) {
  if (recalled) return { kind: "Thu hồi", text: "[Tin nhắn đã thu hồi]" };
  if (typeof content === "string") return { kind: "Tin nhắn", text: content };

  const c = content && typeof content === "object" ? content : {};
  const title = str(c.title);
  const desc = str(c.description);
  const href = str(c.href);
  const join = (...a) => a.filter(Boolean).join(" ");

  switch (msgType) {
    case "chat.photo":
      return { kind: "Hình ảnh", text: join("[Hình ảnh]", title) };
    case "share.file": {
      const fileName = title || "không rõ tên";
      return { kind: "Tập tin", text: `[Tập tin: ${fileName}]`, fileName };
    }
    case "chat.video.msg":
      return { kind: "Video", text: join("[Video]", title) };
    case "chat.voice":
      return { kind: "Ghi âm", text: "[Ghi âm]" };
    case "chat.sticker":
      return { kind: "Sticker", text: "[Sticker]" };
    case "chat.gif":
      return { kind: "GIF", text: "[GIF]" };
    case "chat.location.new":
      return { kind: "Vị trí", text: join("[Vị trí]", title, desc) };
    case "chat.recommended":
    case "chat.link":
      if (/user|card/i.test(str(c.action))) {
        return { kind: "Danh thiếp", text: join("[Danh thiếp]", title, desc) };
      }
      return { kind: "Liên kết", text: join(title, href) || "[Liên kết]" };
    default:
      return { kind: "Khác", text: join(`[${msgType || "không rõ"}]`, title, desc, href) };
  }
}
