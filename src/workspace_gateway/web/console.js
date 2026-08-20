const state = {
  sandboxes: [],
  providers: [],
  templates: [],
  workspaces: [],
  workspaceFiles: [],
  workspaceVersions: [],
  selectedWorkspaceId: null,
  selectedId: null,
  pendingKillId: null,
  configuringProvider: null,
  loading: false,
  apiKey: sessionStorage.getItem("workspace_gateway_api_key") || "",
};

const els = {};
const statusLabels = {
  creating: "创建中",
  running: "运行中",
  paused: "已暂停",
  terminated: "已终止",
  unknown: "未知",
  error: "异常",
};
const providerLabels = { pai: "PAI", e2b: "E2B", volcengine: "火山引擎" };
const viewContent = {
  overview: { title: "沙箱运行总览", subtitle: "统一查看 PAI、E2B 和火山引擎沙箱的运行指标。" },
  sandboxes: { title: "沙箱实例", subtitle: "查看、筛选和管理当前 Gateway 中的沙箱实例。" },
  workspaces: { title: "项目 Workspaces", subtitle: "持久化管理 Agent 编写的代码、版本和沙箱运行。" },
  providers: { title: "Provider 配置", subtitle: "配置 PAI、E2B 和火山引擎的接入参数。" },
  templates: { title: "沙箱模板", subtitle: "独立维护各 Provider 可用于创建沙箱的模板。" },
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function apiFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.apiKey) headers.set("X-Gateway-API-Key", state.apiKey);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const error = new Error(body.detail || `请求失败（${response.status}）`);
    error.status = response.status;
    throw error;
  }
  return response.status === 204 ? null : response.json();
}

function setConnection(online, text) {
  els.connectionDot.className = `connection-dot ${online ? "online" : "offline"}`;
  els.connectionText.textContent = text;
}

function formatDate(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).format(new Date(value));
}

function formatDuration(seconds) {
  if (!seconds) return "Provider 默认值";
  if (seconds >= 3600 && seconds % 3600 === 0) return `${seconds / 3600} 小时`;
  if (seconds >= 60 && seconds % 60 === 0) return `${seconds / 60} 分钟`;
  return `${seconds} 秒`;
}

function statusBadge(status) {
  const safe = statusLabels[status] ? status : "unknown";
  return `<span class="badge ${safe}">${statusLabels[safe]}</span>`;
}

function toast(message) {
  els.toast.textContent = message;
  els.toast.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { els.toast.hidden = true; }, 4000);
}

