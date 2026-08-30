const MEDIA_BY_TAB = new Map();

const MEDIA_EXTENSIONS = [
  ".mp4",
  ".webm",
  ".m3u8",
  ".mpd",
  ".mov",
  ".m4v",
  ".ts",
  ".m4s"
];

function classifyMedia(url, contentType = "") {
  const clean = String(url || "").toLowerCase().split("?")[0].split("#")[0];
  const type = String(contentType || "").toLowerCase();
  if (type.includes("mpegurl") || clean.endsWith(".m3u8")) return "hls";
  if (type.includes("dash+xml") || clean.endsWith(".mpd")) return "dash";
  if (type.includes("video/mp4") || clean.endsWith(".mp4")) return "mp4";
  if (type.includes("video/webm") || clean.endsWith(".webm")) return "webm";
  if (clean.endsWith(".mov")) return "mov";
  if (clean.endsWith(".m4v")) return "m4v";
  if (clean.endsWith(".ts") || clean.endsWith(".m4s")) return "segment";
  if (type.startsWith("video/")) return "video";
  if (type.startsWith("audio/")) return "audio";
  return "media";
}

function looksLikeMedia(url) {
  if (!url || !/^https?:/i.test(url)) return false;
  const lower = url.toLowerCase();
  return MEDIA_EXTENSIONS.some((ext) => lower.includes(ext));
}

function contentTypeFromHeaders(headers = []) {
  const row = headers.find((header) => String(header.name || "").toLowerCase() === "content-type");
  return String(row?.value || "").toLowerCase();
}

function contentLengthFromHeaders(headers = []) {
  const row = headers.find((header) => String(header.name || "").toLowerCase() === "content-length");
  const value = Number(row?.value || 0);
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
  if (["mp4", "webm", "video"].includes(item.type)) score += 55;
  if (item.type === "hls") score += 50;
  if (item.type === "dash") score += 45;
  if (item.source === "video-element") score += 35;
  if (item.source === "response-header") score += 25;
  if (item.source === "network") score += 15;
  if ((item.contentLength || 0) > 5 * 1024 * 1024) score += 20;
  if ((item.contentLength || 0) > 50 * 1024 * 1024) score += 10;
  if (item.url.includes("video.twimg.com")) score += 20;
  if (item.type === "segment") score -= 50;
  return score;
}

function getTabItems(tabId) {
  if (!MEDIA_BY_TAB.has(tabId)) MEDIA_BY_TAB.set(tabId, new Map());
  return MEDIA_BY_TAB.get(tabId);
}

function addCandidate(tabId, candidate) {
  if (!Number.isInteger(tabId) || tabId < 0 || !candidate?.url) return;
  if (!/^https?:/i.test(candidate.url)) return;

  const items = getTabItems(tabId);
  const existing = items.get(candidate.url);
  const contentType = candidate.contentType || existing?.contentType || "";
  const type = candidate.type || classifyMedia(candidate.url, contentType);
  const merged = {
    url: candidate.url,
    type,
    contentType,
    contentLength: candidate.contentLength || existing?.contentLength || 0,
    source: candidate.source || existing?.source || "unknown",
    title: candidate.title || existing?.title || "",
    pageUrl: candidate.pageUrl || existing?.pageUrl || "",
    detectedAt: Date.now()
  };
  merged.score = scoreMedia(merged);
  items.set(candidate.url, merged);

  if (items.size > 180) {
    const sorted = [...items.values()].sort((a, b) => b.score - a.score || b.detectedAt - a.detectedAt);
    items.clear();
    for (const item of sorted.slice(0, 100)) items.set(item.url, item);
  }
}

chrome.webRequest.onBeforeRequest.addListener(
  (details) => {
    if (details.tabId < 0) return;
    if (!looksLikeMedia(details.url) && details.type !== "media") return;
    addCandidate(details.tabId, {
      url: details.url,
      type: classifyMedia(details.url),
      source: "network"
    });
  },
  { urls: ["<all_urls>"] }
);

chrome.webRequest.onHeadersReceived.addListener(
  (details) => {
    if (details.tabId < 0) return;
    const contentType = contentTypeFromHeaders(details.responseHeaders || []);
    if (!isMediaContentType(contentType) && !looksLikeMedia(details.url)) return;
    addCandidate(details.tabId, {
      url: details.url,
      type: classifyMedia(details.url, contentType),
      contentType,
      contentLength: contentLengthFromHeaders(details.responseHeaders || []),
      source: "response-header"
    });
  },
  { urls: ["<all_urls>"] },
  ["responseHeaders"]
);

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "OMNIFETCH_MEDIA_CANDIDATES") {
    const tabId = sender.tab?.id;
    for (const candidate of message.items || []) {
      addCandidate(tabId, candidate);
    }
    sendResponse({ ok: true });
    return;
  }

  if (message?.type === "OMNIFETCH_GET_MEDIA") {
    const tabId = message.tabId;
    const items = [...(MEDIA_BY_TAB.get(tabId)?.values() || [])]
      .filter((item) => item.type !== "segment")
      .sort((a, b) => b.score - a.score || b.detectedAt - a.detectedAt);
    sendResponse({ ok: true, items: items.slice(0, 40) });
    return;
  }

  if (message?.type === "OMNIFETCH_CLEAR_MEDIA") {
    MEDIA_BY_TAB.delete(message.tabId);
    sendResponse({ ok: true });
    return;
  }

  if (message?.type === "OMNIFETCH_DIRECT_DOWNLOAD") {
    chrome.downloads.download(
      {
        url: message.url,
        saveAs: true
      },
      (downloadId) => {
        const error = chrome.runtime.lastError?.message;
        sendResponse({ ok: !error, downloadId, error });
      }
    );
    return true;
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  MEDIA_BY_TAB.delete(tabId);
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status === "loading" && changeInfo.url) {
    MEDIA_BY_TAB.delete(tabId);
  }
});
