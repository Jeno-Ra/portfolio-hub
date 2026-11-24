/*
  作品集 前端逻辑

  数据模型是两层：
  - 项目（project）：容器，只有标题 / 简介 / 封面 / 标签，本身没有类型
  - 素材（asset）：真正装内容的一层，类型由扩展名决定（图片 / 视频 / 音频 / 文本 / 代码 / 文档）
  同一件素材可以挂在多个项目下，所以删项目不删素材。

  安全约定：所有来自后端的数据都用 textContent / dataset 写进 DOM，
  全程不拼 HTML 字符串（innerHTML 会把素材标题里的尖括号当标签执行）。
*/

const $ = (id) => document.getElementById(id);

/* ============ 常量 ============ */

const KIND_LABELS = {
  image: "图片",
  video: "视频",
  audio: "音频",
  text: "文本",
  code: "代码",
  document: "文档",
};

const KEY_FIELDS = [
  ["key-deepseek", "deepseek_api_key"],
  ["key-embedding", "embedding_api_key"],
  ["key-gpt", "gpt_api_key"],
];

/* ============ 状态 ============ */

const state = {
  projects: [],
  project: null,
  selected: new Set(), // 当前打开的项目（含 assets）
  kind: "all",
  editing: null, // 正在编辑的素材；null 表示「添加」模式
  replaceFile: null, // 编辑时要替换上去的新文件
  files: [],
  cover: null,
  titleAuto: true,
  settings: null,
  groups: [],
  currentGroup: "all",
  expandedGroups: new Set(),
};

// 每次切换项目或刷新分组都使旧请求失效。
let projectEpoch = 0;
let groupsEpoch = 0;

/* ============ 请求封装 ============ */

/**
 * 统一解包后端信封 {code, message, data}：
 * code 非 0 一律抛错，调用方只处理成功路径和 catch。
 */
async function api(path, options = {}) {
  const isForm = options.body instanceof FormData;
  let res;
  try {
    res = await fetch(path, {
      ...options,
      headers: isForm
        ? options.headers
        : { "Content-Type": "application/json", ...(options.headers || {}) },
    });
  } catch {
    // 连不上：打包版最常见的情况是后台已经退出了，窗口还留着
    throw new Error(
      "连不上后端服务。打包版可能已经退出，重新双击「作品集」；用 Docker 跑的话先执行 docker compose up -d",
    );
  }

  let payload = null;
  try {
    payload = await res.json();
  } catch {
    // 后端挂了 / 返回的不是 JSON，交给下面统一报错
  }

  if (!payload) {
    throw new Error(`服务没有返回数据（HTTP ${res.status}），可能没起来`);
  }
  if (payload.code !== 0) {
    throw new Error(payload.message || `请求失败（HTTP ${res.status}）`);
  }
  return payload.data;
}

/* ============ 通用小工具 ============ */

function toast(text, isError = false) {
  let node = document.querySelector(".toast");
  if (!node) {
    node = document.createElement("div");
    node.className = "toast";
    document.body.append(node);
  }
  node.textContent = text;
  node.classList.toggle("toast--error", isError);
  node.classList.add("toast--show");
  clearTimeout(node._timer);
  node._timer = setTimeout(() => node.classList.remove("toast--show"), 2600);
}

function fmtDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function fmtSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function stemOf(name) {
  const dot = name.lastIndexOf(".");
  return dot <= 0 ? name : name.slice(0, dot);
}

function excerpt(text, size = 120) {
  const flat = (text || "").replace(/\s+/g, " ").trim();
  return flat.length > size ? `${flat.slice(0, size)}…` : flat;
}

function kindLabel(kind) {
  return KIND_LABELS[kind] || "未分类";
}

/* ============ 项目列表 ============ */

function projectCard(project) {
  const node = $("tpl-project").content.firstElementChild.cloneNode(true);
  node.dataset.id = String(project.id);

  const img = node.querySelector("[data-cover-slot] img");
  const slot = node.querySelector("[data-cover-slot]");
  if (project.cover_url) {
    img.src = project.cover_url;
    img.hidden = false;
    slot.classList.add("project-card__cover--image");
  }

  node.querySelector(".project-card__title").textContent = project.title;
  node.querySelector(".project-card__date").textContent = fmtDate(
    project.updated_at || project.created_at,
  );
  node.querySelector(".project-card__summary").textContent =
    project.summary || "（还没有简介）";

  const kinds = node.querySelector(".project-card__kinds");
  const entries = Object.entries(project.kind_counts || {}).filter(([k]) => k);
  for (const [kind, count] of entries.sort((a, b) => b[1] - a[1])) {
    const li = document.createElement("li");
    li.className = "project-card__kind";
    li.dataset.kind = kind;
    li.textContent = `${kindLabel(kind)} ${count}`;
    kinds.append(li);
  }
  if (!entries.length) kinds.hidden = true;

  node.querySelector(".project-card__count").textContent = `${project.asset_count} 件素材`;

  node.addEventListener("dblclick", (event) => {
    if (event.target.closest("button")) return;
    openProject(project.id).catch((err) => toast(err.message, true));
  });
  node.querySelector(".project-card__open").addEventListener("click", () => {
    openProject(project.id).catch((err) => toast(err.message, true));
  });
  node.querySelector(".project-card__del").addEventListener("click", () => {
    removeProject(project).catch((err) => toast(err.message, true));
  });

  return node;
}

function renderProjects() {
  const list = $("project-list");
  list.replaceChildren(...state.projects.map(projectCard));
  $("project-empty").hidden = state.projects.length > 0;
}

async function loadProjects() {
  state.projects = await api("/projects");
  renderProjects();
}

