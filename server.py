# -*- coding: utf-8 -*-
"""
网盘服务端 —— 纯 Python 标准库实现, 无需安装任何第三方依赖

支持两种部署场景:
  * 局域网共享: 同一网络内的手机/电脑通过浏览器访问
  * 公网服务器部署: 部署在云服务器/公网主机上, 任何人可通过公网地址访问
    (推荐前置 Nginx/Caddy 做 HTTPS 反向代理, 并设置强密码)

功能:
  * 自动识别本机 IP, 启动后自动打开浏览器
  * 多账号体系: 管理员可添加/删除账号、重置密码、授予管理权限
  * 可设置: 监听 IP / 端口 / 标题 / 上传大小上限 (管理员)
  * 浏览、下载: 无需登录 (下载支持断点续传)
  * 上传、删除: 需要登录; 设置、账号管理: 需要管理员

用法:
  python server.py [--ip 0.0.0.0] [--port 8000] [--no-browser]

首次启动自动创建 config.json, 默认账号: admin / admin123 (请尽快修改)
"""

import argparse
import hashlib
import http.cookies
import json
import os
import platform
import re
import secrets
import shutil
import socket
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

APP_VERSION = "1.2.5"   # 程序版本号 (发版时与 Release 标签保持一致)

# ----------------------------------------------------------------------------
# 基础配置
# ----------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    # PyInstaller 打包运行: 配置与上传目录放在 exe 旁边(持久), 网页资源在打包临时目录
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
    RES_DIR = getattr(sys, "_MEIPASS", APP_DIR)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    RES_DIR = APP_DIR

BASE_DIR = APP_DIR
WEB_DIR = os.path.join(RES_DIR, "web")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

DEFAULTS = {
    "ip": "",                # 监听 IP, 空 = 0.0.0.0 (自动)
    "port": 8000,            # 监听端口
    "title": "Tide cloud",    # 页面标题
    "max_upload_mb": 2048,   # 单文件上传上限 (MB), 0 = 不限
    "upload_dir": "uploads", # 文件保存目录 (相对程序目录)
    "users": [],             # 账号列表: [{"username","salt","password_hash","is_admin"}]
    "texts": {},             # 前端自定义文案 (键 -> 文本, 覆盖前端默认值)
    "hidden_files": [],      # 管理员隐藏的文件/文件夹相对路径列表 (普通用户与未登录不可见)
    "trust_proxy": False,    # 是否信任反向代理透传的 X-Forwarded-For (默认不信任, 防伪造绕过登录限流)
    "audit_log": True,       # 是否启用审计日志 (登录/上传/删除/重命名/隐藏/账号/设置变更写入 logs\audit_日期.log)
    "debug_log": False,      # 是否启用调试日志 (比普通日志更详细, 写入 logs\日期_v版本.log, 便于排查问题)
}

SESSION_HOURS = 12          # 登录有效期 (小时)
LOGIN_MAX_FAILS = 5         # 连续失败次数上限
LOGIN_LOCK_SECONDS = 300    # 触发锁定后的等待秒数

CONFIG = {}
SESSIONS = {}               # token -> 过期时间戳
LOGIN_FAILS = {}            # ip -> [失败次数, 最近失败时间]
RESTART_REQUESTED = False
CURRENT_SERVER = None
STOPPING = False            # 服务正在停止 (停止后旧连接再发请求返回 503)

INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_DEVICE = re.compile(r"COM[1-9]|LPT[1-9]")


LOG_SINKS = []  # 桌面管理端注册的日志回调
DEBUG_LOG = None  # 调试模式下的日志文件句柄
AUDIT_LOG = None  # 审计日志文件句柄 (安全事件记录)


def log(*args):
    msg = " ".join(str(a) for a in args)
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    try:
        print(line, flush=True)   # 打包为无控制台窗口模式时 stdout 可能不存在
    except Exception:
        pass
    for sink in LOG_SINKS:
        try:
            sink(line)
        except Exception:
            pass
    if DEBUG_LOG is not None:
        # 调试日志带完整日期时间, 比控制台普通日志更详细
        try:
            DEBUG_LOG.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
            DEBUG_LOG.flush()     # 实时落盘
        except Exception:
            pass


def dbg(*args):
    """调试日志专用: 仅写入调试日志文件 (普通日志不含这些细节, 不打扰控制台/管理端)"""
    if DEBUG_LOG is None:
        return
    try:
        DEBUG_LOG.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), " ".join(str(a) for a in args)))
        DEBUG_LOG.flush()
    except Exception:
        pass


def setup_audit_log():
    """审计日志: 安全事件写入 logs\\audit_日期.log (登录/上传/删除/重命名/隐藏/账号/设置变更)"""
    global AUDIT_LOG
    try:
        log_dir = os.path.join(APP_DIR, "logs")
        os.makedirs(log_dir, exist_ok=True)
        fpath = os.path.join(log_dir, "audit_%s.log" % time.strftime("%Y%m%d"))
        new_file = not os.path.exists(fpath)
        fh = open(fpath, "a", encoding="utf-8")
        if new_file:
            fh.write("=" * 60 + "\n")
            fh.write("Tide cloud 审计日志\n")
            fh.write("程序版本: v%s\n" % APP_VERSION)
            fh.write("启用时间: %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            fh.write("=" * 60 + "\n")
        fh.flush()
        AUDIT_LOG = fh
        log("审计日志已启用: %s" % fpath)
    except Exception as e:
        log("审计日志初始化失败: %s" % e)


def audit(event, detail=""):
    """记录安全审计事件 (同时写入审计文件与常规日志)"""
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), event + (("  " + detail) if detail else ""))
    if AUDIT_LOG is not None:
        try:
            AUDIT_LOG.write(line + "\n")
            AUDIT_LOG.flush()
        except Exception:
            pass
    log(event + (("  " + detail) if detail else ""))


def apply_audit_log(enabled):
    """运行时开启/关闭审计日志 (设置页开关立即生效)"""
    global AUDIT_LOG
    if enabled:
        if AUDIT_LOG is None:
            setup_audit_log()
    elif AUDIT_LOG is not None:
        try:
            AUDIT_LOG.close()
        except Exception:
            pass
        AUDIT_LOG = None
        log("审计日志已关闭")


def apply_debug_log(enabled):
    """运行时开启/关闭调试日志 (设置页开关立即生效)"""
    global DEBUG_LOG
    if enabled:
        if DEBUG_LOG is None:
            setup_debug_log()
    elif DEBUG_LOG is not None:
        try:
            DEBUG_LOG.close()
        except Exception:
            pass
        DEBUG_LOG = None
        log("调试日志已关闭")


