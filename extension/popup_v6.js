const HELPER_BASE = "http://127.0.0.1:17891";
const REQUIRED_HELPER = [0, 5, 6];

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

function setStatus(text) { statusTextEl.textContent = text; }
function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }
function parseVersion(value) { return String(value || "").split(".").slice(0, 3).map((p) => Number.parseInt(p, 10) || 0); }
function versionAtLeast(value, minimum) {
  const current = parseVersion(value);
  for (let i = 0; i < minimum.length; i += 1) {
    if ((current[i] || 0) > minimum[i]) return true;
    if ((current[i] || 0) < minimum[i]) return false;
  }
  return true;
}
function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!bytes) return "";
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${Math.round(bytes / 1024)} KB`;
}

function streamMasterBonus(item) {
  const url = String(item?.url || "").toLowerCase();
  if (/\bmaster\.m3u8(?:[?#]|$)/i.test(url)) return 1000;
  if (/\bmanifest\.m3u8(?:[?#]|$)/i.test(url)) return 850;
  if (/\bmaster\.mpd(?:[?#]|$)/i.test(url) || /\bmanifest\.mpd(?:[?#]|$)/i.test(url)) return 800;
  if (url.includes("master")) return 500;
  return 0;
}

function bestByScore(items, extra = () => 0) {
  return [...items].sort((a, b) => {
    const diff = (Number(b.score || 0) + extra(b)) - (Number(a.score || 0) + extra(a));
    if (diff) return diff;
    return Number(b.contentLength || 0) - Number(a.contentLength || 0);
  })[0] || null;
}
function bestStreamCandidate() { return bestByScore(currentMediaItems.filter((i) => STREAM_TYPES.has(i.type)), streamMasterBonus); }
function bestVideoCandidate() { return bestStreamCandidate() || bestByScore(currentMediaItems.filter((i) => VIDEO_TYPES.has(i.type))); }
function bestAudioCandidate() { return bestByScore(currentMediaItems.filter((i) => AUDIO_TYPES.has(i.type))); }
function requestHeadersFor(item) { return item?.requestHeaders && typeof item.requestHeaders === "object" ? item.requestHeaders : {}; }
function fallbackUrls() {
  return [...new Set([...currentMediaItems]
    .filter((i) => VIDEO_TYPES.has(i.type) || AUDIO_TYPES.has(i.type))
    .sort((a, b) => (streamMasterBonus(b) + Number(b.score || 0)) - (streamMasterBonus(a) + Number(a.score || 0)))
    .map((i) => i.url).filter(Boolean))].slice(0, 12);
}
function validPageUrl() { return Boolean(activeTab?.url && /^https?:/i.test(activeTab.url)); }
async function getActiveTab() { return (await chrome.tabs.query({ active: true, currentWindow: true }))[0] || null; }
async function getDetectedMedia() {
  if (!activeTab?.id) return [];
  const r = await chrome.runtime.sendMessage({ type: "OMNIFETCH_GET_MEDIA", tabId: activeTab.id });
  return r?.items || [];
}

async function checkHelper() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 1000);
  try {
    const res = await fetch(`${HELPER_BASE}/health`, { signal: controller.signal });
    const data = await res.json().catch(() => ({}));
    helperOnline = res.ok && data.ok;
    helperVersion = helperOnline ? String(data.version || "") : "";
    const ready = helperOnline && versionAtLeast(helperVersion, REQUIRED_HELPER);
    helperBadgeEl.textContent = helperOnline ? `流媒体助手 v${helperVersion || "?"}${ready ? "" : " · 版本过旧"}` : "流媒体助手未启动";
    helperBadgeEl.className = `badge ${ready ? "ok" : "bad"}`;
    return ready;
  } catch (_) {
    helperOnline = false; helperVersion = "";
    helperBadgeEl.textContent = "流媒体助手未启动"; helperBadgeEl.className = "badge bad";
    return false;
  } finally { clearTimeout(timer); }
}

async function pollJob(jobId, kind) {
  for (let attempt = 0; attempt < 720; attempt += 1) {
    await sleep(1000);
    try {
      const res = await fetch(`${HELPER_BASE}/jobs/${jobId}`);
      const data = await res.json().catch(() => ({}));
      const job = data?.job;
      if (!res.ok || !data.ok || !job) continue;
      if (job.status === "completed") {
        const q = job.selected_height ? ` · ${job.selected_height}P` : "";
        const size = job.output_bytes ? ` · ${(job.output_bytes / 1024 / 1024).toFixed(1)} MB` : "";
        setStatus(`${kind === "audio" ? "音频" : "视频"}下载完成：${job.title || "文件"}${q}${size}`);
        return;
      }
      if (job.status === "failed") { setStatus(`下载失败：${job.error || "未知错误"}`); return; }
      const q = job.selected_height ? ` · 当前画质 ${job.selected_height}P` : "";
      const p = Number.isFinite(job.percent) ? ` ${job.percent}%` : "";
      const f = Number.isFinite(job.fragment_index) && Number.isFinite(job.fragment_count) ? ` · 分片 ${job.fragment_index}/${job.fragment_count}` : "";
      const labels = { queued: "等待中", starting: "准备下载", resolving: kind === "audio" ? "正在提取最佳音频" : "正在解析最高画质", retrying: "正在自动换下载策略", downloading: "正在下载", processing: kind === "audio" ? "正在生成音频文件" : "正在合并音视频" };
      setStatus(`${labels[job.status] || job.status}${q}${p}${f}`);
    } catch (_) {}
  }
}

async function helperDownload(payload, kind = "video") {
  if (!(await checkHelper())) throw new Error(helperOnline ? `后台助手是 v${helperVersion}，请升级到 v0.5.6。` : "请先启动 v0.5.6 本地助手。");
  const res = await fetch(`${HELPER_BASE}/download`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) throw new Error(data.error || `本地助手返回 ${res.status}`);
  pollJob(data.job_id, kind);
  return data;
}

async function openStreamPage(item) {
  const params = new URLSearchParams({ url: item.url, page: activeTab?.url || item.pageUrl || "", title: activeTab?.title || item.title || "视频", browser: browserName, type: item.type || "stream", tabId: String(activeTab?.id || "") });
  await chrome.tabs.create({ url: `${chrome.runtime.getURL("stream.html")}?${params.toString()}` });
}
async function openRecorder() {
  if (!activeTab?.id || !validPageUrl()) throw new Error("当前页面不能使用录制兜底模式。");
  const params = new URLSearchParams({ tabId: String(activeTab.id), title: activeTab.title || "网页视频" });
  await chrome.tabs.create({ url: `${chrome.runtime.getURL("recorder.html")}?${params.toString()}` });
}

function renderSummary() {
  const video = bestVideoCandidate();
  const stream = bestStreamCandidate();
  const audio = bestAudioCandidate();
  if (stream) {
    const isMaster = streamMasterBonus(stream) >= 500;
    videoHintEl.textContent = `${String(stream.type).toUpperCase()}${isMaster ? " 主播放清单" : " 播放清单"}已捕获 · 下载时选择最高分辨率`;
  } else if (video) {
    const size = formatBytes(video.contentLength);
    videoHintEl.textContent = `${String(video.type || "video").toUpperCase()}${size ? ` · ${size}` : ""} · 将优先尝试浏览器实际播放地址`;
  } else videoHintEl.textContent = "请先播放视频 1–3 秒，再进行识别";

  audioHintEl.textContent = audio ? `${String(audio.type || "audio").toUpperCase()}${formatBytes(audio.contentLength) ? ` · ${formatBytes(audio.contentLength)}` : ""} · 单独下载最佳音轨` : "播放后如存在独立音轨，会自动提取最佳音频";
  const summary = [];
  if (stream) summary.push(streamMasterBonus(stream) ? `主播放清单 ${String(stream.type).toUpperCase()}` : `播放清单 ${String(stream.type).toUpperCase()}`);
  if (!stream && video) summary.push("视频已捕获");
  if (audio) summary.push("音频已捕获");
  captureSummaryEl.textContent = summary.length ? `${summary.join(" · ")}；M4S/TS 分片已隐藏。` : "尚未确认视频播放。播放 1–3 秒后才显示识别结果。";
  downloadVideoBtn.disabled = !validPageUrl() && !video;
  downloadAudioBtn.disabled = !validPageUrl() && !audio && !stream;
}

async function rescan() {
  if (!activeTab?.id) return;
  refreshBtn.disabled = true; setStatus("正在清空旧记录并重新识别…");
  try {
    const response = await chrome.tabs.sendMessage(activeTab.id, { type: "OMNIFETCH_RESCAN" }).catch(() => null);
    const waiting = Boolean(response?.waitingForPlayback);
    await sleep(waiting ? 350 : 1600);
    currentMediaItems = await getDetectedMedia(); renderSummary();
    setStatus(currentMediaItems.length ? "重新识别完成。" : waiting ? "已清空。现在播放视频 1–3 秒，再打开 OmniFetch。" : "暂未抓到完整媒体，继续播放几秒再试。");
  } finally { refreshBtn.disabled = false; }
}

downloadVideoBtn.addEventListener("click", async () => {
  downloadVideoBtn.disabled = true;
  try {
    currentMediaItems = await getDetectedMedia();
    const stream = bestStreamCandidate();
    if (stream) { await openStreamPage(stream); setStatus("已打开流媒体下载页，将显示并下载当前最高画质。" ); return; }
    const video = bestVideoCandidate();
    if (!validPageUrl() && !video?.url) throw new Error("当前页面没有可解析的视频地址。");
    const result = await helperDownload({
      page_url: validPageUrl() ? activeTab.url : "",
      media_url: video?.url || "",
      title: activeTab?.title || "",
      browser: browserName,
      fallback_media_urls: fallbackUrls(),
      request_headers: requestHeadersFor(video),
      download_kind: "video"
    }, "video");
    setStatus(`最高画质视频任务已创建：${result.job_id}`);
  } catch (error) { setStatus(error.message || "视频下载失败"); }
  finally { downloadVideoBtn.disabled = false; }
});

downloadAudioBtn.addEventListener("click", async () => {
  downloadAudioBtn.disabled = true;
  try {
    currentMediaItems = await getDetectedMedia();
    const source = bestAudioCandidate() || bestStreamCandidate();
    if (!validPageUrl() && !source?.url) throw new Error("当前页面没有可提取的音频。");
    const result = await helperDownload({ page_url: validPageUrl() ? activeTab.url : "", media_url: source?.url || "", title: activeTab?.title || "", browser: browserName, fallback_media_urls: fallbackUrls(), request_headers: requestHeadersFor(source), download_kind: "audio" }, "audio");
    setStatus(`最佳音频任务已创建：${result.job_id}`);
  } catch (error) { setStatus(error.message || "音频下载失败"); }
  finally { downloadAudioBtn.disabled = false; }
});

recordModeBtn.addEventListener("click", async () => { try { await openRecorder(); setStatus("已打开录制兜底页。" ); } catch (e) { setStatus(e.message || "无法打开录制模式"); } });
refreshBtn.addEventListener("click", rescan);
clearBtn.addEventListener("click", async () => { if (!activeTab?.id) return; await chrome.runtime.sendMessage({ type: "OMNIFETCH_CLEAR_MEDIA", tabId: activeTab.id }); currentMediaItems = []; renderSummary(); setStatus("已清空当前页面识别记录。重新播放 1–3 秒即可再次捕获。"); });

(async () => {
  activeTab = await getActiveTab();
  pageTitleEl.textContent = activeTab?.title || "当前页面";
  await checkHelper();
  currentMediaItems = await getDetectedMedia();
  renderSummary();
  setStatus(currentMediaItems.length ? "已读取当前视频播放后的识别结果。" : "请先播放视频 1–3 秒。未实际播放前不会显示识别结果。");
})();
