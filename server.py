# -*- coding: utf-8 -*-
r"""见室印制所 · 本地服务（仅监听 127.0.0.1，不联网、不上传任何内容）。

  python server.py                 # 打开 http://127.0.0.1:8777
  python server.py --port 9000 --open

只依赖 Python 标准库；所有书稿、产物都留在本机 out/ 目录。
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import re
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from sees_press.pipeline import build, BuildOptions   # noqa: E402
from sees_press.styles import as_list                 # noqa: E402
from sees_press import tools                          # noqa: E402

OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)
UPLOADS = ROOT / "out" / "_uploads"
UPLOADS.mkdir(parents=True, exist_ok=True)

JOBS: dict[str, dict] = {}
LOCK = threading.Lock()
JOB_RE = re.compile(r"^[A-Za-z0-9_\-]{1,80}$")


def _json(handler, obj, code=200):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)


def _file(handler, path: Path, download: bool = False):
    if not path.exists() or not path.is_file():
        _json(handler, {"error": "文件不存在"}, 404)
        return
    data = path.read_bytes()
    mt = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if path.suffix in (".html", ".htm", ".xhtml"):
        mt = "text/html; charset=utf-8"
    elif path.suffix in (".md", ".txt", ".tex", ".json", ".css"):
        mt += "; charset=utf-8"
    handler.send_response(200)
    handler.send_header("Content-Type", mt)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Accept-Ranges", "bytes")
    if download:
        handler.send_header("Content-Disposition",
                            f'attachment; filename="{path.name.encode("ascii", "ignore").decode() or "book"}"')
    handler.end_headers()
    handler.wfile.write(data)


class Handler(BaseHTTPRequestHandler):
    server_version = "SEESPRESS/1.0"

    def log_message(self, fmt, *args):
        pass                              # 静默，日志由 job 面板呈现

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = unquote(u.path)
        try:
            if p in ("/", "/index.html"):
                return _file(self, ROOT / "web" / "index.html")
            if p == "/api/styles":
                return _json(self, {"styles": as_list()})
            if p == "/api/env":
                return _json(self, tools.environment_report())
            if p == "/api/job":
                jid = q.get("id", [""])[0]
                with LOCK:
                    job = JOBS.get(jid)
                return _json(self, job if job else {"error": "未知任务"},
                             200 if job else 404)
            if p == "/api/file":
                jid, key = q.get("job", [""])[0], q.get("key", [""])[0]
                with LOCK:
                    job = JOBS.get(jid)
                if not job or not JOB_RE.match(jid):
                    return _json(self, {"error": "未知任务"}, 404)
                art = (job.get("result") or {}).get("artifacts", {})
                if key not in art:
                    return _json(self, {"error": "未知产物"}, 404)
                f = Path(art[key])
                # 目录穿越防护：只允许任务目录内的文件
                if not f.resolve().is_relative_to(Path(job["dir"]).resolve()):
                    return _json(self, {"error": "非法路径"}, 403)
                return _file(self, f, download="dl" in q)
            if p == "/api/report":
                jid = q.get("job", [""])[0]
                with LOCK:
                    job = JOBS.get(jid)
                if not job:
                    return _json(self, {"error": "未知任务"}, 404)
                f = Path(job["dir"]) / "report.md"
                return _json(self, {"markdown": f.read_text(encoding="utf-8") if f.exists() else ""})
            if p.startswith("/web/"):
                f = ROOT / p.lstrip("/")
                if f.resolve().is_relative_to((ROOT / "web").resolve()):
                    return _file(self, f)
            return _json(self, {"error": "not found"}, 404)
        except Exception as e:
            return _json(self, {"error": str(e), "tb": traceback.format_exc()}, 500)

    def do_POST(self):
        u = urlparse(self.path)
        p = unquote(u.path)
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if p == "/api/upload":
                name = re.sub(r"[^\w.\-\u4e00-\u9fa5]", "_", body.get("name", "upload.md"))[:80]
                raw = body.get("content", "")
                if body.get("base64"):
                    data = base64.b64decode(raw)
                else:
                    data = raw.encode("utf-8")
                dest = UPLOADS / f"{int(time.time() * 1000)}-{name}"
                dest.write_bytes(data)
                return _json(self, {"path": str(dest), "name": name})
            if p == "/api/build":
                jid = time.strftime("%Y%m%d-%H%M%S") + "-" + str(int(time.time() * 1000) % 1000)
                jobdir = OUT / jid
                jobdir.mkdir(parents=True, exist_ok=True)
                state = {"id": jid, "dir": str(jobdir), "status": "running",
                         "logs": [], "result": None, "started": time.time()}
                with LOCK:
                    JOBS[jid] = state
                t = threading.Thread(target=self._run_build, args=(jid, body), daemon=True)
                t.start()
                return _json(self, {"job": jid})
            return _json(self, {"error": "not found"}, 404)
        except Exception as e:
            return _json(self, {"error": str(e), "tb": traceback.format_exc()}, 500)

    @staticmethod
    def _run_build(jid: str, body: dict):
        with LOCK:
            state = JOBS[jid]

        def emit(msg):
            with LOCK:
                state["logs"].append(str(msg))
                if len(state["logs"]) > 200:
                    state["logs"] = state["logs"][-200:]

        try:
            meta_fields = ("title", "subtitle", "author", "region", "translator",
                           "publisher", "editor", "edition", "isbn", "identifier",
                           "desc", "subject", "language", "rights", "series")
            defaults = BuildOptions()
            kw = {k: (str(body[k]) if body.get(k) not in (None, "")
                      else getattr(defaults, k)) for k in meta_fields}
            kw.update({
                "style": str(body.get("style", "")),
                "quote_style": str(body.get("quote_style", "auto")),
                "cjk_spacing": bool(body.get("cjk_spacing", True)),
                "chapter_newpage": bool(body.get("chapter_newpage", True)),
                "cover": str(body.get("cover", "")),
                "cover_ratio": str(body.get("cover_ratio", "2:3")),
                "formats": tuple(body.get("formats", ["epub", "pdf", "cover", "preview"])),
                "out_dir": state["dir"],
            })
            opt = BuildOptions(**kw)
            source = body.get("source") or body.get("content") or ""
            if not str(source).strip():
                raise ValueError("内容为空，请粘贴文稿或选择文件")
            emit("任务已启动")
            res = build(str(source), opt, progress=emit)
            with LOCK:
                state["result"] = res.to_dict()
                state["status"] = "done" if res.ok else "error"
                state["logs"].append(f"耗时 {time.time() - state['started']:.1f}s")
        except Exception as e:
            with LOCK:
                state["status"] = "error"
                state["logs"].append("异常：" + str(e))
                state["logs"].append(traceback.format_exc(limit=4))


def main():
    ap = argparse.ArgumentParser(description="见室印制所 · 本地服务")
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--open", action="store_true", help="启动后打开浏览器")
    a = ap.parse_args()
    url = f"http://127.0.0.1:{a.port}"
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"见室印制所已启动：{url}\n产物目录：{OUT}\n按 Ctrl+C 停止", flush=True)
    if a.open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
