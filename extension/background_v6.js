const MEDIA_BY_TAB = new Map();
const TAB_STATE = new Map();

const MEDIA_EXTENSIONS = [".mp4", ".webm", ".m3u8", ".mpd", ".mov", ".m4v", ".ts", ".m4s", ".flv", ".mp3", ".m4a", ".aac"];
const AD_MARKERS = ["doubleclick", "googleads", "adservice", "/ads/", "/ad/", "preroll", "pre-roll", "vast", "vpaid", "tracking"];
const REQUEST_HEADER_NAMES = new Map([
  ["referer", "Referer"], ["origin", "Origin"], ["user-agent", "User-Agent"],
  ["cookie", "Cookie"], ["authorization", "Authorization"], ["accept", "Accept"],
  ["accept-language", "Accept-Language"]
]);

function cleanPath(url) {
  return String(url || "").toLowerCase().split("?")[0].split("#")[0];
}

function hostOf(url) {
  try { return new URL(url).hostname.toLowerCase(); } catch (_) { return ""; }
}

function looksLikeDouyinVideo(url) {
  const lower = String(url || "").toLowerCase();
  const host = hostOf(url);
  return host.endsWith("douyinvod.com") || host.includes("douyin-vod") ||
    lower.includes("/aweme/v1/play") || lower.includes("video_id=") ||
    lower.includes("mime_type=video") || lower.includes("/video/tos/");
}

function classifyMedia(url, contentType = "", requestType = "") {
  const clean = cleanPath(url);
  const type = String(contentType || "").toLowerCase();
  if (clean.endsWith(".ts") || clean.endsWith(".m4s")) return "segment";
  if (type.startsWith("audio/")) {
    if (type.includes("mpeg") || clean.endsWith(".mp3")) return "mp3";
    if (type.includes("mp4") || clean.endsWith(".m4a") || clean.endsWith(".mp4")) return "m4a";
    if (type.includes("aac") || clean.endsWith(".aac")) return "aac";
    return "audio";
  }
  if (type.includes("mpegurl") || clean.endsWith(".m3u8")) return "hls";
  if (type.includes("dash+xml") || clean.endsWith(".mpd")) return "dash";
  if (type.includes("video/mp4") || clean.endsWith(".mp4")) return "mp4";
  if (type.includes("video/webm") || clean.endsWith(".webm")) return "webm";
  if (clean.endsWith(".flv")) return "flv";
  if (clean.endsWith(".mov")) return "mov";
  if (clean.endsWith(".m4v")) return "m4v";
  if (clean.endsWith(".mp3")) return "mp3";
  if (clean.endsWith(".m4a")) return "m4a";
  if (clean.endsWith(".aac")) return "aac";
  if (type.startsWith("video/") || requestType === "media" || looksLikeDouyinVideo(url)) return "video";
  return "media";
}

function looksLikeMedia(url) {
  if (!url || !/^https?:/i.test(url)) return false;
  const lower = url.toLowerCase();
  return MEDIA_EXTENSIONS.some((ext) => lower.includes(ext)) || looksLikeDouyinVideo(url);
}

function looksLikeAd(url) {
  const lower = String(url || "").toLowerCase();
  return AD_MARKERS.some((marker) => lower.includes(marker));
}

function headerValue(headers = [], name) {
  const target = String(name || "").toLowerCase();
  return String(headers.find((h) => String(h.name || "").toLowerCase() === target)?.value || "");
}

function requestHeadersFromWebRequest(headers = []) {
  const result = {};
  for (const header of headers || []) {
    const canonical = REQUEST_HEADER_NAMES.get(String(header?.name || "").toLowerCase());
    const value = String(header?.value || "");
    if (canonical && value) result[canonical] = value;
  }
  return result;
}

function contentTypeFromHeaders(headers = []) { return headerValue(headers, "content-type").toLowerCase(); }
function contentLengthFromHeaders(headers = []) {
  const value = Number(headerValue(headers, "content-length") || 0);
  return Number.isFinite(value) ? value : 0;
}
function isMediaContentType(value) {
  const type = String(value || "").toLowerCase();
  return type.startsWith("video/") || type.startsWith("audio/") || type.includes("mpegurl") || type.includes("dash+xml");
}

function streamPriority(item) {
  const url = String(item.url || "").toLowerCase();
  let bonus = 0;
  if (item.type === "hls" && /(^|[\/_-])(master|manifest)([._?/-]|$)/i.test(url)) bonus += 500;
  if (item.type === "dash" && /(^|[\/_-])(master|manifest)([._?/-]|$)/i.test(url)) bonus += 450;
  return bonus;
}