function setView(viewName) {
  const view = viewContent[viewName] ? viewName : "overview";
  document.querySelectorAll("[data-view-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.viewPanel !== view;
  });
  document.querySelectorAll("[data-view]").forEach((link) => {
    const active = link.dataset.view === view;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  els.pageTitle.textContent = viewContent[view].title;
  els.pageSubtitle.textContent = viewContent[view].subtitle;
  document.title = `${viewContent[view].title} · Workspace Gateway`;
}

function navigateToView(viewName) {
  const view = viewContent[viewName] ? viewName : "overview";
  if (window.location.hash !== `#${view}`) history.pushState(null, "", `#${view}`);
  setView(view);
  els.pageTitle.focus?.({ preventScroll: true });
}

function renderMetrics() {
  const running = state.sandboxes.filter((s) => s.state === "running").length;
  const active = state.sandboxes.filter((s) => ["running", "creating"].includes(s.state)).length;
  const paused = state.sandboxes.filter((s) => s.state === "paused").length;
  const providers = state.providers.filter((p) => p.configured).length;
  els.metricActive.textContent = active;
  els.metricRunning.textContent = running;
  els.metricPaused.textContent = paused;
  els.metricProviders.textContent = `${providers}/${state.providers.length}`;
}

function renderProviders() {
  els.providerGrid.innerHTML = state.providers.map((provider) => {
    const label = providerLabels[provider.provider] || provider.provider;
    const initials = provider.provider === "volcengine" ? "VE" : label.slice(0, 3).toUpperCase();
    return `
      <article class="provider-card">
        <div class="provider-card-head">
          <div class="provider-name"><span class="provider-logo">${escapeHtml(initials)}</span>${escapeHtml(label)}</div>
          <div class="provider-card-actions">
            <span class="badge ${provider.configured ? "configured" : "unconfigured"}">${provider.configured ? "已配置" : "未配置"}</span>
            <button class="provider-config-button" type="button" data-config-provider="${escapeHtml(provider.provider)}">配置 ${escapeHtml(label)}</button>
          </div>
        </div>
        <dl class="provider-details">
          <div class="provider-detail"><dt>Endpoint</dt><dd title="${escapeHtml(provider.endpoint || "未设置")}">${escapeHtml(provider.endpoint || "未设置")}</dd></div>
          <div class="provider-detail"><dt>默认超时</dt><dd>${escapeHtml(formatDuration(provider.default_timeout_seconds))}</dd></div>
        </dl>
      </article>`;
  }).join("");
}

function renderTemplates() {
  els.templateEmpty.hidden = state.templates.length > 0;
  els.templateGrid.innerHTML = state.templates.map((template) => `
    <article class="template-card${template.is_default ? " is-default" : ""}">
      <div class="template-card-head">
        <div>
          <h3>${escapeHtml(template.name)}</h3>
          <span class="provider-pill">${escapeHtml(providerLabels[template.provider] || template.provider)}</span>
        </div>
        <div class="template-card-actions">
          ${template.is_default
            ? '<span class="template-default-badge">MCP 默认</span>'
            : `<button class="template-default-button" type="button" data-set-default-template="${escapeHtml(template.id)}">设为默认</button>`}
          <button class="icon-button template-delete" type="button" data-delete-template="${escapeHtml(template.id)}" aria-label="删除模板 ${escapeHtml(template.name)}" title="删除模板">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3m-9 0 1 13h10l1-13M10 11v5M14 11v5"/></svg>
          </button>
        </div>
      </div>
      <p class="template-card-description">${escapeHtml(template.description || "暂无说明")}</p>
      <dl class="template-card-details">
        <div><dt>Template ID</dt><dd title="${escapeHtml(template.template_id)}">${escapeHtml(template.template_id)}</dd></div>
        <div><dt>默认超时</dt><dd>${escapeHtml(formatDuration(template.default_timeout_seconds))}</dd></div>
      </dl>
    </article>`).join("");
}

function shortVersion(version) {
  return version ? version.slice(0, 12) : "—";
}

function renderWorkspaces() {
  els.workspaceEmpty.hidden = state.workspaces.length > 0;
  els.workspaceGrid.innerHTML = state.workspaces.map((workspace) => `
    <article class="workspace-card">
      <div class="workspace-card-head">
        <span class="workspace-icon"><svg viewBox="0 0 24 24"><path d="M3 6h7l2 2h9v11H3z"/></svg></span>
        <span class="badge ${workspace.dirty ? "paused" : "configured"}">${workspace.dirty ? "有未提交修改" : "版本已保存"}</span>
      </div>
      <h3>${escapeHtml(workspace.name)}</h3>
      <p>${escapeHtml(workspace.description || "暂无项目说明")}</p>
      <dl>
        <div><dt>Workspace ID</dt><dd>${escapeHtml(workspace.id)}</dd></div>
        <div><dt>当前版本</dt><dd>${escapeHtml(shortVersion(workspace.current_version))}</dd></div>
        <div><dt>代码文件</dt><dd>${workspace.file_count} 个</dd></div>
      </dl>
      <button class="button secondary workspace-manage" type="button" data-open-workspace="${escapeHtml(workspace.id)}">管理代码与运行</button>
    </article>`).join("");
}

function renderWorkspaceFiles() {
  els.workspaceFileList.innerHTML = state.workspaceFiles.length
    ? state.workspaceFiles.map((file) => `
      <button type="button" data-workspace-file="${escapeHtml(file.path)}" class="workspace-file-item${els.workspaceFilePath.value === file.path ? " active" : ""}"${els.workspaceFilePath.value === file.path ? ' aria-current="true"' : ""}>
        <span>${escapeHtml(file.path)}</span><small>${file.size} B</small>
      </button>`).join("")
    : '<p class="workspace-pane-empty">还没有文件</p>';
}

function renderWorkspaceHistory() {
  els.workspaceHistory.innerHTML = state.workspaceVersions.map((version) => `
    <div class="workspace-version">
      <code>${escapeHtml(version.short_version)}</code>
      <div><strong>${escapeHtml(version.message)}</strong><small>${escapeHtml(formatDate(version.created_at))} · ${escapeHtml(version.author)}</small></div>
    </div>`).join("");
}

function updateWorkspaceHeader(workspace) {
  els.workspaceDetailTitle.textContent = workspace.name;
  els.workspaceDetailId.textContent = workspace.id;
  els.workspaceDetailVersion.textContent = `main · ${shortVersion(workspace.current_version)}`;
  els.workspaceDirtyBadge.className = `badge ${workspace.dirty ? "paused" : "configured"}`;
  els.workspaceDirtyBadge.textContent = workspace.dirty ? "有未提交修改" : "版本已保存";
}

async function refreshWorkspaceDetail({ preserveEditor = true } = {}) {
  const id = state.selectedWorkspaceId;
  if (!id) return;
  const [workspace, files, versions] = await Promise.all([
    apiFetch(`/v1/workspaces/${id}`),
    apiFetch(`/v1/workspaces/${id}/files`),
    apiFetch(`/v1/workspaces/${id}/versions?limit=30`),
  ]);
  const index = state.workspaces.findIndex((item) => item.id === id);
  if (index >= 0) state.workspaces[index] = workspace;
  state.workspaceFiles = files;
  state.workspaceVersions = versions;
  if (!preserveEditor) {
    els.workspaceFilePath.value = "";
    els.workspaceFileContent.value = "";
  }
  updateWorkspaceHeader(workspace);
  renderWorkspaceFiles();
  renderWorkspaceHistory();
  renderWorkspaces();
}

async function openWorkspace(workspaceId) {
  state.selectedWorkspaceId = workspaceId;
  els.workspaceFileError.textContent = "";
  els.workspaceRunOutput.hidden = true;
  els.workspaceDetailDialog.showModal();
  try {
    await refreshWorkspaceDetail({ preserveEditor: false });
  } catch (error) {
    toast(error.message || "Workspace 加载失败");
    els.workspaceDetailDialog.close();
  }
}

async function loadWorkspaceFile(path) {
  if (!state.selectedWorkspaceId) return;
  try {
    const file = await apiFetch(`/v1/workspaces/${state.selectedWorkspaceId}/file?path=${encodeURIComponent(path)}`);
    const bytes = Uint8Array.from(atob(file.content_base64), (char) => char.charCodeAt(0));
    els.workspaceFilePath.value = path;
    els.workspaceFileContent.value = new TextDecoder().decode(bytes);
    renderWorkspaceFiles();
  } catch (error) {
    toast(error.message || "读取文件失败");
  }
}

async function saveWorkspaceFile() {
  const id = state.selectedWorkspaceId;
  const path = els.workspaceFilePath.value.trim();
  if (!id || !path) {
    els.workspaceFileError.textContent = "请填写相对文件路径。";
    return;
  }
  els.workspaceSaveFile.disabled = true;
  try {
    await apiFetch(`/v1/workspaces/${id}/file`, {
      method: "PUT",
      body: JSON.stringify({ path, text: els.workspaceFileContent.value }),
    });
    els.workspaceFileError.textContent = "";
    await refreshWorkspaceDetail();
    toast(`已保存 ${path}，尚未提交版本`);
  } catch (error) {
    els.workspaceFileError.textContent = error.message || "保存文件失败";
  } finally {
    els.workspaceSaveFile.disabled = false;
  }
}

async function commitWorkspace() {
  const id = state.selectedWorkspaceId;
  const message = els.workspaceCommitMessage.value.trim();
  if (!id || !message) return;
  els.workspaceCommit.disabled = true;
  try {
    const result = await apiFetch(`/v1/workspaces/${id}/commits`, {
      method: "POST", body: JSON.stringify({ message }),
    });
    await refreshWorkspaceDetail();
    toast(result.created ? `已提交版本 ${result.version.short_version}` : "没有需要提交的修改");
  } catch (error) {
    toast(error.message || "提交版本失败");
  } finally {
    els.workspaceCommit.disabled = false;
  }
}

async function runWorkspace() {
  const id = state.selectedWorkspaceId;
  const command = els.workspaceRunCommand.value.trim();
  if (!id || !command) return;
  els.workspaceRun.disabled = true;
  els.workspaceRun.textContent = "正在创建沙箱并运行…";
  els.workspaceRunOutput.hidden = false;
  els.workspaceRunOutput.textContent = "正在保存当前版本、同步代码到 Sandbox…";
  try {
    const run = await apiFetch(`/v1/workspaces/${id}/runs`, {
      method: "POST",
      body: JSON.stringify({ command, auto_commit: true, commit_message: `Run: ${command}` }),
    });
    els.workspaceRunOutput.textContent = [
      `Sandbox: ${run.sandbox.id}`,
      `Version: ${run.version}`,
      `Exit code: ${run.result.exit_code}`,
      "",
      run.result.stdout || "",
      run.result.stderr || "",
    ].join("\n");
    await loadDashboard({ quiet: true });
    await refreshWorkspaceDetail();
    toast(run.result.exit_code === 0 ? "Workspace 运行成功" : "命令执行完成，但返回了错误");
  } catch (error) {
    els.workspaceRunOutput.textContent = error.message || "运行失败";
    toast(error.message || "运行失败");
  } finally {
    els.workspaceRun.disabled = false;
    els.workspaceRun.textContent = "同步并运行";
  }
}

async function createWorkspace(event) {
  event.preventDefault();
  els.workspaceCreateSubmit.disabled = true;
  els.workspaceCreateError.textContent = "";
  try {
    const workspace = await apiFetch("/v1/workspaces", {
      method: "POST",
      body: JSON.stringify({
        name: els.workspaceCreateName.value.trim(),
        description: els.workspaceCreateDescription.value.trim(),
      }),
    });
    els.workspaceCreateDialog.close();
    els.workspaceCreateForm.reset();
    await loadDashboard();
    await openWorkspace(workspace.id);
  } catch (error) {
    els.workspaceCreateError.textContent = error.message || "创建 Workspace 失败";
  } finally {
    els.workspaceCreateSubmit.disabled = false;
  }
}

function updateProviderEndpointField() {
  const provider = state.configuringProvider;
  const isE2BCloud = provider === "e2b";
  const isVolc = provider === "volcengine";
  const mode = els.providerMode.value;
  const usesE2BDomain = provider === "pai" || (isVolc && mode === "e2b");
  els.providerModeField.hidden = !isVolc;
  els.providerEndpointField.hidden = isE2BCloud;
  els.providerEndpoint.disabled = isE2BCloud;
  els.providerDomainNote.hidden = !usesE2BDomain;
  els.providerEndpoint.removeAttribute("aria-invalid");

  if (provider === "pai") {
    els.providerEndpointLabel.innerHTML = 'E2B Domain <b aria-hidden="true">*</b>';
    els.providerEndpoint.placeholder = "sandbox01.cn-shanghai.pai-eas.aliyuncs.com";
    els.providerEndpointHelp.textContent = "填写 PAI 控制台提供的基础 Domain，不要填写完整 API URL。";
    els.providerDomainCorrect.textContent = "sandbox01.cn-shanghai.pai-eas.aliyuncs.com";
    els.providerDomainWrong.textContent = "https://api.sandbox01.cn-shanghai.pai-eas.aliyuncs.com";
  } else if (isVolc && mode === "e2b") {
    els.providerEndpointLabel.innerHTML = 'E2B Domain <b aria-hidden="true">*</b>';
    els.providerEndpoint.placeholder = "sandbox.example.com";
    els.providerEndpointHelp.textContent = "填写火山引擎提供的 E2B-compatible 基础 Domain。";
    els.providerDomainCorrect.textContent = "sandbox.example.com";
    els.providerDomainWrong.textContent = "https://api.sandbox.example.com";
  } else if (isVolc) {
    els.providerEndpointLabel.innerHTML = 'Bridge Base URL <b aria-hidden="true">*</b>';
    els.providerEndpoint.placeholder = "https://sandbox-bridge.example.com";
    els.providerEndpointHelp.textContent = "填写实现 Gateway Bridge Contract 的 HTTPS 基础地址。";
  }
  els.providerEndpoint.required = !isE2BCloud;
}

function validateE2BDomain(value) {
  const domain = value.trim();
  if (!domain) return "请填写 E2B Domain。";
  if (/^https?:\/\//i.test(domain)) return "E2B Domain 只填写基础域名，请删除 http:// 或 https://。";
  if (/^api\./i.test(domain)) return "E2B Domain 不要添加 api. 前缀，E2B SDK 会自动拼接。";
  if (/[/?#]/.test(domain)) return "E2B Domain 不能包含路径、查询参数或片段，只填写基础域名。";
  if (/\s/.test(domain)) return "E2B Domain 不能包含空格。";
  return "";
}

function openProviderConfig(providerName) {
  const provider = state.providers.find((item) => item.provider === providerName);
  if (!provider) return;
  state.configuringProvider = providerName;
  const label = providerLabels[providerName] || providerName;
  els.providerDialogTitle.textContent = `配置 ${label}`;
  els.providerDialogDescription.textContent = providerName === "pai"
    ? "连接阿里云 PAI-Sandbox 的 E2B-compatible 接口，保存后立即生效。"
    : `更新 ${label} 的接入参数，保存后立即生效。`;
  els.providerForm.reset();
  els.providerMode.value = "rest";
  els.providerEndpoint.value = providerName === "e2b" || provider.endpoint === "E2B Cloud SDK default"
    ? "" : (provider.endpoint || "");
  els.providerTimeout.value = provider.default_timeout_seconds || 900;
  els.providerApiKey.value = "";
  els.providerApiKey.placeholder = provider.configured ? "已配置，留空保持不变" : "输入 Provider Token";
  els.providerError.textContent = "";
  updateProviderEndpointField();
  els.providerDialog.showModal();
}

async function saveProviderConfiguration(event) {
  event.preventDefault();
  const provider = state.configuringProvider;
  if (!provider) return;
  els.providerError.textContent = "";
  els.providerEndpoint.removeAttribute("aria-invalid");
  const usesE2BDomain = provider === "pai" || (provider === "volcengine" && els.providerMode.value === "e2b");
  if (usesE2BDomain) {
    const domainError = validateE2BDomain(els.providerEndpoint.value);
    if (domainError) {
      els.providerEndpoint.setAttribute("aria-invalid", "true");
      els.providerError.textContent = domainError;
      els.providerEndpoint.focus();
      return;
    }
  }
  els.providerSubmit.disabled = true;
  els.providerSubmit.textContent = "正在保存…";
  const payload = {
    timeout_seconds: Number(els.providerTimeout.value),
  };
  const apiKey = els.providerApiKey.value.trim();
  if (apiKey) payload.api_key = apiKey;
  if (provider === "pai") payload.domain = els.providerEndpoint.value.trim();
  if (provider === "volcengine") {
    payload.mode = els.providerMode.value;
    if (payload.mode === "e2b") payload.e2b_domain = els.providerEndpoint.value.trim();
    else payload.base_url = els.providerEndpoint.value.trim();
  }
  try {
    const updated = await apiFetch(`/v1/providers/${provider}/configuration`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    const index = state.providers.findIndex((item) => item.provider === provider);
    if (index >= 0) state.providers[index] = updated;
    renderProviders();
    renderMetrics();
    els.providerDialog.close();
    toast(`${providerLabels[provider] || provider} 配置已保存并生效`);
  } catch (error) {
    els.providerError.textContent = error.message || "保存失败，请检查配置。";
  } finally {
    els.providerSubmit.disabled = false;
    els.providerSubmit.textContent = "保存配置";
  }
}

function openTemplateCreate() {
  els.templateForm.reset();
  els.templateProvider.innerHTML = state.providers.map((provider) =>
    `<option value="${escapeHtml(provider.provider)}">${escapeHtml(providerLabels[provider.provider] || provider.provider)}${provider.configured ? "" : "（Provider 未配置）"}</option>`
  ).join("");
  els.templateTimeout.value = 900;
  els.templateIsDefault.checked = false;
  els.templateError.textContent = "";
  els.templateDialog.showModal();
}

async function saveTemplate(event) {
  event.preventDefault();
  els.templateError.textContent = "";
  els.templateSubmit.disabled = true;
  els.templateSubmit.textContent = "正在保存…";
  try {
    const created = await apiFetch("/v1/templates", {
      method: "POST",
      body: JSON.stringify({
        provider: els.templateProvider.value,
        template_id: els.templateProviderId.value.trim(),
        name: els.templateName.value.trim(),
        description: els.templateDescription.value.trim(),
        default_timeout_seconds: Number(els.templateTimeout.value),
        is_default: els.templateIsDefault.checked,
      }),
    });
    if (created.is_default) {
      state.templates.forEach((template) => { template.is_default = false; });
    }
    state.templates.push(created);
    state.templates.sort((a, b) => Number(b.is_default) - Number(a.is_default)
      || `${a.provider}:${a.name}`.localeCompare(`${b.provider}:${b.name}`));
    renderTemplates();
    els.templateDialog.close();
    toast(`模板“${created.name}”已保存`);
  } catch (error) {
    els.templateError.textContent = error.message || "保存模板失败。";
  } finally {
    els.templateSubmit.disabled = false;
    els.templateSubmit.textContent = "保存模板";
  }
}

async function setDefaultTemplate(templateId) {
  const template = state.templates.find((item) => item.id === templateId);
  if (!template || template.is_default) return;
  try {
    const updated = await apiFetch(`/v1/templates/${templateId}/default`, { method: "PUT" });
    state.templates.forEach((item) => { item.is_default = item.id === updated.id; });
    state.templates.sort((a, b) => Number(b.is_default) - Number(a.is_default)
      || `${a.provider}:${a.name}`.localeCompare(`${b.provider}:${b.name}`));
    renderTemplates();
    toast(`“${updated.name}”已设为 MCP 默认模板`);
  } catch (error) {
    toast(error.message || "设置默认模板失败");
  }
}

async function deleteTemplate(templateId) {
  const template = state.templates.find((item) => item.id === templateId);
  if (!template) return;
  const warning = template.is_default ? "\n删除后 MCP 将无法创建沙箱，直到设置新的默认模板。" : "";
  if (!window.confirm(`确认删除模板“${template.name}”吗？${warning}`)) return;
  try {
    await apiFetch(`/v1/templates/${templateId}`, { method: "DELETE" });
    state.templates = state.templates.filter((item) => item.id !== templateId);
    renderTemplates();
    toast(`模板“${template.name}”已删除`);
  } catch (error) {
    toast(error.message || "删除模板失败");
  }
}

function filteredSandboxes() {
  const query = els.searchInput.value.trim().toLowerCase();
  const provider = els.providerFilter.value;
  const status = els.stateFilter.value;
  return state.sandboxes.filter((sandbox) => {
    const metadataText = Object.values(sandbox.metadata || {}).join(" ").toLowerCase();
    const matchesQuery = !query || [sandbox.id, sandbox.provider_sandbox_id, sandbox.template_id]
      .some((value) => String(value || "").toLowerCase().includes(query)) || metadataText.includes(query);
    const matchesProvider = provider === "all" || sandbox.provider === provider;
    const matchesStatus = status === "all"
      || (status === "active" && sandbox.state !== "terminated")
      || sandbox.state === status;
    return matchesQuery && matchesProvider && matchesStatus;
  });
}

function renderSandboxes() {
  const filtered = filteredSandboxes();
  els.resultCount.textContent = `${filtered.length} 个实例`;
  els.emptyState.hidden = filtered.length > 0;
  els.sandboxTableBody.innerHTML = filtered.map((sandbox) => {
    const workspace = sandbox.metadata?.workspace_id || sandbox.metadata?.project_name || "—";
    const previewPort = Number(sandbox.metadata?.preview_port || 3000);
    return `
      <tr>
        <td data-label="实例" class="instance-cell">
          <button class="instance-link" data-open-id="${escapeHtml(sandbox.id)}">${escapeHtml(sandbox.id)}</button>
          <small title="${escapeHtml(sandbox.provider_sandbox_id)}">${escapeHtml(sandbox.provider_sandbox_id)}</small>
        </td>
        <td data-label="Provider"><span class="provider-pill">${escapeHtml(providerLabels[sandbox.provider] || sandbox.provider)}</span></td>
        <td data-label="状态">${statusBadge(sandbox.state)}</td>
        <td data-label="模板"><span class="cell-truncate" title="${escapeHtml(sandbox.template_id)}">${escapeHtml(sandbox.template_id || "—")}</span></td>
        <td data-label="工作区"><span class="cell-truncate" title="${escapeHtml(workspace)}">${escapeHtml(workspace)}</span></td>
        <td data-label="访问地址">${sandbox.state === "running"
          ? `<button class="preview-table-link" type="button" data-open-preview="${escapeHtml(sandbox.id)}" data-preview-port="${previewPort}">打开 :${previewPort}</button>`
          : "—"}</td>
        <td data-label="更新时间">${escapeHtml(formatDate(sandbox.updated_at))}</td>
        <td><button class="icon-button row-action" data-open-id="${escapeHtml(sandbox.id)}" aria-label="查看 ${escapeHtml(sandbox.id)} 详情"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></svg></button></td>
      </tr>`;
  }).join("");
}

function detailRows(sandbox) {
  const metadataEntries = Object.entries(sandbox.metadata || {});
  const metadata = metadataEntries.length
    ? metadataEntries.map(([key, value]) => `<span class="tag">${escapeHtml(key)}=${escapeHtml(value)}</span>`).join("")
    : '<span class="tag">无 metadata</span>';
  const env = sandbox.env_keys?.length
    ? sandbox.env_keys.map((key) => `<span class="tag">${escapeHtml(key)}</span>`).join("")
    : '<span class="tag">未记录环境变量</span>';
  const previewPort = Number(sandbox.metadata?.preview_port || 3000);
  return `
    <div class="detail-status"><div><strong>${escapeHtml(sandbox.id)}</strong><small>${escapeHtml(providerLabels[sandbox.provider] || sandbox.provider)} · ${escapeHtml(sandbox.template_id)}</small></div>${statusBadge(sandbox.state)}</div>
    <section class="detail-group"><h3>标识与运行配置</h3><dl class="detail-list">
      <div><dt>Gateway ID</dt><dd>${escapeHtml(sandbox.id)}</dd></div>
      <div><dt>Provider ID</dt><dd>${escapeHtml(sandbox.provider_sandbox_id)}</dd></div>
      <div><dt>Provider</dt><dd>${escapeHtml(providerLabels[sandbox.provider] || sandbox.provider)}</dd></div>
      <div><dt>模板 ID</dt><dd>${escapeHtml(sandbox.template_id || "—")}</dd></div>
      <div><dt>超时时间</dt><dd>${escapeHtml(formatDuration(sandbox.timeout_seconds))}</dd></div>
    </dl></section>
    <section class="detail-group"><h3>Workspace Metadata</h3><div class="tag-list">${metadata}</div></section>
    <section class="detail-group"><h3>应用访问地址</h3>
      <div class="preview-control">
        <label><span>端口</span><input id="sandbox-preview-port" type="number" min="1" max="65535" value="${previewPort}" ${sandbox.state === "running" ? "" : "disabled"}></label>
        <button class="button secondary" type="button" data-prepare-preview="${escapeHtml(sandbox.id)}" ${sandbox.state === "running" ? "" : "disabled"}>生成地址</button>
      </div>
      <p class="preview-help">Gateway 会代理访问并保管 Provider Token；地址有效期为 15 分钟，可随时重新生成。</p>
      <div id="sandbox-preview-result" class="preview-result">${sandbox.state === "running" ? "正在获取默认端口地址…" : "沙箱未运行，当前不可访问"}</div>
    </section>
    <section class="detail-group"><h3>环境变量名称（不展示值）</h3><div class="tag-list">${env}</div></section>
    <section class="detail-group"><h3>生命周期</h3><dl class="detail-list">
      <div><dt>创建时间</dt><dd>${escapeHtml(new Date(sandbox.created_at).toLocaleString("zh-CN"))}</dd></div>
      <div><dt>最后更新</dt><dd>${escapeHtml(new Date(sandbox.updated_at).toLocaleString("zh-CN"))}</dd></div>
    </dl></section>`;
}

function openDetail(id) {
  const sandbox = state.sandboxes.find((item) => item.id === id);
  if (!sandbox) return;
  state.selectedId = id;
  els.detailContent.innerHTML = detailRows(sandbox);
  const canPause = sandbox.state === "running";
  const canKill = sandbox.state !== "terminated";
  els.detailActions.innerHTML = `
    <button class="button secondary" data-detail-action="refresh">刷新状态</button>
    <button class="button secondary" data-detail-action="pause" ${canPause ? "" : "disabled"}>暂停</button>
    <button class="button danger" data-detail-action="kill" ${canKill ? "" : "disabled"}>销毁</button>`;
  els.detailDialog.showModal();
  if (sandbox.state === "running") prepareSandboxPreview(id);
}

async function prepareSandboxPreview(id, { open = false, port = null } = {}) {
  const input = document.getElementById("sandbox-preview-port");
  const selectedPort = Number(port || input?.value || 3000);
  if (!Number.isInteger(selectedPort) || selectedPort < 1 || selectedPort > 65535) {
    toast("端口必须在 1 到 65535 之间");
    return;
  }
  const popup = open ? window.open("about:blank", "_blank") : null;
  if (popup) popup.document.body.textContent = "正在连接 Sandbox…";
  const result = document.getElementById("sandbox-preview-result");
  if (result) result.textContent = "正在生成安全访问地址…";
  try {
    const access = await apiFetch(`/v1/sandboxes/${id}/preview/${selectedPort}/access`, {
      method: "POST",
    });
    if (result) {
      result.innerHTML = `<a href="${escapeHtml(access.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(access.url)}</a><small>上游：${escapeHtml(access.upstream_url)}</small>`;
    }
    if (popup) popup.location.replace(access.url);
    return access;
  } catch (error) {
    if (popup) popup.close();
    if (result) result.textContent = error.message || "访问地址生成失败";
    toast(error.message || "访问地址生成失败，请确认该端口已有服务监听");
    return null;
  }
}

async function loadDashboard({ quiet = false } = {}) {
  if (state.loading) return;
  state.loading = true;
  if (!quiet) document.body.classList.add("loading");
  try {
    const [providers, templates, sandboxes, workspaces] = await Promise.all([
      apiFetch("/v1/providers"),
      apiFetch("/v1/templates"),
      apiFetch("/v1/sandboxes?limit=500"),
      apiFetch("/v1/workspaces?limit=500"),
    ]);
    state.providers = providers;
    state.templates = templates;
    state.sandboxes = sandboxes;
    state.workspaces = workspaces;
    renderMetrics();
    renderProviders();
    renderTemplates();
    renderSandboxes();
    renderWorkspaces();
    setConnection(true, "已连接");
    els.lastUpdated.textContent = `更新于 ${new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;
    if (state.selectedId && els.detailDialog.open) openDetail(state.selectedId);
  } catch (error) {
    setConnection(false, "连接失败");
    if (error.status === 401) {
      els.settingsDialog.showModal();
      toast("需要有效的 Gateway API Key");
    } else if (!quiet) {
      toast(error.message || "加载失败，请稍后重试");
    }
  } finally {
    state.loading = false;
    document.body.classList.remove("loading");
  }
}

function populateCreateProviders() {
  const providersWithTemplates = new Set(state.templates.map((template) => template.provider));
  const configured = state.providers.filter(
    (provider) => provider.configured && providersWithTemplates.has(provider.provider)
  );
  if (!configured.length) {
    els.createProvider.innerHTML = '<option value="" selected disabled>没有已配置的 Provider</option>';
    els.createTemplate.innerHTML = '<option value="" selected disabled>请先新增沙箱模板</option>';
    els.createSubmit.disabled = true;
    els.createError.textContent = "请先配置 Provider，并在“沙箱模板”目录新增对应模板。";
    updateCreateDefaults();
    return;
  }
  els.createSubmit.disabled = false;
  els.createError.textContent = "";
  els.createProvider.innerHTML = state.providers.map((provider) => {
    const usable = provider.configured && providersWithTemplates.has(provider.provider);
    return `<option value="${escapeHtml(provider.provider)}" ${usable ? "" : "disabled"}>${escapeHtml(providerLabels[provider.provider] || provider.provider)}${usable ? "" : "（未配置或无模板）"}</option>`;
  }
  ).join("");
  const firstConfigured = configured[0];
  if (firstConfigured) els.createProvider.value = firstConfigured.provider;
  updateCreateDefaults();
}

function updateCreateDefaults() {
  const templates = state.templates.filter(
    (template) => template.provider === els.createProvider.value
  );
  els.createTemplate.innerHTML = templates.length
    ? templates.map((template) => `<option value="${escapeHtml(template.id)}">${escapeHtml(template.name)} · ${escapeHtml(template.template_id)}</option>`).join("")
    : '<option value="" selected disabled>该 Provider 暂无模板</option>';
  const selected = templates.find((template) => template.id === els.createTemplate.value) || templates[0];
  els.createTimeout.placeholder = selected?.default_timeout_seconds || "900";
  els.createSubmit.disabled = !selected;
}

async function createSandbox(event) {
  event.preventDefault();
  els.createError.textContent = "";
  const submit = els.createSubmit;
  submit.disabled = true;
  submit.textContent = "正在创建…";
  const metadata = {};
  if (els.createWorkspace.value.trim()) metadata.workspace_id = els.createWorkspace.value.trim();
  if (els.createProject.value.trim()) metadata.project_name = els.createProject.value.trim();
  const selectedTemplate = state.templates.find(
    (template) => template.id === els.createTemplate.value
  );
  if (!selectedTemplate) {
    els.createError.textContent = "请选择有效的沙箱模板。";
    submit.disabled = false;
    submit.textContent = "创建沙箱";
    return;
  }
  const payload = {
    provider: selectedTemplate.provider,
    template_id: selectedTemplate.template_id,
    metadata,
    timeout_seconds: els.createTimeout.value
      ? Number(els.createTimeout.value)
      : selectedTemplate.default_timeout_seconds,
  };
  try {
    const sandbox = await apiFetch("/v1/sandboxes", { method: "POST", body: JSON.stringify(payload) });
    els.createDialog.close();
    els.createForm.reset();
    toast(`已创建 ${sandbox.id}`);
    await loadDashboard();
    openDetail(sandbox.id);
  } catch (error) {
    els.createError.textContent = error.message || "创建失败，请检查 Provider 配置。";
  } finally {
    submit.disabled = false;
    submit.textContent = "创建沙箱";
  }
}

async function detailAction(action) {
  const id = state.selectedId;
  if (!id) return;
  if (action === "kill") {
    state.pendingKillId = id;
    els.confirmId.textContent = id;
    els.confirmDialog.showModal();
    return;
  }
  try {
    if (action === "refresh") await apiFetch(`/v1/sandboxes/${id}?refresh=true`);
    if (action === "pause") await apiFetch(`/v1/sandboxes/${id}/pause`, { method: "POST" });
    toast(action === "pause" ? "沙箱已暂停" : "状态已刷新");
    await loadDashboard();
  } catch (error) {
    toast(error.message || "操作失败");
  }
}

async function confirmKill() {
  if (!state.pendingKillId) return;
  els.confirmKill.disabled = true;
  els.confirmKill.textContent = "正在销毁…";
  try {
    await apiFetch(`/v1/sandboxes/${state.pendingKillId}`, { method: "DELETE" });
    els.confirmDialog.close();
    els.detailDialog.close();
    toast("沙箱已销毁");
    await loadDashboard();
  } catch (error) {
    toast(error.message || "销毁失败");
  } finally {
    state.pendingKillId = null;
    els.confirmKill.disabled = false;
    els.confirmKill.textContent = "确认销毁";
  }
}

function bindElements() {
  const ids = [
    "connection-dot", "connection-text", "open-settings", "page-title", "page-subtitle", "last-updated", "refresh-all", "open-create",
    "metric-active", "metric-running", "metric-paused", "metric-providers", "provider-grid", "result-count",
    "template-grid", "template-empty", "open-template-create", "empty-template-create",
    "workspace-grid", "workspace-empty", "open-workspace-create", "empty-workspace-create",
    "workspace-detail-dialog", "workspace-detail-title", "workspace-detail-id", "workspace-detail-version", "workspace-dirty-badge",
    "workspace-file-list", "workspace-new-file", "workspace-file-path", "workspace-file-content", "workspace-file-error", "workspace-save-file",
    "workspace-commit-message", "workspace-commit", "workspace-run-command", "workspace-run", "workspace-run-output", "workspace-history",
    "workspace-create-dialog", "workspace-create-form", "workspace-create-name", "workspace-create-description", "workspace-create-error", "workspace-create-submit",
    "search-input", "provider-filter", "state-filter", "sandbox-table-body", "empty-state", "empty-create",
    "detail-dialog", "detail-content", "detail-actions", "create-dialog", "create-form", "create-provider",
    "create-template", "create-timeout", "create-workspace", "create-project", "create-error", "create-submit",
    "settings-dialog", "settings-form", "api-key-input", "provider-dialog", "provider-form", "provider-dialog-title",
    "provider-dialog-description", "provider-mode-field", "provider-mode", "provider-endpoint-field", "provider-endpoint-label",
    "provider-endpoint", "provider-endpoint-help", "provider-domain-note", "provider-domain-correct", "provider-domain-wrong", "provider-api-key", "provider-api-key-help",
    "provider-timeout", "provider-error", "provider-submit", "template-dialog", "template-form", "template-provider",
    "template-name", "template-provider-id", "template-timeout", "template-is-default", "template-description", "template-error", "template-submit",
    "confirm-dialog", "confirm-id", "confirm-kill", "toast",
  ];
  for (const id of ids) els[id.replaceAll("-", "").replace(/^./, (c) => c.toLowerCase())] = document.getElementById(id);
  Object.assign(els, {
    connectionDot: document.getElementById("connection-dot"), connectionText: document.getElementById("connection-text"),
    openSettings: document.getElementById("open-settings"), lastUpdated: document.getElementById("last-updated"),
    pageTitle: document.getElementById("page-title"), pageSubtitle: document.getElementById("page-subtitle"),
    refreshAll: document.getElementById("refresh-all"), openCreate: document.getElementById("open-create"),
    metricActive: document.getElementById("metric-active"), metricRunning: document.getElementById("metric-running"),
    metricPaused: document.getElementById("metric-paused"), metricProviders: document.getElementById("metric-providers"),
    providerGrid: document.getElementById("provider-grid"), resultCount: document.getElementById("result-count"),
    templateGrid: document.getElementById("template-grid"), templateEmpty: document.getElementById("template-empty"),
    openTemplateCreate: document.getElementById("open-template-create"), emptyTemplateCreate: document.getElementById("empty-template-create"),
    workspaceGrid: document.getElementById("workspace-grid"), workspaceEmpty: document.getElementById("workspace-empty"),
    openWorkspaceCreate: document.getElementById("open-workspace-create"), emptyWorkspaceCreate: document.getElementById("empty-workspace-create"),
    workspaceDetailDialog: document.getElementById("workspace-detail-dialog"), workspaceDetailTitle: document.getElementById("workspace-detail-title"),
    workspaceDetailId: document.getElementById("workspace-detail-id"), workspaceDetailVersion: document.getElementById("workspace-detail-version"),
    workspaceDirtyBadge: document.getElementById("workspace-dirty-badge"), workspaceFileList: document.getElementById("workspace-file-list"),
    workspaceNewFile: document.getElementById("workspace-new-file"), workspaceFilePath: document.getElementById("workspace-file-path"),
    workspaceFileContent: document.getElementById("workspace-file-content"), workspaceFileError: document.getElementById("workspace-file-error"),
    workspaceSaveFile: document.getElementById("workspace-save-file"), workspaceCommitMessage: document.getElementById("workspace-commit-message"),
    workspaceCommit: document.getElementById("workspace-commit"), workspaceRunCommand: document.getElementById("workspace-run-command"),
    workspaceRun: document.getElementById("workspace-run"), workspaceRunOutput: document.getElementById("workspace-run-output"),
    workspaceHistory: document.getElementById("workspace-history"), workspaceCreateDialog: document.getElementById("workspace-create-dialog"),
    workspaceCreateForm: document.getElementById("workspace-create-form"), workspaceCreateName: document.getElementById("workspace-create-name"),
    workspaceCreateDescription: document.getElementById("workspace-create-description"), workspaceCreateError: document.getElementById("workspace-create-error"),
    workspaceCreateSubmit: document.getElementById("workspace-create-submit"),
    searchInput: document.getElementById("search-input"), providerFilter: document.getElementById("provider-filter"),
    stateFilter: document.getElementById("state-filter"), sandboxTableBody: document.getElementById("sandbox-table-body"),
    emptyState: document.getElementById("empty-state"), emptyCreate: document.getElementById("empty-create"),
    detailDialog: document.getElementById("detail-dialog"), detailContent: document.getElementById("detail-content"),
    detailActions: document.getElementById("detail-actions"), createDialog: document.getElementById("create-dialog"),
    createForm: document.getElementById("create-form"), createProvider: document.getElementById("create-provider"),
    createTemplate: document.getElementById("create-template"), createTimeout: document.getElementById("create-timeout"),
    createWorkspace: document.getElementById("create-workspace"), createProject: document.getElementById("create-project"),
    createError: document.getElementById("create-error"), createSubmit: document.getElementById("create-submit"),
    settingsDialog: document.getElementById("settings-dialog"), settingsForm: document.getElementById("settings-form"),
    apiKeyInput: document.getElementById("api-key-input"), confirmDialog: document.getElementById("confirm-dialog"),
    providerDialog: document.getElementById("provider-dialog"), providerForm: document.getElementById("provider-form"),
    providerDialogTitle: document.getElementById("provider-dialog-title"), providerDialogDescription: document.getElementById("provider-dialog-description"),
    providerModeField: document.getElementById("provider-mode-field"), providerMode: document.getElementById("provider-mode"),
    providerEndpointField: document.getElementById("provider-endpoint-field"), providerEndpointLabel: document.getElementById("provider-endpoint-label"),
    providerEndpoint: document.getElementById("provider-endpoint"), providerEndpointHelp: document.getElementById("provider-endpoint-help"),
    providerDomainNote: document.getElementById("provider-domain-note"), providerDomainCorrect: document.getElementById("provider-domain-correct"),
    providerDomainWrong: document.getElementById("provider-domain-wrong"),
    providerApiKey: document.getElementById("provider-api-key"),
    providerTimeout: document.getElementById("provider-timeout"), providerError: document.getElementById("provider-error"),
    providerSubmit: document.getElementById("provider-submit"),
    templateDialog: document.getElementById("template-dialog"), templateForm: document.getElementById("template-form"),
    templateProvider: document.getElementById("template-provider"), templateName: document.getElementById("template-name"),
    templateProviderId: document.getElementById("template-provider-id"), templateTimeout: document.getElementById("template-timeout"),
    templateIsDefault: document.getElementById("template-is-default"),
    templateDescription: document.getElementById("template-description"), templateError: document.getElementById("template-error"),
    templateSubmit: document.getElementById("template-submit"),
    confirmId: document.getElementById("confirm-id"), confirmKill: document.getElementById("confirm-kill"), toast: document.getElementById("toast"),
  });
}

function bindEvents() {
  els.refreshAll.addEventListener("click", () => loadDashboard());
  els.openCreate.addEventListener("click", () => { populateCreateProviders(); els.createDialog.showModal(); });
  els.emptyCreate.addEventListener("click", () => { populateCreateProviders(); els.createDialog.showModal(); });
  els.openTemplateCreate.addEventListener("click", openTemplateCreate);
  els.emptyTemplateCreate.addEventListener("click", openTemplateCreate);
  const showWorkspaceCreate = () => {
    els.workspaceCreateForm.reset();
    els.workspaceCreateError.textContent = "";
    els.workspaceCreateDialog.showModal();
  };
  els.openWorkspaceCreate.addEventListener("click", showWorkspaceCreate);
  els.emptyWorkspaceCreate.addEventListener("click", showWorkspaceCreate);
  els.workspaceCreateForm.addEventListener("submit", createWorkspace);
  els.workspaceNewFile.addEventListener("click", () => {
    els.workspaceFilePath.value = "";
    els.workspaceFileContent.value = "";
    els.workspaceFilePath.focus();
    renderWorkspaceFiles();
  });
  els.workspaceSaveFile.addEventListener("click", saveWorkspaceFile);
  els.workspaceCommit.addEventListener("click", commitWorkspace);
  els.workspaceRun.addEventListener("click", runWorkspace);
  els.openSettings.addEventListener("click", () => { els.apiKeyInput.value = state.apiKey; els.settingsDialog.showModal(); });
  els.searchInput.addEventListener("input", renderSandboxes);
  els.providerFilter.addEventListener("change", renderSandboxes);
  els.stateFilter.addEventListener("change", renderSandboxes);
  els.createProvider.addEventListener("change", updateCreateDefaults);
  els.createTemplate.addEventListener("change", () => {
    const template = state.templates.find((item) => item.id === els.createTemplate.value);
    els.createTimeout.placeholder = template?.default_timeout_seconds || "900";
  });
  els.createForm.addEventListener("submit", createSandbox);
  els.providerMode.addEventListener("change", updateProviderEndpointField);
  els.providerEndpoint.addEventListener("input", () => {
    els.providerEndpoint.removeAttribute("aria-invalid");
    els.providerError.textContent = "";
  });
  els.providerForm.addEventListener("submit", saveProviderConfiguration);
  els.templateForm.addEventListener("submit", saveTemplate);
  els.settingsForm.addEventListener("submit", (event) => {
    event.preventDefault();
    state.apiKey = els.apiKeyInput.value.trim();
    if (state.apiKey) sessionStorage.setItem("workspace_gateway_api_key", state.apiKey);
    else sessionStorage.removeItem("workspace_gateway_api_key");
    els.settingsDialog.close();
    loadDashboard();
  });
  document.addEventListener("click", (event) => {
    const viewLink = event.target.closest("[data-view]");
    if (viewLink) {
      event.preventDefault();
      navigateToView(viewLink.dataset.view);
    }
    const close = event.target.closest(".close-dialog");
    if (close) close.closest("dialog")?.close();
    const open = event.target.closest("[data-open-id]");
    if (open) openDetail(open.dataset.openId);
    const previewOpen = event.target.closest("[data-open-preview]");
    if (previewOpen) {
      prepareSandboxPreview(previewOpen.dataset.openPreview, {
        open: true,
        port: Number(previewOpen.dataset.previewPort || 3000),
      });
    }
    const previewPrepare = event.target.closest("[data-prepare-preview]");
    if (previewPrepare && !previewPrepare.disabled) {
      prepareSandboxPreview(previewPrepare.dataset.preparePreview);
    }
    const action = event.target.closest("[data-detail-action]");
    if (action && !action.disabled) detailAction(action.dataset.detailAction);
    const providerConfig = event.target.closest("[data-config-provider]");
    if (providerConfig) openProviderConfig(providerConfig.dataset.configProvider);
    const templateDelete = event.target.closest("[data-delete-template]");
    if (templateDelete) deleteTemplate(templateDelete.dataset.deleteTemplate);
    const templateDefault = event.target.closest("[data-set-default-template]");
    if (templateDefault) setDefaultTemplate(templateDefault.dataset.setDefaultTemplate);
    const workspaceOpen = event.target.closest("[data-open-workspace]");
    if (workspaceOpen) openWorkspace(workspaceOpen.dataset.openWorkspace);
    const workspaceFile = event.target.closest("[data-workspace-file]");
    if (workspaceFile) loadWorkspaceFile(workspaceFile.dataset.workspaceFile);
  });
  window.addEventListener("hashchange", () => setView(window.location.hash.slice(1)));
  els.confirmKill.addEventListener("click", confirmKill);
}

document.addEventListener("DOMContentLoaded", async () => {
  bindElements();
  bindEvents();
  setView(window.location.hash.slice(1) || "overview");
  await loadDashboard();
  setInterval(() => loadDashboard({ quiet: true }), 15000);
});
