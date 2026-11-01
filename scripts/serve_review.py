#!/usr/bin/env python3
"""Serve the local Drama Dossier review/edit workbench on loopback only."""
from __future__ import annotations

import argparse
import http.server
import os
import socketserver
from pathlib import Path


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        print("[drama-ui] " + fmt % args)


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


def main() -> None:
    p = argparse.ArgumentParser(description="在本机回环地址启动剧情理解与精彩剪辑工作台")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--review", action="store_true", help="启动后提示打开证据审核页，而非精彩剪辑页")
    args = p.parse_args()
    if not 1 <= args.port <= 65535:
        raise SystemExit("端口必须在 1-65535 之间")
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    page = "review.html" if args.review else "workbench.html"
    with ReusableTCPServer(("127.0.0.1", args.port), QuietHandler) as httpd:
        print(f"本地工作台: http://127.0.0.1:{args.port}/web/{page}")
        print(f"证据审核:   http://127.0.0.1:{args.port}/web/review.html")
        print(f"精彩剪辑:   http://127.0.0.1:{args.port}/web/workbench.html")
        print(f"全剧记忆:   http://127.0.0.1:{args.port}/web/series.html")
        print(f"跨集剪辑:   http://127.0.0.1:{args.port}/web/series-workbench.html")
        print(f"故事弧审核: http://127.0.0.1:{args.port}/web/story-arc-workbench.html")
        print(f"重复素材审核: http://127.0.0.1:{args.port}/web/dedupe-review.html")
        print(f"镜头连续性: http://127.0.0.1:{args.port}/web/shot-continuity-review.html")
        print(f"人物状态:   http://127.0.0.1:{args.port}/web/character-states.html")
        print(f"版本比较:   http://127.0.0.1:{args.port}/web/version-compare.html")
        print(f"剧透安全检索需另启 API: python scripts/series_search_server.py <db> --series-id <SERIES_ID> --port 8766")
        print("仅监听本机 127.0.0.1；Ctrl+C 停止。")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止。")


if __name__ == "__main__":
    main()

