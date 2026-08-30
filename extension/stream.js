const HELPER_BASE = "http://127.0.0.1:17891";

const params = new URLSearchParams(location.search);
const mediaUrl = params.get("url") || "";
const pageUrl = params.get("page") || "";
const titleHint = params.get("title") || "视频";
const browserName = params.get("browser") || "chrome";
const streamKind = (params.get("type") || "stream").toUpperCase();

const titleEl = document.getElementById("title");
const sourceEl = document.getElementById("source");
const helperBadgeEl = document.getElementById("helperBadge");
const streamTypeEl = document.getElementById("streamType");
const platformEl = document.getElementById("platform");
const probeStatusEl = document.getElementById("probeStatus");
const bestBtn = document.getElementById("bestBtn");
const retryBtn = document.getElementById("retryBtn");
const copyBtn = document.getElementById("copyBtn");
const formatListEl = document.getElementById("formatList");
const formatCountEl = document.getElementById("formatCount");
const progressPanelEl = document.getElementById("progressPanel");
const progressTitleEl = document.getElementById("progressTitle");
const progressPercentEl = document.getElementById("progressPercent");
const progressBarEl = document.getElementById("progressBar");
const progressMetaEl = document.getElementById("progressMeta");

let helperOnline = false;
let displayFormats = [];
let probeData = null;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!bytes) return "未知大小";
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.round(bytes / 1024)} KB`;
}

function formatSpeed(value) {
  const speed = Number(value || 0);
  if (!speed) return "";
  if (speed >= 1024 * 1024) return `${(speed / 1024 / 1024).toFixed(1)} MB/s`;
  return `${Math.round(speed / 1024)} KB/s`;
}

function showEmpty(message) {
  const empty = document.createElement("div");
  empty.className = "empty";
  empty.textContent = String(message || "");
  formatListEl.replaceChildren(empty);
}

function qualityLabel(item) {
  if (item.has_video) return item.height ? `${item.height}P` : "视频";
  return "音频";
}

function formatDescription(item) {
  const parts = [];
  if (item.width && item.height) parts.push(`${item.width}×${item.height}`);
  if (item.tbr) parts.push(`${Math.round(item.tbr)} kbps`);
  if (item.fps) parts.push(`${item.fps} fps`);
  if (item.ext) parts.push(item.ext.toUpperCase());
  if (item.protocol) parts.push(item.protocol);
  if (item.filesize) parts.push(formatBytes(item.filesize));
  if (item.has_video && !item.has_audio) parts.push("自动合并最佳音频");
  if (item.has_audio && !item.has_video) parts.push("仅音频");
  return parts.join(" · ") || item.format_note || item.format_id;
}

function reduceFormats(formats) {
  const list = Array.isArray(formats) ? formats : [];
  const videos = list.filter((item) => item?.has_video);
  if (!videos.length) return list.filter((item) => item?.has_audio).slice(0, 12);

  const byHeight = new Map();
  for (const item of videos) {
    const key = item.height ? String(item.height) : `id:${item.format_id}`;
    const old = byHeight.get(key);
    if (!old) {
      byHeight.set(key, item);
      continue;
    }
    const oldScore = (old.has_audio ? 100000 : 0) + Number(old.tbr || 0);
    const newScore = (item.has_audio ? 100000 : 0) + Number(item.tbr || 0);
    if (newScore > oldScore) byHeight.set(key, item);
  }

  return [...byHeight.values()]
    .sort((a, b) => Number(b.height || 0) - Number(a.height || 0) || Number(b.tbr || 0) - Number(a.tbr || 0))
    .slice(0, 16);
}

function setProgress(percent, title, meta = "") {
  progressPanelEl.classList.remove("hidden");
  const value = Math.max(0, Math.min(100, Number(percent || 0)));
  progressBarEl.style.width = `${value}%`;
  progressPercentEl.textContent = `${value.toFixed(value % 1 ? 1 : 0)}%`;
  progressTitleEl.textContent = title;
  progressMetaEl.textContent = meta;
}

async function checkHelper() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 1200);
  try {
    const res = await fetch(`${HELPER_BASE}/health`, { signal: controller.signal });
    const data = await res.json().catch(() => ({}));
    helperOnline = res.ok && data.ok;
    helperBadgeEl.textContent = helperOnline ? `助手在线 v${data.version || ""}` : "助手未启动";
  } catch (_) {
    helperOnline = false;
    helperBadgeEl.textContent = "助手未启动";
  } finally {
    clearTimeout(timer);
  }
  helperBadgeEl.className = `badge ${helperOnline ? "ok" : "bad"}`;
  return helperOnline;
}

function renderFormats(formats) {
  displayFormats = reduceFormats(formats);
  formatCountEl.textContent = String(displayFormats.length);
  formatListEl.replaceChildren();

  if (!displayFormats.length) {
    showEmpty("没有拿到独立清晰度列表。仍可以点击“最高画质下载”，让下载器自动选择最佳可用格式。");
    bestBtn.disabled = false;
    return;
  }

  displayFormats.forEach((item, index) => {
    const card = document.createElement("article");
    card.className = "format-card";

    const quality = document.createElement("div");
    quality.className = "quality";
    quality.textContent = `${qualityLabel(item)}${index === 0 ? " · 推荐" : ""}`;

    const meta = document.createElement("div");
    meta.className = "format-meta";
    meta.textContent = formatDescription(item);

    const btn = document.createElement("button");
    btn.className = "format-download";
    btn.textContent = "下载";
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await startDownload(item.format_id);
      } finally {
        btn.disabled = false;
      }
    });

    card.append(quality, meta, btn);
    formatListEl.append(card);
  });

  bestBtn.disabled = false;
}

async function probe() {
  retryBtn.disabled = true;
  bestBtn.disabled = true;
  probeStatusEl.textContent = "正在分析";
  formatCountEl.textContent = "0";
  showEmpty("正在读取媒体清晰度、码率和音视频轨信息…");

  if (!mediaUrl) {
    probeStatusEl.textContent = "缺少地址";
    showEmpty("没有收到媒体地址，请回到视频页面重新捕获。");
    retryBtn.disabled = false;
    return;
  }

  if (!(await checkHelper())) {
    probeStatusEl.textContent = "助手未启动";
    showEmpty("M3U8 / DASH 清晰度分析和合并需要本地助手。请双击 run-helper.bat，或先运行 install-autostart.bat 设置自动后台启动，然后点击“重新分析”。");
    retryBtn.disabled = false;
    return;
  }

  try {
    const res = await fetch(`${HELPER_BASE}/probe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        media_url: mediaUrl,
        page_url: pageUrl,
        title: titleHint,
        browser: browserName
      })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) throw new Error(data.error || `分析失败 (${res.status})`);

    probeData = data;
    titleEl.textContent = data.title || titleHint || "流媒体";
    platformEl.textContent = data.platform || "通用网页";
    probeStatusEl.textContent = data.is_live ? "直播流" : "分析完成";
    renderFormats(data.formats || []);
  } catch (error) {
    probeStatusEl.textContent = "分析失败";
    showEmpty(error.message || error);
    bestBtn.disabled = false;
  } finally {
    retryBtn.disabled = false;
  }
}

