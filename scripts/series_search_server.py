#!/usr/bin/env python3
"""Loopback-only HTTP interface for spoiler-safe series search."""
from __future__ import annotations
import argparse,json,sys
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from series_search import search_series

class Handler(BaseHTTPRequestHandler):
    db_path=''; series_id=''
    def _send(self,status,payload,ctype='application/json; charset=utf-8'):
        body=payload if isinstance(payload,bytes) else json.dumps(payload,ensure_ascii=False).encode('utf-8')
        self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)
    def do_GET(self):
        if self.path in {'/','/web/series-search.html'}:
            p=HERE.parent/'web'/'series-search.html'
            if not p.is_file():return self._send(404,{'error':'series-search.html not found'})
            return self._send(200,p.read_bytes(),'text/html; charset=utf-8')
        if self.path=='/health':return self._send(200,{'status':'ok','series_id':self.series_id})
        return self._send(404,{'error':'not found'})
    def do_POST(self):
        if self.path!='/api/search':return self._send(404,{'error':'not found'})
        try:
            n=int(self.headers.get('Content-Length','0'))
            if n<=0 or n>1_000_000:return self._send(400,{'error':'request body size invalid'})
            req=json.loads(self.rfile.read(n))
            query=str(req.get('query') or '').strip()
            as_of=req.get('as_of')
            if not query:return self._send(400,{'error':'请输入检索内容'})
            if as_of is None or str(as_of).strip()=='':return self._send(400,{'error':'必须指定截止集数，防止剧透'})
            if req.get('series_id') and req['series_id']!=self.series_id:return self._send(400,{'error':'series_id 与当前服务不匹配'})
            result=search_series(self.db_path,self.series_id,query,as_of,req.get('expansion'),req.get('viewpoint_character'),bool(req.get('include_unreviewed',False)),int(req.get('limit',30)))
            return self._send(200,result)
        except Exception as exc:return self._send(400,{'error':str(exc)})
    def log_message(self,fmt,*args):pass

def main():
    p=argparse.ArgumentParser(description='本地全剧语义检索工作台（仅监听 127.0.0.1）')
    p.add_argument('db');p.add_argument('--series-id',required=True);p.add_argument('--port',type=int,default=8766)
    a=p.parse_args();Handler.db_path=str(Path(a.db).resolve());Handler.series_id=a.series_id
    server=ThreadingHTTPServer(('127.0.0.1',a.port),Handler)
    print(f'全剧检索工作台: http://127.0.0.1:{a.port}/ (series={a.series_id})')
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
if __name__=='__main__':main()
