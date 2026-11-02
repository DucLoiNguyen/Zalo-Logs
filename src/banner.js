// Banner hiện ở đầu console khi khởi chạy tool.
import fs from "node:fs";
import { ROOT } from "./paths.js";
import path from "node:path";

const ART = String.raw`
 ______      _          _                                
|___  /     | |        | |                               
   / / __ _ | |  ___   | |      ___   __ _  __ _  ___ _ __ 
  / / / _' || | / _ \  | |     / _ \ / _' |/ _' |/ _ \ '__|
 / /_| (_| || || (_) | | |____| (_) | (_| | (_| |  __/ |   
/_____\__,_||_| \___/  |______|\___/ \__, |\__, |\___|_|   
                                      __/ | __/ |          
                                     |___/ |___/           `;

export function printBanner() {
  let version = "";
  try {
    version = JSON.parse(fs.readFileSync(path.join(ROOT, "package.json"), "utf8")).version;
  } catch {}
  const color = process.stdout.isTTY && !process.env.NO_COLOR;
  const cyan = (s) => (color ? `\x1b[36m${s}\x1b[0m` : s);
  const dim = (s) => (color ? `\x1b[2m${s}\x1b[0m` : s);
  console.log(cyan(ART));
  console.log(`  ${cyan("Zalo Logger")}${version ? ` v${version}` : ""} ${dim("- log tin nhan nhom Zalo -> SQLite + MongoDB Atlas")}`);
  console.log(dim("  Dung: Ctrl+C\n"));
}