async function removeProject(project) {
  const okToDelete = window.confirm(
    `删除项目「${project.title}」？\n\n项目里的素材不会被删，它们还留在库里（可能挂在别的项目下）。`,
  );
  if (!okToDelete) return;

  await api(`/projects/${project.id}`, { method: "DELETE" });
  toast("项目已删除");
  if (state.project && state.project.id === project.id) showList();
  await loadProjects();
}

/* ============ 项目详情 ============ */

function showDetail() {
  $("project-view").hidden = true;
  $("detail-view").hidden = false;
}

function showList() {
  projectEpoch++;
  groupsEpoch++;
  state.project = null;
  $("detail-view").hidden = true;
  $("project-view").hidden = false;
}

function renderTags(container, tags) {
  container.replaceChildren();
  for (const name of tags || []) {
    const li = document.createElement("li");
    li.className = "tag";
    li.textContent = name;
    container.append(li);
  }
  container.hidden = !(tags || []).length;
}

async function previewAsset(asset) {
  const modal = $("preview-modal");
  const container = $("preview-content");
  const status = $("preview-status");
  $("preview-title").textContent = asset.title;
  container.replaceChildren();
  status.textContent = "";
  modal.showModal();
  if (!asset.source_path) {
    const pre = document.createElement("pre");
    pre.textContent = asset.content || "（没有正文）";
    status.textContent = "手写素材正文";
    container.append(pre);
    return;
  }
  const url = `/assets/${asset.id}/preview`;
  const ext = asset.source_path.split(".").pop().toLowerCase();
  if (asset.type === "image" || asset.type === "video" || asset.type === "audio") {
    const node = document.createElement({ image: "img", video: "video", audio: "audio" }[asset.type]);
    if (asset.type === "image") node.alt = asset.title;
    else { node.controls = true; node.preload = "metadata"; }
    node.addEventListener("error", () => { status.textContent = "无法预览此文件：文件可能不存在，或当前设备不支持该格式／编码。"; });
    node.src = url;
    container.append(node);
  } else if (ext === "pdf") {
    const frame = document.createElement("iframe");
    frame.title = "PDF 预览";
    frame.src = url;
    container.append(frame);
    status.textContent = "PDF 能否显示取决于设备和浏览器。";
  } else if (asset.type === "text" || asset.type === "code") {
    try {
      const limit = 1024 * 1024;
      const response = await fetch(url, { headers: { Range: `bytes=0-${limit - 1}` } });
      if (!response.ok) throw new Error("原文件不可用");
      const blob = await response.blob();
      const pre = document.createElement("pre");
      pre.textContent = await blob.slice(0, limit).text();
      if (modal.open) container.append(pre);
      status.textContent = response.status === 206 ? "原文件内容（最多显示前 1 MB）" : "原文件内容";
    } catch (_) { status.textContent = "原文件无法读取，可能已移动或不在上传目录中。"; }
  } else {
    status.textContent = "此格式暂不支持原样预览。下面显示已提取的内容，排版可能不同于原文件。";
    const pre = document.createElement("pre");
    pre.textContent = asset.content || "（暂无提取内容）";
    container.append(pre);
  }
}

function assetCard(asset) {
  const node = $("tpl-asset").content.firstElementChild.cloneNode(true);
  // 多选/拖拽移动需要能在卡片上识别选中状态
  node.draggable = true;
  node.addEventListener("dragstart", (event) => {
    if (!state.selected.has(asset.id)) { state.selected.clear(); state.selected.add(asset.id); updateBatchBar(); }
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/asset-ids", JSON.stringify(selectedAssetIds()));
  });
  node.dataset.id = String(asset.id);
  const check = document.createElement("input"); check.type="checkbox"; check.className="asset-select"; check.checked=state.selected.has(asset.id); check.addEventListener("click", e=>{ e.stopPropagation(); checkAsset(asset.id, check.checked); });
  node.querySelector(".asset-card__head").prepend(check);

  const badge = node.querySelector(".badge");
  badge.textContent = kindLabel(asset.type);
  badge.dataset.kind = asset.type || "unknown";

  node.querySelector(".asset-card__title").textContent = asset.title;
  node.querySelector(".asset-card__excerpt").textContent =
    excerpt(asset.content) || "（还没有正文）";

  renderTags(node.querySelector(".asset-card__tags"), asset.tags);
  node.querySelector(".asset-card__date").textContent = fmtDate(asset.created_at);

  node.querySelector(".asset-card__preview").addEventListener("click", () => {
    previewAsset(asset).catch((err) => toast(err.message, true));
  });
  node.querySelector(".asset-card__edit").addEventListener("click", () => {
    openAssetModal(asset);
  });

  node.querySelector(".asset-card__del").addEventListener("click", () => {
    removeAsset(asset).catch((err) => toast(err.message, true));
  });

  return node;
}