function scoreMedia(item) {
  let score = 0;
  if (["mp4", "webm", "video", "flv"].includes(item.type)) score += 60;
  if (item.type === "hls") score += 78;
  if (item.type === "dash") score += 72;
  if (["mp3", "m4a", "aac", "audio"].includes(item.type)) score += 32;
  if (item.source === "video-element") score += 38;
  if (item.source === "response-header") score += 25;
  if (item.source === "request-header") score += 24;
  if (item.source === "network") score += 15;
  if ((item.contentLength || 0) > 5 * 1024 * 1024) score += 20;
  if ((item.contentLength || 0) > 50 * 1024 * 1024) score += 12;
  if ((item.contentLength || 0) > 0 && (item.contentLength || 0) < 160 * 1024) score -= 18;
  if (item.likelyAd) score -= 100;
  if (item.type === "segment") score -= 1000;
  return score + streamPriority(item);
}

function getTabItems(tabId) {
  if (!MEDIA_BY_TAB.has(tabId)) MEDIA_BY_TAB.set(tabId, new Map());
  return MEDIA_BY_TAB.get(tabId);
}
function getTabState(tabId) {
  if (!TAB_STATE.has(tabId)) TAB_STATE.set(tabId, { playbackConfirmed: false, playbackAt: 0, pageUrl: "" });
  return TAB_STATE.get(tabId);
}
function resetTab(tabId, pageUrl = "") {
  MEDIA_BY_TAB.delete(tabId);
  TAB_STATE.set(tabId, { playbackConfirmed: false, playbackAt: 0, pageUrl });
}
function visibleItems(tabId) {
  if (!getTabState(tabId).playbackConfirmed) return [];
  const all = [...(MEDIA_BY_TAB.get(tabId)?.values() || [])]
    .filter((item) => item.type !== "segment")
    .sort((a, b) => b.score - a.score || b.detectedAt - a.detectedAt);
  const clean = all.filter((item) => !item.likelyAd);
  return clean.length ? clean : all;
}
function findRequestContext(url, tabId) {
  if (!url) return {};
  if (Number.isInteger(tabId)) return MEDIA_BY_TAB.get(tabId)?.get(url)?.requestHeaders || {};
  let newest = null;
  for (const items of MEDIA_BY_TAB.values()) {
    const item = items.get(url);
    if (item && (!newest || item.detectedAt > newest.detectedAt)) newest = item;
  }
  return newest?.requestHeaders || {};
}

async function refreshBadge(tabId) {
  if (!Number.isInteger(tabId) || tabId < 0) return;
  const state = getTabState(tabId);
  const items = visibleItems(tabId);
  const hasVideo = items.some((i) => ["mp4", "webm", "mov", "m4v", "flv", "video", "hls", "dash"].includes(i.type));
  const hasAudio = items.some((i) => ["mp3", "m4a", "aac", "audio"].includes(i.type));
  const count = Number(hasVideo) + Number(hasAudio);
  try {
    await chrome.action.setBadgeBackgroundColor({ tabId, color: "#1677ff" });
    await chrome.action.setBadgeText({ tabId, text: count ? String(count) : "" });
    await chrome.action.setTitle({ tabId, title: count ? `OmniFetch · 已识别 ${count} 类可下载媒体` : state.playbackConfirmed ? "OmniFetch · 已播放，正在等待媒体请求" : "OmniFetch · 播放视频 1–3 秒后开始识别" });
  } catch (_) {}
}

function addCandidate(tabId, candidate) {
  if (!Number.isInteger(tabId) || tabId < 0 || !candidate?.url || !/^https?:/i.test(candidate.url)) return;
  const items = getTabItems(tabId);
  const existing = items.get(candidate.url);
  const contentType = candidate.contentType || existing?.contentType || "";
  const type = candidate.type || classifyMedia(candidate.url, contentType, candidate.requestType || "");
  const merged = {
    url: candidate.url,
    type,
    contentType,
    contentLength: candidate.contentLength || existing?.contentLength || 0,
    contentDisposition: candidate.contentDisposition || existing?.contentDisposition || "",
    source: candidate.source || existing?.source || "unknown",
    title: candidate.title || existing?.title || "",
    pageUrl: candidate.pageUrl || existing?.pageUrl || "",
    initiator: candidate.initiator || existing?.initiator || "",
    requestHeaders: { ...(existing?.requestHeaders || {}), ...(candidate.requestHeaders || {}) },
    likelyAd: looksLikeAd(candidate.url),
    detectedAt: Date.now()
  };
  merged.score = scoreMedia(merged);
  items.set(candidate.url, merged);
  if (items.size > 240) {
    const keep = [...items.values()].sort((a, b) => b.score - a.score || b.detectedAt - a.detectedAt).slice(0, 140);
    items.clear();
    for (const item of keep) items.set(item.url, item);
  }
  refreshBadge(tabId);
}

