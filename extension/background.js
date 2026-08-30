const MEDIA_BY_TAB = new Map();

const MEDIA_EXTENSIONS = [
  ".mp4", ".webm", ".m3u8", ".mpd", ".mov", ".m4v", ".ts", ".m4s", ".flv", ".mp3", ".m4a", ".aac"
];

const AD_MARKERS = [
  "doubleclick", "googleads", "adservice", "/ads/", "/ad/", "preroll", "pre-roll", "vast", "vpaid", "tracking"
];

const REQUEST_HEADER_NAMES = new Map([
  ["referer", "Referer"],
  ["origin", "Origin"],
  ["user-agent", "User-Agent"],
  ["cookie", "Cookie"],
  ["accept", "Accept"],
  ["accept-language", "Accept-Language"]
]);

function cleanPath(url) {
  return String(url || "").toLowerCase().split("?")[0].split("#")[0];
}

function classifyMedia(url, contentType = "") {
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
  if (type.startsWith("video/")) return "video";
  return "media";
}

function looksLikeMedia(url) {
  if (!url || !/^https?:/i.test(url)) return false;
  const lower = url.toLowerCase();
  return MEDIA_EXTENSIONS.some((ext) => lower.includes(ext));
}

function looksLikeAd(url) {
  const lower = String(url || "").toLowerCase();
  return AD_MARKERS.some((marker) => lower.includes(marker));
}

function headerValue(headers = [], name) {
  const target = String(name || "").toLowerCase();
  const row = headers.find((header) => String(header.name || "").toLowerCase() === target);
  return String(row?.value || "");
}

function requestHeadersFromWebRequest(headers = []) {
  const result = {};
  for (const header of headers || []) {
    const lower = String(header?.name || "").toLowerCase();
    const canonical = REQUEST_HEADER_NAMES.get(lower);
    const value = String(header?.value || "");
    if (!canonical || !value) continue;
    result[canonical] = value;
  }
  return result;
}

function contentTypeFromHeaders(headers = []) {
  return headerValue(headers, "content-type").toLowerCase();
}

function contentLengthFromHeaders(headers = []) {
  const value = Number(headerValue(headers, "content-length") || 0);
  return Number.isFinite(value) ? value : 0;
}

function isMediaContentType(value) {
  const type = String(value || "").toLowerCase();
  return (
    type.startsWith("video/") ||
    type.startsWith("audio/") ||
    type.includes("application/vnd.apple.mpegurl") ||
    type.includes("application/x-mpegurl") ||
    type.includes("application/dash+xml")
  );
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
  if (item.source === "performance") score += 12;
  if ((item.contentLength || 0) > 5 * 1024 * 1024) score += 20;
  if ((item.contentLength || 0) > 50 * 1024 * 1024) score += 12;
  if ((item.contentLength || 0) > 0 && (item.contentLength || 0) < 160 * 1024) score -= 18;
  if (item.likelyAd) score -= 100;
  if (item.type === "segment") score -= 1000;
  return score;
}

function getTabItems(tabId) {
  if (!MEDIA_BY_TAB.has(tabId)) MEDIA_BY_TAB.set(tabId, new Map());
  return MEDIA_BY_TAB.get(tabId);
}

function visibleItems(tabId) {
  const all = [...(MEDIA_BY_TAB.get(tabId)?.values() || [])]
    .filter((item) => item.type !== "segment")
    .sort((a, b) => b.score - a.score || b.detectedAt - a.detectedAt);
  const clean = all.filter((item) => !item.likelyAd);
  return clean.length ? clean : all;
}

async function refreshBadge(tabId) {
  if (!Number.isInteger(tabId) || tabId < 0) return;
  const items = visibleItems(tabId);
  const hasVideo = items.some((item) => ["mp4", "webm", "mov", "m4v", "flv", "video", "hls", "dash"].includes(item.type));
  const hasAudio = items.some((item) => ["mp3", "m4a", "aac", "audio"].includes(item.type));
  const count = Number(hasVideo) + Number(hasAudio);
  try {
    await chrome.action.setBadgeBackgroundColor({ tabId, color: "#1677ff" });
    await chrome.action.setBadgeText({ tabId, text: count ? String(count) : "" });
    await chrome.action.setTitle({
      tabId,
      title: count ? `OmniFetch · 已识别 ${count} 类可下载媒体` : "OmniFetch · 等待媒体资源"
    });
  } catch (_) {}
}