function selectedAssetIds() { return [...state.selected]; }
function checkAsset(id, checked) { if (checked) state.selected.add(id); else state.selected.delete(id); renderAssets(); }
function updateBatchBar() {
  const bar = $("asset-batchbar"); if (!bar) return;
  bar.hidden = false;
  $("btn-batch-move").disabled = !state.selected.size;
  $("btn-batch-clear").disabled = !state.selected.size;
  $("asset-selected-count").textContent = `已选 ${state.selected.size} 件`;
  $("asset-select-all").checked = state.selected.size > 0 && visibleAssets().every(a => state.selected.has(a.id));
}
function visibleAssets() {
  const all = state.project?.assets || [];
  return all.filter(asset => {
    const groupId = asset.project_group_id ?? asset.group_id ?? null;
    const matchesGroup = state.currentGroup === "all"
      || (state.currentGroup === null ? groupId === null : isInGroupSubtree(groupId, state.currentGroup));
    return matchesGroup && (state.kind === "all" || asset.type === state.kind);
  });
}
function clearSelection() { state.selected.clear(); updateBatchBar(); renderAssets(); }
async function showMoveDialog() {
  if (!state.selected.size || !state.project) return;
  const pid = state.project.id, epoch = projectEpoch;
  const ids = selectedAssetIds();
  const dialog = document.createElement("dialog"); dialog.className = "modal move-modal";
  const form = document.createElement("form"); form.method = "dialog"; form.className = "modal__body move-modal__body";
  const title = document.createElement("h2"); title.className = "modal__title"; title.textContent = "移动已选 " + state.selected.size + " 件素材";
  const picker = document.createElement("div");
  picker.className = "move-picker";
  picker.setAttribute("role", "radiogroup");
  picker.setAttribute("aria-label", "目标分组");
  let target = null;
  const options = [{ id: null, name: "待归类", depth: 0 }, ...state.groups.map(g => ({ id: g.id, name: fullGroupName(g), depth: 0 }))];
  for (const option of options) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "move-picker__option";
    row.setAttribute("role", "radio");
    row.setAttribute("aria-checked", "false");
    row.dataset.groupId = option.id == null ? "" : String(option.id);
    const icon = document.createElement("span");
    icon.className = "move-picker__icon";
    icon.textContent = option.id == null ? "○" : "□";
    const label = document.createElement("span");
    label.className = "move-picker__label";
    label.textContent = option.name;
    const check = document.createElement("span");
    check.className = "move-picker__check";
    check.textContent = "✓";
    row.append(icon, label, check);
    row.addEventListener("click", () => {
      target = option.id;
      for (const item of picker.children) {
        item.classList.toggle("is-selected", item === row);
        item.setAttribute("aria-checked", item === row ? "true" : "false");
      }
      confirm.disabled = false;
    });
    picker.append(row);
  }
  const actions=document.createElement("div"); actions.className="modal__actions";
  const cancel=document.createElement("button"); cancel.type="button"; cancel.className="btn"; cancel.textContent="取消"; cancel.onclick=()=>dialog.close();
  const confirm=document.createElement("button"); confirm.className="btn btn--primary"; confirm.value="move"; confirm.textContent="移动"; confirm.disabled=true;
  actions.append(cancel,confirm); form.append(title,picker,actions); dialog.append(form); document.body.append(dialog); dialog.showModal();
  await new Promise(resolve=>dialog.addEventListener("close",resolve,{once:true}));
  const accepted=dialog.returnValue==="move"; dialog.remove();
  if(accepted && epoch===projectEpoch && state.project?.id===pid) await batchMove(target, ids);
}
async function batchMove(groupId, ids = selectedAssetIds()) {
  if (!ids.length || !state.project) return;
  const pid=state.project.id, epoch=projectEpoch;
  try {
    await api("/projects/" + pid + "/assets/group", { method: "PATCH", body: JSON.stringify({ asset_ids: ids, group_id: groupId }) });
    if(epoch!==projectEpoch || state.project?.id!==pid) return;
    state.selected.clear(); await refreshDetail(); toast("已移动 " + ids.length + " 件素材");
  } catch (err) { toast(err.message, true); }
}
function renderAssets() {
  const all = state.project?.assets || [];
  const validIds = new Set(all.map(asset => asset.id));
  for (const id of state.selected) if (!validIds.has(id)) state.selected.delete(id);
  const assets = visibleAssets();

  $("asset-list").replaceChildren(...assets.map(assetCard));
  $("asset-list").hidden = assets.length === 0;
  $("asset-empty").hidden = assets.length > 0;
  // 空状态分两种：项目本身空，还是这个筛选下空
  $("asset-empty").textContent =
    all.length === 0
      ? "这个项目还没有素材，点「+ 添加素材」拖几个文件进来。"
      : "当前分组或类型下没有素材，可以拖动素材到左侧分组整理。";
  updateBatchBar();
}

function renderDetail() {
  const project = state.project;
  const cover = $("detail-cover");
  if (project.cover_url) {
    cover.src = project.cover_url;
    cover.hidden = false;
  } else {
    cover.removeAttribute("src");
    cover.hidden = true;
  }

  $("detail-title").textContent = project.title;
  $("detail-summary").textContent = project.summary || "";
  $("detail-summary").hidden = !project.summary;
  renderTags($("detail-tags"), project.tags);

  const parts = [`${project.asset_count} 件素材`];
  for (const [kind, count] of Object.entries(project.kind_counts || {})) {
    if (kind) parts.push(`${kindLabel(kind)} ${count}`);
  }
  $("detail-count").textContent = parts.join(" · ");

  renderAssets();
}

async function openProject(id) {
  const epoch = ++projectEpoch;
  let project;
  try {
    project = await api(`/projects/${id}`);
  } catch (err) {
    if (epoch !== projectEpoch) return;
    toast(err.message, true);
    if (err.message.includes("不存在")) await loadProjects();
    return;
  }
  if (epoch !== projectEpoch) return;
  // 项目和分组一起加载成功后再切换，失败时保留原界面。
  let groups;
  try {
    groups = await fetchGroups(id);
  } catch (err) {
    if (epoch === projectEpoch) toast(err.message || "分组加载失败", true);
    return;
  }
  if (epoch !== projectEpoch) return;
  groupsEpoch++;
  state.project = project;
  state.groups = groups;
  state.kind = "all";
  state.currentGroup = "all";
  state.selected.clear();
  state.expandedGroups = new Set(groups.map(g=>g.id));
  for (const chip of $("kind-filters").children) {
    chip.classList.toggle("chip--active", chip.dataset.kind === "all");
  }
  showDetail();
  renderGroups();
  renderDetail();
}

async function fetchGroups(projectId) {
    const payload = await api(`/projects/${projectId}/groups`) || [];
    // 后端可能返回扁平列表，也可能仅返回根组并在 children 中嵌套；统一拍平成导航用列表。
    const flat = [];
    const seen = new Set();
    const visit = (group, parentId = null) => {
      if (!group || group.id == null || seen.has(group.id)) return;
      seen.add(group.id);
      flat.push({ ...group, parent_id: group.parent_id ?? parentId });
      for (const child of group.children || []) visit(child, group.id);
    };
    for (const group of payload) visit(group);
    return flat;
}