chrome.webRequest.onBeforeRequest.addListener((details) => {
  if (details.tabId < 0 || (!looksLikeMedia(details.url) && details.type !== "media")) return;
  addCandidate(details.tabId, { url: details.url, type: classifyMedia(details.url, "", details.type), requestType: details.type, source: "network", initiator: details.initiator || "" });
}, { urls: ["<all_urls>"] });

chrome.webRequest.onBeforeSendHeaders.addListener((details) => {
  if (details.tabId < 0 || (!looksLikeMedia(details.url) && details.type !== "media")) return;
  addCandidate(details.tabId, { url: details.url, type: classifyMedia(details.url, "", details.type), requestType: details.type, requestHeaders: requestHeadersFromWebRequest(details.requestHeaders || []), source: "request-header", initiator: details.initiator || "" });
}, { urls: ["<all_urls>"] }, ["requestHeaders", "extraHeaders"]);

chrome.webRequest.onHeadersReceived.addListener((details) => {
  if (details.tabId < 0) return;
  const headers = details.responseHeaders || [];
  const contentType = contentTypeFromHeaders(headers);
  if (!isMediaContentType(contentType) && !looksLikeMedia(details.url) && details.type !== "media") return;
  addCandidate(details.tabId, { url: details.url, type: classifyMedia(details.url, contentType, details.type), requestType: details.type, contentType, contentLength: contentLengthFromHeaders(headers), contentDisposition: headerValue(headers, "content-disposition"), source: "response-header", initiator: details.initiator || "" });
}, { urls: ["<all_urls>"] }, ["responseHeaders", "extraHeaders"]);

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "OMNIFETCH_MEDIA_CANDIDATES") {
    const tabId = sender.tab?.id;
    for (const candidate of message.items || []) addCandidate(tabId, candidate);
    sendResponse({ ok: true }); return;
  }
  if (message?.type === "OMNIFETCH_PLAYBACK_STARTED") {
    const tabId = sender.tab?.id;
    if (Number.isInteger(tabId)) {
      const state = getTabState(tabId);
      state.playbackConfirmed = true; state.playbackAt = Date.now(); state.pageUrl = message.pageUrl || state.pageUrl || "";
      refreshBadge(tabId);
    }
    sendResponse({ ok: true }); return;
  }
  if (message?.type === "OMNIFETCH_RESET_TAB_CAPTURE") {
    const tabId = sender.tab?.id;
    if (Number.isInteger(tabId)) { resetTab(tabId, message.pageUrl || ""); refreshBadge(tabId); }
    sendResponse({ ok: true }); return;
  }
  if (message?.type === "OMNIFETCH_GET_MEDIA") {
    const state = getTabState(message.tabId);
    sendResponse({ ok: true, playbackConfirmed: Boolean(state.playbackConfirmed), items: visibleItems(message.tabId).slice(0, 60) }); return;
  }
  if (message?.type === "OMNIFETCH_GET_REQUEST_CONTEXT") {
    sendResponse({ ok: true, headers: findRequestContext(message.url, message.tabId) }); return;
  }
  if (message?.type === "OMNIFETCH_CLEAR_MEDIA") {
    resetTab(message.tabId); refreshBadge(message.tabId); sendResponse({ ok: true }); return;
  }
  if (message?.type === "OMNIFETCH_DIRECT_DOWNLOAD") {
    chrome.downloads.download({ url: message.url, saveAs: true }, (downloadId) => {
      const error = chrome.runtime.lastError?.message;
      sendResponse({ ok: !error, downloadId, error });
    });
    return true;
  }
});

chrome.tabs.onRemoved.addListener((tabId) => { MEDIA_BY_TAB.delete(tabId); TAB_STATE.delete(tabId); });
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status === "loading" && changeInfo.url) { resetTab(tabId, changeInfo.url); refreshBadge(tabId); }
});
