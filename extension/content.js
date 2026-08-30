(() => {
  const sent = new Set();

  function classify(url) {
    const clean = String(url || "").toLowerCase().split("?")[0].split("#")[0];
    if (clean.endsWith(".m3u8")) return "hls";
    if (clean.endsWith(".mpd")) return "dash";
    if (clean.endsWith(".mp4")) return "mp4";
    if (clean.endsWith(".webm")) return "webm";
    if (clean.endsWith(".mov")) return "mov";
    if (clean.endsWith(".m4v")) return "m4v";
    return "media";
  }

  function validHttpUrl(url) {
    return /^https?:\/\//i.test(url || "");
  }

  function emit(items) {
    const fresh = [];
    for (const item of items) {
      if (!validHttpUrl(item.url)) continue;
      const key = item.url;
      if (sent.has(key)) continue;
      sent.add(key);
      fresh.push({
        ...item,
        type: item.type || classify(item.url),
        title: document.title,
        pageUrl: location.href
      });
    }

    if (!fresh.length) return;
    chrome.runtime.sendMessage({
      type: "OMNIFETCH_MEDIA_CANDIDATES",
      items: fresh
    }).catch(() => {});
  }

  function scanVideoElements() {
    const found = [];
    document.querySelectorAll("video, audio").forEach((media) => {
      if (validHttpUrl(media.currentSrc)) {
        found.push({ url: media.currentSrc, source: "video-element" });
      }
      if (validHttpUrl(media.src)) {
        found.push({ url: media.src, source: "video-element" });
      }
      media.querySelectorAll("source").forEach((source) => {
        if (validHttpUrl(source.src)) {
          found.push({ url: source.src, source: "source-element" });
        }
      });
    });
    emit(found);
  }

  function scanPerformanceEntries() {
    const found = [];
    try {
      performance.getEntriesByType("resource").forEach((entry) => {
        const url = entry.name || "";
        if (!validHttpUrl(url)) return;
        const lower = url.toLowerCase();
        if (/\.(mp4|webm|m3u8|mpd|m4v|mov)(\?|#|$)/i.test(lower)) {
          found.push({ url, source: "performance" });
        }
      });
    } catch (_) {}
    emit(found);
  }

  function scan() {
    scanVideoElements();
    scanPerformanceEntries();
  }

  scan();
  setTimeout(scan, 1500);
  setTimeout(scan, 4000);

  const observer = new MutationObserver(() => {
    clearTimeout(observer._timer);
    observer._timer = setTimeout(scan, 300);
  });

  if (document.documentElement) {
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["src"]
    });
  }

  window.addEventListener("play", scan, true);
  window.addEventListener("loadedmetadata", scan, true);

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === "OMNIFETCH_RESCAN") {
      scan();
      sendResponse({ ok: true, title: document.title, pageUrl: location.href });
    }
  });
})();