async function startDownload(formatId = "") {
  if (!(await checkHelper())) {
    setProgress(0, "无法开始下载", "请先双击 run-helper.bat，或运行 install-autostart.bat 设置自动后台启动。");
    return;
  }

  setProgress(0, "正在创建下载任务", formatId ? `格式：${formatId}` : "自动选择最高画质");
  try {
    const res = await fetch(`${HELPER_BASE}/download`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        media_url: mediaUrl,
        page_url: pageUrl,
        title: probeData?.title || titleHint,
        browser: browserName,
        format_id: formatId
      })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) throw new Error(data.error || `创建任务失败 (${res.status})`);
    await pollJob(data.job_id);
  } catch (error) {
    setProgress(0, "下载失败", String(error.message || error));
  }
}

async function pollJob(jobId) {
  for (let attempt = 0; attempt < 720; attempt += 1) {
    await sleep(1000);
    try {
      const res = await fetch(`${HELPER_BASE}/jobs/${jobId}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok || !data.job) continue;

      const job = data.job;
      if (job.status === "completed") {
        setProgress(100, "下载完成", `${job.title || "视频"} · 已保存到 Downloads\\OmniFetch`);
        return;
      }
      if (job.status === "failed") {
        setProgress(Number(job.percent || 0), "下载失败", job.error || "未知错误");
        return;
      }

      const labels = {
        queued: "等待下载",
        starting: "准备下载",
        resolving: "正在解析媒体",
        retrying: "当前策略失败，正在自动切换",
        downloading: "正在并发下载分片",
        processing: "正在合并音视频"
      };
      const pieces = [];
      const speed = formatSpeed(job.speed);
      if (speed) pieces.push(speed);
      if (Number.isFinite(job.eta)) pieces.push(`剩余约 ${job.eta}s`);
      if (job.fragment_index && job.fragment_count) pieces.push(`分片 ${job.fragment_index}/${job.fragment_count}`);
      if (job.downloaded_bytes) pieces.push(`${formatBytes(job.downloaded_bytes)} 已下载`);
      setProgress(Number(job.percent || 0), labels[job.status] || job.status || "下载中", pieces.join(" · ") || "处理中…");
    } catch (_) {}
  }
  setProgress(0, "任务仍在运行", "下载时间较长，请保持本地助手运行。");
}

bestBtn.addEventListener("click", () => {
  const best = displayFormats[0];
  startDownload(best?.format_id || "");
});
retryBtn.addEventListener("click", probe);
copyBtn.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(mediaUrl);
    probeStatusEl.textContent = "地址已复制";
  } catch (_) {
    probeStatusEl.textContent = "复制失败";
  }
});

streamTypeEl.textContent = streamKind;
sourceEl.textContent = mediaUrl;
sourceEl.title = mediaUrl;
titleEl.textContent = titleHint || "流媒体";
probe();
