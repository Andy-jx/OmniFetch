# OmniFetch

全平台媒体下载助手。目标是做成类似 FetchV 的“浏览器媒体嗅探器”体验：打开网页、播放视频、浏览器自动捕获真实媒体请求，工具栏图标显示捕获数量，点开即可保存。

## 当前版本：v0.4.0

v0.4.0 已经完成一套更接近 FetchV 的主流程：

1. **媒体嗅探优先**：后台持续监听页面真实媒体请求。
2. **图标数字角标**：捕获到资源后，OmniFetch 图标直接显示数量。
3. **自动过滤噪声**：降低明显广告、跟踪资源和 TS/M4S 小分片的优先级；如果页面只有这类资源，也不会完全隐藏，避免误杀真正视频。
4. **静态视频直存**：MP4 / WebM / MOV / FLV / MP3 等直接交给浏览器保存，不需要本地助手。
5. **HLS / DASH 清晰度页**：M3U8/HLS、DASH/MPD 会打开独立下载页，读取可用分辨率、码率、FPS、格式和音视频轨。
6. **最高画质推荐**：清晰度页默认把最高分辨率排在第一位，也可以手动选 1080P / 720P / 480P 等可用格式。
7. **8 路并发分片下载**：HLS / DASH 使用 yt-dlp 并发下载分片，然后调用 FFmpeg 自动合并。
8. **自动补音频**：选到 video-only 格式时，会自动尝试匹配最佳音频轨并合并。
9. **页面解析兜底**：如果没有嗅探到媒体直链，最后才使用当前页面 URL + yt-dlp 尝试解析。
10. **助手可自动启动**：Windows 便携包内提供一键自动启动脚本，设置一次后，后续登录 Windows 可在后台自动启动流媒体助手。

默认下载目录：

```text
%USERPROFILE%\Downloads\OmniFetch
```

> 本项目只用于保存你有权下载、平台允许下载或公开可访问的媒体内容。不提供 DRM 绕过、付费墙绕过或访问控制规避功能。

## 使用逻辑

```text
打开视频网站
  ↓
播放视频 2–5 秒
  ↓
OmniFetch 自动监听真实媒体请求
  ↓
工具栏图标出现 1 / 2 / 3...
  ↓
点击扩展
  ├─ MP4 / WebM / FLV / 音频 → 直接保存
  └─ HLS / M3U8 / DASH → 清晰度页
                              ↓
                       选 1080P / 720P...
                              ↓
                       8 路并发下载分片
                              ↓
                       FFmpeg 自动合并
                              ↓
                         本地视频文件
```

这套逻辑不依赖“网站叫什么”，因此可以用于 X、YouTube、TikTok、抖音、B站、Instagram、Facebook、小红书、快手、Vimeo、Twitch、Reddit，以及大量普通网页。实际成功率取决于站点是否使用可访问的标准媒体资源，以及是否存在 DRM、短时签名、登录限制等情况。

## Windows 便携包

GitHub Actions 自动构建：

```text
Actions → Build Windows Package → Artifacts → OmniFetch-Windows
```

解压后包含：

```text
OmniFetchHelper.exe
ffmpeg.exe
run-helper.bat
install-autostart.bat
uninstall-autostart.bat
start-helper-hidden.vbs
extension\
README.md
```

不需要安装 Python，也不需要另外安装 FFmpeg。

## 安装浏览器扩展

Chrome：

```text
chrome://extensions/
```

Edge：

```text
edge://extensions/
```

开启开发者模式 → 加载已解压扩展 → 选择 `extension` 文件夹 → 固定 OmniFetch 到工具栏。

## 推荐：只设置一次自动启动

为了让使用体验更像普通浏览器下载扩展，可以在解压后的 OmniFetch 文件夹中双击：

```text
install-autostart.bat
```

它会：

- 创建当前用户的 Windows 登录启动快捷方式；
- 后台隐藏启动 `OmniFetchHelper.exe`；
- 不需要管理员权限；
- 不会修改浏览器配置。

设置完成后，不要随意移动整个 OmniFetch 文件夹，否则启动快捷方式路径会失效。

如果以后不想自动启动，双击：

```text
uninstall-autostart.bat
```

它只删除自动启动快捷方式，不删除程序、扩展或下载内容。

如果不设置自动启动，也可以需要 HLS/DASH 时手动双击：

```text
run-helper.bat
```

普通 MP4 / WebM 等静态资源不需要助手。

## v0.4.0 新增接口

分析媒体清晰度：

```text
POST http://127.0.0.1:17891/probe
Content-Type: application/json

{
  "media_url": "https://example.com/master.m3u8",
  "page_url": "https://example.com/video",
  "browser": "chrome"
}
```

指定格式下载：

```text
POST http://127.0.0.1:17891/download
Content-Type: application/json

{
  "media_url": "https://example.com/master.m3u8",
  "page_url": "https://example.com/video",
  "browser": "chrome",
  "format_id": "hls-1080"
}
```

## 下一步重点

- blob / MediaSource 无直链页面的录制兜底模式
- 下载任务暂停 / 取消 / 重试
- 多视频页面缩略图和预览
- 更好的重复媒体合并和广告过滤
- HLS 直播按时间段保存

## 当前限制

- DRM 加密视频不处理。
- 付费墙、访问控制、未授权内容不绕过。
- blob / MediaSource 录制模式尚未完成。
- 某些站点媒体 URL 有极短有效期，需要播放后尽快下载。
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
│  ├─ popup.js
│  ├─ stream.html
│  ├─ stream.css
│  └─ stream.js
├─ helper/
│  ├─ server.py
│  └─ requirements.txt
├─ run-helper-exe.bat
├─ install-autostart.bat
├─ uninstall-autostart.bat
├─ start-helper-hidden.vbs
└─ .github/workflows/build-windows.yml
```
