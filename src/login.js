import fs from "node:fs";
import { exec } from "node:child_process";
import { LoginQRCallbackEventType as T } from "zca-js";
import { newZalo, saveCredentials } from "./zaloClient.js";
import { DATA_DIR, QR_FILE } from "./paths.js";

fs.mkdirSync(DATA_DIR, { recursive: true });

const zalo = newZalo();
await zalo.loginQR({ qrPath: QR_FILE }, async (event) => {
  switch (event.type) {
    case T.QRCodeGenerated:
      await event.actions.saveToFile(QR_FILE);
      console.log(`\nMã QR đã lưu tại: ${QR_FILE}`);
      console.log("Mở Zalo trên điện thoại > quét mã QR > bấm xác nhận.");
      if (process.platform === "win32") exec(`start "" "${QR_FILE}"`);
      break;
    case T.QRCodeScanned:
      console.log(`Đã quét bởi "${event.data.display_name}". Hãy bấm XÁC NHẬN trên điện thoại.`);
      break;
    case T.QRCodeExpired:
      console.log("Mã QR hết hạn, đang tạo mã mới...");
      event.actions.retry();
      break;
    case T.QRCodeDeclined:
      console.error("Bạn đã từ chối đăng nhập trên điện thoại.");
      process.exit(1);
      break;
    case T.GotLoginInfo:
      saveCredentials({
        imei: event.data.imei,
        cookie: event.data.cookie,
        userAgent: event.data.userAgent,
      });
      console.log("Đã lưu phiên đăng nhập vào data/credentials.json (giữ bí mật file này).");
      break;
  }
});

try {
  fs.unlinkSync(QR_FILE);
} catch {}
console.log("Đăng nhập xong. Tiếp theo: npm run groups");
process.exit(0);
