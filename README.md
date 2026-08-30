# OmniFetch

全平台媒体下载助手。目标是做成类似 FetchV 的“浏览器媒体嗅探器”体验：打开网页、播放视频、浏览器自动捕获真实媒体请求，工具栏图标显示捕获数量，点开即可保存。

## 当前版本：v0.5.0

v0.5.0 目前形成三层下载链路：

1. **媒体嗅探优先**：后台持续监听页面真实媒体请求，图标数字角标显示捕获数量。
2. **流媒体解析与合并**：HLS/M3U8、DASH/MPD 打开独立清晰度页，支持分辨率选择、8 路并发分片和 FFmpeg 合并。
3. **标签页录制兜底**：网页能播放，但抓不到真实媒体地址时，可以实时录制目标标签页的画面和声音并保存为 WebM。

默认下载目录（本地助手任务）：

```text
%USERPROFILE%\Downloads\OmniFetch
```

录制模式默认放到浏览器下载目录下的：

```text
OmniFetch\Recordings
```

> 本项目只用于保存你有权下载、平台允许下载或公开可访问的媒体内容。不提供 DRM 绕过、付费墙绕过或访问控制规避功能。

## 正常下载流程

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
  ├─ MP4 / WebM / FLV / 音频 → 浏览器直接保存
  ├─ HLS / M3U8 / DASH → 清晰度页 → 选清晰度 → 8 路并发 → FFmpeg 合并
  └─ 视频轨 + 音频轨分离 → “下载当前视频”优先走页面解析并自动合并
```

如果播放几秒后依然捕获不到媒体，或者网页只暴露 blob / MediaSource 播放对象，则使用：

```text
录制兜底
```

录制兜底是**实时标签页录制**，不是原始媒体文件的无损提取。它的价值是：当网页能播放但真实 URL 抓不到时，仍然可以把当前标签页实际播放出的画面与声音保存下来。

## 录制兜底使用方法

1. 在目标视频页面点击 OmniFetch。
2. 点击 **“录制兜底”**。
3. 新页面打开后点击 **“开始录制”**。
4. 切回原视频标签页，从你想保存的位置开始正常播放。
5. 录制结束后回到录制页。
6. 点击 **“停止并保存”**。
7. 浏览器弹出保存窗口，通常输出 WebM。

录制模式会尝试同时捕获标签页画面和标签页声音。关闭录制页面会中断当前录制。

## HLS / DASH 清晰度页

当捕获到 M3U8/HLS 或 DASH/MPD 时，扩展会打开独立下载页。页面会尝试显示：

- 1080P / 720P / 480P 等可用分辨率
- 码率
- FPS
- 视频格式
- 协议
- 预估文件大小（站点提供时）
- 是否需要自动补最佳音频轨

默认把最高分辨率排在前面。选中 video-only 格式时，OmniFetch 会尝试自动匹配最佳音频并用 FFmpeg 合并。

HLS / DASH 分片下载目前默认：

```text
8 路并发
```

下载页会显示进度、速度、ETA 和分片数量。

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

## 安装扩展

Chrome：

```text
chrome://extensions/
```

Edge：

```text
edge://extensions/
```

开启开发者模式 → 加载已解压扩展 → 选择 `extension` 文件夹 → 固定 OmniFetch 到工具栏。

## 推荐：只设置一次助手自动启动

为了让日常体验更接近普通浏览器下载扩展，在解压后的 OmniFetch 文件夹里双击：

```text
install-autostart.bat
```

它会为当前 Windows 用户创建登录启动快捷方式，并在后台隐藏启动 `OmniFetchHelper.exe`。不需要管理员权限。

设置完成后，不要随意移动整个 OmniFetch 文件夹，否则自动启动快捷方式路径会失效。

如果以后不想自动启动，双击：

```text
uninstall-autostart.bat
```

它只取消自动启动，不删除程序、扩展或下载的视频。

如果不设置自动启动，遇到 HLS/DASH、分离音视频或页面解析兜底时，也可以手动双击：

```text
run-helper.bat
```

普通 MP4 / WebM 等静态资源不需要助手。

## 通用架构，而不是平台白名单

OmniFetch 不依赖“这个网站叫什么”来决定能不能工作。它使用同一套浏览器嗅探逻辑处理 X、YouTube、TikTok、抖音、B站、Instagram、Facebook、小红书、快手、Vimeo、Twitch、Reddit 和普通网页。

实际成功率仍取决于站点的媒体分发方式、登录状态、短时签名、浏览器策略及 DRM。所谓“全平台”是通用架构目标，不是承诺任何网站永久 100% 成功。

## 当前本地接口

健康检查：

```text
GET http://127.0.0.1:17891/health
```

能力信息：

```text
GET http://127.0.0.1:17891/capabilities
```

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

## 当前限制

- DRM 加密视频不处理。
- 付费墙、访问控制、未授权内容不绕过。
- 录制兜底是实时录制，不是原始视频字节级提取，画质取决于标签页实际播放与浏览器编码。
- 某些站点媒体 URL 有极短有效期，需要播放后尽快下载。
- 某些视频页面会把音频和视频拆成独立流，单独点击某一资源可能只保存一个轨道；顶部“下载当前视频”会优先尝试自动合并。
- 浏览器升级或网站改版可能导致某条捕获路径暂时失效，需要继续适配。

## 下一步重点

- 下载任务暂停 / 取消 / 重试
- 多视频页面缩略图和预览
- 更好的重复媒体合并和广告过滤
- HLS 直播按时间段保存
- 进一步减少本地助手存在感

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
│  ├─ stream.js
│  ├─ recorder.html
│  ├─ recorder.css
│  └─ recorder.js
├─ helper/
│  ├─ server.py
│  └─ requirements.txt
├─ run-helper-exe.bat
├─ install-autostart.bat
├─ uninstall-autostart.bat
├─ start-helper-hidden.vbs
└─ .github/workflows/build-windows.yml
```
