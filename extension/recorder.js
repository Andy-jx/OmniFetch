const params = new URLSearchParams(location.search);
const targetTabId = Number(params.get("tabId"));
const titleHint = params.get("title") || "网页视频";

const targetTitleEl = document.getElementById("targetTitle");
const stateTextEl = document.getElementById("stateText");
const timerEl = document.getElementById("timer");
const formatTextEl = document.getElementById("formatText");
const statusEl = document.getElementById("status");
const startBtn = document.getElementById("startBtn");
const stopBtn = document.getElementById("stopBtn");

let stream = null;
let recorder = null;
let chunks = [];
let audioContext = null;
let audioSource = null;
let timerHandle = null;
let startedAt = 0;
let stopping = false;

function setStatus(text, kind = "") {
  statusEl.textContent = text;
  statusEl.className = `status${kind ? ` ${kind}` : ""}`;
}

function formatDuration(seconds) {
  const value = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const secs = value % 60;
  if (hours) return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  return `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

function safeFilename(value) {
  return String(value || "video")
    .replace(/[<>:"/\\|?*\u0000-\u001F]/g, "_")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 90) || "video";
}

function chooseMimeType() {
  const candidates = [
    "video/webm;codecs=vp9,opus",
    "video/webm;codecs=vp8,opus",
    "video/webm"
  ];
  for (const mime of candidates) {
    if (MediaRecorder.isTypeSupported(mime)) return mime;
  }
  return "";
}

function updateTimer() {
  if (!startedAt) {
    timerEl.textContent = "00:00";
    return;
  }
  timerEl.textContent = formatDuration((Date.now() - startedAt) / 1000);
}

function stopTracks() {
  if (stream) {
    for (const track of stream.getTracks()) {
      try { track.stop(); } catch (_) {}
    }
  }
  stream = null;
  try { audioSource?.disconnect(); } catch (_) {}
  audioSource = null;
  if (audioContext) {
    audioContext.close().catch(() => {});
    audioContext = null;
  }
}

async function saveRecording() {
  const mime = recorder?.mimeType || "video/webm";
  const blob = new Blob(chunks, { type: mime });
  if (!blob.size) throw new Error("没有录到有效数据，请确认目标标签页正在播放视频。 ");

  const url = URL.createObjectURL(blob);
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const filename = `OmniFetch/Recordings/${safeFilename(titleHint)}_${stamp}.webm`;

  try {
    await chrome.downloads.download({
      url,
      filename,
      saveAs: true
    });
    setStatus(`录制完成，已生成 ${(blob.size / 1024 / 1024).toFixed(1)} MB 文件。`, "ok");
    stateTextEl.textContent = "已保存";
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 15000);
  }
}

async function startRecording() {
  if (!Number.isInteger(targetTabId) || targetTabId < 0) {
    setStatus("目标标签页 ID 无效，请从 OmniFetch 扩展重新打开录制模式。", "bad");
    return;
  }

  startBtn.disabled = true;
  setStatus("正在申请目标标签页的媒体流…");
  stateTextEl.textContent = "启动中";
  chunks = [];
  stopping = false;

  try {
    const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId });
    if (!streamId) throw new Error("没有取得标签页媒体流 ID");

    stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        mandatory: {
          chromeMediaSource: "tab",
          chromeMediaSourceId: streamId
        }
      },
      video: {
        mandatory: {
          chromeMediaSource: "tab",
          chromeMediaSourceId: streamId
        }
      }
    });

    if (stream.getAudioTracks().length) {
      audioContext = new AudioContext();
      await audioContext.resume().catch(() => {});
      audioSource = audioContext.createMediaStreamSource(stream);
      audioSource.connect(audioContext.destination);
    }

    const mimeType = chooseMimeType();
    recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
    formatTextEl.textContent = (recorder.mimeType || "video/webm").split(";")[0].replace("video/", "").toUpperCase();

    recorder.addEventListener("dataavailable", (event) => {
      if (event.data && event.data.size > 0) chunks.push(event.data);
    });

    recorder.addEventListener("stop", async () => {
      clearInterval(timerHandle);
      timerHandle = null;
      stopTracks();
      try {
        await saveRecording();
      } catch (error) {
        stateTextEl.textContent = "保存失败";
        setStatus(error.message || String(error), "bad");
      } finally {
        recorder = null;
        stopping = false;
        startBtn.disabled = false;
        stopBtn.disabled = true;
      }
    }, { once: true });

    const videoTrack = stream.getVideoTracks()[0];
    if (videoTrack) {
      videoTrack.addEventListener("ended", () => {
        if (recorder?.state === "recording") stopRecording();
      }, { once: true });
    }

    recorder.start(1000);
    startedAt = Date.now();
    updateTimer();
    timerHandle = setInterval(updateTimer, 500);
    stateTextEl.textContent = "录制中";
    startBtn.disabled = true;
    stopBtn.disabled = false;
    setStatus("正在录制。现在切回原视频标签页正常播放即可；录完后回到本页点击“停止并保存”。", "ok");
  } catch (error) {
    clearInterval(timerHandle);
    timerHandle = null;
    stopTracks();
    recorder = null;
    stateTextEl.textContent = "启动失败";
    setStatus(`${error.message || error}。请确认 Chrome/Edge 版本较新，并从扩展弹窗重新进入录制模式。`, "bad");
    startBtn.disabled = false;
    stopBtn.disabled = true;
  }
}

function stopRecording() {
  if (!recorder || recorder.state === "inactive" || stopping) return;
  stopping = true;
  stopBtn.disabled = true;
  stateTextEl.textContent = "正在结束";
  setStatus("正在结束录制并生成文件，请不要关闭本页…");
  try {
    recorder.stop();
  } catch (error) {
    stopping = false;
    stateTextEl.textContent = "停止失败";
    setStatus(error.message || String(error), "bad");
    stopBtn.disabled = false;
  }
}

startBtn.addEventListener("click", startRecording);
stopBtn.addEventListener("click", stopRecording);

window.addEventListener("beforeunload", (event) => {
  if (recorder?.state === "recording") {
    event.preventDefault();
    event.returnValue = "正在录制，关闭页面会丢失当前录制。";
  }
});

targetTitleEl.textContent = titleHint;