def collect_sysinfo():
    """收集操作系统与硬件信息 (注册表 + ctypes, 纯标准库)"""
    lines = []
    try:
        lines.append("操作系统: %s" % platform.platform())
        lines.append("系统版本号: %s" % platform.version())
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            import winreg

            def _reg(key, sub, name):
                try:
                    k = winreg.OpenKey(key, sub)
                    v, _ = winreg.QueryValueEx(k, name)
                    winreg.CloseKey(k)
                    return str(v).strip()
                except Exception:
                    return ""

            bios = r"HARDWARE\DESCRIPTION\System\BIOS"
            manu = _reg(winreg.HKEY_LOCAL_MACHINE, bios, "SystemManufacturer")
            model = _reg(winreg.HKEY_LOCAL_MACHINE, bios, "SystemProductName")
            if model:
                lines.append("电脑型号: %s %s" % (manu, model))
            board = (_reg(winreg.HKEY_LOCAL_MACHINE, bios, "BaseBoardManufacturer")
                     + " " + _reg(winreg.HKEY_LOCAL_MACHINE, bios, "BaseBoardProduct")).strip()
            if board:
                lines.append("主板: %s" % board)
            cpu = _reg(winreg.HKEY_LOCAL_MACHINE,
                       r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString")
            if cpu:
                lines.append("CPU: %s" % cpu)
        except Exception:
            pass
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            m = MEMORYSTATUSEX()
            m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                lines.append("内存: %.1f GB" % (m.ullTotalPhys / 1024 ** 3))
        except Exception:
            pass
        try:
            mask = ctypes.windll.kernel32.GetLogicalDrives()
            drives = []
            for i in range(26):
                if mask & (1 << i):
                    d = chr(65 + i)
                    try:
                        u = shutil.disk_usage(d + ":\\")
                        drives.append("%s: %.1fGB" % (d, u.total / 1024 ** 3))
                    except Exception:
                        pass
            if drives:
                lines.append("硬盘: " + "  ".join(drives))
        except Exception:
            pass
    return lines


def setup_debug_log():
    """调试日志: 实时日志写入 logs\\日期_v版本.log, 文件头含程序版本与系统硬件信息"""
    global DEBUG_LOG
    try:
        debug_dir = os.path.join(APP_DIR, "logs")
        os.makedirs(debug_dir, exist_ok=True)
        fname = "%s_v%s.log" % (time.strftime("%Y%m%d"), APP_VERSION)
        fpath = os.path.join(debug_dir, fname)
        new_file = not os.path.exists(fpath)
        fh = open(fpath, "a", encoding="utf-8")
        if new_file:
            fh.write("=" * 60 + "\n")
            fh.write("Tide cloud 调试日志\n")
            fh.write("程序版本: v%s\n" % APP_VERSION)
            fh.write("日志时间: %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            for line in collect_sysinfo():
                fh.write(line + "\n")
            fh.write("=" * 60 + "\n")
        fh.flush()
        DEBUG_LOG = fh
        log("调试日志已启用: %s" % fpath)
        return fpath
    except Exception as e:
        log("调试日志初始化失败: %s" % e)
        return None


# ----------------------------------------------------------------------------
# 配置与密码
# ----------------------------------------------------------------------------
def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_password(password, salt):
    return sha256(salt + "@" + password)


def load_config():
    global CONFIG
    CONFIG = dict(DEFAULTS)
    raw = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                raw = data
                CONFIG.update(data)
        except Exception as e:
            log("读取 config.json 失败, 使用默认配置:", e)
    # 账号: 新版使用 users 列表; 旧版单账号配置自动迁移
    users = CONFIG.get("users")
    if not (isinstance(users, list) and users):
        salt = raw.get("salt") or secrets.token_hex(16)
        has_hash = bool(raw.get("password_hash"))
        CONFIG["users"] = [{
            "username": str(raw.get("username") or "admin"),
            "salt": salt,
            "password_hash": raw.get("password_hash") or hash_password("admin123", salt),
            "is_admin": True,
        }]
        save_config()
        if not has_hash:
            log("首次启动, 已生成初始账号: admin / admin123 (请尽快修改)")
    # 规范化
    try:
        CONFIG["port"] = int(CONFIG["port"])
    except (TypeError, ValueError):
        CONFIG["port"] = DEFAULTS["port"]
    try:
        CONFIG["max_upload_mb"] = int(CONFIG["max_upload_mb"])
    except (TypeError, ValueError):
        CONFIG["max_upload_mb"] = DEFAULTS["max_upload_mb"]
    if not isinstance(CONFIG.get("hidden_files"), list):
        CONFIG["hidden_files"] = []
    else:
        # 迁移旧数据: 隐藏路径统一按 Windows 语义归一化 (防尾部点/空格/大小写别名绕过)
        CONFIG["hidden_files"] = sorted({norm_component(h) for h in CONFIG["hidden_files"]})
    if not isinstance(CONFIG.get("texts"), dict):
        CONFIG["texts"] = {}
    if not isinstance(CONFIG.get("trust_proxy"), bool):
        CONFIG["trust_proxy"] = False
    if not isinstance(CONFIG.get("audit_log"), bool):
        CONFIG["audit_log"] = True
    if not isinstance(CONFIG.get("debug_log"), bool):
        CONFIG["debug_log"] = False


def save_config():
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(CONFIG, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


def update_settings(data):
    """校验并应用服务器设置 (网页端与桌面端共用); 返回 (need_restart, err)"""
    if not isinstance(data, dict):
        return False, "请求格式错误"
    need_restart = False

    ip = str(data.get("ip", "")).strip()
    if ip in ("", "auto"):
        ip = ""
    elif not is_valid_ip(ip):
        return False, "IP 地址格式不正确"
    if ip != CONFIG.get("ip", ""):
        need_restart = True

    try:
        port = int(data.get("port", CONFIG["port"]))
    except (TypeError, ValueError):
        return False, "端口必须是数字"
    if not (1 <= port <= 65535):
        return False, "端口必须在 1-65535 之间"
    if port != CONFIG["port"]:
        need_restart = True

    title = str(data.get("title", CONFIG["title"])).strip()[:50] or CONFIG["title"]

    try:
        max_mb = int(data.get("max_upload_mb", CONFIG["max_upload_mb"]))
    except (TypeError, ValueError):
        return False, "大小上限必须是数字"
    if max_mb < 0:
        return False, "大小上限不能为负数"

    ud = str(data.get("upload_dir", CONFIG.get("upload_dir", "uploads"))).strip()
    if not ud:
        return False, "上传目录不能为空"
    try:
        os.makedirs(resolve_upload_dir(ud), exist_ok=True)
    except OSError as e:
        return False, "无法创建上传目录: %s" % e

    texts = data.get("texts")
    if texts is not None:
        if not isinstance(texts, dict):
            return False, "文案设置格式不正确"
        cleaned = {}
        for k, v in texts.items():
            if not isinstance(k, str) or not isinstance(v, str):
                return False, "文案设置格式不正确"
            v = v.strip()
            if len(k) > 64 or len(v) > 200:
                return False, "文案内容过长 (每条不超过 200 字)"
            cleaned[k] = v
        merged = dict(CONFIG.get("texts", {}))
        merged.update(cleaned)
        CONFIG["texts"] = merged

    tp = data.get("trust_proxy", CONFIG.get("trust_proxy", False))
    if not isinstance(tp, bool):
        return False, "trust_proxy 必须是布尔值"
    if tp != CONFIG.get("trust_proxy", False):
        CONFIG["trust_proxy"] = tp

    al = data.get("audit_log", CONFIG.get("audit_log", True))
    if not isinstance(al, bool):
        return False, "audit_log 必须是布尔值"
    if al != CONFIG.get("audit_log", True):
        CONFIG["audit_log"] = al
        apply_audit_log(al)

    dl = data.get("debug_log", CONFIG.get("debug_log", False))
    if not isinstance(dl, bool):
        return False, "debug_log 必须是布尔值"
    if dl != CONFIG.get("debug_log", False):
        CONFIG["debug_log"] = dl
        apply_debug_log(dl)

    CONFIG.update({"ip": ip, "port": port, "title": title,
                   "max_upload_mb": max_mb, "upload_dir": ud})
    save_config()
    return need_restart, None


def resolve_upload_dir(value):
    """把配置中的上传目录解析为绝对路径 (支持绝对路径与相对路径)"""
    d = str(value or "uploads").strip().strip('"')
    if not d:
        d = "uploads"
    if os.path.isabs(d):
        return os.path.normpath(d)
    return os.path.join(BASE_DIR, d)


def upload_dir():
    full = resolve_upload_dir(CONFIG.get("upload_dir"))
    try:
        os.makedirs(full, exist_ok=True)
    except OSError:
        pass
    return full


# ----------------------------------------------------------------------------
# 网络与安全工具
# ----------------------------------------------------------------------------
def get_local_ips():
    """自动获取本机所有 IPv4 地址 (局域网与公网部署均适用)"""
    ips = set()
    # 方法1: UDP 探测默认路由对应的网卡 IP (最可靠, 不会真正发包)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        if not ip.startswith("127."):
            ips.add(ip)
        s.close()
    except Exception:
        pass
    # 方法2: 主机名解析所有地址
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass
    return sorted(ips)


def is_valid_ip(s):
    parts = s.split(".")
    if len(parts) != 4:
        return False
    for p in parts:
        if not p.isdigit() or int(p) > 255:
            return False
    return True


def safe_name(name):
    """校验并清理文件名, 防止路径穿越与非法字符"""
    if not name:
        return None
    name = name.replace("\\", "/").split("/")[-1]   # 只取最后一段
    name = name.strip()
    if not name or name in (".", ".."):
        return None
    if INVALID_CHARS.search(name):
        return None
    name = name.rstrip(" .")                        # Windows 不允许末尾是点或空格
    if not name or len(name) > 180:
        return None
    stem = name.split(".")[0].upper()
    if stem in ("CON", "PRN", "AUX", "NUL") or WINDOWS_DEVICE.fullmatch(stem):
        return None
    return name


def norm_component(c):
    """Windows 语义归一化: 裁掉尾部点/空格 + 统一小写 (FS 大小写不敏感)"""
    c = str(c).rstrip(" .")
    return c.casefold()


def valid_component(c):
    """目录/文件名分量校验 (不含路径分隔符)"""
    if not c or c in (".", ".."):
        return False
    if INVALID_CHARS.search(c):
        return False
    stem = c.split(".")[0].upper()
    if stem in ("CON", "PRN", "AUX", "NUL") or WINDOWS_DEVICE.fullmatch(stem):
        return False
    return True


def safe_relpath(p):
    """把用户传入的相对路径规范化 (POSIX 风格); 含 .. 或非法分量时返回 None

    每个分量裁掉尾部点/空格 (Windows 语义, 与文件系统解析一致), 因此:
      - ".. ." / ".. " 等别名会被识别为 ".." 并拒绝
      - "name." 与 "name" 解析到同一文件, 无法借此绕过隐藏检查
    (保留原始大小写, 仅用于文件系统访问; 匹配类检查另行归一化)
    """
    p = str(p or "").replace("\\", "/").strip()
    if not p:
        return ""
    norm = []
    for c in p.strip("/").split("/"):
        c2 = c.rstrip(" .")
        # 注意: ".." 经 rstrip 后会变成空串, 必须拒绝, 不能跳过
        # (否则 ".." / ".. ." 被当成根目录放行)
        if c2 in (".", "..") or not valid_component(c2):
            return None
        norm.append(c2)
    return "/".join(norm)


def hidden_set():
    hs = CONFIG.get("hidden_files")
    return set(hs) if isinstance(hs, list) else set()


def hidden_state(rel):
    """返回 (是否隐藏, 命中的隐藏项) — 该项本身或其任意父目录被隐藏都算

    匹配按 Windows 语义归一化 (去尾部点/空格 + 小写), 别名无法绕过。
    """
    hs = {norm_component(h) for h in hidden_set()}
    cur = ""
    for part in (rel or "").split("/"):
        if not part:
            continue
        cur = cur + "/" + part if cur else part
        if norm_component(cur) in hs:
            return True, norm_component(cur)
    return False, None


def set_hidden(rel, hidden):
    hs = hidden_set()
    if hidden:
        hs.add(norm_component(rel))
    else:
        hs.discard(rel)
        hs.discard(norm_component(rel))
    CONFIG["hidden_files"] = sorted(hs)
    save_config()


def migrate_hidden(old_rel, new_rel=None):
    """重命名/删除后同步隐藏标记 (new_rel=None 表示删除)"""
    hs = hidden_set()
    old_n = norm_component(old_rel)
    changed = False
    for h in list(hs):
        if h == old_n or h.startswith(old_n + "/"):
            changed = True
            hs.discard(h)
            if new_rel is not None:
                hs.add(norm_component(new_rel + h[len(old_n):]))
    if changed:
        CONFIG["hidden_files"] = sorted(hs)
        save_config()


# ----------------------------------------------------------------------------
# 文件所有权 (水平越权防护: 多用户场景下仅属主/管理员可写)
# ----------------------------------------------------------------------------
META_PATH = os.path.join(APP_DIR, "filemeta.json")


def load_meta():
    try:
        with open(META_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_meta(d):
    try:
        tmp = META_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, META_PATH)
    except Exception:
        pass


def meta_can_write(rel, username, is_admin):
    """写操作所有权校验: 属主本人或管理员可写; 无归属记录的历史文件仅管理员可写"""
    owner = load_meta().get(norm_component(rel))
    if owner is None:
        return is_admin
    return is_admin or owner == username


def meta_set_owner(rel, username):
    d = load_meta()
    d[norm_component(rel)] = username
    save_meta(d)


def meta_migrate(old_rel, new_rel=None):
    """重命名/删除后同步所有权记录 (new_rel=None 表示删除, 前缀迁移)"""
    d = load_meta()
    old_n = norm_component(old_rel)
    changed = False
    for k in list(d.keys()):
        if k == old_n or k.startswith(old_n + "/"):
            owner = d.pop(k)
            changed = True
            if new_rel is not None:
                d[norm_component(new_rel + k[len(old_n):])] = owner
    if changed:
        save_meta(d)


def create_session(username):
    token = secrets.token_hex(24)
    SESSIONS[token] = [username, time.time() + SESSION_HOURS * 3600]
    return token


def get_token_cookie(handler):
    """从请求 Cookie 中提取登录 token"""
    try:
        c = http.cookies.SimpleCookie()
        c.load(handler.headers.get("Cookie") or "")
        m = c.get("pan_token")
        return m.value if m else None
    except Exception:
        return None


def extract_token(handler):
    """从请求头或 Cookie 中提取 token (不做有效性校验)"""
    h = handler.headers.get("X-Auth-Token") or ""
    auth = handler.headers.get("Authorization") or ""
    if not h and auth.lower().startswith("bearer "):
        h = auth[7:].strip()
    if not h:
        h = get_token_cookie(handler) or ""
    h = h.strip()
    return h or None


def check_token(handler):
    """已登录则返回用户名, 否则返回 None (支持请求头与 Cookie 两种方式)"""
    h = extract_token(handler)
    if not h:
        return None
    sess = SESSIONS.get(h)
    if not sess:
        return None
    username, exp = sess
    if exp < time.time():
        SESSIONS.pop(h, None)
        return None
    return username


def find_user(username):
    for u in CONFIG.get("users", []):
        if u.get("username") == username:
            return u
    return None


def check_admin(handler):
    """管理员则返回用户名, 否则返回 None"""
    username = check_token(handler)
    if not username:
        return None
    u = find_user(username)
    if u and u.get("is_admin"):
        return username
    return None


def count_admin():
    return sum(1 for u in CONFIG.get("users", []) if u.get("is_admin"))


def kill_sessions_of(username):
    for t in [t for t, sess in SESSIONS.items() if sess and sess[0] == username]:
        SESSIONS.pop(t, None)


def client_ip(handler):
    """客户端 IP (登录限流用)

    默认使用真实 socket 地址, 防止攻击者伪造 X-Forwarded-For 绕过限流;
    仅当配置 trust_proxy=true (确定部署在可信反向代理之后) 时才信任 XFF。
    """
    if CONFIG.get("trust_proxy"):
        xff = handler.headers.get("X-Forwarded-For") or ""
        if xff:
            return xff.split(",")[0].strip() or handler.client_address[0]
    return handler.client_address[0]


def login_allowed(ip):
    entry = LOGIN_FAILS.get(ip)
    if not entry:
        return True, 0
    count, first = entry
    if count >= LOGIN_MAX_FAILS and time.time() - first < LOGIN_LOCK_SECONDS:
        return False, int(LOGIN_LOCK_SECONDS - (time.time() - first))
    if time.time() - first >= LOGIN_LOCK_SECONDS:
        LOGIN_FAILS.pop(ip, None)
    return True, 0


# ----------------------------------------------------------------------------
# HTTP 工具
# ----------------------------------------------------------------------------
def send_json(handler, data, status=200, close=False):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    for hname, hval in getattr(handler, "_pending_headers", []):
        handler.send_header(hname, hval)
    handler._pending_headers = []
    if close:
        # 请求体未被读取时, 必须关闭连接, 否则残留字节会破坏 keep-alive 下一条请求
        handler.close_connection = True
        handler.send_header("Connection", "close")
    handler.end_headers()
    try:
        handler.wfile.write(body)
    except OSError:
        pass


def ok(handler, **kw):
    send_json(handler, {"ok": True, **kw})


def fail(handler, msg, status=400):
    send_json(handler, {"ok": False, "msg": msg}, status, close=True)


def read_json_body(handler, max_bytes=65536):
    # 要求 Content-Type 为 application/json, 防止 text/plain 等跨站提交 (CSRF 缓解)
    ct = (handler.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ct != "application/json":
        return None
    cl = handler.headers.get("Content-Length")
    if cl is None:
        return None
    try:
        length = int(cl)
    except ValueError:
        return None
    if length <= 0 or length > max_bytes:
        return None
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}

# 可在线预览的媒体文件类型 (视频/音频/图片)
# 注意: 不含 .svg —— SVG 可携带脚本, 按附件下载处理, 绝不内联返回
MEDIA_TYPES = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".ogv": "video/ogg",
    ".mov": "video/quicktime", ".avi": "video/x-msvideo", ".mkv": "video/x-matroska",
    ".flv": "video/x-flv", ".ts": "video/mp2t", ".3gp": "video/3gpp",
    ".mpg": "video/mpeg", ".mpeg": "video/mpeg", ".wmv": "video/x-ms-wmv",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac", ".ogg": "audio/ogg",
    ".m4a": "audio/mp4", ".aac": "audio/aac", ".opus": "audio/ogg",
    ".wma": "audio/x-ms-wma", ".aif": "audio/aiff", ".aiff": "audio/aiff",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".jfif": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp", ".ico": "image/x-icon",
    ".avif": "image/avif", ".tif": "image/tiff", ".tiff": "image/tiff",
}


def request_restart():
    global RESTART_REQUESTED
    RESTART_REQUESTED = True
    if CURRENT_SERVER is not None:
        try:
            CURRENT_SERVER.shutdown()
        except Exception:
            pass


# ----------------------------------------------------------------------------
# HTTP 请求处理
# ----------------------------------------------------------------------------
class PanHandler(BaseHTTPRequestHandler):
    server_version = "PanServer/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # 使用自定义日志

    def _resolve(self, rel):
        """相对路径 -> 上传目录内的绝对路径; 越界或非法返回 None"""
        base = os.path.abspath(upload_dir())
        full = os.path.abspath(os.path.join(base, *rel.split("/"))) if rel else base
        if full != base and not full.startswith(base + os.sep):
            return None
        return full

    def handle_one_request(self):
        self._req_start = time.time()
        # 客户端中途断开(如关闭浏览器、停止服务)是正常现象, 静默处理不打印堆栈
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, socket.timeout):
            self.close_connection = True

    def _log(self, status):
        log("%s  %s %s  ->  %s" % (self.client_address[0], self.command, self.path, status))
        # 调试日志: 记录每个请求的完整细节 (耗时/UA/登录用户), 便于排查问题
        try:
            elapsed = (time.time() - self._req_start) * 1000
        except AttributeError:
            elapsed = -1
        user = check_token(self) or "-"
        ua = (self.headers.get("User-Agent") or "-")[:120]
        dbg("请求  %s  %s  ->  %s   耗时=%.0fms   用户=%s   UA=%s" % (
            self.client_address[0], self.command, self.path, elapsed, user, ua))

    # ------------------------- GET -------------------------
    def do_GET(self):
        if STOPPING:
            self._log(503)
            return fail(self, "服务已停止", 503)
        try:
            self._route_get()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        except Exception as e:
            log("处理 GET 出错:", repr(e))
            try:
                fail(self, "服务器内部错误", 500)
            except Exception:
                pass

    def _route_get(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        if path in ("/", "/index.html"):
            self._log(200)
            self._serve_file(os.path.join(WEB_DIR, "index.html"))
        elif path.startswith("/web/"):
            self._serve_static(path[len("/web/"):])
        elif path.startswith("/files/"):
            self._download(path[len("/files/"):])
        elif path.startswith("/preview/"):
            self._preview(path[len("/preview/"):])
        elif path.startswith("/thumb/"):
            self._thumb(path[len("/thumb/"):])
        elif path == "/api/info":
            self._api_info()
        elif path == "/api/list":
            self._api_list()
        elif path == "/api/session":
            self._api_session()
        elif path == "/api/settings":
            self._api_get_settings()
        elif path == "/api/users":
            self._api_list_users()
        elif path == "/api/zip":
            self._api_zip()
        else:
            self._log(404)
            fail(self, "页面不存在", 404)

    def _serve_static(self, rel):
        name = safe_name(rel)
        full = os.path.join(WEB_DIR, name) if name else None
        abspath = os.path.abspath(full) if full else ""
        if not full or not os.path.isfile(full) or not abspath.startswith(os.path.abspath(WEB_DIR) + os.sep):
            self._log(404)
            return fail(self, "文件不存在", 404)
        self._log(200)
        self._serve_file(full)

    def _serve_file(self, full, status=200):
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            self._log(404)
            return fail(self, "文件不存在", 404)
        ext = os.path.splitext(full)[1].lower()
        ctype = CONTENT_TYPES.get(ext, "application/octet-stream")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if ext == ".html":
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(data)
        except OSError:
            pass

    def _api_info(self):
        self._log(200)
        ok(self,
           title=CONFIG["title"],
           port=CONFIG["port"],
           addresses=get_local_ips(),
           bound_ip=CONFIG.get("ip") or "0.0.0.0",
           max_upload_mb=CONFIG["max_upload_mb"],
           texts=CONFIG.get("texts", {}))

    def _api_list(self):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        rel = safe_relpath((query.get("path") or [""])[0])
        if rel is None:
            self._log(400)
            return fail(self, "路径不合法")
        full = self._resolve(rel)
        if not full or not os.path.isdir(full):
            self._log(404)
            return fail(self, "目录不存在", 404)
        is_admin = bool(check_admin(self))
        # 当前目录本身被隐藏时, 非管理员视为不存在
        if rel and hidden_state(rel)[0] and not is_admin:
            self._log(404)
            return fail(self, "目录不存在", 404)
        hs = hidden_set()
        try:
            names = os.listdir(full)
        except OSError:
            names = []
        items = []
        for n in names:
            p = os.path.join(full, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            relpath = rel + "/" + n if rel else n
            hid = relpath in hs
            if hid and not is_admin:
                continue
            is_dir = os.path.isdir(p)
            items.append({"name": n,
                          "size": 0 if is_dir else st.st_size,
                          "mtime": st.st_mtime,
                          "is_dir": is_dir,
                          "hidden": hid})
        items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        self._log(200)
        ok(self, path=rel, files=items)

    def _api_get_settings(self):
        if not check_admin(self):
            self._log(403)
            return fail(self, "需要管理员权限", 403)
        self._log(200)
        ok(self,
           ip=CONFIG.get("ip", ""),
           port=CONFIG["port"],
           title=CONFIG["title"],
           max_upload_mb=CONFIG["max_upload_mb"],
           upload_dir=CONFIG.get("upload_dir", "uploads"),
           trust_proxy=CONFIG.get("trust_proxy", False),
           audit_log=CONFIG.get("audit_log", True),
           debug_log=CONFIG.get("debug_log", False),
           texts=CONFIG.get("texts", {}),
           addresses=get_local_ips())

    # ------------------------- 批量打包下载 -------------------------
    def _api_zip(self):
        """批量下载: 把所选文件/文件夹打包为 zip 返回 (下载免登录; 隐藏项仅管理员可见)"""
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        raws = query.get("p") or query.get("paths") or []
        rels = []
        for r in raws:
            rel = safe_relpath(r)
            if rel and rel not in rels:
                rels.append(rel)
        if not rels:
            self._log(400)
            return fail(self, "未选择要下载的文件", 400)
        is_admin = bool(check_admin(self))
        base = os.path.abspath(upload_dir())
        entries = []  # (zip 内相对路径, 磁盘绝对路径)
        for rel in rels:
            full = self._resolve(rel)
            if not full or not os.path.lexists(full):
                continue
            if hidden_state(rel)[0] and not is_admin:
                continue
            if os.path.isdir(full):
                for root, dirs, files in os.walk(full):
                    dirs[:] = [d for d in dirs if is_admin or
                               not hidden_state(os.path.relpath(os.path.join(root, d), base).replace(os.sep, "/"))[0]]
                    for fn in files:
                        p = os.path.join(root, fn)
                        child_rel = os.path.relpath(p, base).replace(os.sep, "/")
                        if hidden_state(child_rel)[0] and not is_admin:
                            continue
                        entries.append((child_rel, p))
            else:
                entries.append((rel, full))
        if not entries:
            self._log(404)
            return fail(self, "文件不存在或不可下载", 404)
        import tempfile
        import zipfile
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        tmp.close()
        try:
            with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_STORED) as zf:
                for arc, full in entries:
                    try:
                        zf.write(full, arc)
                    except OSError:
                        continue
            size = os.path.getsize(tmp.name)
            name = "Tide_cloud_%s.zip" % time.strftime("%Y%m%d_%H%M%S")
            dbg("批量打包下载: %d 个文件 -> %s (%d 字节)" % (len(entries), name, size))
            self._send_file(tmp.name, name, size, 0, size, 200, "application/zip")
        finally:
            try:
                os.remove(tmp.name)
            except OSError:
                pass

    # ------------------------- 下载 / 预览 -------------------------
    def _download(self, raw_name):
        rel = safe_relpath(raw_name)
        full = self._resolve(rel) if rel is not None else None
        if not full or not os.path.isfile(full):
            self._log(404)
            return fail(self, "文件不存在", 404)
        if hidden_state(rel)[0] and not check_admin(self):
            self._log(404)
            return fail(self, "文件不存在", 404)
        name = rel.split("/")[-1]
        size = os.path.getsize(full)
        range_header = self.headers.get("Range")
        if range_header:
            self._download_range(full, name, size, range_header)
        else:
            self._send_file(full, name, size, 0, size, 200)

    def _preview(self, raw_name):
        """在线预览媒体文件 (视频/音频/图片), 免登录, 支持拖动进度"""
        rel = safe_relpath(raw_name)
        full = self._resolve(rel) if rel is not None else None
        if not full or not os.path.isfile(full):
            self._log(404)
            return fail(self, "文件不存在", 404)
        if hidden_state(rel)[0] and not check_admin(self):
            self._log(404)
            return fail(self, "文件不存在", 404)
        name = rel.split("/")[-1]
        ext = os.path.splitext(name)[1].lower()
        ctype = MEDIA_TYPES.get(ext)
        if not ctype:
            self._log(415)
            return fail(self, "该文件类型不支持在线预览", 415)
        size = os.path.getsize(full)
        range_header = self.headers.get("Range")
        if range_header:
            self._download_range(full, name, size, range_header, ctype, "inline")
        else:
            self._send_file(full, name, size, 0, size, 200, ctype, "inline")

    def _thumb(self, raw_name):
        """生成并返回 320px 缩略图 (浏览模式/悬停预览用, 磁盘缓存, 大幅降低大图加载开销)"""
        rel = safe_relpath(raw_name)
        full = self._resolve(rel) if rel is not None else None
        if not full or not os.path.isfile(full):
            self._log(404)
            return fail(self, "文件不存在", 404)
        if hidden_state(rel)[0] and not check_admin(self):
            self._log(404)
            return fail(self, "文件不存在", 404)
        try:
            import hashlib
            from PIL import Image
            cache_dir = os.path.join(APP_DIR, "thumbs")
            os.makedirs(cache_dir, exist_ok=True)
            key = hashlib.md5(rel.encode("utf-8")).hexdigest() + ".jpg"
            cache_path = os.path.join(cache_dir, key)
            src_mtime = os.path.getmtime(full)
            if not os.path.exists(cache_path) or os.path.getmtime(cache_path) < src_mtime:
                dbg("缩略图生成: %s" % rel)
                im = Image.open(full)
                im.thumbnail((320, 320))
                if im.mode not in ("RGB", "L"):
                    im = im.convert("RGB")
                im.save(cache_path, "JPEG", quality=82)
            else:
                dbg("缩略图命中缓存: %s" % rel)
            self._log(200)
            return self._serve_file(cache_path)
        except Exception:
            pass
        # 无法生成缩略图 (格式不支持等): 回退为完整预览
        return self._preview(raw_name)

    def _send_file(self, full, name, size, start, length, status,
                   content_type="application/octet-stream", disposition="attachment"):
        dbg("发送文件  %s  大小=%d  起点=%d 长度=%d  类型=%s  方式=%s" % (
            name, size, start, length, content_type, disposition))
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        if disposition == "inline":
            # 内联预览 (图片/音视频): 禁止任何脚本执行, 防上传内容被当文档渲染
            self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
            self.send_header("X-Content-Type-Options", "nosniff")
        quoted = urllib.parse.quote(name, safe="")
        ascii_fb = name.encode("ascii", "ignore").decode().replace('"', "") or "download"
        if disposition == "inline":
            self.send_header("Content-Disposition", "inline; filename*=UTF-8''%s" % quoted)
        else:
            self.send_header("Content-Disposition",
                             'attachment; filename="%s"; filename*=UTF-8\'\'%s' % (ascii_fb, quoted))
        if status == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, start + length - 1, size))
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        sent = 0
        try:
            with open(full, "rb") as f:
                f.seek(start)
                while sent < length:
                    chunk = f.read(min(256 * 1024, length - sent))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except OSError:
                        break
                    sent += len(chunk)
        except OSError:
            pass
        self._log(status)

    def _download_range(self, full, name, size, header,
                        content_type="application/octet-stream", disposition="attachment"):
        m = re.match(r"bytes=(\d*)-(\d*)$", header.strip())
        if not m:
            return self._send_file(full, name, size, 0, size, 200, content_type, disposition)
        a, b = m.group(1), m.group(2)
        if a == "" and b == "":
            return self._send_file(full, name, size, 0, size, 200, content_type, disposition)
        if a == "":                      # 最后 b 字节
            suffix = min(int(b), size)
            if suffix <= 0:
                return self._range_not_satisfiable(size)
            return self._send_file(full, name, size, size - suffix, suffix, 206, content_type, disposition)
        start = int(a)
        if start >= size:
            return self._range_not_satisfiable(size)
        end = int(b) if b != "" else size - 1
        end = min(end, size - 1)
        if end < start:
            return self._send_file(full, name, size, 0, size, 200, content_type, disposition)
        return self._send_file(full, name, size, start, end - start + 1, 206, content_type, disposition)

    def _range_not_satisfiable(self, size):
        self.send_response(416)
        self.send_header("Content-Range", "bytes */%d" % size)
        self.send_header("Content-Length", "0")
        self.end_headers()
        self._log(416)

    # ------------------------- POST -------------------------
    def do_POST(self):
        if STOPPING:
            self._log(503)
            return fail(self, "服务已停止", 503)
        try:
            parsed = urllib.parse.urlsplit(self.path)
            path = urllib.parse.unquote(parsed.path)
            query = urllib.parse.parse_qs(parsed.query)
            if path == "/api/login":
                self._api_login()
            elif path == "/api/logout":
                self._api_logout()
            elif path == "/api/upload":
                self._api_upload(query)
            elif path == "/api/delete":
                self._api_delete()
            elif path == "/api/mkdir":
                self._api_mkdir()
            elif path == "/api/hide":
                self._api_hide()
            elif path == "/api/rename":
                self._api_rename()
            elif path == "/api/settings":
                self._api_save_settings()
            elif path == "/api/users":
                self._api_add_user()
            elif path == "/api/users/delete":
                self._api_delete_user()
            elif path == "/api/users/password":
                self._api_reset_password()
            elif path == "/api/users/admin":
                self._api_set_admin()
            else:
                self._log(404)
                fail(self, "接口不存在", 404)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        except Exception as e:
            log("处理 POST 出错:", repr(e))
            try:
                fail(self, "服务器内部错误", 500)
            except Exception:
                pass

    def _api_login(self):
        ip = client_ip(self)
        allowed, wait = login_allowed(ip)
        if not allowed:
            self._log(429)
            audit("登录锁定拒绝", "来源=%s" % ip)
            return fail(self, "尝试次数过多, 请 %d 秒后再试" % wait, 429)
        data = read_json_body(self)
        if not isinstance(data, dict):
            self._log(400)
            return fail(self, "请求格式错误")
        name = str(data.get("username", "")).strip()
        pwd = str(data.get("password", ""))
        u = find_user(name)
        if u and secrets.compare_digest(hash_password(pwd, u["salt"]), u["password_hash"]):
            LOGIN_FAILS.pop(ip, None)
            token = create_session(name)
            audit("登录成功", "用户=%s 来源=%s" % (name, ip))
            # 同时下发 Cookie, 使下载/预览等普通链接请求也自动携带登录态
            self._pending_headers = getattr(self, "_pending_headers", []) + [
                ("Set-Cookie",
                 "pan_token=%s; Path=/; Max-Age=%d; SameSite=Lax; HttpOnly" % (token, SESSION_HOURS * 3600))]
            self._log(200)
            return ok(self, token=token, username=name, is_admin=bool(u.get("is_admin")))
        entry = LOGIN_FAILS.get(ip, [0, time.time()])
        entry[0] += 1
        entry[1] = time.time()
        LOGIN_FAILS[ip] = entry
        audit("登录失败", "用户=%s 来源=%s 第%d次" % (name, ip, entry[0]))
        self._log(401)
        fail(self, "用户名或密码错误", 401)

    def _api_logout(self):
        h = extract_token(self) or ""
        SESSIONS.pop(h, None)
        # 清除登录 Cookie
        self._pending_headers = getattr(self, "_pending_headers", []) + [
            ("Set-Cookie", "pan_token=; Path=/; Max-Age=0")]
        self._log(200)
        ok(self)

    def _api_session(self):
        """会话校验: 前端页面加载时用于同步登录状态"""
        token = extract_token(self)
        username = check_token(self)
        if not username:
            self._log(200)
            return ok(self, logged=False)
        u = find_user(username)
        self._log(200)
        ok(self, logged=True, token=token, username=username,
           is_admin=bool(u.get("is_admin")) if u else False)

    def _api_upload(self, query):
        me = check_token(self)
        if not me:
            self._log(401)
            return fail(self, "需要登录", 401)
        name = safe_name((query.get("name") or [""])[0])
        if not name:
            self._log(400)
            return fail(self, "文件名不合法")
        rel = safe_relpath((query.get("path") or [""])[0])
        if rel is None:
            self._log(400)
            return fail(self, "路径不合法")
        if rel and hidden_state(rel)[0] and not check_admin(self):
            self._log(404)
            return fail(self, "目录不存在", 404)
        target = self._resolve(rel)
        if not target or not os.path.isdir(target):
            self._log(404)
            return fail(self, "目录不存在", 404)
        cl = self.headers.get("Content-Length")
        if cl is None:
            self._log(411)
            return fail(self, "缺少文件内容", 411)
        try:
            length = int(cl)
        except ValueError:
            self._log(411)
            return fail(self, "缺少文件内容", 411)
        if length < 0:
            self._log(411)
            return fail(self, "缺少文件内容", 411)
        max_bytes = CONFIG["max_upload_mb"] * 1024 * 1024
        if max_bytes > 0 and length > max_bytes:
            self._log(413)
            return fail(self, "文件超过大小上限 %d MB" % CONFIG["max_upload_mb"], 413)

        up = upload_dir()
        full = os.path.join(target, name)
        if len(full) > 250:
            self._log(400)
            return fail(self, "文件名过长")
        file_rel = rel + "/" + name if rel else name
        overwrite = (query.get("overwrite") or [""])[0] == "1"
        if os.path.exists(full) and not overwrite:
            self._log(409)
            return send_json(self, {"ok": False, "exists": True, "msg": "同名文件已存在"}, 409, close=True)
        if os.path.exists(full) and overwrite:
            # 水平越权防护: 覆盖他人文件需属主或管理员
            is_admin = bool(check_admin(self))
            if not meta_can_write(file_rel, me, is_admin):
                self._log(403)
                return send_json(self, {"ok": False, "msg": "无权覆盖他人上传的文件"}, 403, close=True)

        written = 0
        error = None
        try:
            with open(full, "wb") as f:
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        raise IOError("客户端中断")
                    f.write(chunk)
                    written += len(chunk)
                    remaining -= len(chunk)
        except Exception as e:
            error = str(e)
        if written != length or error:
            try:
                os.remove(full)
            except OSError:
                pass
            self._log(500)
            return fail(self, "上传中断或失败", 500)
        meta_set_owner(file_rel, me)   # 记录文件属主
        audit("上传文件", "路径=%s 大小=%d 用户=%s 来源=%s" % (file_rel, written, me, client_ip(self)))
        log("上传完成: %s (%d 字节) 来自 %s" % (name, written, self.client_address[0]))
        self._log(200)
        ok(self, name=name, size=written)

    def _api_delete(self):
        me = check_token(self)
        if not me:
            self._log(401)
            return fail(self, "需要登录", 401)
        data = read_json_body(self)
        if not isinstance(data, dict):
            self._log(400)
            return fail(self, "请求格式错误")
        raw = str(data.get("path") or data.get("name") or "")
        rel = safe_relpath(raw)
        full = self._resolve(rel) if rel is not None else None
        if not full or not os.path.lexists(full):
            self._log(404)
            return fail(self, "文件不存在", 404)
        is_dir = os.path.isdir(full)
        if is_dir and not check_admin(self):
            self._log(403)
            return fail(self, "删除文件夹需要管理员权限", 403)
        if hidden_state(rel)[0] and not check_admin(self):
            self._log(404)
            return fail(self, "文件不存在", 404)
        if not is_dir:
            # 水平越权防护: 删除文件需属主或管理员
            is_admin = bool(check_admin(self))
            if not meta_can_write(rel, me, is_admin):
                self._log(403)
                return fail(self, "无权删除他人上传的文件", 403)
        try:
            if is_dir:
                shutil.rmtree(full)
            else:
                os.remove(full)
        except OSError as e:
            self._log(500)
            return fail(self, "删除失败: %s" % e, 500)
        migrate_hidden(rel, None)
        meta_migrate(rel, None)
        audit("删除", "路径=%s 类型=%s 用户=%s 来源=%s" % (rel, "文件夹" if is_dir else "文件", me, client_ip(self)))
        log("已删除: %s%s" % (rel, " (文件夹)" if is_dir else ""))
        self._log(200)
        ok(self)

    def _api_mkdir(self):
        me = check_token(self)
        if not me:
            self._log(401)
            return fail(self, "需要登录", 401)
        data = read_json_body(self)
        if not isinstance(data, dict):
            self._log(400)
            return fail(self, "请求格式错误")
        raw = str(data.get("path") or "")
        rel = safe_relpath(raw)
        if not rel:
            self._log(400)
            return fail(self, "文件夹名不合法")
        parent_rel = rel.rsplit("/", 1)[0] if "/" in rel else ""
        if parent_rel and hidden_state(parent_rel)[0] and not check_admin(self):
            self._log(404)
            return fail(self, "目录不存在", 404)
        full = self._resolve(rel)
        if not full:
            self._log(400)
            return fail(self, "文件夹名不合法")
        if os.path.exists(full):
            self._log(409)
            return fail(self, "同名文件或文件夹已存在", 409)
        try:
            os.makedirs(full)
        except OSError as e:
            self._log(500)
            return fail(self, "创建失败: %s" % e, 500)
        log("已创建文件夹: %s" % rel)
        audit("新建文件夹", "路径=%s 用户=%s 来源=%s" % (rel, me, client_ip(self)))
        self._log(200)
        ok(self, path=rel)

    def _api_hide(self):
        me = check_admin(self)
        if not me:
            self._log(403)
            return fail(self, "需要管理员权限", 403)
        data = read_json_body(self)
        if not isinstance(data, dict):
            self._log(400)
            return fail(self, "请求格式错误")
        rel = safe_relpath(str(data.get("path", "")))
        if not rel:
            self._log(400)
            return fail(self, "路径不合法")
        full = self._resolve(rel)
        if not full or not os.path.lexists(full):
            self._log(404)
            return fail(self, "文件不存在", 404)
        hidden = bool(data.get("hidden"))
        set_hidden(rel, hidden)
        audit("隐藏" if hidden else "取消隐藏", "路径=%s 用户=%s 来源=%s" % (rel, me, client_ip(self)))
        log("已%s: %s" % ("隐藏" if hidden else "取消隐藏", rel))
        self._log(200)
        ok(self, path=rel, hidden=hidden)

    def _api_rename(self):
        me = check_admin(self)
        if not me:
            self._log(403)
            return fail(self, "需要管理员权限", 403)
        data = read_json_body(self)
        if not isinstance(data, dict):
            self._log(400)
            return fail(self, "请求格式错误")
        raw = str(data.get("path") or data.get("old_name") or "")
        rel = safe_relpath(raw)
        if not rel:
            self._log(400)
            return fail(self, "路径不合法")
        new_name = safe_name(str(data.get("new_name", "")))
        if not new_name:
            self._log(400)
            return fail(self, "文件名不合法")
        src = self._resolve(rel)
        if not src or not os.path.lexists(src):
            self._log(404)
            return fail(self, "文件不存在", 404)
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        new_rel = parent + "/" + new_name if parent else new_name
        if rel == new_rel:
            self._log(200)
            return ok(self)
        dst = self._resolve(new_rel)
        if os.path.lexists(dst):
            self._log(409)
            return fail(self, "目标文件名已存在", 409)
        try:
            os.rename(src, dst)
        except OSError as e:
            self._log(500)
            return fail(self, "重命名失败: %s" % e, 500)
        migrate_hidden(rel, new_rel)
        meta_migrate(rel, new_rel)
        audit("重命名", "原=%s 新=%s 用户=%s 来源=%s" % (rel, new_rel, me, client_ip(self)))
        log("已重命名: %s -> %s" % (rel, new_rel))
        self._log(200)
        ok(self, old_name=rel, new_name=new_rel)

    def _api_save_settings(self):
        me = check_admin(self)
        if not me:
            self._log(403)
            return fail(self, "需要管理员权限", 403)
        data = read_json_body(self)
        need_restart, err = update_settings(data)
        if err:
            self._log(400)
            return fail(self, err)
        log("设置已保存 (ip=%s, port=%d, title=%s)" % (CONFIG.get("ip") or "auto", CONFIG["port"], CONFIG["title"]))
        audit("修改设置", "ip=%s port=%d title=%s upload_dir=%s trust_proxy=%s audit_log=%s debug_log=%s 用户=%s 来源=%s" % (
            CONFIG.get("ip") or "auto", CONFIG["port"], CONFIG["title"],
            CONFIG.get("upload_dir", "uploads"), CONFIG.get("trust_proxy", False), CONFIG.get("audit_log", True),
            CONFIG.get("debug_log", False), me, client_ip(self)))
        if need_restart:
            host = (self.headers.get("Host") or "").split(":")[0] or "127.0.0.1"
            new_ip = CONFIG.get("ip") or host
            self._log(200)
            ok(self, restart=True, url="http://%s:%d/" % (new_ip, CONFIG["port"]))
            threading.Timer(1.0, request_restart).start()
        else:
            self._log(200)
            ok(self, restart=False)

    # ------------------------- 账号管理 -------------------------
    def _api_list_users(self):
        if not check_admin(self):
            self._log(403)
            return fail(self, "需要管理员权限", 403)
        self._log(200)
        ok(self, users=[{"username": u["username"], "is_admin": bool(u.get("is_admin"))}
                        for u in CONFIG.get("users", [])])

    def _api_add_user(self):
        me = check_admin(self)
        if not me:
            self._log(403)
            return fail(self, "需要管理员权限", 403)
        data = read_json_body(self)
        if not isinstance(data, dict):
            self._log(400)
            return fail(self, "请求格式错误")
        username = str(data.get("username", "")).strip()
        if not (1 <= len(username) <= 32) or any(ch.isspace() for ch in username):
            self._log(400)
            return fail(self, "用户名需为 1-32 个非空白字符")
        if find_user(username):
            self._log(409)
            return fail(self, "账号已存在", 409)
        password = str(data.get("password", ""))
        if len(password) < 4:
            self._log(400)
            return fail(self, "密码至少 4 位")
        salt = secrets.token_hex(16)
        CONFIG["users"].append({
            "username": username,
            "salt": salt,
            "password_hash": hash_password(password, salt),
            "is_admin": bool(data.get("is_admin")),
        })
        save_config()
        log("已添加账号: %s" % username)
        audit("添加账号", "账号=%s 管理员=%s 用户=%s 来源=%s" % (username, bool(data.get("is_admin")), me, client_ip(self)))
        self._log(200)
        ok(self)

    def _api_delete_user(self):
        me = check_admin(self)
        if not me:
            self._log(403)
            return fail(self, "需要管理员权限", 403)
        data = read_json_body(self)
        if not isinstance(data, dict):
            self._log(400)
            return fail(self, "请求格式错误")
        target = str(data.get("username", "")).strip()
        if target == check_token(self):
            self._log(400)
            return fail(self, "不能删除当前登录的账号")
        u = find_user(target)
        if not u:
            self._log(404)
            return fail(self, "账号不存在", 404)
        if u.get("is_admin") and count_admin() <= 1:
            self._log(400)
            return fail(self, "不能删除最后一个管理员")
        CONFIG["users"].remove(u)
        kill_sessions_of(target)
        save_config()
        log("已删除账号: %s" % target)
        audit("删除账号", "账号=%s 用户=%s 来源=%s" % (target, me, client_ip(self)))
        self._log(200)
        ok(self)

    def _api_reset_password(self):
        me = check_token(self)
        if not me:
            self._log(401)
            return fail(self, "需要登录", 401)
        data = read_json_body(self)
        if not isinstance(data, dict):
            self._log(400)
            return fail(self, "请求格式错误")
        target = str(data.get("username", "")).strip()
        password = str(data.get("password", ""))
        if len(password) < 4:
            self._log(400)
            return fail(self, "密码至少 4 位")
        if target != me and not check_admin(self):
            self._log(403)
            return fail(self, "只能修改自己的密码", 403)
        u = find_user(target)
        if not u:
            self._log(404)
            return fail(self, "账号不存在", 404)
        u["salt"] = secrets.token_hex(16)
        u["password_hash"] = hash_password(password, u["salt"])
        save_config()
        if target != me:
            kill_sessions_of(target)   # 重置他人密码后, 注销其所有登录
        log("已重置密码: %s" % target)
        audit("重置密码", "账号=%s 操作者=%s 来源=%s" % (target, me, client_ip(self)))
        self._log(200)
        ok(self)

    def _api_set_admin(self):
        me = check_admin(self)
        if not me:
            self._log(403)
            return fail(self, "需要管理员权限", 403)
        data = read_json_body(self)
        if not isinstance(data, dict):
            self._log(400)
            return fail(self, "请求格式错误")
        target = str(data.get("username", "")).strip()
        u = find_user(target)
        if not u:
            self._log(404)
            return fail(self, "账号不存在", 404)
        is_admin = bool(data.get("is_admin"))
        if not is_admin and u.get("is_admin") and count_admin() <= 1:
            self._log(400)
            return fail(self, "不能取消最后一个管理员")
        u["is_admin"] = is_admin
        save_config()
        log("已%s管理员权限: %s" % ("授予" if is_admin else "取消", target))
        audit("变更管理员权限", "账号=%s 操作=%s 用户=%s 来源=%s" % (target, "授予" if is_admin else "取消", me, client_ip(self)))
        self._log(200)
        ok(self)