async function loadGroups() {
  if (!state.project) return false;
  const projectId = state.project.id;
  const epoch = projectEpoch;
  const request = ++groupsEpoch;
  try {
    const groups = await fetchGroups(projectId);
    if (epoch !== projectEpoch || request !== groupsEpoch || state.project?.id !== projectId) return false;
    state.groups = groups;
    return true;
  } catch (err) {
    if (epoch !== projectEpoch || request !== groupsEpoch || state.project?.id !== projectId) return false;
    throw err;
  }
}

function groupChildren(parentId) { return state.groups.filter(g => (g.parent_id ?? null) === (parentId ?? null)); }
function isDescendant(id, ancestorId) {
  let g = state.groups.find(x => x.id === id);
  while (g?.parent_id != null) { if (g.parent_id === ancestorId) return true; g = state.groups.find(x => x.id === g.parent_id); }
  return false;
}
function fullGroupName(g) { const names = [g.name]; let p = state.groups.find(x => x.id === g.parent_id); while (p) { names.unshift(p.name); p = state.groups.find(x => x.id === p.parent_id); } return names.join(" / "); }
function groupPath() {
  const path = []; let g = state.groups.find(x => x.id === state.currentGroup);
  while (g) { path.unshift(g); g = state.groups.find(x => x.id === g.parent_id); }
  return path;
}
function isInGroupSubtree(groupId, rootId) {
  let current = state.groups.find(group => group.id === groupId);
  while (current) {
    if (current.id === rootId) return true;
    current = state.groups.find(group => group.id === current.parent_id);
  }
  return false;
}
function selectGroup(id) { state.currentGroup=id; state.selected.clear(); renderGroups(); renderAssets(); }
function renderGroups() {
  const list=$("group-list"); list.replaceChildren();
  const path=groupPath();
  $("group-breadcrumb").textContent=state.currentGroup==="all"?"全部素材":state.currentGroup===null?"待归类":path.map(g=>g.name).join(" / ");
  $("btn-group-up").hidden=true;
  const addRow=(id,name,depth,group=null)=>{
    const li=document.createElement("li"); li.className="tree-row"; li.style.paddingLeft=(8+depth*16)+"px"; li.classList.toggle("tree-row--active",state.currentGroup===id);
    if(group && groupChildren(id).length) { const exp=document.createElement("button"); exp.className="tree-expander"; exp.textContent=state.expandedGroups.has(id)?"▾":"▸"; exp.setAttribute("aria-label","展开或收起 "+name); exp.onclick=()=>{state.expandedGroups.has(id)?state.expandedGroups.delete(id):state.expandedGroups.add(id);renderGroups();};li.append(exp); }
    const b=document.createElement("button"); b.className="tree-label"; b.textContent=name; b.onclick=()=>selectGroup(id);li.append(b);
    const count=document.createElement("span");count.className="tree-count";count.textContent=(state.project?.assets||[]).filter(a=>id==="all" || (id===null ? (a.project_group_id??a.group_id??null)===null : isInGroupSubtree(a.project_group_id??a.group_id??null,id))).length;li.append(count);
    if(group){const edit=document.createElement("button");edit.className="tree-action";edit.textContent="⋯";edit.title="编辑分组";edit.onclick=()=>editGroup(group);const del=document.createElement("button");del.className="tree-action";del.textContent="×";del.title="删除分组";del.onclick=()=>deleteGroup(group);li.append(edit,del);}
    if(id!=="all"){
      li.addEventListener("dragover",e=>{if(e.dataTransfer.types.includes("text/asset-ids")){e.preventDefault();e.dataTransfer.dropEffect="move";li.classList.add("tree-row--drop");}});
      li.addEventListener("dragleave",()=>li.classList.remove("tree-row--drop"));
      li.addEventListener("drop",e=>{const raw=e.dataTransfer.getData("text/asset-ids");if(!raw)return;e.preventDefault();e.stopPropagation();li.classList.remove("tree-row--drop");try{const ids=JSON.parse(raw);if(Array.isArray(ids)&&ids.every(Number.isInteger))batchMove(id,ids);}catch(_){toast("无法读取拖动的素材",true);}});
    }
    list.append(li);
  };
  addRow("all","全部素材",0);addRow(null,"待归类",0);
  const visit=(parent,depth)=>{for(const g of groupChildren(parent)){addRow(g.id,g.name,depth,g);if(state.expandedGroups.has(g.id))visit(g.id,depth+1);}};
  visit(null,0);
}

async function editGroup(group) {
  if (!state.project) return;
  const projectId = state.project.id;
  const epoch = projectEpoch;
  const name = window.prompt("分组名称", group.name); if (!name?.trim()) return;
  const choices = [{ id: null, label: "根目录" }, ...state.groups.filter(g => g.id !== group.id && !isDescendant(g.id, group.id)).map(g => ({ id: g.id, label: fullGroupName(g) }))];
  const dialog = document.createElement("dialog"); dialog.className = "modal";
  const form = document.createElement("form"); form.className = "modal__body"; form.method = "dialog";
  const heading = document.createElement("h2"); heading.className = "modal__title"; heading.textContent = "移动分组";
  const label = document.createElement("label"); label.className = "field"; label.textContent = "父分组";
  const select = document.createElement("select"); select.className = "field__input";
  choices.forEach(c => { const o = document.createElement("option"); o.value = c.id ?? ""; o.textContent = c.label; o.selected = c.id === group.parent_id; select.append(o); });
  label.append(select); const actions = document.createElement("div"); actions.className = "modal__actions";
  const cancel = document.createElement("button"); cancel.type = "button"; cancel.className = "btn"; cancel.textContent = "取消"; cancel.onclick = () => dialog.close("cancel");
  const ok = document.createElement("button"); ok.type = "submit"; ok.value = "confirm"; ok.className = "btn btn--primary"; ok.textContent = "确定"; actions.append(cancel, ok); form.append(heading, label, actions); dialog.append(form); document.body.append(dialog); dialog.showModal();
  await new Promise(resolve => dialog.addEventListener("close", resolve, { once: true }));
  if (dialog.returnValue !== "confirm") { dialog.remove(); return; }
  const parent_id = select.value ? Number(select.value) : null; dialog.remove();
  if (epoch !== projectEpoch || state.project?.id !== projectId) return;
  try {
    await api(`/projects/${projectId}/groups/${group.id}`, { method: "PATCH", body: JSON.stringify({ name: name.trim(), parent_id }) });
    if (epoch !== projectEpoch || state.project?.id !== projectId) return;
    if (await loadGroups()) { renderGroups(); renderAssets(); toast("分组已更新"); }
  } catch (err) { toast(err.message, true); }
}

