/* ============================================================
   PortfolioHub 前端逻辑
   安全约定：用户数据一律走 textContent / dataset / addEventListener，
   绝不拼接 HTML 字符串，绝不使用 innerHTML 注入数据。
   ============================================================ */

// 同源：页面从哪来，请求就发到哪（手机浏览器访问时靠这个连到后端）。
// APK 里页面是从本地加载的，同源会指向手机自己，所以 APK 构建时
// 会注入 window.PORTFOLIO_API 写明电脑地址（见 mobile/sync-web.mjs）。
const API = window.PORTFOLIO_API ?? "";

const TYPE_LABEL = {
  video: "视频",
  image: "图片",
  text: "文本",
  code: "代码",
  project: "项目",
};

const state = {
  works: [],
  filter: "all",
  editingId: null,
  files: [], // 新增时待上传的文件（File 对象）
  cover: null, // 视频封面（单个 File）
};

const $ = (id) => document.getElementById(id);

const listEl = $("work-list");
const emptyEl = $("work-empty");
const filtersEl = $("filters");
const messagesEl = $("messages");
const modal = $("work-modal");

const tplWork = $("tpl-work");
const tplMessage = $("tpl-message");

/* ---------- 统一请求封装 ---------- */
async function api(path, options = {}) {
  // 上传文件时不能手动设 Content-Type：浏览器要自己拼 multipart 的 boundary
  const isForm = options.body instanceof FormData;
  const headers = isForm ? options.headers : { "Content-Type": "application/json", ...options.headers };

  const res = await fetch(API + path, {
    cache: "no-store", // 禁用缓存：否则新增后重新拉取可能拿到旧响应
    ...options,
    headers,
  });

  if (res.status === 204) return null;

  const data = await res.json().catch(() => null);

  if (!res.ok) {
    // FastAPI 的校验错误是数组，其余是字符串
    let msg = `请求失败（${res.status}）`;
    if (typeof data?.detail === "string") msg = data.detail;
    else if (Array.isArray(data?.detail)) {
      msg = data.detail.map((d) => d.msg).join("；");
    }
    throw new Error(msg);
  }
  return data;
}

/* ---------- 轻提示 ---------- */
function toast(text) {
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = text; // 安全：纯文本
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3200);
}

/* ---------- 渲染作品列表 ---------- */
function workCard(work) {
  const node = tplWork.content.firstElementChild.cloneNode(true);

  const badge = node.querySelector(".badge");
  badge.dataset.type = work.type; // 属性赋值，不解析 HTML
  badge.textContent = TYPE_LABEL[work.type] ?? work.type;

  node.querySelector(".work-card__date").textContent = (work.created_at ?? "").slice(0, 10);
  node.querySelector(".work-card__title").textContent = work.title;
  node.querySelector(".work-card__excerpt").textContent = work.content || "（无内容）";

  const tagList = node.querySelector(".work-card__tags");
  for (const name of work.tags ?? []) {
    const li = document.createElement("li");
    li.textContent = name;
    tagList.appendChild(li);
  }

  node.querySelector(".work-card__edit").addEventListener("click", () => openModal(work));
  node.querySelector(".work-card__del").addEventListener("click", () => removeWork(work));

  return node;
}

function renderWorks() {
  const items =
    state.filter === "all" ? state.works : state.works.filter((w) => w.type === state.filter);

  listEl.replaceChildren(...items.map(workCard));
  emptyEl.hidden = items.length > 0;
  emptyEl.textContent =
    state.works.length === 0 ? "还没有作品，点右上角「新增」开始。" : "该类型下暂无作品。";
}

async function loadWorks() {
  try {
    state.works = await api("/works?limit=100");
    renderWorks();
  } catch (err) {
    toast(err.message);
  }
}

/* ---------- 筛选 ---------- */
function syncChips() {
  for (const chip of filtersEl.querySelectorAll(".chip")) {
    chip.classList.toggle("chip--active", chip.dataset.type === state.filter);
  }
}

function setFilter(type) {
  state.filter = type;
  syncChips();
  renderWorks();
}

filtersEl.addEventListener("click", (event) => {
  const chip = event.target.closest(".chip");
  if (!chip) return;
  setFilter(chip.dataset.type);
});

