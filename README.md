# OmniFetch

全平台媒体下载助手。目标不是只适配 X，而是尽量覆盖常见视频网站和普通网页中的可访问媒体资源。

## 当前版本：v0.2.0

OmniFetch 采用 **Chrome / Edge 扩展 + Windows 本地下载助手** 的双层方案：

1. **页面解析优先**：把当前页面交给 `yt-dlp`，优先使用站点专用解析器和浏览器登录 Cookie。
2. **媒体流自动回退**：如果页面解析失败，自动尝试浏览器实际捕获到的 MP4 / WebM / M3U8/HLS / DASH/MPD 等媒体地址。
3. **响应头识别**：即使 CDN 地址没有 `.mp4` / `.m3u8` 后缀，只要响应 `Content-Type` 是视频、音频、HLS 或 DASH，也会进入候选列表。
4. **页面元素扫描**：扫描 `<video>` / `<audio>`、`<source>`、OpenGraph 视频地址、Twitter stream meta 和 Performance Resource。
5. **自带 FFmpeg**：Windows 便携包会自动打包 `ffmpeg.exe`，用于高画质音视频合并和 HLS/DASH 处理。

默认下载目录：

```text
%USERPROFILE%\Downloads\OmniFetch
```

> 本项目只用于保存你有权下载、平台允许下载或公开可访问的媒体内容。不提供 DRM 绕过、付费墙绕过或访问控制规避功能。

## 平台目标

当前内置平台识别包括：

- X / Twitter
- YouTube
- TikTok
- 抖音
- 哔哩哔哩 / Bilibili
- Instagram
- Facebook
- 小红书
- 快手
- Vimeo
- Twitch
- Reddit
- Dailymotion
- SoundCloud
- 以及普通网页中的 MP4 / WebM / M3U8/HLS / DASH 媒体

这里需要区分两种支持方式：

### A. 页面解析型

只要当前 `yt-dlp` 版本支持该站点，OmniFetch 会直接使用页面 URL 解析，并可尝试读取当前 Chrome / Edge / Firefox 登录状态。

### B. 浏览器捕获型

对于站点专用解析器暂时不支持、经常变化、或页面需要登录的情况，OmniFetch 会继续监听页面真正请求到的媒体流。只要浏览器能正常播放、并且媒体不是 DRM 保护流，就有机会通过捕获到的真实媒体地址下载。

因此“全平台”是 **通用架构目标**，不是承诺任何网站永远 100% 可下载。抖音、小红书、快手等站点经常调整接口和签名，实际成功率会受网站版本、登录状态和媒体分发方式影响。

## 使用方式

### 推荐：Windows 便携包

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

不需要安装 Python，也不需要另外安装 FFmpeg。

### 第一步：启动本地助手

双击：

```text
run-helper.bat
```

保持窗口运行。

### 第二步：安装浏览器扩展

Chrome：

```text
chrome://extensions/
```

Edge：

```text
edge://extensions/
```

然后：

1. 开启“开发者模式”
2. 点击“加载已解压的扩展程序”
3. 选择 `extension` 文件夹
4. 把 OmniFetch 固定到工具栏

### 第三步：下载视频

最简单的流程：

1. 打开目标视频页面
2. 如果页面已经正常显示视频，可直接点 OmniFetch
3. 点击 **“智能下载当前视频”**
4. OmniFetch 先解析当前页面
5. 页面解析失败时，自动尝试已捕获媒体资源
6. 如果还没捕获到资源，先播放视频 2–5 秒，再点“重新检测”后重试

## 为什么不是只靠一个解析接口

单独针对某个平台写固定接口很容易失效。OmniFetch v0.2.0 使用多层方案：

```text
当前网页
  ↓
站点页面解析 + 浏览器 Cookie
  ↓ 失败
无 Cookie 页面解析
  ↓ 失败
浏览器网络监听
  ↓
真实媒体响应 / video 元素 / meta / performance
  ↓
MP4 / WebM / HLS / DASH
  ↓
yt-dlp + FFmpeg
  ↓
本地文件
```

这样网站改版时，不会因为单一路径失效就整个工具不能用。

## 当前限制

- DRM 加密视频不处理。
- 付费墙、访问控制、未授权内容不绕过。
- 某些站点的媒体 URL 有极短有效期，需要在播放后尽快下载。
- 部分登录站点可能需要关闭其他占用浏览器 Cookie 数据库的程序后重试。
- 网站更新可能导致某个平台短期失效，需要更新 `yt-dlp` 或适配捕获逻辑。

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

## 本地接口

健康检查：

```text
GET http://127.0.0.1:17891/health
```

能力信息：

```text
GET http://127.0.0.1:17891/capabilities
```

创建智能下载任务：

```text
POST http://127.0.0.1:17891/download
Content-Type: application/json

{
  "page_url": "https://example.com/video-page",
  "browser": "chrome",
  "fallback_media_urls": [
    "https://cdn.example.com/video/master.m3u8"
  ]
}
```