async function deleteGroup(group) {
  if (!state.project) return;
  if (!window.confirm(`删除分组「${group.name}」？\n\n子分组及素材不会删除，将移至根目录。`)) return;
  const projectId = state.project.id;
  const epoch = projectEpoch;
  try {
    await api(`/projects/${projectId}/groups/${group.id}`, { method: "DELETE" });
    if (epoch !== projectEpoch || state.project?.id !== projectId) return;
    if (state.currentGroup === group.id) state.currentGroup = group.parent_id ?? null;
    if (!await loadGroups()) return;
    renderGroups(); await refreshDetail();
    if (epoch === projectEpoch && state.project?.id === projectId) toast("分组已删除，子分组和素材已保留");
  } catch (err) { toast(err.message, true); }
}

async function createGroup() {
  const name = window.prompt("分组名称"); if (!name?.trim() || !state.project) return;
  const projectId = state.project.id;
  const epoch = projectEpoch;
  try { await api(`/projects/${projectId}/groups`, { method: "POST", body: JSON.stringify({ name: name.trim(), parent_id: typeof state.currentGroup === "number" ? state.currentGroup : null }) }); if (epoch !== projectEpoch || state.project?.id !== projectId) return; if (await loadGroups()) { renderGroups(); toast("分组已创建"); } }
  catch (err) { toast(err.message, true); }
}

async function refreshDetail() {
  if (!state.project) return;
  const projectId = state.project.id;
  const epoch = projectEpoch;
  const project = await api(`/projects/${projectId}`);
  if (epoch !== projectEpoch || state.project?.id !== projectId) return;
  if (!await loadGroups()) return;
  if (epoch !== projectEpoch || state.project?.id !== projectId) return;
  state.project = project;
  renderGroups();
  renderDetail();
}

async function removeAsset(asset) {
  if (!window.confirm(`移除素材「${asset.title}」？\n\n这是删除，不是从这个项目里摘掉。`)) {
    return;
  }
  await api(`/assets/${asset.id}`, { method: "DELETE" });
  toast("素材已删除");
  await Promise.all([loadProjects(), refreshDetail()]);
}

/* ============ 项目弹窗（新建 / 编辑） ============ */

function renderCoverPick() {
  const list = $("project-coverlist");
  list.replaceChildren();
  if (!state.cover) return;
  const li = document.createElement("li");
  li.className = "filelist__item";
  const name = document.createElement("span");
  name.className = "filelist__name";
  name.textContent = state.cover.name;
  const size = document.createElement("span");
  size.className = "filelist__size";
  size.textContent = fmtSize(state.cover.size);
  const del = document.createElement("button");
  del.type = "button";
  del.className = "filelist__del";
  del.textContent = "×";
  del.title = "去掉这张封面";
  del.addEventListener("click", () => {
    state.cover = null;
    $("project-cover").value = "";
    renderCoverPick();
  });
  li.append(name, size, del);
  list.append(li);
}

function openProjectModal(project = null) {
  state.cover = null;
  $("project-id").value = project ? String(project.id) : "";
  $("project-title").value = project ? project.title : "";
  $("project-summary").value = project ? project.summary : "";
  $("project-tags").value = project ? (project.tags || []).join(", ") : "";
  $("project-cover").value = "";
  $("project-modal-title").textContent = project ? "编辑项目" : "新增项目";
  renderCoverPick();
  $("project-modal").showModal();
  $("project-title").focus();
}

async function submitProject(event) {
  event.preventDefault();
  const id = $("project-id").value;
  const title = $("project-title").value.trim();
  if (!title) {
    toast("项目标题不能为空", true);
    return;
  }

  const body = JSON.stringify({
    title,
    summary: $("project-summary").value.trim(),
    tags: $("project-tags")
      .value.replace(/，/g, ",")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean),
  });

  try {
    const project = id
      ? await api(`/projects/${id}`, { method: "PATCH", body })
      : await api("/projects", { method: "POST", body });

    if (state.cover) {
      const form = new FormData();
      form.append("file", state.cover);
      await api(`/projects/${project.id}/cover`, { method: "POST", body: form });
    }

    $("project-modal").close();
    toast(id ? "项目已更新" : "项目已创建");
    await loadProjects();
    if (state.project && state.project.id === project.id) await refreshDetail();
  } catch (err) {
    toast(err.message, true);
  }
}

/* ============ 素材弹窗 ============ */

