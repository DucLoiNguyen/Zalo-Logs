import fs from "node:fs";
import path from "node:path";
import { Zalo } from "zca-js";
import { CRED_FILE, DATA_DIR } from "./paths.js";

// selfListen: true để log cả tin do chính tài khoản này gửi.
export const newZalo = () => new Zalo({ selfListen: true });

export function saveCredentials(creds) {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  fs.writeFileSync(CRED_FILE, JSON.stringify(creds, null, 2), { mode: 0o600 });
}

export async function loginSaved() {
  if (!fs.existsSync(CRED_FILE)) {
    throw new Error("Chưa đăng nhập. Chạy `npm run login` trước và quét mã QR.");
  }
  const creds = JSON.parse(fs.readFileSync(CRED_FILE, "utf8"));
  try {
    return await newZalo().login(creds);
  } catch (e) {
    throw new Error(
      `Đăng nhập bằng phiên đã lưu thất bại (${e.message}). Có thể phiên hết hạn: xoá ${path.relative(process.cwd(), CRED_FILE)} rồi chạy lại \`npm run login\`.`
    );
  }
}

/** Lấy toàn bộ nhóm của tài khoản: Map id -> { id, name, total } */
export async function fetchAllGroups(api) {
  const { gridVerMap } = await api.getAllGroups();
  const ids = Object.keys(gridVerMap ?? {});
  const out = new Map();
  for (let i = 0; i < ids.length; i += 50) {
    const info = await api.getGroupInfo(ids.slice(i, i + 50));
    for (const [id, g] of Object.entries(info.gridInfoMap ?? {})) {
      out.set(id, { id, name: g.name ?? "(không tên)", total: g.totalMember ?? 0 });
    }
  }
  return out;
}
