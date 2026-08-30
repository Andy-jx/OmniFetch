# OmniFetch

全平台媒体下载助手。浏览网页时自动识别当前标签页中的视频资源，并支持一键保存到本地。

## 当前版本：v0.1.0 MVP

第一版采用 **Chrome / Edge 扩展 + Windows 本地下载助手** 的结构：

- 自动识别网页中的 MP4 / WebM / M3U8 / HLS 媒体请求
- 在扩展弹窗中列出当前页面检测到的视频资源
- MP4 / WebM 可直接调用浏览器下载
- M3U8 / HLS 可交给本地助手下载
- “下载当前页面视频”会把当前页面 URL 交给本地助手，由 `yt-dlp` 解析并保存
- 适合 X 等公开可访问的视频页面，也可用于大量采用常见网页媒体格式的网站
- 默认保存到：`%USERPROFILE%\Downloads\OmniFetch`

> 本项目只用于保存你有权下载、平台允许下载或公开可访问的媒体内容。不提供 DRM 绕过、付费墙绕过或访问控制规避功能。

## 目录

```text
OmniFetch/
├─ extension/                 浏览器扩展
│  ├─ manifest.json
│  ├─ background.js
│  ├─ content.js
│  ├─ popup.html
│  ├─ popup.css
│  └─ popup.js
├─ helper/                    Windows 本地下载助手
│  ├─ server.py
│  └─ requirements.txt
├─ install-helper.bat         首次安装本地助手
├─ run-helper.bat             启动本地助手
└─ README.md
```

## Windows 安装

### 1. 安装本地助手

双击：

```text
install-helper.bat
```

安装完成后双击：

```text
run-helper.bat
```

看到以下内容说明本地助手已启动：

```text
OmniFetch helper listening on http://127.0.0.1:17891
```

建议系统安装 FFmpeg。没有 FFmpeg 时助手仍会尝试下载可直接获取的单文件格式，但最高画质的视频/音频合并可能不可用。

### 2. 安装 Chrome / Edge 扩展

Chrome：打开 `chrome://extensions/`

Edge：打开 `edge://extensions/`

然后：

1. 开启“开发者模式”
2. 点击“加载已解压的扩展程序”
3. 选择仓库里的 `extension` 文件夹
4. 将 OmniFetch 固定到浏览器工具栏

## 使用

1. 启动 `run-helper.bat`
2. 打开一个包含视频的网页
3. 播放视频几秒，让网页产生真实媒体请求
4. 点击浏览器右上角 OmniFetch
5. 可以：
   - 点击“下载当前页面视频”让本地助手解析当前页面
   - 对检测到的 MP4 / WebM 点击“直接下载”
   - 对 M3U8 / HLS 点击“助手下载”

对于 X 这类动态网站，通常先播放几秒再打开扩展，识别成功率更高。

## 下一步计划

- 下载任务进度与历史记录
- 文件名规则与保存目录设置
- 更好的 HLS 质量选择
- 多视频页面识别
- 可选浏览器 Cookie 读取，用于用户本人已登录且有权访问的内容
- Windows 一键打包版本

## 开发说明

本地助手监听：

```text
http://127.0.0.1:17891
```

健康检查：

```text
GET /health
```

创建下载任务：

```text
POST /download
Content-Type: application/json

{
  "page_url": "https://example.com/video-page",
  "browser": "chrome"
}
```