/* ---------- 新增 / 编辑弹窗 ---------- */

// 按扩展名猜类型，省得每拖一个文件都手动选下拉框
const IMAGE_EXT = new Set(["png", "jpg", "jpeg", "webp", "gif", "bmp", "heic"]);
const VIDEO_EXT = new Set(["mp4", "mov", "mkv", "avi", "webm"]);
const CODE_EXT = new Set([
  "py", "js", "ts", "tsx", "jsx", "java", "cpp", "c", "h", "go",
  "rs", "rb", "php", "css", "scss", "sh", "ps1", "sql", "html",
]);

function extOf(name) {
  const dot = name.lastIndexOf(".");
  return dot < 0 ? "" : name.slice(dot + 1).toLowerCase();
}

function stemOf(name) {
  const dot = name.lastIndexOf(".");
  return dot <= 0 ? name : name.slice(0, dot);
}

function inferType(name) {
  const ext = extOf(name);
  if (IMAGE_EXT.has(ext)) return "image";
  if (VIDEO_EXT.has(ext)) return "video";
  if (CODE_EXT.has(ext)) return "code";
  return "text";
}

function fmtSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

// 列表项一律用 textContent 构造（文件名来自用户，不能当 HTML）
function fileItem(file, onRemove) {
  const li = document.createElement("li");
  li.className = "filelist__item";

  const name = document.createElement("span");
  name.className = "filelist__name";
  name.textContent = file.name;
  name.title = file.name;

  const size = document.createElement("span");
  size.className = "filelist__size";
  size.textContent = fmtSize(file.size);

  const del = document.createElement("button");
  del.type = "button";
  del.className = "filelist__del";
  del.textContent = "×";
  del.title = "移除";
  del.addEventListener("click", onRemove);

  li.append(name, size, del);
  return li;
}

function renderFiles() {
  $("filelist").replaceChildren(
    ...state.files.map((file, index) =>
      fileItem(file, () => {
        state.files.splice(index, 1);
        if (state.files.length === 0) {
          state.titleAuto = false;
        }
        renderFiles();
      }),
    ),
  );

  $("coverlist").replaceChildren(
    ...(state.cover
      ? [
          fileItem(state.cover, () => {
            state.cover = null;
            renderFiles();
          }),
        ]
      : []),
  );
}

function addFiles(incoming) {
  const before = state.files.length;

  for (const file of incoming) {
    if (!file) continue;
    // 同名同大小视为重复，拖两遍不会加两条
    const dup = state.files.some((x) => x.name === file.name && x.size === file.size);
    if (!dup) state.files.push(file);
  }

  if (state.files.length === before) return;

  // 刚好一个文件：类型和标题跟着它走（标题留空才自动填，用户改过就不再覆盖）
  // 多个文件：标题清空，后端按各自文件名取，避免变成"a·b"这种串味标题
  if (state.files.length === 1) {
    $("work-type").value = inferType(state.files[0].name);
    if (!$("work-title").value.trim()) {
      $("work-title").value = stemOf(state.files[0].name);
      state.titleAuto = true;
    }
  } else if (state.titleAuto) {
    $("work-title").value = "";
    state.titleAuto = false;
  }

  syncIngestFields();
  renderFiles();
}

function syncIngestFields() {
  // 只有"新增"才显示文件区；封面只在视频类型下出现
  const creating = !state.editingId;
  const isVideo = $("work-type").value === "video";
  const hasFiles = state.files.length > 0;
  $("field-files").hidden = !creating;
  $("field-cover").hidden = !creating || !isVideo;

  // 有文件时正文由解析结果生成，这个输入框只起"备注"作用（备注会拼在正文最前面）
  $("content-label").textContent = hasFiles || isVideo ? "备注（可选）" : "内容";
}

function openModal(work = null) {
  state.editingId = work ? work.id : null;
  state.files = [];
  state.cover = null;
  state.titleAuto = false;
  $("modal-title").textContent = work ? "编辑作品" : "新增作品";
  $("work-id").value = work ? String(work.id) : "";
  $("work-title").value = work ? work.title : "";
  $("work-type").value = work ? work.type : "text";
  $("work-content").value = work ? work.content : "";
  $("work-tags").value = work ? (work.tags ?? []).join(", ") : "";
  $("work-file").value = "";
  $("work-cover").value = "";
  renderFiles();
  syncIngestFields();

  modal.showModal();
  $("work-title").focus();
}

