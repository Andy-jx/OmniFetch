const HELPER_BASE = "http://127.0.0.1:17891";
const REQUIRED_HELPER = [0, 5, 4];

const pageTitleEl = document.getElementById("pageTitle");
const helperBadgeEl = document.getElementById("helperBadge");
const statusTextEl = document.getElementById("statusText");
const captureSummaryEl = document.getElementById("captureSummary");
const videoHintEl = document.getElementById("videoHint");
const audioHintEl = document.getElementById("audioHint");
const downloadVideoBtn = document.getElementById("downloadVideo");
const downloadAudioBtn = document.getElementById("downloadAudio");
const refreshBtn = document.getElementById("refresh");
const recordModeBtn = document.getElementById("recordMode");
const clearBtn = document.getElementById("clear");

let activeTab = null;
let helperOnline = false;
let helperVersion = "";
let currentMediaItems = [];
const browserName = navigator.userAgent.includes("Edg/") ? "edge" : "chrome";

const VIDEO_TYPES = new Set(["mp4", "webm", "mov", "m4v", "flv", "video", "hls", "dash"]);
const AUDIO_TYPES = new Set(["mp3", "m4a", "aac", "audio"]);
const STREAM_TYPES = new Set(["hls", "dash"]);

function setStatus(text) {
  statusTextEl.textContent = text;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function parseVersion(value) {
  return String(value || "")
    .split(".")
    .slice(0, 3)
    .map((part) => Number.parseInt(part, 10) || 0);
}

function versionAtLeast(value, minimum) {
  const current = parseVersion(value);
  for (let index = 0; index < minimum.length; index += 1) {
    if ((current[index] || 0) > minimum[index]) return true;
    if ((current[index] || 0) < minimum[index]) return false;
  }
  return true;
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!bytes) return "";
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.round(bytes / 1024)} KB`;
}

function bestByScore(items) {
  return [...items].sort((a, b) => {
    const scoreDiff = Number(b.score || 0) - Number(a.score || 0);
    if (scoreDiff) return scoreDiff;
    return Number(b.contentLength || 0) - Number(a.contentLength || 0);
  })[0] || null;
}

function bestStreamCandidate() {
  return bestByScore(currentMediaItems.filter((item) => STREAM_TYPES.has(item.type)));
}

function bestVideoCandidate() {
  return bestStreamCandidate() || bestByScore(currentMediaItems.filter((item) => VIDEO_TYPES.has(item.type)));
}

function bestAudioCandidate() {
  return bestByScore(currentMediaItems.filter((item) => AUDIO_TYPES.has(item.type)));
}

function requestHeadersFor(item) {
  return item?.requestHeaders && typeof item.requestHeaders === "object" ? item.requestHeaders : {};
}

function fallbackUrls() {
  const prioritized = [...currentMediaItems]
    .filter((item) => VIDEO_TYPES.has(item.type) || AUDIO_TYPES.has(item.type))
    .sort((a, b) => {
      const aStream = STREAM_TYPES.has(a.type) ? 1 : 0;
      const bStream = STREAM_TYPES.has(b.type) ? 1 : 0;
      if (aStream !== bStream) return bStream - aStream;
      return Number(b.score || 0) - Number(a.score || 0);
    });
  return [...new Set(prioritized.map((item) => item.url).filter(Boolean))].slice(0, 10);
}

function validPageUrl() {
  return Boolean(activeTab?.url && /^https?:/i.test(activeTab.url));
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
    helperVersion = helperOnline ? String(data.version || "") : "";
    const currentEnough = helperOnline && versionAtLeast(helperVersion, REQUIRED_HELPER);
    helperBadgeEl.textContent = helperOnline
      ? `流媒体助手 v${helperVersion || "?"}${currentEnough ? "" : " · 版本过旧"}`
      : "流媒体助手未启动";
    helperBadgeEl.className = `badge ${currentEnough ? "ok" : "bad"}`;
    return currentEnough;
  } catch (_) {
    helperOnline = false;
    helperVersion = "";
    helperBadgeEl.textContent = "流媒体助手未启动";
    helperBadgeEl.className = "badge bad";
    return false;
  } finally {
    clearTimeout(timer);
  }
}

async function pollJob(jobId, kind) {
  for (let attempt = 0; attempt < 720; attempt += 1) {
    await sleep(1000);
    try {
      const res = await fetch(`${HELPER_BASE}/jobs/${jobId}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok || !data.job) continue;
      const job = data.job;
      if (job.status === "completed") {
        const bytes = Number(job.output_bytes || 0);
        const suffix = bytes ? ` · ${(bytes / 1024 / 1024).toFixed(1)} MB` : "";
        setStatus(`${kind === "audio" ? "音频" : "视频"}下载完成：${job.title || "文件"}${suffix}`);
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
        resolving: kind === "audio" ? "正在提取最佳音频" : "正在解析最高画质",
        retrying: "当前结果无效，正在自动换下载策略",
        downloading: "正在下载",
        processing: kind === "audio" ? "正在生成音频文件" : "正在合并音视频"
      };
      setStatus(`${labels[job.status] || job.status}${percent}${fragment}`);
    } catch (_) {}
  }
}

