# OmniFetch

全平台媒体下载助手。浏览网页时自动识别当前标签页中的视频资源，并支持一键保存到本地。

## 当前版本：v0.1.0 MVP

第一版采用 **Chrome / Edge 扩展 + Windows 本地下载助手**：

- 自动监听网页中的 MP4 / WebM / M3U8 / HLS / DASH 等常见媒体请求
- 扫描 `<video>` / `<audio>` 元素和浏览器资源请求
- 在扩展弹窗中列出当前页面检测到的媒体资源
- MP4 / WebM 等单文件资源可直接调用浏览器下载
- M3U8 / HLS 可交给本地助手下载
- “下载当前页面视频”会把当前页面 URL 交给 `yt-dlp` 解析
- 下载任务显示解析、下载、合并、完成/失败状态
- Chrome / Edge 自动识别
- 默认保存到：`%USERPROFILE%\Downloads\OmniFetch`

对于 X 这类动态网页，一般先播放视频 2–5 秒，让浏览器产生真实媒体请求，再打开 OmniFetch，识别率更高。

> 本项目只用于保存你有权下载、平台允许下载或公开可访问的媒体内容。不提供 DRM 绕过、付费墙绕过或访问控制规避功能。

## 目录

```text
OmniFetch/
├─ extension/                 Chrome / Edge 浏览器扩展
│  ├─ manifest.json
│  ├─ background.js
│  ├─ content.js
│  ├─ popup.html
│  ├─ popup.css
│  └─ popup.js
├─ helper/
│  ├─ server.py               本地下载服务
│  ├─ requirements.txt
│  └─ tools/                  可选 FFmpeg 目录
├─ install-helper.bat         源码版首次安装
├─ run-helper.bat             源码版启动
├─ run-helper-exe.bat         打包版启动
└─ .github/workflows/
   └─ build-windows.yml       自动生成 Windows 便携包
```

# 推荐：直接使用 Windows 便携包

仓库已经配置 GitHub Actions 自动构建：

```text
Actions → Build Windows Package → Artifacts → OmniFetch-Windows
```

下载并解压 `OmniFetch-Windows.zip` 后，目录中包含：

```text
OmniFetchHelper.exe
run-helper.bat
extension\
README.md
```

不需要另外安装 Python。

双击：

```text
run-helper.bat
```

保持窗口运行即可。

然后安装浏览器扩展。

# 安装 Chrome / Edge 扩展

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
3. 选择解压包中的 `extension` 文件夹
4. 将 OmniFetch 固定到浏览器工具栏

# 使用

1. 双击 `run-helper.bat` 启动本地助手
2. 打开包含视频的网页
3. 播放目标视频 2–5 秒
4. 点击浏览器右上角 OmniFetch
5. 根据情况选择：
   - `下载当前页面视频`：优先推荐，直接让本地助手解析当前页面
   - `直接下载`：保存检测到的 MP4 / WebM 等单文件
   - `助手下载`：处理检测到的 M3U8 / HLS 等流媒体地址

下载目录：

```text
%USERPROFILE%\Downloads\OmniFetch
```

# 源码版安装

如果不使用打包好的 EXE，可直接下载仓库源码。

首次双击：

```text
install-helper.bat
```

完成后双击：

```text
run-helper.bat
```

源码版需要 Python 3。

# FFmpeg

OmniFetch 会按以下顺序查找 FFmpeg：

1. `helper\tools\ffmpeg.exe`
2. Windows 系统 PATH 中的 `ffmpeg.exe`

没有 FFmpeg 时，程序仍会优先尝试下载已经包含音视频的单文件格式；但部分网站最高画质的视频和音频需要 FFmpeg 合并。

# 本地助手接口

本地服务只监听：

```text
127.0.0.1:17891
```

主要接口：

```text
GET  /health
GET  /jobs
GET  /jobs/<job_id>
POST /download
```

`POST /download` 示例：

```json
{
  "page_url": "https://example.com/video-page",
  "browser": "chrome"
}
```

# v0.2 计划

- 下载历史列表
- 下载目录设置
- 视频质量选择
- 页面多视频预览与区分
- 更完整的 HLS / DASH 资源整理
- 一键安装浏览器扩展
- Windows 托盘常驻助手
