const MEDIA_BY_TAB = new Map();

const MEDIA_EXTENSIONS = [
  ".mp4",
  ".webm",
  ".m3u8",
  ".mpd",
  ".mov",
  ".m4v",
  ".ts"
];

function classifyMedia(url) {
  const clean = url.toLowerCase().split("?")[0].split("#")[0];
  if (clean.endsWith(".m3u8")) return "hls";
  if (clean.endsWith(".mpd")) return "dash";
  if (clean.endsWith(".mp4")) return "mp4";
  if (clean.endsWith(".webm")) return "webm";
  if (clean.endsWith(".mov")) return "mov";
  if (clean.endsWith(".m4v")) return "m4v";
  if (clean.endsWith(".ts")) return "segment";
  return "media";
}

function looksLikeMedia(url) {
  if (!url || !/^https?:/i.test(url)) return false;
  const lower = url.toLowerCase();
  return MEDIA_EXTENSIONS.some((ext) => lower.includes(ext));
}

function scoreMedia(item) {
  let score = 0;
  if (item.type === "mp4" || item.type === "webm") score += 50;
  if (item.type === "hls") score += 45;
  if (item.source === "video-element") score += 30;
  if (item.source === "network") score += 15;
  if (item.url.includes("video.twimg.com")) score += 25;
  if (item.url.includes("blob:")) score -= 100;
  if (item.type === "segment") score -= 40;
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
  const type = candidate.type || classifyMedia(candidate.url);
  const existing = items.get(candidate.url);
  const merged = {
    url: candidate.url,
    type,
    source: candidate.source || existing?.source || "unknown",
    title: candidate.title || existing?.title || "",
    pageUrl: candidate.pageUrl || existing?.pageUrl || "",
    detectedAt: Date.now()
  };
  merged.score = scoreMedia(merged);
  items.set(candidate.url, merged);

  // 防止长时间播放把分片请求堆满内存。
  if (items.size > 120) {
    const sorted = [...items.values()].sort((a, b) => b.score - a.score || b.detectedAt - a.detectedAt);
    items.clear();
    for (const item of sorted.slice(0, 80)) items.set(item.url, item);
  }
}

chrome.webRequest.onBeforeRequest.addListener(
  (details) => {
    if (details.tabId < 0 || !looksLikeMedia(details.url)) return;
    addCandidate(details.tabId, {
      url: details.url,
      type: classifyMedia(details.url),
      source: "network"
    });
  },
  { urls: ["<all_urls>"] }
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
    sendResponse({ ok: true, items: items.slice(0, 30) });
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