async function helperDownload(payload, kind = "video") {
  const ready = await checkHelper();
  if (!ready) {
    if (helperOnline) {
      throw new Error(`后台助手还是 v${helperVersion || "旧版"}。请运行 v0.5.4 的 install-autostart.bat，看到 Helper v0.5.4 后再下载。`);
    }
    throw new Error("流媒体助手未启动。请运行 v0.5.4 的 install-autostart.bat 或 run-helper.bat。");
  }

  const res = await fetch(`${HELPER_BASE}/download`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) throw new Error(data.error || `本地助手返回 ${res.status}`);
  pollJob(data.job_id, kind);
  return data;
}

async function openStreamPage(item) {
  const params = new URLSearchParams({
    url: item.url,
    page: activeTab?.url || item.pageUrl || "",
    title: activeTab?.title || item.title || "视频",
    browser: browserName,
    type: item.type || "stream"
  });
  await chrome.tabs.create({ url: `${chrome.runtime.getURL("stream.html")}?${params.toString()}` });
}

async function openRecorder() {
  if (!activeTab?.id || !Number.isInteger(activeTab.id)) throw new Error("没有可录制的目标标签页。");
  if (!validPageUrl()) throw new Error("当前页面不能使用录制兜底模式。");
  const params = new URLSearchParams({
    tabId: String(activeTab.id),
    title: activeTab.title || "网页视频"
  });
  await chrome.tabs.create({ url: `${chrome.runtime.getURL("recorder.html")}?${params.toString()}` });
}

async function getDetectedMedia() {
  if (!activeTab?.id) return [];
  const response = await chrome.runtime.sendMessage({ type: "OMNIFETCH_GET_MEDIA", tabId: activeTab.id });
  return response?.items || [];
}

function renderSummary() {
  const video = bestVideoCandidate();
  const stream = bestStreamCandidate();
  const audio = bestAudioCandidate();
  const videoCount = currentMediaItems.filter((item) => VIDEO_TYPES.has(item.type)).length;
  const audioCount = currentMediaItems.filter((item) => AUDIO_TYPES.has(item.type)).length;

  if (stream) {
    videoHintEl.textContent = `${String(stream.type).toUpperCase()} 播放清单已捕获 · 点击后进入专用下载页，自动取最高画质`;
  } else if (video) {
    const size = formatBytes(video.contentLength);
    const type = String(video.type || "video").toUpperCase();
    videoHintEl.textContent = `${type}${size ? ` · ${size}` : ""} · 页面解析最高画质，捕获地址只作兜底`;
  } else {
    videoHintEl.textContent = "未捕获到完整视频地址；仍会直接解析当前页面的最高画质";
  }

  if (audio) {
    const size = formatBytes(audio.contentLength);
    audioHintEl.textContent = `${String(audio.type || "audio").toUpperCase()}${size ? ` · ${size}` : ""} · 单独下载最佳音轨`;
  } else {
    audioHintEl.textContent = "未看到独立音频直链；助手会从当前页面或播放清单提取最佳音频";
  }

  const summary = [];
  if (stream) summary.push(`播放清单 ${String(stream.type).toUpperCase()}`);
  else if (videoCount) summary.push(`视频候选 ${videoCount}`);
  if (audioCount) summary.push(`音频候选 ${audioCount}`);
  captureSummaryEl.textContent = summary.length
    ? `${summary.join(" · ")}；M4S/TS 播放分片已隐藏。`
    : "暂未识别完整媒体；可先播放几秒后重新检测。";

  downloadVideoBtn.disabled = !validPageUrl() && !video;
  downloadAudioBtn.disabled = !validPageUrl() && !audio && !stream;
}

