const HELPER_BASE = "http://127.0.0.1:17891";
const REQUIRED_HELPER = [0, 6, 0];

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
let playbackWidth = 0;
let playbackHeight = 0;
let playbackDuration = 0;
let capturedCandidates = [];

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
  const bytes = Number(value || 0); if (!bytes) return "未知大小";
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${Math.round(bytes / 1024)} KB`;
}
function formatSpeed(value) { const speed = Number(value || 0); if (!speed) return ""; return speed >= 1024 ** 2 ? `${(speed / 1024 ** 2).toFixed(1)} MB/s` : `${Math.round(speed / 1024)} KB/s`; }
function streamMasterBonus(item) {
  const url = String(item?.url || "").toLowerCase();
  if (/\bmaster\.m3u8(?:[?#]|$)/i.test(url)) return 1000;
  if (/\bmanifest\.m3u8(?:[?#]|$)/i.test(url)) return 850;
  if (/\bmaster\.mpd(?:[?#]|$)/i.test(url) || /\bmanifest\.mpd(?:[?#]|$)/i.test(url)) return 800;
  if (url.includes("master")) return 500;
  return 0;
}
function qualityP(width, height, explicit = 0) { const q = Number(explicit || 0); if (q) return q; const w = Number(width || 0), h = Number(height || 0); return w && h ? Math.min(w, h) : h || w || 0; }
function qualityLabel(item) {
  if (!item) return "待解析"; const q = qualityP(item.width, item.height, item.quality_p);
  if (item.width && item.height) return `${q || item.height}P · ${item.width}×${item.height}`;
  return q ? `${q}P` : "最高可用";
}
function showEmpty(message) { const empty = document.createElement("div"); empty.className = "empty"; empty.textContent = String(message || ""); formatListEl.replaceChildren(empty); }
function formatDescription(item, kind) {
  const parts = [];
  if (item?.width && item?.height) parts.push(`${item.width}×${item.height}`);
  if (item?.tbr) parts.push(`${Math.round(item.tbr)} kbps`);
  if (item?.fps) parts.push(`${item.fps} fps`);
  if (item?.ext) parts.push(item.ext.toUpperCase());
  if (item?.protocol) parts.push(item.protocol);
  if (item?.filesize) parts.push(formatBytes(item.filesize));
  if (kind === "video" && item?.has_video && !item?.has_audio) parts.push("自动合并最佳音频");
  if (kind === "audio") parts.push("单独音频");
  return parts.join(" · ") || item?.format_note || item?.format_id || "自动选择最佳可用媒体";
}
function pickBestFormats(formats) {
  const list = Array.isArray(formats) ? formats : [];
  const videos = list.filter((item) => item?.has_video); const audios = list.filter((item) => item?.has_audio && !item?.has_video);
  bestVideoFormat = [...videos].sort((a, b) => qualityP(b.width, b.height, b.quality_p) - qualityP(a.width, a.height, a.quality_p) || Number(b.tbr || 0) - Number(a.tbr || 0))[0] || null;
  bestAudioFormat = [...audios].sort((a, b) => Number(b.tbr || 0) - Number(a.tbr || 0))[0] || null;
  if ((!bestVideoFormat || !bestVideoFormat.width || !bestVideoFormat.height) && playbackWidth && playbackHeight) {
    bestVideoFormat = { ...(bestVideoFormat || {}), format_id: bestVideoFormat?.format_id || "", ext: bestVideoFormat?.ext || "mp4", protocol: bestVideoFormat?.protocol || "m3u8", width: playbackWidth, height: playbackHeight, quality_p: qualityP(playbackWidth, playbackHeight), has_video: true, has_audio: Boolean(bestVideoFormat?.has_audio), format_note: bestVideoFormat?.format_note || "浏览器播放器实际分辨率" };
  }
  qualityStatusEl.textContent = qualityLabel(bestVideoFormat);
}
function createChoiceCard(kind, item) {
  const card = document.createElement("article"); card.className = "format-card";
  const quality = document.createElement("div"); quality.className = "quality"; quality.textContent = kind === "video" ? `最高画质视频${qualityP(item?.width, item?.height, item?.quality_p) ? ` · ${qualityP(item?.width, item?.height, item?.quality_p)}P` : ""}` : "最佳音频";
  const meta = document.createElement("div"); meta.className = "format-meta"; meta.textContent = formatDescription(item, kind);
  const btn = document.createElement("button"); btn.className = "format-download"; btn.textContent = kind === "video" ? "下载视频" : "下载音频";
  btn.addEventListener("click", async () => { btn.disabled = true; try { await startDownload(kind, item?.format_id || ""); } finally { btn.disabled = false; } });
  card.append(quality, meta, btn); return card;
}
function renderFormats(formats) { pickBestFormats(formats); formatListEl.replaceChildren(createChoiceCard("video", bestVideoFormat), createChoiceCard("audio", bestAudioFormat)); formatCountEl.textContent = "2"; bestBtn.disabled = false; audioBtn.disabled = false; }
function setProgress(percent, title, meta = "") { progressPanelEl.classList.remove("hidden"); const value = Math.max(0, Math.min(100, Number(percent || 0))); progressBarEl.style.width = `${value}%`; progressPercentEl.textContent = `${value.toFixed(value % 1 ? 1 : 0)}%`; progressTitleEl.textContent = title; progressMetaEl.textContent = meta; }

function candidatePayload() {
  return capturedCandidates.slice(0, 20).map((item) => ({
    url: item.url, type: item.type, score: Number(item.score || 0), contentLength: Number(item.contentLength || 0), detectedAt: Number(item.detectedAt || 0),
    requestHeaders: item.requestHeaders || {}, playbackWidth: Number(item.playbackWidth || playbackWidth || 0), playbackHeight: Number(item.playbackHeight || playbackHeight || 0), playbackDuration: Number(item.playbackDuration || playbackDuration || 0)
  }));
}

async function resolvePreferredCapturedStream() {
  if (!Number.isInteger(sourceTabId)) return mediaUrl;
  try {
    const response = await chrome.runtime.sendMessage({ type: "OMNIFETCH_GET_MEDIA", tabId: sourceTabId });
    playbackWidth = Number(response?.playbackWidth || 0); playbackHeight = Number(response?.playbackHeight || 0); playbackDuration = Number(response?.playbackDuration || 0);
    const expected = streamKind === "DASH" ? "dash" : "hls";
    capturedCandidates = (response?.items || []).filter((item) => item?.type === expected && item?.url);
    capturedCandidates.sort((a, b) => (streamMasterBonus(b) + Number(b.score || 0)) - (streamMasterBonus(a) + Number(a.score || 0)) || Number(b.detectedAt || 0) - Number(a.detectedAt || 0));
    if (!capturedCandidates.length) return mediaUrl;
    mediaUrl = capturedCandidates[0].url;
    playbackWidth = Number(capturedCandidates[0].playbackWidth || playbackWidth || 0); playbackHeight = Number(capturedCandidates[0].playbackHeight || playbackHeight || 0); playbackDuration = Number(capturedCandidates[0].playbackDuration || playbackDuration || 0);
    sourceEl.textContent = mediaUrl; sourceEl.title = mediaUrl;
  } catch (_) {}
  return mediaUrl;
}
async function loadRequestContext() {
  try { const response = await chrome.runtime.sendMessage({ type: "OMNIFETCH_GET_REQUEST_CONTEXT", url: mediaUrl, tabId: sourceTabId }); requestContext = response?.headers || {}; } catch (_) { requestContext = {}; }
  return requestContext;
}
async function checkHelper() {
  const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 1200);
  try {
    const res = await fetch(`${HELPER_BASE}/health`, { signal: controller.signal }); const data = await res.json().catch(() => ({})); helperOnline = res.ok && data.ok; helperVersion = helperOnline ? String(data.version || "") : "";
    const ready = helperOnline && versionAtLeast(helperVersion, REQUIRED_HELPER); helperBadgeEl.textContent = helperOnline ? `助手 v${helperVersion}${ready ? "" : " · 版本过旧"}` : "助手未启动"; helperBadgeEl.className = `badge ${ready ? "ok" : "bad"}`; return ready;
  } catch (_) { helperOnline = false; helperVersion = ""; helperBadgeEl.textContent = "助手未启动"; helperBadgeEl.className = "badge bad"; return false; } finally { clearTimeout(timer); }
}

async function probe() {
  retryBtn.disabled = true; bestBtn.disabled = true; audioBtn.disabled = true; probeStatusEl.textContent = "正在分析"; qualityStatusEl.textContent = "正在解析"; formatCountEl.textContent = "0"; showEmpty("正在对比已捕获播放清单并解析最高画质…");
  await resolvePreferredCapturedStream();
  if (!mediaUrl) { probeStatusEl.textContent = "缺少地址"; qualityStatusEl.textContent = "未知"; showEmpty("没有收到播放清单地址，请回到视频页面重新捕获。"); retryBtn.disabled = false; return; }
  if (!(await checkHelper())) { probeStatusEl.textContent = helperOnline ? "助手版本过旧" : "助手未启动"; qualityStatusEl.textContent = "不可用"; showEmpty(helperOnline ? `当前助手 v${helperVersion}，请升级到 v0.6.0。` : "请先启动 v0.6.0 本地助手。"); retryBtn.disabled = false; return; }
  await loadRequestContext();
  try {
    const res = await fetch(`${HELPER_BASE}/probe`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ media_url: mediaUrl, page_url: pageUrl, title: titleHint, browser: browserName, request_headers: requestContext, media_candidates: candidatePayload(), playback_width: playbackWidth, playback_height: playbackHeight, playback_duration: playbackDuration }) });
    const data = await res.json().catch(() => ({})); if (!res.ok || !data.ok) throw new Error(data.error || `分析失败 (${res.status})`);
    probeData = data; titleEl.textContent = data.title || titleHint || "流媒体"; platformEl.textContent = data.platform || "通用网页"; probeStatusEl.textContent = data.is_live ? "直播流" : `分析完成 · ${data.candidate_count || capturedCandidates.length || 1} 条候选`; renderFormats(data.formats || []);
  } catch (error) { probeStatusEl.textContent = "分析失败"; qualityStatusEl.textContent = playbackWidth && playbackHeight ? `${qualityP(playbackWidth, playbackHeight)}P · ${playbackWidth}×${playbackHeight}` : "未知"; showEmpty(error.message || error); bestBtn.disabled = false; audioBtn.disabled = false; }
  finally { retryBtn.disabled = false; }
}

async function startDownload(kind = "video", formatId = "") {
  if (!(await checkHelper())) { setProgress(0, "无法开始下载", helperOnline ? `请升级助手到 v0.6.0，当前 v${helperVersion}` : "请先启动 v0.6.0 本地助手。"); return; }
  await resolvePreferredCapturedStream(); await loadRequestContext();
  const initialQuality = kind === "video" ? qualityLabel(bestVideoFormat) : "最佳音频";
  setProgress(0, kind === "audio" ? "正在创建音频任务" : `正在创建稳定下载任务 · ${initialQuality}`, capturedCandidates.length > 1 ? `已准备 ${capturedCandidates.length} 条播放清单，失败会自动换源` : `当前目标画质：${initialQuality}`);
  try {
    const payload = { media_url: mediaUrl, page_url: pageUrl, title: probeData?.title || titleHint, browser: browserName, download_kind: kind, request_headers: requestContext, media_candidates: candidatePayload(), fallback_media_urls: capturedCandidates.map((i) => i.url).slice(0, 12), playback_width: playbackWidth, playback_height: playbackHeight, playback_duration: playbackDuration };
    if (kind === "video" && formatId) payload.format_id = formatId;
    const res = await fetch(`${HELPER_BASE}/download`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }); const data = await res.json().catch(() => ({})); if (!res.ok || !data.ok) throw new Error(data.error || `创建任务失败 (${res.status})`); await pollJob(data.job_id, kind);
  } catch (error) { setProgress(0, "下载失败", String(error.message || error)); }
}

async function pollJob(jobId, kind) {
  for (let attempt = 0; attempt < 1200; attempt += 1) {
    await sleep(1000);
    try {
      const res = await fetch(`${HELPER_BASE}/jobs/${jobId}`); const data = await res.json().catch(() => ({})); const job = data?.job; if (!res.ok || !data.ok || !job) continue;
      const selectedHeight = Number(job.selected_height || bestVideoFormat?.height || playbackHeight || 0); const selectedWidth = Number(job.selected_width || bestVideoFormat?.width || playbackWidth || 0); const selectedP = Number(job.selected_quality_p || qualityP(selectedWidth, selectedHeight));
      const selectedQuality = kind === "video" ? (selectedP ? `${selectedP}P${selectedWidth && selectedHeight ? ` · ${selectedWidth}×${selectedHeight}` : ""}` : "最高可用") : "最佳音频"; if (kind === "video") qualityStatusEl.textContent = selectedQuality;
      if (job.status === "completed") { const meta = [kind === "video" ? `画质 ${selectedQuality}` : "最佳音频", "已保存到 Downloads\\OmniFetch"]; if (job.output_bytes) meta.unshift(formatBytes(job.output_bytes)); if (job.output_duration) meta.push(`时长 ${Math.round(job.output_duration)}秒`); setProgress(100, `${kind === "audio" ? "音频" : "视频"}下载完成`, meta.join(" · ")); return; }
      if (job.status === "failed") { setProgress(Number(job.percent || 0), "下载失败", job.error || "未知错误"); return; }
      const labels = { queued: "等待下载", starting: "准备下载", resolving: "正在选择最佳候选", retrying: "当前候选失败，自动切换下一条", downloading: kind === "audio" ? "正在下载音频" : `正在下载 · ${selectedQuality}`, processing: "正在校验时长并封装" };
      const pieces = [kind === "video" ? `画质 ${selectedQuality}` : "最佳音频"];
      const speed = formatSpeed(job.speed); if (speed) pieces.push(speed); if (job.attempt_total) pieces.push(`候选 ${job.attempt || 1}/${job.attempt_total}`); if (job.fragment_index && job.fragment_count) pieces.push(`分片 ${job.fragment_index}/${job.fragment_count}`); if (job.downloaded_bytes) pieces.push(`${formatBytes(job.downloaded_bytes)} 已下载`);
      setProgress(Number(job.percent || 0), labels[job.status] || job.status || "下载中", pieces.join(" · "));
    } catch (_) {}
  }
  setProgress(0, "任务仍在运行", "下载时间较长，请保持本地助手运行。");
}

bestBtn.addEventListener("click", () => startDownload("video", bestVideoFormat?.format_id || ""));
audioBtn.addEventListener("click", () => startDownload("audio", bestAudioFormat?.format_id || ""));
retryBtn.addEventListener("click", probe);
copyBtn.addEventListener("click", async () => { try { await navigator.clipboard.writeText(mediaUrl); probeStatusEl.textContent = "地址已复制"; } catch (_) { probeStatusEl.textContent = "复制失败"; } });

streamTypeEl.textContent = streamKind; sourceEl.textContent = mediaUrl; sourceEl.title = mediaUrl; titleEl.textContent = titleHint || "流媒体"; probe();