function renderFiles() {
  const list = $("filelist");
  list.replaceChildren();
  state.files.forEach((file, index) => {
    const li = document.createElement("li");
    li.className = "filelist__item";

    const name = document.createElement("span");
    name.className = "filelist__name";
    name.textContent = file.name;

    const size = document.createElement("span");
    size.className = "filelist__size";
    size.textContent = fmtSize(file.size);

    const del = document.createElement("button");
    del.type = "button";
    del.className = "filelist__del";
    del.textContent = "×";
    del.title = "不算这个文件";
    del.addEventListener("click", () => {
      state.files.splice(index, 1);
      renderFiles();
    });

    li.append(name, size, del);
    list.append(li);
  });

  if (state.editing) {
    $("asset-notes-label").textContent = "正文 / 描述";
    $("asset-notes").placeholder = "这件素材的正文；图片和音频这里就是描述";
    $("asset-title-label").textContent = "标题";
    return;
  }

  // 有文件时「标题」可以不填（自动取文件名），「备注」的意义变成给所有文件用
  const hasFiles = state.files.length > 0;
  $("asset-notes-label").textContent = hasFiles
    ? "备注（可选）"
    : "正文 / 描述";
  $("asset-title-label").textContent = hasFiles
    ? "标题（可选，留空用文件名）"
    : "标题";
  $("asset-notes").placeholder = hasFiles
    ? "会加在正文开头；音频 / 视频直接当描述"
    : "不传文件时，这里就是这件素材的正文";

  $("dropzone").classList.toggle("dropzone--filled", hasFiles);
}

function renderReplace() {
  const list = $("replace-filelist");
  const file = state.replaceFile;
  const notes = $("asset-notes");

  // 选了新文件，正文会由新文件解析出来；此时正文框只作「备注」
  if (file && state.notesBefore == null) {
    state.notesBefore = notes.value;
    notes.value = "";
  } else if (!file && state.notesBefore != null) {
    notes.value = state.notesBefore;
    state.notesBefore = null;
  }
  if (state.editing) {
    $("asset-notes-label").textContent = file
      ? "备注（可选，拼在新正文前面）"
      : "正文 / 描述";
    notes.placeholder = file
      ? "会加在新解析出来的正文最前面；一般不用填"
      : "这件素材的正文；图片和音频这里就是描述";
  }

  list.replaceChildren();
  list.hidden = !file;
  if (!file) return;

  const li = document.createElement("li");
  li.className = "filelist__item";

  const name = document.createElement("span");
  name.className = "filelist__name";
  name.textContent = file.name;

  const size = document.createElement("span");
  size.className = "filelist__size";
  size.textContent = fmtSize(file.size);

  const del = document.createElement("button");
  del.type = "button";
  del.className = "filelist__del";
  del.textContent = "×";
  del.title = "不替换了";
  del.addEventListener("click", () => {
    state.replaceFile = null;
    renderReplace();
  });

  li.append(name, size, del);
  list.append(li);
}

function addFiles(files) {
  const existing = new Set(state.files.map((f) => `${f.name}:${f.size}`));
  for (const file of files) {
    const key = `${file.name}:${file.size}`;
    if (existing.has(key)) continue;
    existing.add(key);
    state.files.push(file);
  }

  if (state.files.length === 1 && state.titleAuto) {
    $("asset-title").value = stemOf(state.files[0].name);
  } else if (state.files.length > 1 && state.titleAuto) {
    // 多个文件各自成一条素材，标题得按文件来，表单里的统一标题就没意义了
    $("asset-title").value = "";
  }
  renderFiles();
}

function openAssetModal(asset = null) {
  if (!state.project) return;

  state.editing = asset;
  state.files = [];
  state.titleAuto = true;
  $("asset-file").value = "";

  const editing = Boolean(asset);
  state.replaceFile = null;
  state.notesBefore = null;
  // 添加模式显示上传区；编辑模式显示「替换文件」（同一件素材换版本）
  $("asset-pick").hidden = editing;
  $("asset-replace").hidden = !editing;
  renderReplace();
  $("asset-modal-title").textContent = editing ? "编辑素材" : "添加素材";
  $("btn-asset-save").textContent = editing ? "保存修改" : "保存";

  $("asset-title").value = editing ? asset.title : "";
  $("asset-notes").value = editing ? asset.content : "";
  $("asset-tags").value = editing ? (asset.tags || []).join(", ") : "";

  renderFiles();
  $("asset-modal").showModal();
  $("asset-title").focus();
}

