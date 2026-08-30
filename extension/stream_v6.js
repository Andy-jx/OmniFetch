const HELPER_BASE = "http://127.0.0.1:17891";
const REQUIRED_HELPER = [0, 5, 6];

const params = new URLSearchParams(location.search);
let mediaUrl = params.get("url") || "";
const pageUrl = params.get("page") || "";
const titleHint = params.get("title") || "视频";
const browserName = params.get("browser") || "chrome";
const streamKind = (params.get("type") || "stream").toUpperCase();
const parsedTabId = Number.parseInt(params.get("tabId") || "", 10);
const sourceTabId = Number.isInteger(parsedTabId) ? parsedTabId : null;

const titleEl = document.getElementById("title");
const sourceEl = document.getElementById("source");
const helperBadgeEl = document.getElementById("helperBadge");
const streamTypeEl = document.getElementById("streamType");
const platformEl = document.getElementById("platform");
const probeStatusEl = document.getElementById("probeStatus");
const qualityStatusEl = document.getElementById("qualityStatus");
const bestBtn = document.getElementById("bestBtn");
const audioBtn = document.getElementById("audioBtn");
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
let helperVersion = "";
let probeData = null;
let bestVideoFormat = null;
let bestAudioFormat = null;
let requestContext = {};

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
  if (!bytes) return "未知大小";
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${Math.round(bytes / 1024)} KB`;
}
function formatSpeed(value) {
  const speed = Number(value || 0);
  if (!speed) return "";
  if (speed >= 1024 ** 2) return `${(speed / 1024 ** 2).toFixed(1)} MB/s`;
  return `${Math.round(speed / 1024)} KB/s`;
}
function streamMasterBonus(item) {
  const url = String(item?.url || "").toLowerCase();
  if (/\bmaster\.m3u8(?:[?#]|$)/i.test(url)) return 1000;
  if (/\bmanifest\.m3u8(?:[?#]|$)/i.test(url)) return 850;
  if (/\bmaster\.mpd(?:[?#]|$)/i.test(url) || /\bmanifest\.mpd(?:[?#]|$)/i.test(url)) return 800;
  if (url.includes("master")) return 500;
  return 0;
}
function qualityLabel(item) {
  if (!item) return "待解析";
  if (item.width && item.height) return `${item.height}P · ${item.width}×${item.height}`;
  if (item.height) return `${item.height}P`;
  return "最高可用";
}
function showEmpty(message) {
  const empty = document.createElement("div");
  empty.className = "empty";
  empty.textContent = String(message || "");
  formatListEl.replaceChildren(empty);
}
function formatDescription(item, kind) {
  const parts = [];
  if (item.width && item.height) parts.push(`${item.width}×${item.height}`);
  if (item.tbr) parts.push(`${Math.round(item.tbr)} kbps`);
  if (item.fps) parts.push(`${item.fps} fps`);
  if (item.ext) parts.push(item.ext.toUpperCase());
  if (item.protocol) parts.push(item.protocol);
  if (item.filesize) parts.push(formatBytes(item.filesize));
  if (kind === "video" && item.has_video && !item.has_audio) parts.push("自动合并最佳音频");
  if (kind === "audio") parts.push("单独音频");
  return parts.join(" · ") || item.format_note || item.format_id;
}

function pickBestFormats(formats) {
  const list = Array.isArray(formats) ? formats : [];
  const videos = list.filter((item) => item?.has_video);
  const audios = list.filter((item) => item?.has_audio && !item?.has_video);
  bestVideoFormat = [...videos].sort((a, b) => Number(b.height || 0) - Number(a.height || 0) || Number(b.tbr || 0) - Number(a.tbr || 0) || Number(b.filesize || 0) - Number(a.filesize || 0))[0] || null;
  bestAudioFormat = [...audios].sort((a, b) => Number(b.tbr || 0) - Number(a.tbr || 0) || Number(b.filesize || 0) - Number(a.filesize || 0))[0] || null;
  qualityStatusEl.textContent = qualityLabel(bestVideoFormat);
}
function createChoiceCard(kind, item) {
  const card = document.createElement("article"); card.className = "format-card";
  const quality = document.createElement("div"); quality.className = "quality";
  quality.textContent = kind === "video" ? `最高画质视频${item?.height ? ` · ${item.height}P` : ""}` : "最佳音频";
  const meta = document.createElement("div"); meta.className = "format-meta";
  meta.textContent = item ? formatDescription(item, kind) : kind === "video" ? "未拿到独立清晰度信息，将自动选择最高画质" : "未拿到独立音轨信息，将尝试提取最佳音频";
  const btn = document.createElement("button"); btn.className = "format-download"; btn.textContent = kind === "video" ? "下载视频" : "下载音频";
  btn.addEventListener("click", async () => { btn.disabled = true; try { await startDownload(kind, item?.format_id || ""); } finally { btn.disabled = false; } });
  card.append(quality, meta, btn); return card;
}
function renderFormats(formats) {
  pickBestFormats(formats);
  formatListEl.replaceChildren(createChoiceCard("video", bestVideoFormat), createChoiceCard("audio", bestAudioFormat));
  formatCountEl.textContent = "2"; bestBtn.disabled = false; audioBtn.disabled = false;
}
function setProgress(percent, title, meta = "") {
  progressPanelEl.classList.remove("hidden");
  const value = Math.max(0, Math.min(100, Number(percent || 0)));
  progressBarEl.style.width = `${value}%`; progressPercentEl.textContent = `${value.toFixed(value % 1 ? 1 : 0)}%`;
  progressTitleEl.textContent = title; progressMetaEl.textContent = meta;
}

async function resolvePreferredCapturedStream() {
  if (!Number.isInteger(sourceTabId)) return mediaUrl;
  try {
    const response = await chrome.runtime.sendMessage({ type: "OMNIFETCH_GET_MEDIA", tabId: sourceTabId });
    const expected = streamKind === "DASH" ? "dash" : "hls";
    const candidates = (response?.items || []).filter((item) => item?.type === expected && item?.url);
    if (!candidates.length) return mediaUrl;
    candidates.sort((a, b) => (streamMasterBonus(b) + Number(b.score || 0)) - (streamMasterBonus(a) + Number(a.score || 0)));
    mediaUrl = candidates[0].url;
    sourceEl.textContent = mediaUrl; sourceEl.title = mediaUrl;
  } catch (_) {}
  return mediaUrl;
}
async function loadRequestContext() {
  try {
    const response = await chrome.runtime.sendMessage({ type: "OMNIFETCH_GET_REQUEST_CONTEXT", url: mediaUrl, tabId: sourceTabId });
    requestContext = response?.headers || {};
  } catch (_) { requestContext = {}; }
  return requestContext;
}
async function checkHelper() {
  const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 1200);
  try {
    const res = await fetch(`${HELPER_BASE}/health`, { signal: controller.signal }); const data = await res.json().catch(() => ({}));
    helperOnline = res.ok && data.ok; helperVersion = helperOnline ? String(data.version || "") : "";
    const ready = helperOnline && versionAtLeast(helperVersion, REQUIRED_HELPER);
    helperBadgeEl.textContent = helperOnline ? `助手 v${helperVersion}${ready ? "" : " · 版本过旧"}` : "助手未启动";
    helperBadgeEl.className = `badge ${ready ? "ok" : "bad"}`; return ready;
  } catch (_) { helperOnline = false; helperVersion = ""; helperBadgeEl.textContent = "助手未启动"; helperBadgeEl.className = "badge bad"; return false; }
  finally { clearTimeout(timer); }
}

async function probe() {
  retryBtn.disabled = true; bestBtn.disabled = true; audioBtn.disabled = true;
  probeStatusEl.textContent = "正在分析"; qualityStatusEl.textContent = "正在解析"; formatCountEl.textContent = "0";
  showEmpty("正在定位主播放清单并解析最高画质…");
  await resolvePreferredCapturedStream();
  if (!mediaUrl) { probeStatusEl.textContent = "缺少地址"; qualityStatusEl.textContent = "未知"; showEmpty("没有收到播放清单地址，请回到视频页面重新捕获。"); retryBtn.disabled = false; return; }
  if (!(await checkHelper())) { probeStatusEl.textContent = helperOnline ? "助手版本过旧" : "助手未启动"; qualityStatusEl.textContent = "不可用"; showEmpty(helperOnline ? `当前助手 v${helperVersion}，请升级到 v0.5.6。` : "请先启动 v0.5.6 本地助手。"); retryBtn.disabled = false; return; }
  await loadRequestContext();
  try {
    const res = await fetch(`${HELPER_BASE}/probe`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ media_url: mediaUrl, page_url: pageUrl, title: titleHint, browser: browserName, request_headers: requestContext }) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) throw new Error(data.error || `分析失败 (${res.status})`);
    probeData = data; titleEl.textContent = data.title || titleHint || "流媒体"; platformEl.textContent = data.platform || "通用网页";
    probeStatusEl.textContent = data.is_live ? "直播流" : "分析完成"; renderFormats(data.formats || []);
  } catch (error) { probeStatusEl.textContent = "分析失败"; qualityStatusEl.textContent = "未知"; showEmpty(error.message || error); bestBtn.disabled = false; audioBtn.disabled = false; }
  finally { retryBtn.disabled = false; }
}

async function startDownload(kind = "video", formatId = "") {
  if (!(await checkHelper())) { setProgress(0, "无法开始下载", helperOnline ? `请升级助手到 v0.5.6，当前 v${helperVersion}` : "请先启动 v0.5.6 本地助手。"); return; }
  await resolvePreferredCapturedStream();
  if (!Object.keys(requestContext).length) await loadRequestContext();
  const initialQuality = kind === "video" ? qualityLabel(bestVideoFormat) : "最佳音频";
  setProgress(0, kind === "audio" ? "正在创建音频任务" : `正在创建视频任务 · ${initialQuality}`, formatId ? `格式：${formatId}` : kind === "audio" ? "自动选择最佳音频" : `当前目标画质：${initialQuality}`);
  try {
    const payload = { media_url: mediaUrl, page_url: pageUrl, title: probeData?.title || titleHint, browser: browserName, download_kind: kind, request_headers: requestContext };
    if (kind === "video" && formatId) payload.format_id = formatId;
    const res = await fetch(`${HELPER_BASE}/download`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) throw new Error(data.error || `创建任务失败 (${res.status})`);
    await pollJob(data.job_id, kind);
  } catch (error) { setProgress(0, "下载失败", String(error.message || error)); }
}

async function pollJob(jobId, kind) {
  for (let attempt = 0; attempt < 720; attempt += 1) {
    await sleep(1000);
    try {
      const res = await fetch(`${HELPER_BASE}/jobs/${jobId}`); const data = await res.json().catch(() => ({}));
      const job = data?.job; if (!res.ok || !data.ok || !job) continue;
      const selectedHeight = Number(job.selected_height || bestVideoFormat?.height || 0);
      const selectedWidth = Number(job.selected_width || bestVideoFormat?.width || 0);
      const selectedQuality = kind === "video" ? (selectedHeight ? `${selectedHeight}P${selectedWidth ? ` · ${selectedWidth}×${selectedHeight}` : ""}` : "最高可用") : "最佳音频";
      if (kind === "video") qualityStatusEl.textContent = selectedQuality;
      if (job.status === "completed") {
        const meta = [kind === "video" ? `画质 ${selectedQuality}` : "最佳音频", "已保存到 Downloads\\OmniFetch"];
        if (job.output_bytes) meta.unshift(formatBytes(job.output_bytes));
        setProgress(100, `${kind === "audio" ? "音频" : "视频"}下载完成`, meta.join(" · ")); return;
      }
      if (job.status === "failed") { setProgress(Number(job.percent || 0), "下载失败", job.error || "未知错误"); return; }
      const labels = { queued: "等待下载", starting: "准备下载", resolving: kind === "audio" ? "正在解析最佳音频" : "正在解析最高画质", retrying: "当前结果无效，正在自动切换下载策略", downloading: kind === "audio" ? "正在下载音频" : `正在下载 · ${selectedQuality}`, processing: kind === "audio" ? "正在生成音频文件" : `正在合并音视频 · ${selectedQuality}` };
      const pieces = [kind === "video" ? `画质 ${selectedQuality}` : "最佳音频"];
      const speed = formatSpeed(job.speed); if (speed) pieces.push(speed);
      if (Number.isFinite(job.eta)) pieces.push(`剩余约 ${job.eta}s`);
      if (job.fragment_index && job.fragment_count) pieces.push(`分片 ${job.fragment_index}/${job.fragment_count}`);
      if (job.downloaded_bytes) pieces.push(`${formatBytes(job.downloaded_bytes)} 已下载`);
      setProgress(Number(job.percent || 0), labels[job.status] || job.status || "下载中", pieces.join(" · "));
    } catch (_) {}
  }
  setProgress(0, "任务仍在运行", "下载时间较长，请保持本地助手运行。");
}

bestBtn.addEventListener("click", () => startDownload("video", bestVideoFormat?.format_id || ""));
audioBtn.addEventListener("click", () => startDownload("audio", bestAudioFormat?.format_id || ""));
retryBtn.addEventListener("click", probe);
copyBtn.addEventListener("click", async () => { try { await navigator.clipboard.writeText(mediaUrl); probeStatusEl.textContent = "地址已复制"; } catch (_) { probeStatusEl.textContent = "复制失败"; } });

streamTypeEl.textContent = streamKind; sourceEl.textContent = mediaUrl; sourceEl.title = mediaUrl; titleEl.textContent = titleHint || "流媒体";
probe();
