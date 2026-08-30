const HELPER_BASE = "http://127.0.0.1:17891";

const pageTitleEl = document.getElementById("pageTitle");
const helperBadgeEl = document.getElementById("helperBadge");
const statusTextEl = document.getElementById("statusText");
const mediaListEl = document.getElementById("mediaList");
const captureCountEl = document.getElementById("captureCount");
const downloadPageBtn = document.getElementById("downloadPage");
const refreshBtn = document.getElementById("refresh");
const recordModeBtn = document.getElementById("recordMode");
const clearBtn = document.getElementById("clear");

let activeTab = null;
let helperOnline = false;
let currentMediaItems = [];
const browserName = navigator.userAgent.includes("Edg/") ? "edge" : "chrome";

const DIRECT_TYPES = new Set(["mp4", "webm", "mov", "m4v", "flv", "mp3", "m4a", "aac", "video", "audio"]);
const VIDEO_DIRECT_TYPES = new Set(["mp4", "webm", "mov", "m4v", "flv", "video"]);
const AUDIO_TYPES = new Set(["mp3", "m4a", "aac", "audio"]);
const STREAM_TYPES = new Set(["hls", "dash"]);

function setStatus(text) {
  statusTextEl.textContent = text;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function shortUrl(url) {
  if (!url) return "";
  if (url.length <= 108) return url;
  return `${url.slice(0, 64)}…${url.slice(-34)}`;
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!bytes) return "";
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.round(bytes / 1024)} KB`;
}

function sourceLabel(item) {
  const labels = {
    "video-element": "播放器",
    "source-element": "播放器",
    "response-header": "网络响应",
    network: "网络请求",
    performance: "网页资源",
    meta: "页面信息"
  };
  return labels[item.source] || "已捕获";
}

function hasSeparateAudioTrack() {
  return currentMediaItems.some((item) => AUDIO_TYPES.has(item.type));
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
    const data = await res.json().catch(() => ({}));
    helperOnline = res.ok && data.ok;
    helperBadgeEl.textContent = helperOnline ? `流媒体助手 v${data.version || ""}` : "流媒体助手未启动";
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
      const fragment = Number.isFinite(job.fragment_index) && Number.isFinite(job.fragment_count)
        ? ` · 分片 ${job.fragment_index}/${job.fragment_count}`
        : "";
      const labels = {
        queued: "等待中",
        starting: "准备下载",
        resolving: "正在解析",
        retrying: "正在切换下载策略",
        downloading: "正在下载",
        processing: "正在合并"
      };
      setStatus(`${labels[job.status] || job.status}${percent}${fragment}`);
    } catch (_) {}
  }
}

async function helperDownload(payload) {
  if (!helperOnline) await checkHelper();
  if (!helperOnline) {
    throw new Error("这个资源需要流媒体助手。请先双击 run-helper.bat；也可以先运行 install-autostart.bat 设置一次自动后台启动。");
  }

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

async function openStreamDetails(item) {
  const params = new URLSearchParams({
    url: item.url,
    page: activeTab?.url || item.pageUrl || "",
    title: activeTab?.title || item.title || "",
    browser: browserName,
    type: item.type || "stream"
  });
  await chrome.tabs.create({ url: `${chrome.runtime.getURL("stream.html")}?${params.toString()}` });
}

async function openRecorder() {
  if (!activeTab?.id || !Number.isInteger(activeTab.id)) {
    throw new Error("没有可录制的目标标签页。");
  }
  if (!/^https?:/i.test(activeTab.url || "")) {
    throw new Error("当前页面不能使用录制兜底模式。");
  }
  const params = new URLSearchParams({
    tabId: String(activeTab.id),
    title: activeTab.title || "网页视频"
  });
  await chrome.tabs.create({ url: `${chrome.runtime.getURL("recorder.html")}?${params.toString()}` });
}

async function downloadItem(item) {
  if (DIRECT_TYPES.has(item.type)) {
    await directDownload(item.url);
    setStatus("已交给浏览器保存。无需本地助手。");
    return;
  }

  if (STREAM_TYPES.has(item.type)) {
    await openStreamDetails(item);
    setStatus("已打开清晰度与流媒体下载页。");
    return;
  }

  const result = await helperDownload({
    media_url: item.url,
    page_url: activeTab?.url || item.pageUrl || "",
    title: activeTab?.title || item.title || "",
    browser: browserName
  });
  setStatus(`已创建下载任务：${result.job_id}`);
}

function createMediaCard(item, index) {
  const card = document.createElement("article");
  card.className = "media-card";

  const head = document.createElement("div");
  head.className = "media-head";

  const type = document.createElement("span");
  type.className = "media-type";
  type.textContent = String(item.type || "media").toUpperCase();

  const meta = document.createElement("span");
  meta.className = "media-source";
  const size = formatBytes(item.contentLength);
  meta.textContent = `${sourceLabel(item)}${size ? ` · ${size}` : ""}`;
  head.append(type, meta);

  const url = document.createElement("div");
  url.className = "media-url";
  url.textContent = shortUrl(item.url);
  url.title = item.url;

  const actions = document.createElement("div");
  actions.className = "media-actions";

  const downloadBtn = document.createElement("button");
  downloadBtn.className = "small-btn primary";
  if (STREAM_TYPES.has(item.type)) {
    downloadBtn.textContent = "清晰度 / 下载";
  } else if (VIDEO_DIRECT_TYPES.has(item.type) && hasSeparateAudioTrack()) {
    downloadBtn.textContent = "保存此视频轨";
  } else {
    downloadBtn.textContent = DIRECT_TYPES.has(item.type) ? "保存" : "下载";
  }
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

  const secondBtn = document.createElement("button");
  secondBtn.className = "small-btn";
  if (DIRECT_TYPES.has(item.type)) {
    secondBtn.textContent = "打开";
    secondBtn.addEventListener("click", () => chrome.tabs.create({ url: item.url }));
  } else {
    secondBtn.textContent = "复制地址";
    secondBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(item.url);
        setStatus("媒体地址已复制。");
      } catch (_) {
        setStatus("复制失败。");
      }
    });
  }

  actions.append(downloadBtn, secondBtn);
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
    empty.textContent = "还没有捕获到媒体。先播放视频 2–5 秒；如果仍然是 0，可以使用上方“录制兜底”。";
    mediaListEl.append(empty);
    setStatus("等待网页产生媒体请求…");
    return;
  }

  currentMediaItems.forEach((item, index) => mediaListEl.append(createMediaCard(item, index)));
  if (hasSeparateAudioTrack() && currentMediaItems.some((item) => VIDEO_DIRECT_TYPES.has(item.type))) {
    setStatus(`已捕获 ${currentMediaItems.length} 个资源，并检测到分离的音频轨；顶部“下载当前视频”会优先尝试合并。`);
  } else {
    setStatus(`已捕获 ${currentMediaItems.length} 个可用资源；明显广告和小分片已降权。`);
  }
}

async function rescan() {
  if (!activeTab?.id) return;
  refreshBtn.disabled = true;
  try {
    await chrome.tabs.sendMessage(activeTab.id, { type: "OMNIFETCH_RESCAN" }).catch(() => null);
    await sleep(320);
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
      const top = currentMediaItems[0];
      const splitTracks = VIDEO_DIRECT_TYPES.has(top.type) && hasSeparateAudioTrack();
      if (splitTracks && activeTab?.url && /^https?:/i.test(activeTab.url)) {
        const result = await helperDownload({
          page_url: activeTab.url,
          title: activeTab.title || "",
          browser: browserName,
          fallback_media_urls: currentMediaItems.slice(0, 10).map((item) => item.url)
        });
        setStatus(`检测到分离音视频，已创建自动合并任务：${result.job_id}`);
        return;
      }
      await downloadItem(top);
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
    setStatus(`没有嗅探到媒体直链，已改用页面解析：${result.job_id}`);
  } catch (error) {
    setStatus(error.message || "下载失败");
  } finally {
    downloadPageBtn.disabled = false;
  }
});

recordModeBtn.addEventListener("click", async () => {
  recordModeBtn.disabled = true;
  try {
    await openRecorder();
    setStatus("已打开录制兜底页。该模式会实时录制标签页画面和声音。");
  } catch (error) {
    setStatus(error.message || "无法打开录制模式");
  } finally {
    recordModeBtn.disabled = false;
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
