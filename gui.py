# -*- coding: utf-8 -*-
"""
网盘管理器 - Windows 桌面管理端 (tkinter, 纯标准库, 零依赖)

功能:
  * 一键启动/停止服务, 实时显示运行状态与访问地址
  * 文件管理: 上传 / 删除 / 重命名 / 复制下载链接 / 打开上传目录
  * 账号管理: 添加账号 / 删除账号 / 重置密码 / 设置管理员 (仅管理员)
  * 服务器设置: 标题 / 监听 IP / 端口 / 上传大小上限 / 上传目录 (直接生效, 自动重启)
  * 自定义文案: 修改网页前端所有展示文本, 保存立即生效

运行: python gui.py   或双击 start_gui.bat
"""

import json
import os
import queue
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.client import HTTPConnection

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

import server as panserver


def fmt_size(n):
    if n is None:
        return "-"
    if n < 1024:
        return "%d B" % n
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1024.0
        if n < 1024 or unit == "TB":
            return ("%.0f" % n if n >= 100 else "%.1f" % n) + " " + unit


def fmt_time(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _trunc(s, n):
    s = str(s)
    return s if len(s) <= n else s[:n - 1] + "…"


# 前端展示文本的默认值 (与 web/index.html 中的 DEFAULT_TEXTS 保持一致)
DEFAULT_TEXTS = {
    "brand_logo": "📦",
    "login": "登录", "logout": "退出登录", "logged_in_as": "已登录", "admin_badge": "管理员",
    "tab_files": "📁 文件列表", "tab_upload": "⬆ 上传文件", "tab_settings": "⚙ 设置",
    "refresh": "🔄 刷新", "badge_public": "下载免登录 · 上传需登录",
    "col_name": "文件名", "col_size": "大小", "col_time": "修改时间", "col_ops": "操作",
    "btn_download": "下载", "btn_preview": "预览", "btn_copy_link": "复制链接", "btn_rename": "重命名", "btn_delete": "删除",
    "file_count": "共 {n} 个文件", "empty_tip": "还没有文件, 登录后可在「上传文件」页上传",
    "confirm_delete": '确定删除 "{name}" 吗?', "confirm_overwrite": '文件 "{name}" 已存在, 是否覆盖?',
    "rename_prompt": "请输入新的文件名",
    "upload_need_login": "上传文件需要登录账号", "go_login": "去登录",
    "dropzone_text": "把文件拖拽到这里, 或", "btn_choose_file": "选择文件",
    "uploading": "上传中", "upload_success": "上传成功", "upload_overwritten": "已覆盖",
    "upload_skipped": "已跳过", "upload_failed": "上传失败", "need_login_first": "请先登录",
    "settings_need_login": "查看和修改设置需要登录", "settings_need_admin": "服务器设置仅管理员账号可修改",
    "settings_title": "服务器设置", "field_title": "服务器标题", "field_ip": "监听 IP",
    "field_port": "端口", "field_max": "单文件大小上限 (MB, 0 = 不限)", "field_upload_dir": "上传目录",
    "hint_settings": "IP 留空 = 自动监听所有网卡; 修改 IP/端口后服务器自动重启并跳转到新地址。仅管理员可修改设置。",
    "btn_save_settings": "保存设置",
    "login_title": "登录", "login_username": "用户名", "login_password": "密码", "btn_cancel": "取消",
    "login_success": "登录成功, 欢迎", "login_failed": "用户名或密码错误",
    "login_error_connect": "无法连接服务器", "login_error_empty": "请输入用户名和密码",
    "need_login": "需要登录", "logout_done": "已退出登录", "deleted": "已删除", "delete_failed": "删除失败",
    "link_copied": "下载链接已复制", "copy_failed": "复制失败, 链接",
    "settings_saved": "设置已保存", "server_restarting": "设置已保存, 服务器重启中...",
    "port_invalid": "端口必须在 1-65535 之间", "request_failed": "请求失败",
    "addr_lan": "访问地址: {url}", "addr_local": "本机访问: {url}",
    "addr_unreachable": "⚠ 无法连接服务器 — 请确认电脑上的网盘程序(桌面管理端或 start.bat)正在运行",
    "rename_done": "重命名成功", "rename_failed": "重命名失败",
    "preview_close": "关闭", "prev_file": "上一个", "next_file": "下一个",
    "not_logged_in": "未登录", "server_error": "服务器异常", "file_exists": "同名文件已存在",
    "btn_up": "⬆ 返回上级", "root_dir": "根目录", "btn_hide": "隐藏", "btn_unhide": "取消隐藏",
    "hidden_mark": "已隐藏", "confirm_delete_folder": '确定删除文件夹 "{name}" 及其全部内容吗? 此操作不可恢复!',
    "folder_label": "文件夹", "btn_mkdir": "➕ 新建文件夹", "mkdir_prompt": "请输入新文件夹名称", "mkdir_done": "文件夹已创建",
    "btn_upload": "⬆ 上传文件", "btn_view_list": "📋 列表", "btn_view_browse": "🔲 浏览",
}

# 自定义文案编辑器分组 (组名, [键...])
TEXT_GROUPS = [
    ("页头与地址栏 (页面顶部显示内容)", ["brand_logo", "addr_lan", "addr_local", "login", "logout",
                                        "logged_in_as", "admin_badge", "tab_files", "tab_upload", "tab_settings"]),
    ("文件列表页", ["refresh", "btn_upload", "btn_view_list", "btn_view_browse", "badge_public",
                    "col_name", "col_size", "col_time", "col_ops",
                    "btn_download", "btn_preview", "btn_copy_link", "btn_rename", "btn_delete",
                    "file_count", "empty_tip", "confirm_delete", "rename_prompt"]),
    ("上传页", ["upload_need_login", "go_login", "dropzone_text", "btn_choose_file", "uploading",
                "upload_success", "upload_overwritten", "upload_skipped", "upload_failed",
                "need_login_first", "confirm_overwrite"]),
    ("设置页", ["settings_need_login", "settings_need_admin", "settings_title", "field_title", "field_ip",
                "field_port", "field_max", "field_upload_dir", "hint_settings", "btn_save_settings"]),
    ("登录弹窗", ["login_title", "login_username", "login_password", "btn_cancel", "login_success",
                  "login_failed", "login_error_connect", "login_error_empty"]),
    ("提示与弹窗", ["need_login", "logout_done", "deleted", "delete_failed", "link_copied", "copy_failed",
                    "settings_saved", "server_restarting", "port_invalid", "request_failed",
                    "addr_unreachable", "rename_done", "rename_failed",
                    "preview_close", "prev_file", "next_file", "not_logged_in", "server_error", "file_exists"]),
    ("文件夹与隐藏", ["btn_up", "root_dir", "btn_hide", "btn_unhide", "hidden_mark", "confirm_delete_folder",
                     "folder_label", "btn_mkdir", "mkdir_prompt", "mkdir_done"]),
]


class ApiError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status
        self.msg = msg


class Client:
    """通过 HTTP API 与同进程服务端通信"""

    def __init__(self):
        self.host = "127.0.0.1"
        self.port = 8000
        self.token = ""
        self.sync_config()

    def sync_config(self):
        bind = panserver.CONFIG.get("ip") or "0.0.0.0"
        self.host = "127.0.0.1" if bind in ("", "0.0.0.0") else bind
        try:
            self.port = int(panserver.CONFIG["port"])
        except (TypeError, ValueError):
            self.port = 8000

    @property
    def base(self):
        return "http://%s:%d" % (self.host, self.port)

    @staticmethod
    def _errmsg(body):
        try:
            d = json.loads(body)
            return d.get("msg")
        except Exception:
            return None

    def request(self, path, method="GET", data=None, timeout=15):
        url = self.base + path
        headers = {}
        body = None
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["X-Auth-Token"] = self.token
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            try:
                text = e.read().decode("utf-8")
            except Exception:
                text = ""
            raise ApiError(e.code, self._errmsg(text) or ("请求失败 (HTTP %d)" % e.code))
        except ApiError:
            raise
        except Exception as e:
            raise ApiError(0, "无法连接服务器: %s" % e)

    def login(self, username, password):
        status, text = self.request("/api/login", "POST", {"username": username, "password": password})
        d = json.loads(text)
        if d.get("ok"):
            self.token = d["token"]
            return d
        raise ApiError(status, d.get("msg", "登录失败"))

    def upload_file(self, name, filepath, overwrite, path="", progress_cb=None):
        size = os.path.getsize(filepath)
        qs = urllib.parse.urlencode({"name": name, "overwrite": "1" if overwrite else "0", "path": path})
        conn = HTTPConnection(self.host, self.port, timeout=600)
        try:
            conn.putrequest("POST", "/api/upload?" + qs)
            conn.putheader("X-Auth-Token", self.token)
            conn.putheader("Content-Length", str(size))
            conn.endheaders()
            sent = 0
            with open(filepath, "rb") as f:
                while True:
                    chunk = f.read(256 * 1024)
                    if not chunk:
                        break
                    conn.send(chunk)
                    sent += len(chunk)
                    if progress_cb:
                        progress_cb(sent, size)
            resp = conn.getresponse()
            body = resp.read().decode("utf-8")
            if resp.status == 409:
                raise ApiError(409, "同名文件已存在")
            if resp.status != 200:
                raise ApiError(resp.status, self._errmsg(body) or ("上传失败 (HTTP %d)" % resp.status))
            return json.loads(body)
        except ApiError:
            raise
        except Exception as e:
            raise ApiError(0, "上传失败: %s" % e)
        finally:
            try:
                conn.close()
            except Exception:
                pass


class PanGUI:
    # 这些字段填 null 会隐藏网页端的登录/上传等关键入口, 保存前需要确认
    CRITICAL_TEXT_KEYS = ["tab_files", "tab_upload", "tab_settings", "btn_choose_file",
                          "upload_need_login", "login", "go_login", "btn_save_settings"]

    def __init__(self, root, skip_login=False):
        self.root = root
        self.q = queue.Queue()
        self.client = Client()
        self.login_user = ""
        self._upload_queue = []
        self._upload_overwrite = False
        self.current_path = ""
        self._current_dirs = set()
        self._hidden_names = set()
        self._build_ui()
        panserver.LOG_SINKS.append(lambda line: self.q.put(("log", line)))
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self._poll)
        self.load_settings_into_form()
        self.texts_load()
        if not skip_login:
            self.root.after(1200, self._prompt_login_if_needed)
        self.start_server()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        self.root.title("网盘管理器")
        self.root.geometry("880x640")
        self.root.minsize(760, 540)

        top = ttk.Frame(self.root, padding=(14, 10))
        top.pack(fill="x")
        ttk.Label(top, text="📦 网盘管理器", font=("Microsoft YaHei", 13, "bold")).pack(side="left")
        self.status_lbl = ttk.Label(top, text="● 启动中...", foreground="#888888")
        self.status_lbl.pack(side="left", padx=(18, 0))
        self.user_lbl = ttk.Label(top, text="未登录", foreground="#888888")
        self.user_lbl.pack(side="right", padx=(0, 10))
        ttk.Button(top, text="登录", command=self.prompt_login).pack(side="right")
        ttk.Button(top, text="🌐 打开网页", command=self.open_web).pack(side="right", padx=(0, 8))

        self.nb = nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=14, pady=(6, 0))
        self._build_status_tab(nb)
        self._build_files_tab(nb)
        self._build_users_tab(nb)
        self._build_settings_tab(nb)
        self._build_texts_tab(nb)

        bar = ttk.Frame(self.root, padding=(14, 6))
        bar.pack(fill="x", side="bottom")
        self.progress = ttk.Progressbar(bar, mode="determinate", length=220)
        self.progress.pack(side="left")
        self.status_msg = ttk.Label(bar, text="", foreground="#666666")
        self.status_msg.pack(side="left", padx=(10, 0))

    def _build_status_tab(self, nb):
        f = ttk.Frame(nb, padding=12)
        nb.add(f, text=" 状态 ")
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(0, 10))
        ttk.Button(row, text="▶ 启动服务", command=self.start_server).pack(side="left")
        ttk.Button(row, text="■ 停止服务", command=self.stop_server).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="📁 打开上传目录", command=self.open_upload_dir).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="🔗 复制地址", command=self.copy_addr).pack(side="left", padx=(8, 0))

        ttk.Label(f, text="访问地址 (发给其他人即可免登录下载):").pack(anchor="w")
        self.addr_list = tk.Listbox(f, height=4, activestyle="none")
        self.addr_list.pack(fill="x", pady=(4, 10))

        ttk.Label(f, text="运行日志:").pack(anchor="w")
        logf = ttk.Frame(f)
        logf.pack(fill="both", expand=True)
        self.log_text = tk.Text(logf, state="disabled", wrap="none",
                                font=("Consolas", 9), background="#f7f8fa")
        sb = ttk.Scrollbar(logf, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True)

    def _build_files_tab(self, nb):
        f = ttk.Frame(nb, padding=12)
        nb.add(f, text=" 文件管理 ")
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(0, 8))
        ttk.Button(row, text="⬆ 上传文件", command=self.choose_upload).pack(side="left")
        ttk.Button(row, text="🗑 删除选中", command=self.delete_selected).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="✏ 重命名", command=self.rename_file).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="🔗 复制下载链接", command=self.copy_link).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="🔄 刷新", command=self.refresh_files).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="⬆ 上级目录", command=self.dir_up).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="➕ 新建文件夹", command=self.mkdir).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="👁 隐藏/取消隐藏", command=self.toggle_hide).pack(side="left", padx=(8, 0))
        self.path_lbl = ttk.Label(row, text="根目录", foreground="#888888")
        self.path_lbl.pack(side="left", padx=(10, 0))
        self.file_count_lbl = ttk.Label(row, text="", foreground="#888888")
        self.file_count_lbl.pack(side="right")

        wrap = ttk.Frame(f)
        wrap.pack(fill="both", expand=True)
        cols = ("name", "size", "mtime")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="extended")
        self.tree.heading("name", text="文件名")
        self.tree.heading("size", text="大小")
        self.tree.heading("mtime", text="修改时间")
        self.tree.column("name", width=420, anchor="w")
        self.tree.column("size", width=110, anchor="e")
        self.tree.column("mtime", width=170, anchor="center")
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", self._tree_double)

    def _build_users_tab(self, nb):
        f = ttk.Frame(nb, padding=12)
        nb.add(f, text=" 账号管理 ")
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(0, 8))
        ttk.Button(row, text="➕ 添加账号", command=self.add_user).pack(side="left")
        ttk.Button(row, text="🗑 删除账号", command=self.delete_user).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="🔑 重置密码", command=self.reset_password).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="⭐ 设为/取消管理员", command=self.toggle_admin).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="🔄 刷新", command=self.refresh_users).pack(side="left", padx=(8, 0))
        ttk.Label(f, text="账号管理仅管理员可用; 普通账号可登录网页上传文件。", foreground="#888888").pack(anchor="w", pady=(0, 6))

        wrap = ttk.Frame(f)
        wrap.pack(fill="both", expand=True)
        cols = ("username", "admin")
        self.utree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="browse")
        self.utree.heading("username", text="用户名")
        self.utree.heading("admin", text="管理员")
        self.utree.column("username", width=300, anchor="w")
        self.utree.column("admin", width=150, anchor="center")
        vsb2 = ttk.Scrollbar(wrap, orient="vertical", command=self.utree.yview)
        self.utree.configure(yscrollcommand=vsb2.set)
        vsb2.pack(side="right", fill="y")
        self.utree.pack(fill="both", expand=True)

    def _build_settings_tab(self, nb):
        f = ttk.Frame(nb, padding=16)
        nb.add(f, text=" 服务器设置 ")
        self.var_title = tk.StringVar()
        self.var_ip = tk.StringVar()
        self.var_port = tk.StringVar()
        self.var_max = tk.StringVar()
        self.var_upload = tk.StringVar()

        grid = ttk.Frame(f)
        grid.pack(fill="x")

        def add_row(rowidx, label, widget):
            ttk.Label(grid, text=label, width=16, anchor="e").grid(row=rowidx, column=0, sticky="e", padx=(0, 10), pady=6)
            widget.grid(row=rowidx, column=1, sticky="we", pady=6)

        add_row(0, "服务器标题", ttk.Entry(grid, textvariable=self.var_title, width=36))
        self.cmb_ip = ttk.Combobox(grid, textvariable=self.var_ip, width=33)
        add_row(1, "监听 IP", self.cmb_ip)
        add_row(2, "端口", ttk.Entry(grid, textvariable=self.var_port, width=36))
        add_row(3, "上传大小上限(MB)", ttk.Entry(grid, textvariable=self.var_max, width=36))
        uprow = ttk.Frame(grid)
        ttk.Entry(uprow, textvariable=self.var_upload, width=28).pack(side="left")
        ttk.Button(uprow, text="浏览...", command=self.browse_upload_dir).pack(side="left", padx=(8, 0))
        add_row(4, "上传目录", uprow)
        ttk.Label(grid, text="IP 留空 = 自动监听所有网卡; 修改 IP/端口后服务自动重启。",
                  foreground="#888888").grid(row=5, column=1, sticky="w", pady=(4, 0))
        ttk.Label(grid, text="上传目录留空 = 程序目录下的 uploads; 修改后立即生效(原目录文件不会自动搬移)。",
                  foreground="#888888").grid(row=6, column=1, sticky="w")
        ttk.Button(f, text="保存设置", command=self.save_settings).pack(pady=(16, 0))

    def _build_texts_tab(self, nb):
        f = ttk.Frame(nb, padding=12)
        nb.add(f, text=" 自定义文案 ")
        top = ttk.Frame(f)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="修改网页前端所有展示文本 (对所有人可见)。输入框留空 = 使用左侧默认文案, 保存后刷新网页生效。\n"
                            "填入 null = 该字段不在前端显示 (如不需要某个按钮或某行提示, 填 null 即可隐藏)。\n"
                            "第一组「页头与地址栏」可修改顶部 📦 图标 (brand_logo) 与「访问地址: http://...」地址行 (addr_lan)。",
                  foreground="#666666", justify="left").pack(side="left")
        ttk.Button(top, text="全部恢复默认", command=self.texts_reset).pack(side="right")
        ttk.Button(top, text="保存文案", command=self.texts_save).pack(side="right", padx=(8, 0))

        canvas = tk.Canvas(f, highlightthickness=0, background="#f4f6fa")
        vsb = ttk.Scrollbar(f, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)
        inner = ttk.Frame(canvas)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        # 鼠标滚轮: 全局绑定, 仅当「自定义文案」页签激活时滚动 (在任意控件上滚动都有效)
        def _on_wheel(ev):
            try:
                if self.nb.tab(self.nb.select(), "text").strip() == "自定义文案":
                    canvas.yview_scroll(int(-ev.delta / 120), "units")
            except Exception:
                pass
        self.root.bind_all("<MouseWheel>", _on_wheel, add="+")
        # 切换到本页签时刷新滚动区域 (修复首次显示时滚动失效)
        self.nb.bind("<<NotebookTabChanged>>",
                     lambda e: self.root.after(120, lambda: canvas.configure(scrollregion=canvas.bbox("all"))))

        self.text_entries = {}
        for title, keys in TEXT_GROUPS:
            grp = ttk.LabelFrame(inner, text=" " + title + " ", padding=10)
            grp.pack(fill="x", padx=6, pady=(0, 10))
            for k in keys:
                row = ttk.Frame(grp)
                row.pack(fill="x", pady=2)
                ttk.Label(row, text=_trunc(DEFAULT_TEXTS.get(k, k), 30), width=30,
                          anchor="e", foreground="#888888").pack(side="left", padx=(0, 8))
                e = ttk.Entry(row)
                e.pack(side="left", fill="x", expand=True)
                self.text_entries[k] = e

    def texts_load(self):
        custom = panserver.CONFIG.get("texts", {}) or {}
        for k, e in self.text_entries.items():
            e.delete(0, "end")
            e.insert(0, custom.get(k, ""))

    def texts_save(self):
        data = {}
        for k, e in self.text_entries.items():
            data[k] = e.get().strip()
        # 关键入口被隐藏时提醒确认, 防止网页端无法登录/上传
        hidden_critical = [k for k in self.CRITICAL_TEXT_KEYS
                           if data.get(k, "").strip().lower() == "null"]
        if hidden_critical:
            if not messagebox.askyesno(
                    "确认隐藏关键入口",
                    "以下字段将被隐藏, 网页端可能因此无法登录或上传:\n\n    %s\n\n确定要保存吗?"
                    % "、".join(hidden_critical)):
                return
        need_restart, err = panserver.update_settings({"texts": data})
        if err:
            messagebox.showerror("保存失败", err)
            return
        self.append_log("自定义文案已保存 (%d 项)" % len(data))
        self.toast("文案已保存, 刷新网页即可看到效果")

    def texts_reset(self):
        if not messagebox.askyesno("恢复默认", "确定将所有自定义文案恢复为默认值?"):
            return
        for e in self.text_entries.values():
            e.delete(0, "end")
        self.texts_save()

    # ------------------------------------------------------------------ 状态
    def set_status(self, text, color="#888888"):
        self.status_lbl.config(text=text, foreground=color)

    def update_status(self):
        if panserver.CURRENT_SERVER is not None:
            cfg = panserver.CONFIG
            ips = panserver.get_local_ips()
            self.set_status("● 运行中", "#1e8e3e")
            self.addr_list.delete(0, "end")
            if ips:
                for ip in ips:
                    self.addr_list.insert("end", "  http://%s:%d/   (访问地址)" % (ip, cfg["port"]))
            else:
                self.addr_list.insert("end", "  http://127.0.0.1:%d/   (本机访问)" % cfg["port"])
        else:
            self.set_status("● 已停止", "#c62828")
            self.addr_list.delete(0, "end")

    def toast(self, msg):
        self.status_msg.config(text=msg)
        self.root.after(3000, lambda: self.status_msg.config(text="") if self.status_msg.cget("text") == msg else None)

    def append_log(self, line):
        self.log_text.config(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        try:
            if float(self.log_text.index("end-1c")) > 2000:
                self.log_text.delete("1.0", "1000.0")
        except Exception:
            pass
        self.log_text.config(state="disabled")

    # ------------------------------------------------------------------ 服务控制
    def start_server(self):
        if panserver.CURRENT_SERVER is not None:
            self.toast("服务已在运行")
            return
        self.set_status("● 启动中...")
        self.append_log("正在启动服务...")
        self.server_thread = threading.Thread(target=self._server_main, daemon=True)
        self.server_thread.start()

    def _server_main(self):
        cfg = panserver.CONFIG
        bind_ip = cfg.get("ip") or "0.0.0.0"
        port = int(cfg.get("port") or 8000)

        def on_event(kind, info=None):
            self.q.put(("srv", kind, info))

        try:
            panserver.run_server(bind_ip, port, open_browser=False, on_event=on_event)
        except Exception as e:
            self.q.put(("srv", "error", str(e)))

    def stop_server(self):
        if panserver.CURRENT_SERVER is None:
            self.toast("服务未在运行")
            return
        self.append_log("正在停止服务...")
        panserver.stop_server()

    # ------------------------------------------------------------------ 事件循环
    def _poll(self):
        try:
            while True:
                self._handle_event(self.q.get_nowait())
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _handle_event(self, ev):
        kind = ev[0]
        if kind == "log":
            self.append_log(ev[1])
        elif kind == "srv":
            if ev[1] == "started":
                self.client.sync_config()
                self.update_status()
            elif ev[1] == "stopped":
                self.update_status()
            elif ev[1] == "error":
                self.set_status("● 启动失败", "#c62828")
                self.append_log("启动失败: " + str(ev[2]))
                messagebox.showwarning("服务启动失败",
                                       str(ev[2]) + "\n\n请到「服务器设置」修改 IP/端口后重新启动")
        elif kind == "prog":
            self.set_progress(ev[1], ev[2], ev[3])
        elif kind == "upload_done":
            self.append_log("上传完成: " + ev[1])
            self.toast("上传成功: " + ev[1])
            self._process_next_upload()
        elif kind == "upload_error":
            name, status, msg, filepath, dirpath = ev[1], ev[2], ev[3], ev[4], ev[5]
            if status == 409:
                if messagebox.askyesno("同名文件", "“%s” 已存在, 是否覆盖?" % name):
                    self._upload_overwrite = True
                    self._upload_queue.insert(0, (filepath, dirpath))
                    self._process_next_upload()
                else:
                    self._process_next_upload()
            elif status == 401:
                self.toast("登录已过期, 请重新登录")
                self._upload_queue = []
                self.set_progress(None)
                self.prompt_login()
                self.refresh_files()
            else:
                self.toast("上传失败 %s: %s" % (name, msg))
                self._process_next_upload()

    # ------------------------------------------------------------------ 登录
    def _prompt_login_if_needed(self):
        if not self.client.token:
            self.prompt_login()

    def ensure_login(self):
        if self.client.token:
            return True
        self.prompt_login()
        return bool(self.client.token)

    def prompt_login(self):
        if self.client.token:
            self.toast("已登录")
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("登录")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()
        box = ttk.Frame(dlg, padding=20)
        box.pack()
        ttk.Label(box, text="用户名").grid(row=0, column=0, sticky="e", pady=(0, 8))
        e_user = ttk.Entry(box, width=24)
        e_user.grid(row=0, column=1, padx=(10, 0), pady=(0, 8))
        e_user.insert(0, "admin")
        ttk.Label(box, text="密码").grid(row=1, column=0, sticky="e", pady=(0, 8))
        e_pass = ttk.Entry(box, width=24, show="*")
        e_pass.grid(row=1, column=1, padx=(10, 0), pady=(0, 8))
        err = ttk.Label(box, text="", foreground="#c62828")
        err.grid(row=2, column=0, columnspan=2)
        btns = ttk.Frame(box)
        btns.grid(row=3, column=0, columnspan=2, pady=(14, 0))
        ttk.Button(btns, text="取消", command=dlg.destroy).pack(side="right", padx=(8, 0))
        ok_btn = ttk.Button(btns, text="登录", command=lambda: do_login())
        ok_btn.pack(side="right")

        def do_login():
            u = e_user.get().strip()
            p = e_pass.get().strip()
            if not u or not p:
                err.config(text="请输入用户名和密码")
                return
            ok_btn.config(state="disabled")
            err.config(text="登录中...")
            dlg.update()
            try:
                d = self.client.login(u, p)
            except ApiError as e:
                err.config(text=e.msg)
                ok_btn.config(state="normal")
                return
            self.login_user = d.get("username", u)
            self.user_lbl.config(text="已登录: %s%s" % (self.login_user, " (管理员)" if d.get("is_admin") else ""))
            self.append_log("已登录: " + self.login_user)
            dlg.destroy()
            self.refresh_files()
            self.refresh_users()

        e_pass.bind("<Return>", lambda e: do_login())
        dlg.wait_window()

    # ------------------------------------------------------------------ 文件管理
    def rel_of(self, name):
        return self.current_path + "/" + name if self.current_path else name

    def refresh_files(self):
        if not self.client.token:
            self.file_count_lbl.config(text="未登录")
            return
        try:
            _, text = self.client.request("/api/list?path=" + urllib.parse.quote(self.current_path))
            data = json.loads(text)
            files = data.get("files", [])
        except ApiError as e:
            self.file_count_lbl.config(text="")
            self.toast(e.msg)
            return
        self.tree.delete(*self.tree.get_children())
        self._current_dirs = set()
        self._hidden_names = set()
        for f in files:
            if f.get("is_dir"):
                self._current_dirs.add(f["name"])
                label = "📁 " + f["name"]
                size = "文件夹"
            else:
                label = f["name"]
                size = fmt_size(f["size"])
            if f.get("hidden"):
                self._hidden_names.add(f["name"])
                label += "  [已隐藏]"
            self.tree.insert("", "end", iid=f["name"],
                             values=(label, size, fmt_time(f["mtime"])))
        self.path_lbl.config(text="根目录" if not self.current_path else "/" + self.current_path)
        self.file_count_lbl.config(text="共 %d 项" % len(files))

    def _tree_double(self, _evt):
        sel = self.tree.selection()
        if not sel:
            return
        name = str(sel[0])
        if name in self._current_dirs:
            self.dir_enter(name)
        else:
            self.copy_link()

    def dir_enter(self, name):
        self.current_path = self.rel_of(name)
        self.refresh_files()

    def dir_up(self):
        parts = self.current_path.split("/") if self.current_path else []
        self.current_path = "/".join(parts[:-1])
        self.refresh_files()

    def mkdir(self):
        if not self.ensure_login():
            return
        name = simpledialog.askstring("新建文件夹", "请输入新文件夹名称:", parent=self.root)
        if not name:
            return
        name = name.strip()
        if not name:
            return
        try:
            self.client.request("/api/mkdir", "POST", {"path": self.rel_of(name)})
        except ApiError as e:
            self.toast("创建失败: " + e.msg)
            return
        self.append_log("已创建文件夹: " + self.rel_of(name))
        self.refresh_files()

    def toggle_hide(self):
        if not self.ensure_login():
            return
        sel = self.tree.selection()
        if not sel:
            self.toast("请先选中文件或文件夹")
            return
        name = str(sel[0])
        hidden = name in self._hidden_names
        try:
            self.client.request("/api/hide", "POST", {"path": self.rel_of(name), "hidden": not hidden})
        except ApiError as e:
            self.toast(e.msg)
            return
        self.append_log("已%s: %s" % ("隐藏" if not hidden else "取消隐藏", self.rel_of(name)))
        self.refresh_files()

    def choose_upload(self):
        if not self.ensure_login():
            return
        paths = filedialog.askopenfilenames(title="选择要上传的文件")
        if not paths:
            return
        self._upload_overwrite = False
        self._upload_queue = [(p, self.current_path) for p in paths]
        self._process_next_upload()

    def _process_next_upload(self):
        if not self._upload_queue:
            self.set_progress(None)
            self.refresh_files()
            return
        item = self._upload_queue.pop(0)
        p, path = item
        name = os.path.basename(p)
        self.set_progress(0, os.path.getsize(p), name)
        threading.Thread(target=self._upload_one, args=(p, path, name), daemon=True).start()

    def _upload_one(self, p, path, name):
        try:
            self.client.upload_file(name, p, self._upload_overwrite, path,
                                    lambda s, t: self.q.put(("prog", s, t, name)))
            self.q.put(("upload_done", name))
        except ApiError as e:
            self.q.put(("upload_error", name, e.status, e.msg, p, path))
        except Exception as e:
            self.q.put(("upload_error", name, 0, str(e), p, path))

    def set_progress(self, loaded, total=None, name=None):
        if loaded is None:
            self.progress.config(value=0, maximum=100)
            self.status_msg.config(text="")
            return
        self.progress.config(maximum=max(total or 1, 1), value=loaded)
        self.status_msg.config(text="正在上传: %s  %s / %s" % (name, fmt_size(loaded), fmt_size(total)))

    def delete_selected(self):
        if not self.ensure_login():
            return
        sel = self.tree.selection()
        if not sel:
            self.toast("请先选中文件或文件夹")
            return
        names = [str(i) for i in sel]
        show = "\n".join((("/" if n in self._current_dirs else "") + n) for n in names[:6])
        show += ("\n..." if len(names) > 6 else "")
        if not messagebox.askyesno("确认删除",
                                   "确定删除以下 %d 项?\n文件夹将连同其中全部内容一起删除!\n\n%s" % (len(names), show)):
            return
        errs = []
        for n in names:
            try:
                self.client.request("/api/delete", "POST", {"path": self.rel_of(n)})
            except ApiError as e:
                errs.append("%s: %s" % (n, e.msg))
        if errs:
            self.toast("删除失败: " + "; ".join(errs))
        else:
            self.append_log("已删除 %d 项" % len(names))
        self.refresh_files()

    def rename_file(self):
        if not self.ensure_login():
            return
        sel = self.tree.selection()
        if not sel:
            self.toast("请先选中文件")
            return
        name = str(sel[0])
        new = simpledialog.askstring("重命名", "请输入新的文件名:", initialvalue=name, parent=self.root)
        if not new:
            return
        new = new.strip()
        if not new or new == name:
            return
        try:
            self.client.request("/api/rename", "POST", {"path": self.rel_of(name), "new_name": new})
        except ApiError as e:
            self.toast("重命名失败: " + e.msg)
            return
        self.append_log("已重命名: %s -> %s" % (self.rel_of(name), new))
        self.refresh_files()

    def copy_link(self):
        sel = self.tree.selection()
        if not sel:
            self.toast("请先选中文件")
            return
        name = str(sel[0])
        ips = panserver.get_local_ips() or ["127.0.0.1"]
        rel = self.rel_of(name)
        url = "http://%s:%d/files/%s" % (ips[0], panserver.CONFIG["port"],
                                         "/".join(urllib.parse.quote(s) for s in rel.split("/")))
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(url)
            self.toast("下载链接已复制: " + url)
        except Exception:
            self.toast("复制失败, 链接: " + url)

    # ------------------------------------------------------------------ 账号管理
    def refresh_users(self):
        if not self.client.token:
            return
        try:
            _, text = self.client.request("/api/users")
            data = json.loads(text)
            users = data.get("users", [])
        except ApiError as e:
            self.toast(e.msg)
            return
        self.utree.delete(*self.utree.get_children())
        for u in users:
            self.utree.insert("", "end", iid=u["username"],
                              values=(u["username"], "✔ 是" if u.get("is_admin") else "—"))

    def add_user(self):
        if not self.ensure_login():
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("添加账号")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()
        box = ttk.Frame(dlg, padding=20)
        box.pack()
        var_name = tk.StringVar()
        var_pass = tk.StringVar()
        var_admin = tk.BooleanVar(value=False)
        ttk.Label(box, text="用户名").grid(row=0, column=0, sticky="e", pady=(0, 8))
        ttk.Entry(box, textvariable=var_name, width=24).grid(row=0, column=1, padx=(10, 0), pady=(0, 8))
        ttk.Label(box, text="密码").grid(row=1, column=0, sticky="e", pady=(0, 8))
        ttk.Entry(box, textvariable=var_pass, width=24, show="*").grid(row=1, column=1, padx=(10, 0), pady=(0, 8))
        ttk.Checkbutton(box, text="设为管理员", variable=var_admin).grid(row=2, column=1, sticky="w")
        err = ttk.Label(box, text="", foreground="#c62828")
        err.grid(row=3, column=0, columnspan=2)
        btns = ttk.Frame(box)
        btns.grid(row=4, column=0, columnspan=2, pady=(14, 0))
        ttk.Button(btns, text="取消", command=dlg.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(btns, text="添加", command=lambda: do_add()).pack(side="right")

        def do_add():
            try:
                self.client.request("/api/users", "POST",
                                    {"username": var_name.get().strip(), "password": var_pass.get(),
                                     "is_admin": var_admin.get()})
            except ApiError as e:
                err.config(text=e.msg)
                return
            self.append_log("已添加账号: " + var_name.get().strip())
            dlg.destroy()
            self.refresh_users()

        dlg.wait_window()

    def delete_user(self):
        if not self.ensure_login():
            return
        sel = self.utree.selection()
        if not sel:
            self.toast("请先选中账号")
            return
        name = str(sel[0])
        if name == self.login_user:
            self.toast("不能删除当前登录的账号")
            return
        if not messagebox.askyesno("确认删除", "确定删除账号 “%s”?" % name):
            return
        try:
            self.client.request("/api/users/delete", "POST", {"username": name})
        except ApiError as e:
            self.toast(e.msg)
            return
        self.append_log("已删除账号: " + name)
        self.refresh_users()

    def reset_password(self):
        if not self.ensure_login():
            return
        sel = self.utree.selection()
        if not sel:
            self.toast("请先选中账号")
            return
        name = str(sel[0])
        dlg = tk.Toplevel(self.root)
        dlg.title("重置密码 - " + name)
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()
        box = ttk.Frame(dlg, padding=20)
        box.pack()
        ttk.Label(box, text="新密码").grid(row=0, column=0, sticky="e", pady=(0, 8))
        var_pass = tk.StringVar()
        e_pass = ttk.Entry(box, textvariable=var_pass, width=24, show="*")
        e_pass.grid(row=0, column=1, padx=(10, 0), pady=(0, 8))
        err = ttk.Label(box, text="", foreground="#c62828")
        err.grid(row=1, column=0, columnspan=2)
        btns = ttk.Frame(box)
        btns.grid(row=2, column=0, columnspan=2, pady=(14, 0))
        ttk.Button(btns, text="取消", command=dlg.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(btns, text="确定", command=lambda: do_reset()).pack(side="right")

        def do_reset():
            try:
                self.client.request("/api/users/password", "POST",
                                    {"username": name, "password": var_pass.get()})
            except ApiError as e:
                err.config(text=e.msg)
                return
            self.append_log("已重置密码: " + name)
            dlg.destroy()
            self.toast("已重置 %s 的密码" % name)

        dlg.wait_window()

    def toggle_admin(self):
        if not self.ensure_login():
            return
        sel = self.utree.selection()
        if not sel:
            self.toast("请先选中账号")
            return
        name = str(sel[0])
        current = self.utree.set(name, "admin")
        is_admin = not ("是" in current)
        try:
            self.client.request("/api/users/admin", "POST", {"username": name, "is_admin": is_admin})
        except ApiError as e:
            self.toast(e.msg)
            return
        self.append_log("已%s管理员权限: %s" % ("授予" if is_admin else "取消", name))
        self.refresh_users()

    # ------------------------------------------------------------------ 设置
    def load_settings_into_form(self):
        cfg = panserver.CONFIG
        self.var_title.set(cfg.get("title", "Tide cloud"))
        self.var_ip.set(cfg.get("ip", ""))
        self.var_port.set(str(cfg.get("port", 8000)))
        self.var_max.set(str(cfg.get("max_upload_mb", 2048)))
        self.var_upload.set(cfg.get("upload_dir", "uploads"))
        self.cmb_ip.config(values=[""] + panserver.get_local_ips() + ["0.0.0.0"])

    def browse_upload_dir(self):
        cur = self.var_upload.get().strip() or "uploads"
        try:
            full = panserver.resolve_upload_dir(cur)
        except Exception:
            full = None
        initial = full if full and os.path.isdir(full) else None
        target = filedialog.askdirectory(title="选择上传目录", initialdir=initial)
        if target:
            self.var_upload.set(os.path.normpath(target))

    def save_settings(self):
        try:
            port = int(self.var_port.get().strip())
        except ValueError:
            port = 0
        if not (1 <= port <= 65535):
            messagebox.showerror("保存失败", "端口必须在 1-65535 之间")
            return
        try:
            maxm = int(self.var_max.get().strip())
        except ValueError:
            maxm = -1
        if maxm < 0:
            messagebox.showerror("保存失败", "大小上限必须是 ≥0 的整数 (0 = 不限)")
            return
        need_restart, err = panserver.update_settings({
            "title": self.var_title.get().strip(),
            "ip": self.var_ip.get().strip(),
            "port": port,
            "max_upload_mb": maxm,
            "upload_dir": self.var_upload.get().strip() or "uploads",
        })
        if err:
            messagebox.showerror("保存失败", err)
            return
        self.client.sync_config()
        if need_restart:
            self.append_log("设置已保存, 正在重启服务...")
            if panserver.CURRENT_SERVER is not None:
                panserver.request_restart()
            else:
                self.start_server()
        else:
            self.append_log("设置已保存")
        self.update_status()
        self.toast("设置已保存")
        if self.client.token:
            self.refresh_files()

    # ------------------------------------------------------------------ 其他
    def open_web(self):
        webbrowser.open("http://127.0.0.1:%d/" % panserver.CONFIG["port"])

    def open_upload_dir(self):
        try:
            os.startfile(panserver.upload_dir())
        except Exception as e:
            self.toast("无法打开目录: %s" % e)

    def copy_addr(self):
        items = self.addr_list.get(0, "end")
        if not items:
            self.toast("服务未运行, 没有可复制的地址")
            return
        addr = items[0].strip().split("  ")[0]
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(addr)
            self.toast("地址已复制: " + addr)
        except Exception:
            self.toast("复制失败: " + addr)

    def on_close(self):
        try:
            panserver.stop_server()
        except Exception:
            pass
        self.root.destroy()


def main():
    try:
        _main()
    except Exception:
        # 打包为无控制台窗口时看不到报错, 落盘到 error.log 便于排查
        try:
            import traceback
            with open(os.path.join(panserver.APP_DIR, "error.log"), "w", encoding="utf-8") as f:
                f.write("网盘管理器启动失败\n\n")
                traceback.print_exc(file=f)
        except Exception:
            pass
        raise


def _main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    panserver.load_config()
    if "--headless" in sys.argv:
        # 无窗口模式: 仅启动服务 (服务器场景 / 自动化测试用)
        cfg = panserver.CONFIG
        panserver.run_server(cfg.get("ip") or "0.0.0.0",
                             int(cfg.get("port") or 8000),
                             open_browser=False)
        return
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass
    if "--selftest" in sys.argv:
        root.withdraw()
        g = PanGUI(root, skip_login=True)
        root.update()
        g.update_status()
        g.load_settings_into_form()
        time.sleep(0.6)
        print("GUI SELFTEST OK")
        root.destroy()
        return
    PanGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
