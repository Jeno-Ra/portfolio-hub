/**
 * 把 frontend/ 复制成 Capacitor 的 www/，并注入后端地址。
 *
 * 为什么需要这一步：
 * - 浏览器里前后端同源，前端用相对路径（API = ""）就行
 * - APK 里页面是从 file:/// 或 https://localhost 加载的，
 *   相对路径会指向手机自己，所以必须写死后端地址
 *
 * 地址通过 window.PORTFOLIO_API 注入（index.html 里插一段 script），
 * frontend/app.js 会优先读它。这样同一份前端代码两处都能用。
 */

import { cp, readFile, writeFile, rm, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const frontend = join(here, "..", "frontend");
const www = join(here, "www");

// 后端地址：改这里 → 重新 npm run build:apk
// Tailscale（默认）：手机在任何网络下都能连，地址固定不变
// 局域网（备用）：http://<电脑内网IP>:8000，只在同一 WiFi 下有效
// 可用环境变量覆盖：$env:PORTFOLIO_API="http://x.x.x.x:8000"; npm run build:apk
const API_BASE = process.env.PORTFOLIO_API || "http://100.126.133.63:8000";

if (!existsSync(frontend)) {
  console.error("找不到 frontend/ 目录");
  process.exit(1);
}

await rm(www, { recursive: true, force: true });
await mkdir(www, { recursive: true });
await cp(frontend, www, { recursive: true });

// 在 app.js 之前插入后端地址
const indexPath = join(www, "index.html");
const html = await readFile(indexPath, "utf8");
const inject = `<script>window.PORTFOLIO_API = ${JSON.stringify(API_BASE)};</script>\n  <script src="app.js"></script>`;

if (!html.includes('<script src="app.js"></script>')) {
  console.error('index.html 里没找到 <script src="app.js"></script>，注入失败');
  process.exit(1);
}

await writeFile(indexPath, html.replace('<script src="app.js"></script>', inject), "utf8");
console.log(`www/ 已生成，后端地址 = ${API_BASE}`);