async function submitAsset(event) {
  event.preventDefault();
  const title = $("asset-title").value.trim();
  const notes = $("asset-notes").value.trim();
  const tags = $("asset-tags")
    .value.replace(/，/g, ",")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .join(",");

  const editing = state.editing;
  if (editing) {
    if (!title) {
      toast("标题不能为空", true);
      return;
    }
    const button = $("btn-asset-save");
    button.disabled = true;
    try {
      if (state.replaceFile) {
        // 换文件：后端按新文件重新判定类型、重新解析正文，正文框里的字当备注
        const form = new FormData();
        form.append("file", state.replaceFile);
        form.append("notes", notes);
        await api(`/assets/${editing.id}/file`, { method: "POST", body: form });
        await api(`/assets/${editing.id}`, {
          method: "PATCH",
          body: JSON.stringify({ title, tags: tags ? tags.split(",") : [] }),
        });
      } else {
        await api(`/assets/${editing.id}`, {
          method: "PATCH",
          body: JSON.stringify({
            title,
            content: notes,
            tags: tags ? tags.split(",") : [],
          }),
        });
      }
      state.editing = null;
      state.replaceFile = null;
      $("asset-modal").close();
      toast("已保存修改");
      await Promise.all([loadProjects(), refreshDetail()]);
    } catch (err) {
      toast(err.message, true);
    } finally {
      button.disabled = false;
      button.textContent = "保存修改";
    }
    return;
  }

  if (!state.files.length && !title) {
    toast("没有文件时标题必须填", true);
    return;
  }

  const button = $("btn-asset-save");
  button.disabled = true;
  try {
    if (!state.files.length) {
      // 手写一条文本素材：正文直接来自输入框
      await api("/assets", {
        method: "POST",
        body: JSON.stringify({
          title,
          type: "text",
          content: notes,
          tags: tags ? tags.split(",") : [],
          project_ids: [state.project.id],
          ...(typeof state.currentGroup === "number" ? { group_id: state.currentGroup } : {}),
        }),
      });
    } else {
      let done = 0;
      for (const file of state.files) {
        const form = new FormData();
        form.append("file", file);
        form.append("project_id", String(state.project.id));
        if (typeof state.currentGroup === "number") form.append("group_id", String(state.currentGroup));
        // 单文件时用表单里的标题；多文件各用各的文件名
        form.append(
          "title",
          state.files.length === 1 ? title : stemOf(file.name),
        );
        form.append("notes", notes);
        form.append("tags", tags);

        button.textContent = `上传中 ${done + 1}/${state.files.length}…`;
        try {
          await api("/assets/upload", { method: "POST", body: form });
          done += 1;
        } catch (err) {
          // 前几件已经进库了，不能装作整体失败
          throw new Error(`已上传 ${done} 件，第 ${done + 1} 件失败：${err.message}`);
        }
      }
    }

    $("asset-modal").close();
    toast(state.files.length > 1 ? `已上传 ${state.files.length} 件素材` : "素材已保存");
    await Promise.all([loadProjects(), refreshDetail()]);
  } catch (err) {
    toast(err.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "保存";
  }
}

/* ============ 拖拽 ============ */

function wireDropzone(zone, input, onFiles) {
  const pick = () => input.click();

  zone.addEventListener("click", pick);
  zone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      pick();
    }
  });

  input.addEventListener("change", () => {
    if (input.files && input.files.length) onFiles([...input.files]);
    input.value = "";
  });

  zone.addEventListener("dragover", (event) => {
    event.preventDefault();
    zone.classList.add("dropzone--active");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("dropzone--active"));
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("dropzone--active");
    const dropped = [...(event.dataTransfer?.files || [])];
    if (dropped.length) onFiles(dropped);
  });
}

function wireWindowDrop() {
  // 拖到页面空白处也算：直接开「添加素材」并把文件带进去
  window.addEventListener("dragover", (event) => {
    if (event.dataTransfer?.types?.includes("Files")) event.preventDefault();
  });
  window.addEventListener("drop", (event) => {
    const files = [...(event.dataTransfer?.files || [])];
    if (!files.length) return;
    event.preventDefault();
    if (!state.project) {
      toast("先打开一个项目，再拖文件进来", true);
      return;
    }
    if (state.editing) {
      toast("编辑素材时不能换文件；要加新文件请关掉后点「+ 添加素材」", true);
      return;
    }
    if (!$("asset-modal").open) openAssetModal();
    addFiles(files);
  });
}

/* ============ 对话 ============ */

function appendMessage(role, text, sources = []) {
  const node = $("tpl-message").content.firstElementChild.cloneNode(true);
  node.classList.add(role === "user" ? "msg--user" : "msg--bot");

  const bubble = node.querySelector(".msg__bubble");
  // 正文单独一个 span：流式生成时只改它，否则 textContent 会把下面的引用列表也冲掉
  const body = document.createElement("span");
  body.className = "msg__text";
  body.textContent = text;
  bubble.append(body);

  if (sources.length) {
    const list = document.createElement("ul");
    list.className = "msg__sources";
    for (const source of sources) {
      const li = document.createElement("li");
      li.textContent = `[${source.index}] 《${source.project || "未归入项目"} · ${source.title}》`;
      list.append(li);
    }
    bubble.append(list);
  }

  $("messages").append(node);
  $("messages").scrollTop = $("messages").scrollHeight;
  return body;
}

function parseSseFrame(frame) {
  let event = "message";
  const dataLines = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length) return null;
  try {
    return { event, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    return null;
  }
}

