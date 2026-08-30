const HELPER_BASE = "http://127.0.0.1:17891";

const pageTitleEl = document.getElementById("pageTitle");
const platformBadgeEl = document.getElementById("platformBadge");
const helperBadgeEl = document.getElementById("helperBadge");
const statusTextEl = document.getElementById("statusText");
const mediaListEl = document.getElementById("mediaList");
const downloadPageBtn = document.getElementById("downloadPage");
const refreshBtn = document.getElementById("refresh");
const clearBtn = document.getElementById("clear");

let activeTab = null;
let helperOnline = false;
let currentMediaItems = [];
const browserName = navigator.userAgent.includes("Edg/") ? "edge" : "chrome";

const PLATFORM_RULES = [
  [["x.com", "twitter.com"], "X / Twitter"],
  [["youtube.com", "youtu.be"], "YouTube"],
  [["tiktok.com"], "TikTok"],
  [["douyin.com", "iesdouyin.com"], "抖音"],
  [["bilibili.com", "b23.tv"], "哔哩哔哩"],
  [["instagram.com"], "Instagram"],
  [["facebook.com", "fb.watch"], "Facebook"],
  [["xiaohongshu.com", "xhslink.com"], "小红书"],
  [["kuaishou.com", "gifshow.com"], "快手"],
  [["vimeo.com"], "Vimeo"],
  [["twitch.tv"], "Twitch"],
  [["reddit.com", "redd.it"], "Reddit"],
  [["dailymotion.com", "dai.ly"], "Dailymotion"],
  [["soundcloud.com"], "SoundCloud"]
];

function detectPlatform(url) {
  try {
    const host = new URL(url).hostname.toLowerCase();
    for (const [domains, name] of PLATFORM_RULES) {
      if (domains.some((domain) => host === domain || host.endsWith(`.${domain}`))) return name;
    }
  } catch (_) {}
  return "通用网页";
}

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
  const timer = setTimeout(() => controller.abort(), 1000);
  try {
    const res = await fetch(`${HELPER_BASE}/health`, { signal: controller.signal });
    const data = await res.json().catch(() => ({}));
    helperOnline = res.ok && data.ok;
    helperBadgeEl.textContent = helperOnline
      ? `助手在线${data.version ? ` v${data.version}` : ""}`
      : "本地助手未启动";
  } catch (_) {
    helperOnline = false;
    helperBadgeEl.textContent = "本地助手未启动";
  } finally {
    clearTimeout(timer);
  }

  helperBadgeEl.className = `badge ${helperOnline ? "ok" : "bad"}`;
}

async function pollJob(jobId) {
  for (let attempt = 0; attempt < 240; attempt += 1) {
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
      const attemptText = Number.isFinite(job.attempt) && Number.isFinite(job.attempt_total)
        ? ` [${job.attempt}/${job.attempt_total}]`
        : "";
      const labels = {
        queued: "等待中",
        starting: "准备下载",
        resolving: "正在解析",
        retrying: "当前策略失败，自动切换",
        downloading: "正在下载",
        processing: "正在合并"
      };
      setStatus(`${labels[job.status] || job.status}${attemptText}${percent}${eta}`);
    } catch (_) {}
  }
}

async function helperDownload(payload) {
  if (!helperOnline) {
    await checkHelper();
  }
  if (!helperOnline) {
    throw new Error("本地助手未启动。请先双击 run-helper.bat。");
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
  const sizeMb = item.contentLength ? ` · ${(item.contentLength / 1024 / 1024).toFixed(1)} MB` : "";
  source.textContent = `${escapeText(item.source || "detected")}${sizeMb}`;

  head.append(type, source);

  const url = document.createElement("div");
  url.className = "media-url";
  url.textContent = shortUrl(item.url);
  url.title = item.url;

  const actions = document.createElement("div");
  actions.className = "media-actions";

  const mainBtn = document.createElement("button");
  mainBtn.className = "small-btn primary";
  mainBtn.textContent = "下载这个资源";
  mainBtn.addEventListener("click", async () => {
    mainBtn.disabled = true;
    try {
      const result = await helperDownload({
        media_url: item.url,
        page_url: activeTab?.url || item.pageUrl || "",
        title: activeTab?.title || item.title || "",
        browser: browserName
      });
      setStatus(`已创建下载任务：${result.job_id}`);
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
      setStatus("媒体链接已复制。");
    } catch (_) {
      setStatus("复制失败。");
    }
  });

  actions.append(mainBtn, copyBtn);
  card.append(head, url, actions);
  return card;
}

async function getDetectedMedia() {
  if (!activeTab?.id) return [];
  const response = await chrome.runtime.sendMessage({
    type: "OMNIFETCH_GET_MEDIA",
    tabId: activeTab.id
  });
  return response?.items || [];
}

async function renderMedia() {
  currentMediaItems = await getDetectedMedia();
  mediaListEl.replaceChildren();

  if (!currentMediaItems.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "暂未捕获到媒体流。仍可直接点“智能下载当前视频”；如果失败，再播放视频 2–5 秒后重新检测。";
    mediaListEl.append(empty);
    setStatus("当前捕获到 0 个媒体资源；页面解析仍可尝试。");
    return;
  }

  for (const item of currentMediaItems) {
    mediaListEl.append(createMediaCard(item));
  }
  setStatus(`已捕获 ${currentMediaItems.length} 个媒体资源，可直接智能下载。`);
}

async function rescan() {
  if (!activeTab?.id) return;
  refreshBtn.disabled = true;
  try {
    await chrome.tabs.sendMessage(activeTab.id, { type: "OMNIFETCH_RESCAN" }).catch(() => null);
    await sleep(350);
    await renderMedia();
  } finally {
    refreshBtn.disabled = false;
  }
}

downloadPageBtn.addEventListener("click", async () => {
  if (!activeTab?.url || !/^https?:/i.test(activeTab.url)) {
    setStatus("当前页面不能作为下载地址。");
    return;
  }

  downloadPageBtn.disabled = true;
  try {
    currentMediaItems = await getDetectedMedia();
    const fallbackMediaUrls = currentMediaItems
      .filter((item) => item?.url && item.type !== "segment")
      .slice(0, 10)
      .map((item) => item.url);

    const result = await helperDownload({
      page_url: activeTab.url,
      title: activeTab.title || "",
      browser: browserName,
      fallback_media_urls: fallbackMediaUrls
    });
    setStatus(`已创建 ${result.platform || detectPlatform(activeTab.url)} 智能下载任务：${result.job_id}`);
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
  platformBadgeEl.textContent = detectPlatform(activeTab?.url || "");
  await Promise.all([checkHelper(), rescan()]);
})();