/* ---------- 拖拽 / 点选文件 ---------- */
const dropzone = $("dropzone");
const fileInput = $("work-file");
const coverZone = $("dropzone-cover");
const coverInput = $("work-cover");

function markZone(el, active) {
  el.classList.toggle("dropzone--active", active);
}

dropzone.addEventListener("click", () => fileInput.click());
dropzone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    fileInput.click();
  }
});
fileInput.addEventListener("change", () => {
  addFiles([...fileInput.files]);
  fileInput.value = ""; // 清空后才能再次选同一个文件
});

coverZone.addEventListener("click", () => coverInput.click());
coverInput.addEventListener("change", () => {
  state.cover = coverInput.files[0] ?? null;
  coverInput.value = "";
  renderFiles();
});

for (const zone of [dropzone, coverZone]) {
  zone.addEventListener("dragenter", (event) => {
    event.preventDefault();
    markZone(zone, true);
  });
  zone.addEventListener("dragover", (event) => {
    event.preventDefault();
    markZone(zone, true);
  });
  zone.addEventListener("dragleave", () => markZone(zone, false));
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    event.stopPropagation(); // 别再触发 window 上的兜底 drop
    markZone(zone, false);
    const dropped = [...(event.dataTransfer?.files ?? [])];

    if (zone === coverZone) {
      state.cover = dropped.find((f) => f.type.startsWith("image/")) ?? dropped[0] ?? state.cover;
      renderFiles();
    } else {
      addFiles(dropped);
    }
  });
}

// 拖到窗口任意位置都能加：弹窗没开就先打开
window.addEventListener("dragover", (event) => {
  if (event.dataTransfer?.types?.includes("Files")) event.preventDefault();
});
window.addEventListener("drop", (event) => {
  if (!event.dataTransfer?.types?.includes("Files")) return;
  event.preventDefault();
  if (!modal.open) openModal();
  addFiles([...event.dataTransfer.files]);
});

$("work-type").addEventListener("change", syncIngestFields);
$("btn-new").addEventListener("click", () => openModal());
$("btn-cancel").addEventListener("click", () => modal.close());

/* 逐个上传：一次拖 5 个文件 → 5 条作品，每条各自解析 */
async function uploadFiles({ title, type, tags, notes }) {
  const total = state.files.length;
  const saveBtn = $("btn-save");
  let done = 0;

  for (const file of state.files) {
    const form = new FormData();
    form.append("file", file);
    // 单文件用表单里的标题/类型；多文件时每条按自己的文件名和扩展名来
    form.append(
      "title",
      total === 1 ? title : title ? `${title}·${stemOf(file.name)}` : stemOf(file.name),
    );
    form.append("type", total === 1 ? type : inferType(file.name));
    form.append("tags", tags.join(","));
    form.append("notes", notes);
    if (total === 1 && state.cover) form.append("cover", state.cover);

    saveBtn.textContent = `处理中 ${done + 1}/${total}…`;
    try {
      await api("/works/upload", { method: "POST", body: form });
    } catch (err) {
      throw new Error(
        done > 0 ? `已上传 ${done} 条，第 ${done + 1} 条失败：${err.message}` : err.message,
      );
    }
    done += 1;
  }
}

