/* ============================================================
   PortfolioHub 前端逻辑
   安全约定：用户数据一律走 textContent / dataset / addEventListener，
   绝不拼接 HTML 字符串，绝不使用 innerHTML 注入数据。
   ============================================================ */

const API = "http://127.0.0.1:8000"; // 部署后改为同源相对路径 ""

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
const tplTyping = $("tpl-typing");

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
function syncImageField() {
  // 只有"新增 + 类型=图片"才显示路径输入；编辑时不重新生成描述
  const show = !state.editingId && $("work-type").value === "image";
  $("field-image").hidden = !show;
}

function openModal(work = null) {
  state.editingId = work ? work.id : null;
  $("modal-title").textContent = work ? "编辑作品" : "新增作品";
  $("work-id").value = work ? String(work.id) : "";
  $("work-title").value = work ? work.title : "";
  $("work-type").value = work ? work.type : "text";
  $("work-content").value = work ? work.content : "";
  $("work-tags").value = work ? (work.tags ?? []).join(", ") : "";
  $("work-image").value = "";
  syncImageField();

  modal.showModal();
  $("work-title").focus();
}

$("work-type").addEventListener("change", syncImageField);
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
  const imagePath = $("work-image").value.trim();

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
    } else if (payload.type === "image" && imagePath) {
      // 走图片录入：后端生成描述后再建作品，可能要几秒
      saveBtn.textContent = "生成描述中…";
      await api("/works/image", {
        method: "POST",
        body: JSON.stringify({
          image_path: imagePath,
          title: payload.title,
          tags: payload.tags,
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
function appendMessage(role, text, sources = []) {
  const node = tplMessage.content.firstElementChild.cloneNode(true);
  node.classList.add(`msg--${role}`);
  node.querySelector(".msg__bubble").textContent = text; // 安全：纯文本

  // 引用来源：把 work_id 映射成标题显示，让"答案从哪来"在界面上可见
  if (sources.length) {
    const titles = sources.map(
      (id) => state.works.find((w) => w.id === id)?.title ?? `作品 #${id}`,
    );
    const line = document.createElement("div");
    line.className = "msg__sources";
    line.textContent = `引用：${[...new Set(titles)].join("、")}`;
    node.appendChild(line);
  }

  messagesEl.appendChild(node);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return node;
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

  const typing = tplTyping.content.firstElementChild.cloneNode(true);
  messagesEl.appendChild(typing);
  messagesEl.scrollTop = messagesEl.scrollHeight;

  try {
    const data = await api("/ask", {
      method: "POST",
      body: JSON.stringify({ question }),
    });
    typing.remove();
    appendMessage("assistant", data.answer, data.sources ?? []);
  } catch (err) {
    typing.remove();
    appendMessage("assistant", `⚠ ${err.message}`);
  } finally {
    sendBtn.disabled = false;
  }
});

/* ---------- 启动 ---------- */
loadWorks();
