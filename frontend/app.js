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
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    cache: "no-store", // 禁用缓存：否则新增后重新拉取可能拿到旧响应
    ...options,
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
function syncIngestFields() {
  // 只有"新增"才显示文件路径；封面只在视频类型下出现
  const creating = !state.editingId;
  const isVideo = $("work-type").value === "video";
  $("field-file").hidden = !creating;
  $("field-cover").hidden = !creating || !isVideo;
}

function openModal(work = null) {
  state.editingId = work ? work.id : null;
  $("modal-title").textContent = work ? "编辑作品" : "新增作品";
  $("work-id").value = work ? String(work.id) : "";
  $("work-title").value = work ? work.title : "";
  $("work-type").value = work ? work.type : "text";
  $("work-content").value = work ? work.content : "";
  $("work-tags").value = work ? (work.tags ?? []).join(", ") : "";
  $("work-file").value = "";
  $("work-cover").value = "";
  syncIngestFields();

  modal.showModal();
  $("work-title").focus();
}

$("work-type").addEventListener("change", syncIngestFields);
$("btn-new").addEventListener("click", () => openModal());
$("btn-cancel").addEventListener("click", () => modal.close());

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
  const filePath = $("work-file").value.trim();
  const coverPath = $("work-cover").value.trim();

  if (!payload.title) {
    toast("标题不能为空");
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
    } else if (filePath) {
      // 统一录入：后端按类型解析（图片生成描述 / PDF 读正文 / 视频只存元数据）
      saveBtn.textContent = "解析中…";
      await api("/works/ingest", {
        method: "POST",
        body: JSON.stringify({
          path: filePath,
          title: payload.title,
          type: payload.type,
          tags: payload.tags,
          notes: payload.content,
          cover_path: coverPath || null,
        }),
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

/* ---------- 启动 ---------- */
loadWorks();
