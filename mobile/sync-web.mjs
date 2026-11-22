/**
 * 生成 Capacitor 的 www/：一份「连不上服务器」的兜底页 + 页面副本（备用）。
 *
 * 现在的模式是「远程加载」：capacitor.config.json 里配了 server.url，
 * App 启动时直接从后端拉页面。好处是改前端**不用重新装 APK**，
 * 电脑上 rebuild 一次容器，手机刷新就是这个新界面。
 *
 * www/ 里仍放一份 index.html 副本：万一以后要做离线版，改掉 server.url 就能用。
 */

import { cp, writeFile, rm, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const frontend = join(here, "..", "frontend");
const www = join(here, "www");

if (!existsSync(frontend)) {
  console.error("找不到 frontend/ 目录");
  process.exit(1);
}

await rm(www, { recursive: true, force: true });
await mkdir(www, { recursive: true });
await cp(frontend, www, { recursive: true });

// 兜底页：server.url 拉不到内容时 Capacitor 会显示这个文件（见 server.errorPath）
const errorPage = `<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>连不上作品集</title>
    <style>
      body {
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        background: #0f1115;
        color: #e6e8ee;
        font: 15px/1.7 system-ui, "Microsoft YaHei", sans-serif;
        padding: 24px;
      }
      .card { max-width: 420px; }
      h1 { font-size: 18px; margin: 0 0 12px; }
      p { color: #9aa3b2; margin: 0 0 8px; }
      ul { color: #9aa3b2; padding-left: 20px; margin: 0 0 20px; }
      code { color: #7c9cff; }
      button {
        padding: 10px 18px;
        border: 0;
        border-radius: 10px;
        background: #4f7cff;
        color: #fff;
        font-size: 14px;
      }
    </style>
  </head>
  <body>
    <div class="card">
      <h1>连不上电脑上的作品集</h1>
      <p>这个 App 的界面和接口都在你自己的电脑上，按顺序检查：</p>
      <ul>
        <li>手机上的 <code>Tailscale</code> 开着且已登录</li>
        <li>电脑开着，Docker Desktop 在跑（三个容器 healthy）</li>
        <li>电脑上的 <code>Tailscale</code> 也开着</li>
      </ul>
      <p>改完点下面的按钮重试。</p>
      <button onclick="location.reload()">重试</button>
    </div>
  </body>
</html>
`;

await writeFile(join(www, "error.html"), errorPage, "utf8");
console.log("www/ 已生成（远程加载模式，兜底页 error.html）");
