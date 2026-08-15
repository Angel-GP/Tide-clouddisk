# 📦 网盘管理器 (Pan Manager)

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078D6.svg)]()
[![License](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)]()

一个开箱即用的 **Windows 局域网网盘 / 文件共享工具**。纯 Python 标准库实现(网页 + 桌面双端),自动识别本机 IP,免登录下载、登录后上传,支持多账号权限、文件夹浏览、媒体在线预览、隐藏文件与前端文案自定义。也提供打包好的单文件 exe(免 Python 环境)。

> 🌐 典型场景:把电脑上的照片/视频/文件目录共享给同一 WiFi 下的手机、平板和其他电脑,对方用浏览器即可访问。

## ✨ 功能特性

- 🖥 **桌面管理端**(`网盘管理器`):一键启停服务、文件管理(上传/删除/重命名/复制链接/新建文件夹)、账号管理、服务器设置、自定义文案
- 🌐 **网页端**:所有人可访问,手机/电脑浏览器直接使用
- 🔍 **自动识别 IP**:启动即显示局域网访问地址(如 `http://192.168.1.100:8080/`)
- 📥 **下载免登录**:浏览/下载无需账号,支持断点续传(HTTP Range)
- 📤 **上传需登录**:拖拽上传、进度条、同名覆盖确认
- 👥 **多账号权限**:管理员可增删账号、重置密码、授予管理权限;登录失败 5 次锁定
- 📁 **文件夹浏览**:子目录导航、面包屑、新建文件夹;上传/下载/删除/重命名全路径化
- 🙈 **隐藏文件**:管理员可隐藏任意文件/文件夹,普通用户与未登录访客完全不可见(含直链拦截)
- 🎬 **媒体在线预览**:图片/视频/音频直接在浏览器播放(支持进度拖动、上一张/下一张、键盘切换)
- 🖱 **悬停预览**:鼠标悬停文件名即浮出缩略图 / 静音循环视频 / 迷你播放条
- 🔲 **双视图**:列表 / 大图标网格(图片大缩略图、视频封面)随时切换
- ✏️ **自定义前端文案**:桌面端可改网页全部展示文本,填 `null` 即隐藏该字段
- 📂 **上传目录可配置**:支持任意绝对路径,立即生效
- 🛡 **安全**:密码哈希(随机盐)存储、防路径穿越、登录会话过期、Cookie 登录态、隐藏文件访问控制

## 🚀 快速开始

### 方式一:直接运行 exe(推荐,无需 Python)

下载 Release 中的 `网盘管理器.exe`,双击运行即可。配置与上传文件保存在 exe 所在目录旁,整个文件夹可随意拷贝到其他 Windows 电脑使用。

### 方式二:源码运行(需要 Python 3.8+)

```bash
# 安装 Python 3.8+, 勾选 "Add Python to PATH"
双击 start_gui.bat        # 打开桌面管理端(服务自动启动)
# 或
双击 start.bat            # 纯服务模式(控制台窗口, Ctrl+C 停止)
```

首次启动生成默认管理员账号:

| 项目 | 值 |
| --- | --- |
| 用户名 | `admin` |
| 密码 | `admin123`(请尽快在「账号管理」中修改) |

启动后把「状态」页显示的局域网地址(如 `http://192.168.1.100:8080/`)发给同一局域网的人,对方免登录即可浏览下载。

## ⚙️ 使用说明

### 权限一览

| 操作 | 权限要求 |
| --- | --- |
| 浏览文件 / 下载 / 复制链接 / 在线预览 | 无需登录 |
| 上传文件 / 删除文件 / 新建文件夹 | 登录(任意账号) |
| 删除文件夹(连同内容) / 重命名 | 管理员 |
| 隐藏 / 取消隐藏文件或文件夹 | 管理员 |
| 服务器设置(IP/端口/标题/上限/上传目录) | 管理员 |
| 自定义前端文案 | 管理员(桌面管理端) |

### 命令行参数

```bash
python server.py --ip 192.168.1.100   # 临时指定监听 IP(覆盖配置文件)
python server.py --port 9000          # 临时指定端口
python server.py --no-browser         # 启动时不自动打开浏览器
python gui.py --selftest              # 桌面端无窗口自检
python gui.py --headless              # 无窗口纯服务模式
```

## 🗂 项目结构

```
pan/
├─ gui.py            桌面管理端(主程序, tkinter)
├─ server.py         服务端(纯 Python 标准库, 零第三方依赖)
├─ web/
│  └─ index.html     网页前端(单文件, 内嵌 CSS/JS)
├─ start_gui.bat     桌面端一键启动(优先使用 exe)
├─ start.bat         纯服务模式一键启动
├─ config.json       运行时自动生成(含账号密码哈希, 不入库)
└─ uploads/          默认上传目录(不入库)
```

## 🔌 HTTP API 简介

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/list?path=` | 文件/文件夹列表(公开, 隐藏项仅管理员可见) |
| GET | `/files/<path>` | 下载(公开, 支持 Range 断点续传) |
| GET | `/preview/<path>` | 媒体在线预览(公开, 支持 Range) |
| GET | `/api/info` | 服务器信息与自定义文案 |
| GET | `/api/session` | 会话校验(前端登录态同步) |
| POST | `/api/login` / `/api/logout` | 登录 / 退出(登录下发 Cookie) |
| POST | `/api/upload?name=&path=&overwrite=` | 上传(需登录, 原始字节流) |
| POST | `/api/mkdir` / `/api/delete` / `/api/rename` | 新建文件夹 / 删除 / 重命名 |
| POST | `/api/hide` | 隐藏 / 取消隐藏(管理员) |
| POST | `/api/users` 系列 | 账号管理(管理员) |
| POST | `/api/settings` | 服务器设置与文案(管理员) |

## 🔐 安全说明

本程序面向**局域网**使用:密码仅存哈希、登录态通过随机 token + Cookie 校验、防路径穿越、登录失败锁定、隐藏文件直链拦截。如需暴露公网,请务必自行加 HTTPS 反向代理并设置强密码。

## ❓ 常见问题

- **其他设备打不开**:确认同一局域网,且 Windows 防火墙放行(首次启动弹窗选「专用网络→允许」;程序也会尝试自动添加规则)
- **网页显示无法连接服务器**:确认桌面端/服务窗口仍在运行;本机可访问 `http://127.0.0.1:端口/`
- **忘记管理员密码**:删除 `config.json` 后重启(账号重置为 admin/admin123,文件不受影响)
- **端口被占用**:桌面端「服务器设置」里换端口,保存后自动重启生效
- **杀毒软件报毒**:单文件 exe 由 PyInstaller 打包,个别杀软会误报,添加信任即可

## 🛠 开发与打包

```bash
# 桌面端开发运行
python gui.py

# 打包单文件 exe (需先安装 PyInstaller)
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name PanManager --icon app.ico --add-data "web;web" gui.py
```

## 🤝 贡献

欢迎提交 Issue 与 Pull Request。改动后请运行 `python gui.py --selftest` 做基础自检。

## License

This project is partially AI‑assisted generated, with human review applied.
Licensed under **AGPL‑3.0**.
Any modified derivative works providing network‑based services must make source code available to users.
