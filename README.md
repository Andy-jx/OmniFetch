# OmniFetch

全平台媒体下载助手。目标是做成类似 FetchV 的“浏览器媒体嗅探器”体验：打开网页、播放视频、浏览器自动捕获真实媒体请求，工具栏图标直接显示捕获数量，点开即可保存。

## 当前版本：v0.3.0

v0.3.0 已把产品逻辑从“平台解析优先”调整为 **媒体嗅探优先**：

1. 浏览器后台持续监听当前标签页真实媒体请求。
2. 捕获到可用资源后，OmniFetch 图标右上角显示数字角标。
3. 点击扩展后直接列出捕获到的 MP4 / WebM / HLS/M3U8 / DASH / FLV / 音频等资源。
4. MP4 / WebM / MOV / FLV / MP3 等静态资源直接交给浏览器保存，不要求本地助手。
5. HLS/M3U8、DASH 等需要分片合并的资源交给本地助手 + FFmpeg。
6. 如果页面没有捕获到直链，最后才使用页面 URL + yt-dlp 作为兜底解析。

默认下载目录（本地助手任务）：

```text
%USERPROFILE%\Downloads\OmniFetch
```

> 本项目只用于保存你有权下载、平台允许下载或公开可访问的媒体内容。不提供 DRM 绕过、付费墙绕过或访问控制规避功能。

## 为什么这样更接近 FetchV

核心不是给每个平台单独写一个下载接口，而是统一监听浏览器真正播放的媒体。

```text
网页播放视频
  ↓
浏览器网络请求
  ↓
OmniFetch 自动捕获
  ↓
工具栏数字角标
  ↓
点击扩展查看资源
  ├─ MP4 / WebM / FLV / 音频 → 浏览器直接保存
  └─ M3U8/HLS / DASH → 本地助手 + FFmpeg 合并
  ↓
没有捕获到直链时
  ↓
页面解析兜底
```

这种方式对 X、TikTok、抖音、B站、Instagram、Facebook、小红书、快手以及普通视频网页使用的是同一套通用逻辑，不依赖“这个网站叫什么”。只要浏览器能访问到标准媒体资源，并且不是 DRM 保护流，就有机会捕获。

## Windows 便携包

GitHub Actions 会自动构建：

```text
Actions → Build Windows Package → Artifacts → OmniFetch-Windows
```

解压后包含：

```text
OmniFetchHelper.exe
ffmpeg.exe
run-helper.bat
extension\
README.md
```

不需要安装 Python，也不需要另装 FFmpeg。

## 使用

### 1. 安装扩展

Chrome：

```text
chrome://extensions/
```

Edge：

```text
edge://extensions/
```

开启开发者模式 → 加载已解压扩展 → 选择 `extension` 文件夹 → 固定 OmniFetch 到工具栏。

### 2. 日常下载

1. 打开包含视频的网页。
2. 播放视频几秒。
3. 捕获成功后，OmniFetch 图标右上角出现数字。
4. 点击 OmniFetch。
5. 排在列表最上面的资源通常最值得下载。
6. 静态媒体直接点“保存”。
7. HLS/M3U8/DASH 点“下载并合并”。

只有 HLS/DASH 或页面解析兜底需要启动：

```text
run-helper.bat
```

普通 MP4 / WebM 等不需要助手。

## 下一步重点

为了继续接近 FetchV 的完整体验，后续优先做：

- HLS master playlist 分辨率识别与最高画质自动选择
- 多线程分片下载
- 独立下载任务页：速度、进度、暂停、取消、改名
- 多媒体预览，方便多视频页面判断目标
- blob / MediaSource 无直链页面的“录制模式”
- 域名过滤，减少广告和无关媒体资源
- HLS 直播保存

## 当前限制

- DRM 加密视频不处理。
- 付费墙、访问控制、未授权内容不绕过。
- blob / MediaSource 的缓存录制模式尚未完成。
- 部分站点媒体 URL 有极短有效期，需要播放后尽快下载。
- “全平台”是通用架构目标，不代表任何网站永久 100% 成功。

## 项目目录

```text
OmniFetch/
├─ extension/
│  ├─ manifest.json
│  ├─ background.js
│  ├─ content.js
│  ├─ popup.html
│  ├─ popup.css
│  └─ popup.js
├─ helper/
│  ├─ server.py
│  └─ requirements.txt
├─ install-helper.bat
├─ run-helper.bat
├─ run-helper-exe.bat
└─ .github/workflows/build-windows.yml
```
