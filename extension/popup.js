const HELPER_BASE = "http://127.0.0.1:17891";

const pageTitleEl = document.getElementById("pageTitle");
const helperBadgeEl = document.getElementById("helperBadge");
const statusTextEl = document.getElementById("statusText");
const mediaListEl = document.getElementById("mediaList");
const captureCountEl = document.getElementById("captureCount");
const downloadPageBtn = document.getElementById("downloadPage");
const refreshBtn = document.getElementById("refresh");
const clearBtn = document.getElementById("clear");

let activeTab = null;
let helperOnline = false;
let currentMediaItems = [];
const browserName = navigator.userAgent.includes("Edg/") ? "edge" : "chrome";

const DIRECT_TYPES = new Set(["mp4", "webm", "mov", "m4v", "flv", "mp3", "m4a", "aac", "video", "audio"]);

function setStatus(text) {
  statusTextEl.textContent = text;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function shortUrl(url) {
  if (!url) return "";
  if (url.length <= 110) return url;
  return `${url.slice(0, 66)}…${url.slice(-34)}`;
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!bytes) return "";
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.round(bytes / 1024)} KB`;
}

async function getActiveTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs[0] || null;
}

async function checkHelper() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 800);
  try {
    const res = await fetch(`${HELPER_BASE}/health`, { signal: controller.signal });
    const data = await res.json().catch(() => ({}));
    helperOnline = res.ok && data.ok;
    helperBadgeEl.textContent = helperOnline ? "流媒体助手在线" : "流媒体助手未启动";
  } catch (_) {
    helperOnline = false;
    helperBadgeEl.textContent = "流媒体助手未启动";
  } finally {
    clearTimeout(timer);
  }
  helperBadgeEl.className = `badge ${helperOnline ? "ok" : "bad"}`;
}

async function pollJob(jobId) {
  for (let attempt = 0; attempt < 360; attempt += 1) {
    await sleep(1000);
    try {
      const res = await fetch(`${HELPER_BASE}/jobs/${jobId}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok || !data.job) continue;
      const job = data.job;
      if (job.status === "completed") {
        setStatus(`下载完成：${job.title || "视频"}`);
        return;
      }
      if (job.status === "failed") {
        setStatus(`下载失败：${job.error || "未知错误"}`);
        return;
      }
      const percent = Number.isFinite(job.percent) ? ` ${job.percent}%` : "";
      const labels = {
        queued: "等待中",
        starting: "准备下载",
        resolving: "正在解析",
        retrying: "正在切换下载策略",
        downloading: "正在下载",
        processing: "正在合并"
      };
      setStatus(`${labels[job.status] || job.status}${percent}`);
    } catch (_) {}
  }
}

async function helperDownload(payload) {
  if (!helperOnline) await checkHelper();
  if (!helperOnline) throw new Error("这个资源需要流媒体助手，请先双击 run-helper.bat。普通 MP4/WebM 不需要助手。");

  const res = await fetch(`${HELPER_BASE}/download`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) throw new Error(data.error || `本地助手返回 ${res.status}`);
  pollJob(data.job_id);
  return data;
}

async function directDownload(url) {
  const result = await chrome.runtime.sendMessage({ type: "OMNIFETCH_DIRECT_DOWNLOAD", url });
  if (!result?.ok) throw new Error(result?.error || "浏览器下载失败");
  return result;
}

async function downloadItem(item) {
  if (DIRECT_TYPES.has(item.type)) {
    await directDownload(item.url);
    setStatus("已交给浏览器下载。无需本地助手。");
    return;
  }

  const result = await helperDownload({
    media_url: item.url,
    page_url: activeTab?.url || item.pageUrl || "",
    title: activeTab?.title || item.title || "",
    browser: browserName
  });
  setStatus(`已创建流媒体下载任务：${result.job_id}`);
}