async function rescan() {
  if (!activeTab?.id) return;
  refreshBtn.disabled = true;
  try {
    await chrome.tabs.sendMessage(activeTab.id, { type: "OMNIFETCH_RESCAN" }).catch(() => null);
    await sleep(320);
    currentMediaItems = await getDetectedMedia();
    renderSummary();
    if (helperOnline && !versionAtLeast(helperVersion, REQUIRED_HELPER)) {
      setStatus(`检测到旧后台助手 v${helperVersion}，请先升级到 v0.5.4。`);
    } else {
      setStatus(currentMediaItems.length ? "识别完成。优先使用 M3U8/DASH 播放清单，并复用浏览器实际请求环境。" : "还没抓到完整媒体，可继续播放几秒再试。");
    }
  } finally {
    refreshBtn.disabled = false;
  }
}

downloadVideoBtn.addEventListener("click", async () => {
  downloadVideoBtn.disabled = true;
  try {
    currentMediaItems = await getDetectedMedia();
    const stream = bestStreamCandidate();
    if (stream) {
      await openStreamPage(stream);
      setStatus("已打开流媒体专用下载页。它会携带浏览器请求头解析并下载播放清单。" );
      return;
    }
    const video = bestVideoCandidate();
    if (!validPageUrl()) throw new Error("当前页面没有可解析的视频页面地址。");
    const result = await helperDownload({
      page_url: activeTab.url,
      title: activeTab.title || "",
      browser: browserName,
      fallback_media_urls: fallbackUrls(),
      request_headers: requestHeadersFor(video),
      download_kind: "video"
    }, "video");
    setStatus(`最高画质视频任务已创建：${result.job_id}`);
  } catch (error) {
    setStatus(error.message || "视频下载失败");
  } finally {
    downloadVideoBtn.disabled = false;
  }
});

downloadAudioBtn.addEventListener("click", async () => {
  downloadAudioBtn.disabled = true;
  try {
    currentMediaItems = await getDetectedMedia();
    const audio = bestAudioCandidate();
    const stream = bestStreamCandidate();
    const source = audio || stream;
    if (!validPageUrl() && !source?.url) throw new Error("当前页面没有可提取的音频。");
    const result = await helperDownload({
      page_url: validPageUrl() ? activeTab.url : "",
      media_url: source?.url || "",
      title: activeTab?.title || "",
      browser: browserName,
      fallback_media_urls: fallbackUrls(),
      request_headers: requestHeadersFor(source),
      download_kind: "audio"
    }, "audio");
    setStatus(`最佳音频任务已创建：${result.job_id}`);
  } catch (error) {
    setStatus(error.message || "音频下载失败");
  } finally {
    downloadAudioBtn.disabled = false;
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
  currentMediaItems = [];
  renderSummary();
  setStatus("已清空当前页面识别记录。重新播放视频即可再次捕获。");
});

(async () => {
  activeTab = await getActiveTab();
  pageTitleEl.textContent = activeTab?.title || "当前页面";
  await checkHelper();
  await rescan();
})();