function addCandidate(tabId, candidate) {
  if (!Number.isInteger(tabId) || tabId < 0 || !candidate?.url) return;
  if (!/^https?:/i.test(candidate.url)) return;

  const items = getTabItems(tabId);
  const existing = items.get(candidate.url);
  const contentType = candidate.contentType || existing?.contentType || "";
  const type = candidate.type || classifyMedia(candidate.url, contentType);
  const requestHeaders = {
    ...(existing?.requestHeaders || {}),
    ...(candidate.requestHeaders || {})
  };
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
    requestHeaders,
    likelyAd: looksLikeAd(candidate.url),
    detectedAt: Date.now()
  };
  merged.score = scoreMedia(merged);
  items.set(candidate.url, merged);

  if (items.size > 220) {
    const sorted = [...items.values()].sort((a, b) => b.score - a.score || b.detectedAt - a.detectedAt);
    items.clear();
    for (const item of sorted.slice(0, 120)) items.set(item.url, item);
  }
  refreshBadge(tabId);
}

chrome.webRequest.onBeforeRequest.addListener(
  (details) => {
    if (details.tabId < 0) return;
    if (!looksLikeMedia(details.url) && details.type !== "media") return;
    addCandidate(details.tabId, {
      url: details.url,
      type: classifyMedia(details.url),
      source: "network",
      initiator: details.initiator || ""
    });
  },
  { urls: ["<all_urls>"] }
);

chrome.webRequest.onBeforeSendHeaders.addListener(
  (details) => {
    if (details.tabId < 0) return;
    if (!looksLikeMedia(details.url) && details.type !== "media") return;
    addCandidate(details.tabId, {
      url: details.url,
      type: classifyMedia(details.url),
      requestHeaders: requestHeadersFromWebRequest(details.requestHeaders || []),
      source: "request-header",
      initiator: details.initiator || ""
    });
  },
  { urls: ["<all_urls>"] },
  ["requestHeaders", "extraHeaders"]
);

chrome.webRequest.onHeadersReceived.addListener(
  (details) => {
    if (details.tabId < 0) return;
    const headers = details.responseHeaders || [];
    const contentType = contentTypeFromHeaders(headers);
    if (!isMediaContentType(contentType) && !looksLikeMedia(details.url)) return;
    addCandidate(details.tabId, {
      url: details.url,
      type: classifyMedia(details.url, contentType),
      contentType,
      contentLength: contentLengthFromHeaders(headers),
      contentDisposition: headerValue(headers, "content-disposition"),
      source: "response-header",
      initiator: details.initiator || ""
    });
  },
  { urls: ["<all_urls>"] },
  ["responseHeaders", "extraHeaders"]
);

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "OMNIFETCH_MEDIA_CANDIDATES") {
    const tabId = sender.tab?.id;
    for (const candidate of message.items || []) addCandidate(tabId, candidate);
    sendResponse({ ok: true });
    return;
  }

  if (message?.type === "OMNIFETCH_GET_MEDIA") {
    const items = visibleItems(message.tabId);
    sendResponse({ ok: true, items: items.slice(0, 50) });
    return;
  }

  if (message?.type === "OMNIFETCH_GET_REQUEST_CONTEXT") {
    const item = MEDIA_BY_TAB.get(message.tabId)?.get(message.url);
    sendResponse({ ok: true, headers: item?.requestHeaders || {} });
    return;
  }

  if (message?.type === "OMNIFETCH_CLEAR_MEDIA") {
    MEDIA_BY_TAB.delete(message.tabId);
    refreshBadge(message.tabId);
    sendResponse({ ok: true });
    return;
  }

  if (message?.type === "OMNIFETCH_DIRECT_DOWNLOAD") {
    chrome.downloads.download({ url: message.url, saveAs: true }, (downloadId) => {
      const error = chrome.runtime.lastError?.message;
      sendResponse({ ok: !error, downloadId, error });
    });
    return true;
  }
});

chrome.tabs.onRemoved.addListener((tabId) => MEDIA_BY_TAB.delete(tabId));

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status === "loading" && changeInfo.url) {
    MEDIA_BY_TAB.delete(tabId);
    refreshBadge(tabId);
  }
});