function createMediaCard(item, index) {
  const card = document.createElement("article");
  card.className = "media-card";

  const head = document.createElement("div");
  head.className = "media-head";

  const type = document.createElement("span");
  type.className = "media-type";
  type.textContent = String(item.type || "media").toUpperCase();

  const source = document.createElement("span");
  source.className = "media-source";
  const size = formatBytes(item.contentLength);
  source.textContent = size ? `#${index + 1} · ${size}` : `#${index + 1}`;
  head.append(type, source);

  const url = document.createElement("div");
  url.className = "media-url";
  url.textContent = shortUrl(item.url);
  url.title = item.url;

  const actions = document.createElement("div");
  actions.className = "media-actions";

  const downloadBtn = document.createElement("button");
  downloadBtn.className = "small-btn primary";
  downloadBtn.textContent = DIRECT_TYPES.has(item.type) ? "保存" : "下载并合并";
  downloadBtn.addEventListener("click", async () => {
    downloadBtn.disabled = true;
    try {
      await downloadItem(item);
    } catch (error) {
      setStatus(error.message || "下载失败");
    } finally {
      downloadBtn.disabled = false;
    }
  });

  const copyBtn = document.createElement("button");
  copyBtn.className = "small-btn";
  copyBtn.textContent = "复制地址";
  copyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(item.url);
      setStatus("媒体地址已复制。");
    } catch (_) {
      setStatus("复制失败。");
    }
  });

  actions.append(downloadBtn, copyBtn);
  card.append(head, url, actions);
  return card;
}

async function getDetectedMedia() {
  if (!activeTab?.id) return [];
  const response = await chrome.runtime.sendMessage({ type: "OMNIFETCH_GET_MEDIA", tabId: activeTab.id });
  return response?.items || [];
}

async function renderMedia() {
  currentMediaItems = await getDetectedMedia();
  captureCountEl.textContent = String(currentMediaItems.length);
  mediaListEl.replaceChildren();

  if (!currentMediaItems.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "还没有捕获到媒体。先播放视频几秒；捕获成功后，浏览器右上角 OmniFetch 图标会出现数字角标。";
    mediaListEl.append(empty);
    setStatus("等待网页产生媒体请求…");
    return;
  }

  currentMediaItems.forEach((item, index) => mediaListEl.append(createMediaCard(item, index)));
  setStatus(`已捕获 ${currentMediaItems.length} 个资源，排在最上面的通常最值得下载。`);
}

async function rescan() {
  if (!activeTab?.id) return;
  refreshBtn.disabled = true;
  try {
    await chrome.tabs.sendMessage(activeTab.id, { type: "OMNIFETCH_RESCAN" }).catch(() => null);
    await sleep(300);
    await renderMedia();
  } finally {
    refreshBtn.disabled = false;
  }
}

downloadPageBtn.addEventListener("click", async () => {
  downloadPageBtn.disabled = true;
  try {
    currentMediaItems = await getDetectedMedia();
    if (currentMediaItems.length) {
      await downloadItem(currentMediaItems[0]);
      return;
    }

    if (!activeTab?.url || !/^https?:/i.test(activeTab.url)) {
      throw new Error("当前页面没有可下载媒体。");
    }

    const result = await helperDownload({
      page_url: activeTab.url,
      title: activeTab.title || "",
      browser: browserName
    });
    setStatus(`没有嗅探到直链，已改用页面解析：${result.job_id}`);
  } catch (error) {
    setStatus(error.message || "下载失败");
  } finally {
    downloadPageBtn.disabled = false;
  }
});

refreshBtn.addEventListener("click", rescan);

clearBtn.addEventListener("click", async () => {
  if (!activeTab?.id) return;
  await chrome.runtime.sendMessage({ type: "OMNIFETCH_CLEAR_MEDIA", tabId: activeTab.id });
  await renderMedia();
});

(async () => {
  activeTab = await getActiveTab();
  pageTitleEl.textContent = activeTab?.title || "当前页面";
  await Promise.all([checkHelper(), rescan()]);
})();