# ----------------------------------------------------------------------------
# 服务启动
# ----------------------------------------------------------------------------
class PanServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_close(self):
        # 跳过 ThreadingMixIn 的请求线程等待:
        # 浏览器 keep-alive 长连接会让请求线程一直等待下一条请求,
        # join() 会导致"停止服务"卡死且旧连接仍可继续访问。直接关闭监听即可。
        HTTPServer.server_close(self)


def banner(bind_ip, port):
    ips = get_local_ips()
    log("=" * 52)
    log("  %s 已启动   (程序版本 v%s)" % (CONFIG["title"], APP_VERSION))
    log("  操作系统: %s" % platform.platform())
    log("  监听地址: %s:%d" % (bind_ip, port))
    for ip in ips:
        log("  访问地址:   http://%s:%d/" % (ip, port))
    if not ips:
        log("  本机访问:   http://127.0.0.1:%d/" % port)
    log("  上传目录:   %s" % upload_dir())
    log("-" * 52)
    log("  下载免登录; 上传/删除需登录; 设置/账号管理需管理员")
    log("  按 Ctrl+C 停止服务")
    log("=" * 52)


def try_add_firewall_rule():
    """尽力添加 Windows 防火墙放行规则 (需要管理员权限, 失败则静默忽略)"""
    if sys.platform != "win32":
        return
    try:
        import subprocess
        subprocess.run(
            ["netsh", "advfirewall", "firewall", "add", "rule",
             "name=PAN Tide cloud", "dir=in", "action=allow",
             "program=%s" % sys.executable, "enable=yes", "profile=any"],
            capture_output=True, timeout=15)
    except Exception:
        pass


