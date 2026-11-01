#!/usr/bin/env python3
"""Resumable batch media pre-scan for a whole series.
Creates per-video multimodal scan packages; it does not infer story semantics.
"""
from __future__ import annotations
import argparse, hashlib, json, re, sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from multimodal_scan import build_scan, write_ai_review_prompt, write_contact_sheets, write_storyboard

def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def sha256(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def slug(path:Path,root:Path)->str:
    rel=path.relative_to(root).with_suffix('')
    name=re.sub(r'[^A-Za-z0-9._-]+','_',str(rel)).strip('._') or 'episode'
    digest=hashlib.sha1(str(rel).encode('utf-8')).hexdigest()[:8]
    return f'{name[:70]}_{digest}'
def atomic_json(path:Path,obj):
    path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); tmp.replace(path)
def run_batch(input_dir,output_dir,pattern='**/*.mp4',subtitle_dir=None,threshold=.30,audio_window=.5,max_frames=80,sample_interval=10.0,replace=False):
    src=Path(input_dir).resolve(); out=Path(output_dir).resolve()
    if not src.is_dir():raise ValueError(f'视频目录不存在: {src}')
    videos=sorted(p for p in src.glob(pattern) if p.is_file())
    manifest_path=out/'batch-scan-manifest.json'; out.mkdir(parents=True,exist_ok=True)
    old={}
    if manifest_path.exists():
        try: old={x['relative_path']:x for x in json.loads(manifest_path.read_text(encoding='utf-8')).get('items',[])}
        except Exception: old={}
    rows=[]
    for video in videos:
        rel=str(video.relative_to(src)); digest=sha256(video); previous=old.get(rel)
        if previous and previous.get('sha256')==digest and previous.get('status')=='DONE' and Path(previous.get('scan_json','')).is_file():
            rows.append(previous|{'status':'SKIPPED_UNCHANGED'}); continue
        if previous and previous.get('sha256')==digest and previous.get('status')=='CHANGED_NEEDS_REPLACE' and not replace:
            rows.append(previous); continue
        if previous and previous.get('sha256')!=digest and not replace:
            rows.append({'relative_path':rel,'source_video':str(video),'sha256':digest,'status':'CHANGED_NEEDS_REPLACE','message':'源视频内容变化；如确认重新扫描请加 --replace'}); continue
        key=slug(video,src); folder=out/key; scan_path=folder/'multimodal.json'; frames_dir=folder/'frames'
        subtitle=None
        if subtitle_dir:
            subroot=Path(subtitle_dir)
            for ext in ('.srt','.vtt','.json'):
                candidate=subroot/(video.stem+ext)
                if candidate.is_file(): subtitle=candidate; break
        try:
            payload=build_scan(video,subtitle,threshold,audio_window,frames_dir,max_frames,sample_interval)
            payload['series_batch']={'relative_path':rel,'source_sha256':digest,'scanned_at':now()}
            atomic_json(scan_path,payload)
            if payload.get('frame_manifest'):
                write_storyboard(frames_dir/'index.html',payload['frame_manifest'])
                contacts=write_contact_sheets(payload['frame_manifest'],frames_dir)
                payload['contact_sheets']=contacts
                atomic_json(scan_path,payload)
            prompt=scan_path.with_name('ai_review_prompt.md')
            write_ai_review_prompt(prompt,payload)
            rows.append({'relative_path':rel,'source_video':str(video),'sha256':digest,'status':'DONE','scan_json':str(scan_path),'frames_dir':str(frames_dir),'contact_sheets':payload.get('contact_sheets',[]),'ai_review_prompt':str(prompt),'cue_count':payload.get('cue_count',0),'frame_count':len(payload.get('frame_manifest',[])),'subtitle_source':str(subtitle) if subtitle else None})
        except Exception as exc:
            rows.append({'relative_path':rel,'source_video':str(video),'sha256':digest,'status':'FAILED','error':str(exc)})
        atomic_json(manifest_path,{'engine_version':'3.5.0','input_dir':str(src),'output_dir':str(out),'updated_at':now(),'scanned_count':len(rows),'items':rows})
    atomic_json(manifest_path,{'engine_version':'3.5.0','input_dir':str(src),'output_dir':str(out),'updated_at':now(),'scanned_count':len(rows),'items':rows})
    return {'found':len(videos),'done':sum(x['status']=='DONE' for x in rows),'skipped':sum(x['status']=='SKIPPED_UNCHANGED' for x in rows),'changed_needs_replace':sum(x['status']=='CHANGED_NEEDS_REPLACE' for x in rows),'failed':sum(x['status']=='FAILED' for x in rows),'manifest':str(manifest_path),'items':rows}
def main():
    p=argparse.ArgumentParser(description='对整季原片做可断点续跑的多模态预扫描（不生成语义剧情结论）')
    p.add_argument('input_dir'); p.add_argument('output_dir'); p.add_argument('--pattern',default='**/*.mp4'); p.add_argument('--subtitle-dir')
    p.add_argument('--threshold',type=float,default=.30); p.add_argument('--audio-window',type=float,default=.5); p.add_argument('--max-frames',type=int,default=80); p.add_argument('--sample-interval',type=float,default=10.0); p.add_argument('--replace',action='store_true')
    a=p.parse_args()
    if not 0<a.threshold<1 or a.audio_window<=0 or not 1<=a.max_frames<=500 or a.sample_interval<=0:raise SystemExit('参数范围无效')
    try:
        r=run_batch(a.input_dir,a.output_dir,a.pattern,a.subtitle_dir,a.threshold,a.audio_window,a.max_frames,a.sample_interval,a.replace)
        print(json.dumps({k:v for k,v in r.items() if k!='items'},ensure_ascii=False,indent=2))
        for x in r['items']:
            print(f"[{x['status']}] {x['relative_path']}"+(f" — {x.get('error',x.get('message',''))}" if x['status'] in {'FAILED','CHANGED_NEEDS_REPLACE'} else ''))
    except Exception as exc:print(f'错误: {exc}',file=sys.stderr); raise SystemExit(2)
if __name__=='__main__':main()
