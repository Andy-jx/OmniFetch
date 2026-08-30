(() => {
  const sent = new Set();
  let playbackToken = 0;
  let playbackConfirmed = false;

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

  function absoluteUrl(value) {
    if (!value) return "";
    try {
      return new URL(value, location.href).href;
    } catch (_) {
      return "";
    }
  }

  function emit(items) {
    const fresh = [];
    for (const item of items) {
      const resolved = absoluteUrl(item.url);
      if (!validHttpUrl(resolved)) continue;
      if (sent.has(resolved)) continue;
      sent.add(resolved);
      fresh.push({
        ...item,
        url: resolved,
        type: item.type || classify(resolved),
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

  function scanMediaElements() {
    const found = [];
    document.querySelectorAll("video, audio").forEach((media) => {
      if (media.currentSrc) found.push({ url: media.currentSrc, source: "video-element" });
      if (media.src) found.push({ url: media.src, source: "video-element" });
      media.querySelectorAll("source").forEach((source) => {
        if (source.src) found.push({ url: source.src, source: "source-element" });
      });
    });
    emit(found);
  }

  function scanMetaTags() {
    const found = [];
    const selectors = [
      'meta[property="og:video"]',
      'meta[property="og:video:url"]',
      'meta[property="og:video:secure_url"]',
      'meta[name="twitter:player:stream"]',
      'meta[itemprop="contentUrl"]',
      'link[rel="video_src"]'
    ];

    document.querySelectorAll(selectors.join(",")).forEach((node) => {
      const value = node.content || node.href || node.getAttribute("content") || node.getAttribute("href") || "";
      if (value) found.push({ url: value, source: "meta" });
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
        const initiator = String(entry.initiatorType || "").toLowerCase();
        if (/\.(mp4|webm|m3u8|mpd|m4v|mov)(\?|#|$)/i.test(lower) || initiator === "video" || initiator === "audio") {
          found.push({ url, source: "performance" });
        }
      });
    } catch (_) {}
    emit(found);
  }

  function scan() {
    scanMediaElements();
    scanMetaTags();
    scanPerformanceEntries();
  }

  function mediaIsPlaying(media) {
    return Boolean(media && !media.paused && !media.ended && media.readyState >= 2);
  }

  function confirmPlayback(media) {
    const token = ++playbackToken;
    setTimeout(() => {
      if (token !== playbackToken) return;
      if (!mediaIsPlaying(media)) return;
      if (Number(media.currentTime || 0) < 0.5) return;

      playbackConfirmed = true;
      chrome.runtime.sendMessage({
        type: "OMNIFETCH_PLAYBACK_STARTED",
        pageUrl: location.href,
        title: document.title,
        currentTime: Number(media.currentTime || 0),
        videoWidth: Number(media.videoWidth || 0),
        videoHeight: Number(media.videoHeight || 0)
      }).catch(() => {});
      scan();
    }, 1200);
  }

  function activePlayingMedia() {
    return [...document.querySelectorAll("video, audio")].find(mediaIsPlaying) || null;
  }

  scan();
  setTimeout(scan, 1200);
  setTimeout(scan, 3500);
  setTimeout(scan, 7000);
  setTimeout(() => {
    const playing = activePlayingMedia();
    if (playing && !playbackConfirmed) confirmPlayback(playing);
  }, 500);

  const observer = new MutationObserver(() => {
    clearTimeout(observer._timer);
    observer._timer = setTimeout(scan, 250);
  });

  if (document.documentElement) {
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["src", "content", "href"]
    });
  }

  window.addEventListener("play", (event) => {
    const media = event.target;
    if (media instanceof HTMLMediaElement) confirmPlayback(media);
  }, true);

  window.addEventListener("playing", (event) => {
    const media = event.target;
    if (media instanceof HTMLMediaElement && !playbackConfirmed) confirmPlayback(media);
  }, true);

  window.addEventListener("pause", () => {
    playbackToken += 1;
  }, true);

  window.addEventListener("loadedmetadata", scan, true);
  window.addEventListener("popstate", () => setTimeout(scan, 300));
  window.addEventListener("hashchange", () => setTimeout(scan, 300));

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === "OMNIFETCH_RESCAN") {
      sent.clear();
      playbackConfirmed = false;
      playbackToken += 1;
      const playing = activePlayingMedia();

      chrome.runtime.sendMessage({
        type: "OMNIFETCH_RESET_TAB_CAPTURE",
        pageUrl: location.href
      }).then(() => {
        if (playing) {
          scan();
          confirmPlayback(playing);
        }
      }).catch(() => {});

      sendResponse({
        ok: true,
        title: document.title,
        pageUrl: location.href,
        waitingForPlayback: !playing
      });
    }
  });
})();