def stop_server():
    """停止服务 (桌面管理端调用)"""
    global STOPPING
    if CURRENT_SERVER is not None:
        STOPPING = True
        log("正在停止服务...")
        threading.Thread(target=CURRENT_SERVER.shutdown, daemon=True).start()


def port_in_use(port, bind_ip):
    """检测端口是否已被其他进程占用

    Windows 上 SO_REUSEADDR 允许多个实例同时绑定同一端口 (导致关一个另一个仍在服务),
    因此启动前需要主动探测: 任一候选地址能连通即视为已占用。
    """
    hosts = []
    if bind_ip and bind_ip not in ("", "0.0.0.0"):
        hosts.append(bind_ip)
    hosts.append("127.0.0.1")
    hosts.extend(get_local_ips())
    for h in dict.fromkeys(hosts):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.8)
            s.connect((h, port))
            s.close()
            return True
        except Exception:
            continue
    return False


def run_server(bind_ip, port, open_browser=True, on_event=None):
    global CURRENT_SERVER, RESTART_REQUESTED, STOPPING
    if on_event is None:
        on_event = lambda kind, info=None: None

    opened_browser = False
    # 启动前检测端口占用, 避免多实例并存导致"停止后仍可访问"
    if port_in_use(port, bind_ip):
        msg = ("端口 %d 已被其他进程占用 (可能同时运行了多个程序实例)。\n"
               "请先关闭其他实例 (任务管理器结束 python/Tide_cloud 进程) 再重新启动。" % port)
        log("启动失败: " + msg.replace("\n", " "))
        on_event("error", msg)
        if threading.current_thread() is threading.main_thread():
            try:
                input("按回车键退出...")
            except Exception:
                pass
        return
    try:
        while True:
            RESTART_REQUESTED = False
            try:
                CURRENT_SERVER = PanServer((bind_ip, port), PanHandler)
            except OSError as e:
                msg = "无法绑定 %s:%d: %s" % (bind_ip, port, e)
                log("启动失败: " + msg)
                log("可能原因: 端口被占用, 或指定的 IP 不是本机地址")
                log("可到「设置」中修改端口, 或删除 config.json 恢复默认")
                on_event("error", msg)
                if threading.current_thread() is threading.main_thread():
                    try:
                        input("按回车键退出...")   # 无控制台窗口(打包exe)时 stdin 可能不存在
                    except Exception:
                        pass
                return
            banner(bind_ip, port)
            STOPPING = False
            if bind_ip in ("", "0.0.0.0"):
                try_add_firewall_rule()
            if open_browser and not opened_browser:
                # 默认打开 127.0.0.1: 本机访问不受防火墙影响
                opened_browser = True
                threading.Timer(1.2, lambda: webbrowser.open("http://127.0.0.1:%d/" % port)).start()
            on_event("started", (bind_ip, port))
            try:
                CURRENT_SERVER.serve_forever(poll_interval=0.5)
            except KeyboardInterrupt:
                CURRENT_SERVER.server_close()
                CURRENT_SERVER = None
                log("")
                log("已停止服务, 再见!")
                return
            CURRENT_SERVER.server_close()
            CURRENT_SERVER = None
            if not RESTART_REQUESTED:
                log("服务已停止")
                on_event("stopped", None)
                return
            log("正在应用新配置并重启服务...")
            time.sleep(0.5)  # 等待监听端口完全释放
            bind_ip = CONFIG.get("ip") or "0.0.0.0"
            port = CONFIG["port"]
    except KeyboardInterrupt:
        log("")
        log("已停止服务, 再见!")


def main():
    parser = argparse.ArgumentParser(description="网盘服务端 (支持局域网共享与公网部署)")
    parser.add_argument("--ip", help="监听 IP (覆盖 config.json 中的设置)")
    parser.add_argument("--port", type=int, help="监听端口 (覆盖 config.json 中的设置)")
    parser.add_argument("--no-browser", action="store_true", help="启动时不自动打开浏览器")
    args = parser.parse_known_args()[0]

    load_config()

    # 调试日志 (默认关闭, 可在服务器设置中开启)
    apply_debug_log(CONFIG.get("debug_log", False))
    # 审计日志 (默认开启, 可在设置中关闭)
    apply_audit_log(CONFIG.get("audit_log", True))

    bind_ip = args.ip if args.ip is not None else (CONFIG.get("ip") or "0.0.0.0")
    port = args.port if args.port is not None else CONFIG["port"]

    run_server(bind_ip, port, open_browser=not args.no_browser)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
