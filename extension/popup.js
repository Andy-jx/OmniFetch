const HELPER_BASE = "http://127.0.0.1:17891";

const pageTitleEl = document.getElementById("pageTitle");
const helperBadgeEl = document.getElementById("helperBadge");
const statusTextEl = document.getElementById("statusText");
const mediaListEl = document.getElementById("mediaList");
const downloadPageBtn = document.getElementById("downloadPage");
const refreshBtn = document.getElementById("refresh");
const clearBtn = document.getElementById("clear");

let activeTab = null;
let helperOnline = false;
const browserName = navigator.userAgent.includes("Edg/") ? "edge" : "chrome";

function setStatus(text) {
  statusTextEl.textContent = text;
}

function shortUrl(url) {
  if (url.length <= 120) return url;
  return `${url.slice(0, 72)}…${url.slice(-38)}`;
}

function escapeText(value) {
  return String(value || "");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function getActiveTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs[0] || null;
}

async function checkHelper() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 900);
  try {
    const res = await fetch(`${HELPER_BASE}/health`, { signal: controller.signal });
    helperOnline = res.ok;
  } catch (_) {
    helperOnline = false;
  } finally {
    clearTimeout(timer);
  }

  helperBadgeEl.textContent = helperOnline ? "本地助手在线" : "本地助手未启动";
  helperBadgeEl.className = `badge ${helperOnline ? "ok" : "bad"}`;
}

async function pollJob(jobId) {
  for (let attempt = 0; attempt < 180; attempt += 1) {
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
      const eta = Number.isFinite(job.eta) ? `，剩余约 ${job.eta}s` : "";
      const labels = {
        queued: "等待中",
        starting: "准备下载",
        resolving: "正在解析",
        retrying: "正在重试",
        downloading: "正在下载",
        processing: "正在合并"
      };
      setStatus(`${labels[job.status] || job.status}${percent}${eta}`);
    } catch (_) {
      // 弹窗关闭后轮询自然结束；短暂失败时继续等待。
    }
  }
}

async function helperDownload(payload) {
  if (!helperOnline) {
    await checkHelper();
  }
  if (!helperOnline) {
    throw new Error("本地助手未启动。请先双击 run-helper.bat。 ");
  }

  const res = await fetch(`${HELPER_BASE}/download`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) {
    throw new Error(data.error || `本地助手返回 ${res.status}`);
  }
  pollJob(data.job_id);
  return data;
}

function createMediaCard(item) {
  const card = document.createElement("article");
  card.className = "media-card";

  const head = document.createElement("div");
  head.className = "media-head";

  const type = document.createElement("span");
  type.className = "media-type";
  type.textContent = escapeText(item.type || "media");

  const source = document.createElement("span");
  source.className = "media-source";
  source.textContent = escapeText(item.source || "detected");

  head.append(type, source);

  const url = document.createElement("div");
  url.className = "media-url";
  url.textContent = shortUrl(item.url);
  url.title = item.url;

  const actions = document.createElement("div");
  actions.className = "media-actions";

  const isDirect = ["mp4", "webm", "mov", "m4v"].includes(item.type);
  const mainBtn = document.createElement("button");
  mainBtn.className = "small-btn primary";
  mainBtn.textContent = isDirect ? "直接下载" : "助手下载";
  mainBtn.addEventListener("click", async () => {
    mainBtn.disabled = true;
    try {
      if (isDirect) {
        const result = await chrome.runtime.sendMessage({
          type: "OMNIFETCH_DIRECT_DOWNLOAD",
          url: item.url
        });
        if (!result?.ok) throw new Error(result?.error || "浏览器下载失败");
        setStatus("已交给浏览器下载。 ");
      } else {
        const result = await helperDownload({
          media_url: item.url,
          page_url: activeTab?.url || item.pageUrl || "",
          title: activeTab?.title || item.title || "",
          browser: browserName
        });
        setStatus(`已创建下载任务：${result.job_id}`);
      }
    } catch (error) {
      setStatus(error.message || "下载失败");
    } finally {
      mainBtn.disabled = false;
    }
  });

  const copyBtn = document.createElement("button");
  copyBtn.className = "small-btn";
  copyBtn.textContent = "复制链接";
  copyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(item.url);
      setStatus("媒体链接已复制。 ");
    } catch (_) {
      setStatus("复制失败。 ");
    }
  });

  actions.append(mainBtn, copyBtn);
  card.append(head, url, actions);
  return card;
}

async function renderMedia() {
  if (!activeTab?.id) return;
  const response = await chrome.runtime.sendMessage({
    type: "OMNIFETCH_GET_MEDIA",
    tabId: activeTab.id
  });

  const items = response?.items || [];
  mediaListEl.replaceChildren();

  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "暂未检测到媒体资源。先播放当前视频 2–5 秒，再点“重新检测”。";
    mediaListEl.append(empty);
    setStatus("当前检测到 0 个可用媒体资源。 ");
    return;
  }

  for (const item of items) {
    mediaListEl.append(createMediaCard(item));
  }
  setStatus(`当前检测到 ${items.length} 个媒体资源。`);
}

async function rescan() {
  if (!activeTab?.id) return;
  refreshBtn.disabled = true;
  try {
    await chrome.tabs.sendMessage(activeTab.id, { type: "OMNIFETCH_RESCAN" }).catch(() => null);
    await sleep(250);
    await renderMedia();
  } finally {
    refreshBtn.disabled = false;
  }
}

downloadPageBtn.addEventListener("click", async () => {
  if (!activeTab?.url || !/^https?:/i.test(activeTab.url)) {
    setStatus("当前页面不能作为下载地址。 ");
    return;
  }

  downloadPageBtn.disabled = true;
  try {
    const result = await helperDownload({
      page_url: activeTab.url,
      title: activeTab.title || "",
      browser: browserName
    });
    setStatus(`已创建页面下载任务：${result.job_id}`);
  } catch (error) {
    setStatus(error.message || "创建下载任务失败");
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