async function askStream(question) {
  const res = await fetch("/ask/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });

  if (!res.ok || !res.body) {
    let message = `请求失败（HTTP ${res.status}）`;
    try {
      const failure = await res.json();
      if (failure?.message) message = failure.message;
    } catch {
      // 响应不是 JSON 就用默认文案
    }
    throw new Error(message);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let bubble = null;
  let answer = "";
  let sources = [];
  let needRefresh = false;

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let cut = buffer.indexOf("\n\n");
    while (cut >= 0) {
      const frame = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const parsed = parseSseFrame(frame);
      if (parsed) {
        if (parsed.event === "meta") {
          sources = parsed.data.sources || [];
          bubble = appendMessage("bot", "", sources);
        } else if (parsed.event === "delta") {
          answer += parsed.data.text || "";
          if (bubble) bubble.textContent = answer;
          $("messages").scrollTop = $("messages").scrollHeight;
        } else if (parsed.event === "done") {
          needRefresh = Boolean(parsed.data.refresh);
        } else if (parsed.event === "error") {
          throw new Error(parsed.data.message || "生成失败");
        }
      }
      cut = buffer.indexOf("\n\n");
    }
  }

  // 管理动作（改名 / 删除）改了数据，界面得跟着刷新
  if (needRefresh) await Promise.all([loadProjects(), refreshDetail()]);
}

async function submitQuestion(event) {
  event.preventDefault();
  const input = $("question");
  const question = input.value.trim();
  if (!question) return;

  appendMessage("user", question);
  input.value = "";
  try {
    await askStream(question);
  } catch (err) {
    appendMessage("bot", err.message);
  }
}

/* ============ 设置 ============ */

function renderProbe(results) {
  const list = $("probe-results");
  list.replaceChildren(
    ...results.map((item) => {
      const li = document.createElement("li");
      li.className = `probe__item probe__item--${item.ok ? "ok" : "fail"}`;
      li.textContent = `${item.ok ? "✓" : "✗"} ${item.message}`;
      return li;
    }),
  );
  list.hidden = false;
}

async function loadSettings() {
  try {
    const settings = await api("/settings");
    state.settings = settings;

    for (const [id, field] of KEY_FIELDS) {
      const masked = settings.keys?.[field];
      $(id).placeholder = masked ? `已保存 ${masked}，留空表示不改` : "还没有填";
    }
    $("settings-storage").textContent = `数据存在：${settings.storage}`;
    $("btn-quit").hidden = !settings.portable;

    // 一个都没配：第一次用，直接弹出来
    if (Object.values(settings.configured || {}).every((value) => !value)) {
      $("settings-note").textContent =
        "第一次用，先把密钥填上。只保存在这台电脑上，不会上传。";
      $("settings-modal").showModal();
    }
  } catch (err) {
    toast(err.message, true);
  }
}

function collectKeys() {
  const body = {};
  for (const [id, field] of KEY_FIELDS) {
    const value = $(id).value.trim();
    if (value) body[field] = value;
  }
  return body;
}

async function submitSettings(event) {
  event.preventDefault();
  const body = collectKeys();
  if (!Object.keys(body).length) {
    toast("没有要保存的内容（留空表示不改）", true);
    return;
  }
  try {
    const result = await api("/settings", {
      method: "PUT",
      body: JSON.stringify(body),
    });
    toast(`保存成功，已为 ${result.indexed_works ?? 0} 件素材建好索引`);
    for (const [id] of KEY_FIELDS) $(id).value = "";
    $("settings-modal").close();
    await loadSettings();
  } catch (err) {
    toast(err.message, true);
  }
}

async function testSettings() {
  const button = $("btn-settings-test");
  button.disabled = true;
  button.textContent = "测试中…";
  try {
    const result = await api("/settings/test", {
      method: "POST",
      body: JSON.stringify(collectKeys()),
    });
    renderProbe(result.results || []);
  } catch (err) {
    toast(err.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "测试连接";
  }
}

async function quitApp() {
  if (!window.confirm("退出作品集？退出后这个窗口里的服务就停了。")) return;
  try {
    await api("/quit", { method: "POST" });
    document.body.replaceChildren();
    const tip = document.createElement("p");
    tip.className = "quit-note";
    tip.textContent = "已退出。下次双击桌面上的「作品集」就行。";
    document.body.append(tip);
  } catch (err) {
    toast(err.message, true);
  }
}

/* ============ 初始化 ============ */

function wire() {
  const setChatOpen = (open) => {
    $("chat-panel").hidden = !open;
    $("btn-chat").setAttribute("aria-expanded", String(open));
    $("btn-chat").textContent = open ? "收起对话" : "AI 对话";
    if (open) $("question").focus();
    else $("btn-chat").focus();
  };
  $("btn-chat").addEventListener("click", () => setChatOpen($("chat-panel").hidden));
  $("btn-chat-close").addEventListener("click", () => setChatOpen(false));
  $("preview-close").addEventListener("click", () => $("preview-modal").close());
  $("preview-modal").addEventListener("close", () => $("preview-content").replaceChildren());
  $("btn-new-project").addEventListener("click", () => openProjectModal());
  $("btn-back").addEventListener("click", showList);
  $("btn-new-group").addEventListener("click", createGroup);
  $("btn-group-up").addEventListener("click", () => { const g = state.groups.find(x => x.id === state.currentGroup); state.currentGroup = g?.parent_id ?? null; renderGroups(); renderAssets(); });
  // 不能直接传函数引用：click 事件会被当成 asset 参数传进去
  $("btn-new-asset").addEventListener("click", () => openAssetModal());
  $("btn-edit-project").addEventListener("click", () => {
    if (state.project) openProjectModal(state.project);
  });
  $("btn-del-project").addEventListener("click", () => {
    if (!state.project) return;
    removeProject(state.project).catch((err) => toast(err.message, true));
  });

  $("project-form").addEventListener("submit", submitProject);
  $("btn-project-cancel").addEventListener("click", () => $("project-modal").close());
  $("asset-form").addEventListener("submit", submitAsset);
  $("btn-asset-cancel").addEventListener("click", () => {
    state.editing = null;
    state.replaceFile = null;
    $("asset-modal").close();
  });

  $("asset-title").addEventListener("input", (event) => {
    // 用户自己改过标题就别再自动覆盖
    if (event.target.value.trim()) state.titleAuto = false;
  });

  $("kind-filters").addEventListener("click", (event) => {
    const chip = event.target.closest(".chip");
    if (!chip) return;
    state.kind = chip.dataset.kind;
    for (const node of $("kind-filters").children) {
      node.classList.toggle("chip--active", node === chip);
    }
    renderAssets();
  });

  $("composer").addEventListener("submit", submitQuestion);

  $("asset-select-all").addEventListener("change", e => { visibleAssets().forEach(a => e.target.checked ? state.selected.add(a.id) : state.selected.delete(a.id)); renderAssets(); });
  $("btn-batch-clear").addEventListener("click", clearSelection);
  $("btn-batch-move").addEventListener("click", showMoveDialog);
  $("btn-settings").addEventListener("click", () => $("settings-modal").showModal());
  $("settings-form").addEventListener("submit", submitSettings);
  $("btn-settings-close").addEventListener("click", () => $("settings-modal").close());
  $("btn-settings-test").addEventListener("click", testSettings);
  $("btn-quit").addEventListener("click", quitApp);

  wireDropzone($("dropzone"), $("asset-file"), addFiles);
  wireDropzone($("dropzone-replace"), $("asset-replace-file"), (files) => {
    state.replaceFile = files[0] ?? null;
    renderReplace();
  });
  wireDropzone($("dropzone-project-cover"), $("project-cover"), (files) => {
    state.cover = files[0];
    renderCoverPick();
  });
  wireWindowDrop();
}

async function start() {
  wire();
  try {
    await loadProjects();
  } catch (err) {
    toast(err.message, true);
  }
  loadSettings();
}

start();