$("work-form").addEventListener("submit", async (event) => {
  event.preventDefault();

  const payload = {
    title: $("work-title").value.trim(),
    type: $("work-type").value,
    content: $("work-content").value,
    tags: $("work-tags")
      .value.split(/[,，]/) // 中文逗号也要认：输入法下用户不会切英文
      .map((s) => s.trim())
      .filter(Boolean),
  };

  // 有文件时标题可以留空（后端按文件名兜底），纯手写作品则必须有标题
  if (!state.editingId && state.files.length === 0 && !payload.title) {
    toast("标题不能为空（或拖一个文件进来，标题会自动取文件名）");
    return;
  }

  const saveBtn = $("btn-save");
  saveBtn.disabled = true;

  try {
    if (state.editingId) {
      await api(`/works/${state.editingId}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
    } else if (state.files.length) {
      await uploadFiles({
        title: payload.title,
        type: payload.type,
        tags: payload.tags,
        notes: payload.content,
      });
    } else {
      await api("/works", { method: "POST", body: JSON.stringify(payload) });
    }
    modal.close();

    // 当前筛选与作品类型不符时切回「全部」，否则用户会以为没保存成功
    if (state.filter !== "all" && state.filter !== payload.type) {
      state.filter = "all";
      syncChips();
    }
    await loadWorks();
  } catch (err) {
    toast(err.message);
  } finally {
    saveBtn.disabled = false;
    saveBtn.textContent = "保存";
  }
});

/* ---------- 删除 ---------- */
async function removeWork(work) {
  if (!confirm(`删除「${work.title}」？该操作不可撤销。`)) return;
  try {
    await api(`/works/${work.id}`, { method: "DELETE" });
    await loadWorks();
  } catch (err) {
    toast(err.message);
  }
}

/* ---------- 对话 ---------- */
function renderSources(node, sources) {
  const old = node.querySelector(".msg__sources");
  if (old) old.remove();
  if (!sources || sources.length === 0) return;

  // work_id → 标题；去重（同一作品多个片段只显示一次）
  const titles = sources.map(
    (id) => state.works.find((w) => w.id === id)?.title ?? `作品 #${id}`,
  );
  const line = document.createElement("div");
  line.className = "msg__sources";
  line.textContent = `引用：${[...new Set(titles)].join("、")}`;
  node.appendChild(line);
}

function appendMessage(role, text, sources = []) {
  const node = tplMessage.content.firstElementChild.cloneNode(true);
  node.classList.add(`msg--${role}`);
  node.querySelector(".msg__bubble").textContent = text; // 安全：纯文本
  messagesEl.appendChild(node);
  renderSources(node, sources);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return node;
}

/* 解析一个 SSE 帧：可能有多行 data，最后 JSON.parse */
function parseSseFrame(raw) {
  let event = "message";
  const dataLines = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return null;
  try {
    return { event, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    return null;
  }
}

/* 流式问答：fetch + ReadableStream 手动解析 SSE
   （EventSource 只支持 GET 且不能带 body，带问题正文的 POST 用不了） */
async function askStream(question) {
  const node = appendMessage("assistant", "…"); // 占位，等第一个字
  const bubble = node.querySelector(".msg__bubble");

  const startedAt = performance.now();
  let firstDeltaAt = 0;
  let sources = [];
  let buffer = "";

  try {
    const resp = await fetch(API + "/ask/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      body: JSON.stringify({ question }),
    });
    if (!resp.ok || !resp.body) throw new Error(`请求失败（${resp.status}）`);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let sep;
      while ((sep = buffer.indexOf("\n\n")) >= 0) {
        const frame = parseSseFrame(buffer.slice(0, sep));
        buffer = buffer.slice(sep + 2);
        if (!frame) continue;

        if (frame.event === "meta") {
          sources = frame.data.sources ?? [];
        } else if (frame.event === "delta") {
          if (firstDeltaAt === 0) {
            firstDeltaAt = performance.now();
            bubble.textContent = ""; // 清掉占位符
          }
          bubble.textContent += frame.data.text ?? "";
          messagesEl.scrollTop = messagesEl.scrollHeight;
        } else if (frame.event === "error") {
          throw new Error(frame.data.message || "生成失败");
        }
      }
    }

    renderSources(node, sources);
    console.debug(
      `[stream] 首字 ${Math.round(firstDeltaAt - startedAt)}ms · ` +
        `总时长 ${Math.round(performance.now() - startedAt)}ms`,
    );
  } catch (err) {
    bubble.textContent = `⚠ ${err.message}`;
  }
}

$("composer").addEventListener("submit", async (event) => {
  event.preventDefault();

  const question = $("question").value.trim();
  if (!question) return;

  const sendBtn = event.target.querySelector('button[type="submit"]');
  if (sendBtn.disabled) return; // 请求进行中，忽略重复提交

  $("question").value = "";
  sendBtn.disabled = true;
  appendMessage("user", question);

  try {
    await askStream(question);
  } finally {
    sendBtn.disabled = false;
  }
});

/* ---------- 设置：填密钥 ---------- */
// 打包版没有 .env 文件，密钥由用户在这里填，后端写进用户目录的 设置.json
const settingsModal = $("settings-modal");

const KEY_FIELDS = {
  deepseek_api_key: "key-deepseek",
  embedding_api_key: "key-embedding",
  gpt_api_key: "key-gpt",
};

function collectKeys() {
  const payload = {};
  for (const [key, id] of Object.entries(KEY_FIELDS)) {
    const value = $(id).value.trim();
    if (value) payload[key] = value; // 留空 = 不改，不提交这一项
  }
  return payload;
}

function applySettings(s) {
  for (const [key, id] of Object.entries(KEY_FIELDS)) {
    const input = $(id);
    const masked = s.keys?.[key] || "";
    input.value = "";
    input.placeholder = masked ? `已保存 ${masked}，留空表示不改` : "sk-...";
  }
  $("settings-storage").textContent = `数据存在：${s.storage}`;
  // 只有打包版才有「退出程序」：关窗口不会结束进程，得给它一个显式的出口
  $("btn-quit").hidden = !s.portable;
}

function openSettings(firstRun = false) {
  $("probe-results").hidden = true;
  $("probe-results").replaceChildren();
  $("settings-note").textContent = firstRun
    ? "第一次用，先填密钥：对话密钥、检索密钥填上，作品才能被搜到、AI 才能回答问题。只管理作品可以先跳过。"
    : "填一次就行。密钥只保存在这台电脑上，不会上传到任何地方。";
  settingsModal.showModal();
}

function probeItem(text, ok) {
  const li = document.createElement("li");
  li.className = `probe__item probe__item--${ok ? "ok" : "fail"}`;
  li.textContent = `${ok ? "✓" : "✗"} ${text}`; // 安全：纯文本
  return li;
}

async function testSettings() {
  const list = $("probe-results");
  list.hidden = false;
  list.replaceChildren(probeItem("正在测试…", true));
  try {
    const res = await api("/settings/test", {
      method: "POST",
      body: JSON.stringify(collectKeys()),
    });
    list.replaceChildren(
      ...res.results.map((r) => probeItem(`${r.service}：${r.message}`, r.ok)),
    );
  } catch (err) {
    list.replaceChildren(probeItem(err.message, false));
  }
}

async function loadSettings() {
  try {
    const s = await api("/settings");
    state.settings = s;
    applySettings(s);
    // 一个密钥都没有 = 全新安装，直接把设置弹出来，省得用户找不到入口
    const missing = Object.values(s.configured ?? {}).filter((ok) => !ok).length;
    if (missing === Object.keys(KEY_FIELDS).length) openSettings(true);
  } catch (err) {
    console.debug(`[settings] ${err.message}`);
  }
}

$("btn-settings").addEventListener("click", () => openSettings(false));
$("btn-settings-close").addEventListener("click", () => settingsModal.close());
$("btn-settings-test").addEventListener("click", testSettings);
$("btn-quit").addEventListener("click", async () => {
  if (!confirm("退出作品集？退出后需要重新双击图标才能再用。")) return;
  try {
    await api("/quit", { method: "POST" });
  } catch {
    // 服务端退出时连接会被切断，这里报错是正常的
  }
  document.body.replaceChildren();
  const tip = document.createElement("p");
  tip.className = "empty";
  tip.textContent = "已退出。可以关掉这个窗口了。";
  document.body.appendChild(tip);
});

$("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();

  const payload = collectKeys();
  if (!Object.keys(payload).length) {
    toast("没有要保存的内容：三个都留空表示不改");
    return;
  }

  const btn = $("btn-settings-save");
  btn.disabled = true;
  try {
    const s = await api("/settings", { method: "PUT", body: JSON.stringify(payload) });
    state.settings = s;
    applySettings(s);
    const extra = s.indexed_works ? `，已为 ${s.indexed_works} 条作品建好索引` : "";
    toast(`保存成功${extra}`);
    settingsModal.close();
    await loadWorks();
  } catch (err) {
    toast(err.message);
  } finally {
    btn.disabled = false;
  }
});

/* ---------- 启动 ---------- */
loadWorks();
loadSettings();
