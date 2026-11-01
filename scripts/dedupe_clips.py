#!/usr/bin/env python3
"""Conservative duplicate/replay detection for cross-episode edit projects.

Uses FFmpeg 9x8 grayscale difference hashes when available, plus textual and
source-time overlap signals. It only emits review candidates; it never deletes
or disables a clip automatically.
"""
from __future__ import annotations
import argparse, hashlib, json, re, shutil, subprocess, sys
from collections import defaultdict
from pathlib import Path
from typing import Any

VERSION='3.8.0'

def load_json(p):
    with open(p,encoding='utf-8') as f:return json.load(f)
def save_json(p,obj):
    out=Path(p);out.parent.mkdir(parents=True,exist_ok=True);tmp=out.with_suffix(out.suffix+'.tmp')
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');tmp.replace(out)
def tokenize_text(text):
    s=(text or '').casefold(); toks=set(re.findall(r'[a-z0-9]+|[\u3400-\u9fff]',s))
    for run in re.findall(r'[\u3400-\u9fff]+',s):
        toks.update(run[i:i+2] for i in range(max(0,len(run)-1)))
        if len(run)>=3:toks.update(run[i:i+3] for i in range(len(run)-2))
    return toks
def text_similarity(a,b):
    aa=tokenize_text(a);bb=tokenize_text(b)
    if not aa or not bb:return 0.0
    return len(aa&bb)/len(aa|bb)
def hamming(a,b):return (int(a)^int(b)).bit_count()
def hash_similarity(a,b):return 1.0-hamming(a,b)/64.0

def frame_dhash(video,time_sec):
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:raise RuntimeError('未找到 ffmpeg，无法提取画面指纹')
    cp=subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-ss',f'{max(0,float(time_sec)):.3f}','-i',str(video),'-vf','scale=9:8:flags=area,format=gray','-frames:v','1','-f','rawvideo','-pix_fmt','gray','pipe:1'],capture_output=True,check=True)
    raw=cp.stdout
    if len(raw)!=72:raise ValueError(f'无法从 {video} 的 {time_sec:.3f}s 提取完整画面指纹')
    bits=0
    for y in range(8):
        row=raw[y*9:(y+1)*9]
        for x in range(8):bits=(bits<<1)|(1 if row[x]>row[x+1] else 0)
    return bits

def sample_clip_hashes(clip,samples=3):
    start=float(clip['start_sec']);end=float(clip['end_sec']);duration=end-start
    source=clip.get('source_video')
    if not source or not Path(source).is_file():return []
    fractions=[.2,.5,.8] if samples==3 else [(i+1)/(samples+1) for i in range(samples)]
    return [frame_dhash(source,start+duration*f) for f in fractions]

def overlap_ratio(a,b):
    if Path(a.get('source_video','')).resolve()!=Path(b.get('source_video','')).resolve():return 0.0
    lo=max(float(a['start_sec']),float(b['start_sec']));hi=min(float(a['end_sec']),float(b['end_sec']))
    inter=max(0,hi-lo); union=max(float(a['end_sec']),float(b['end_sec']))-min(float(a['start_sec']),float(b['start_sec']))
    return inter/union if union>0 else 0.0

def compare_hash_sequences(a,b):
    if not a or not b:return {'visual_similarity':None,'matched_frames':0}
    pairs=[]
    for i,x in enumerate(a):
        for j,y in enumerate(b):
            sim=hash_similarity(x,y)
            if sim>=.90:pairs.append((i,j,sim))
    # Greedy one-to-one assignment across all pair scores; prevents ties from
    # mapping every sample on one side to the same frame on the other.
    used_a=set();used_b=set();matched=[]
    for i,j,sim in sorted(pairs,key=lambda x:-x[2]):
        if i not in used_a and j not in used_b:
            used_a.add(i);used_b.add(j);matched.append(sim)
    return {'visual_similarity':round(sum(matched)/len(matched),4) if matched else 0.0,'matched_frames':len(matched)}

def clip_text(clip):
    return ' '.join(str(clip.get(k) or '') for k in ('title','reason','action','quote','segment_rationale','connection_note'))

def detect_duplicates(project,threshold=.95,hash_cache=None):
    clips=[x for x in project.get('clips',[]) if x.get('enabled',True) is not False]
    cache=hash_cache or {}; pairs=[]
    for i,a in enumerate(clips):
        aid=a.get('clip_id') or f'clip_{i+1}'
        ha=cache.get(aid)
        if ha is None:
            try:ha=sample_clip_hashes(a);cache[aid]=ha
            except Exception as exc:ha=[];cache[aid]=[]
        for j in range(i+1,len(clips)):
            b=clips[j];bid=b.get('clip_id') or f'clip_{j+1}'
            ov=overlap_ratio(a,b);tb=text_similarity(clip_text(a),clip_text(b))
            hb=cache.get(bid)
            if hb is None:
                try:hb=sample_clip_hashes(b);cache[bid]=hb
                except Exception:hb=[];cache[bid]=[]
            visual=compare_hash_sequences(ha,hb)
            vs=visual['visual_similarity'];reason=None;confidence='LOW'
            if ov>=.70:
                reason='同一原片时间区间高度重叠';confidence='HIGH'
            elif vs is not None and vs>=threshold and visual['matched_frames']>=2 and (tb>=.12 or vs>=.985):
                reason='多帧画面指纹高度相似';confidence='HIGH' if vs>=.98 else 'MEDIUM'
            elif vs is not None and vs>=.985 and visual['matched_frames']>=1 and tb>=.25:
                reason='单帧高度相似且文本/台词相近';confidence='MEDIUM'
            if reason:
                pairs.append({'left_clip_id':aid,'right_clip_id':bid,'left_episode_id':a.get('episode_id'),'right_episode_id':b.get('episode_id'),'visual_similarity':vs,'matched_frames':visual['matched_frames'],'text_similarity':round(tb,4),'same_source_overlap':round(ov,4),'confidence':confidence,'reason':reason,'action':'REVIEW_MANUALLY','suggested_keep':aid,'suggested_disable':bid})
    # Connected components form duplicate/replay groups, but the report never edits the project.
    graph=defaultdict(set)
    for p in pairs:graph[p['left_clip_id']].add(p['right_clip_id']);graph[p['right_clip_id']].add(p['left_clip_id'])
    groups=[];seen=set()
    for node in graph:
        if node in seen:continue
        stack=[node];comp=[];seen.add(node)
        while stack:
            x=stack.pop();comp.append(x)
            for y in graph[x]:
                if y not in seen:seen.add(y);stack.append(y)
        if len(comp)>1:groups.append(sorted(comp))
    return {'engine_version':VERSION,'project_type':project.get('project_type'),'project_title':project.get('title'),'clip_count':len(clips),'duplicate_pair_count':len(pairs),'groups':groups,'pairs':pairs,'policy':{'automatic_removal':False,'requires_human_review':True,'threshold':threshold,'hash_method':'FFmpeg grayscale 9x8 difference hash','warning':'相似镜头不必然是重复素材；回忆、闪回、重复播放和同场景反打均可能是有意叙事。'}}

def main():
    p=argparse.ArgumentParser(description='跨集剪辑相似镜头/重复素材候选检测（只报告，不自动删除）')
    p.add_argument('project');p.add_argument('output');p.add_argument('--threshold',type=float,default=.95)
    a=p.parse_args()
    if not .80<=a.threshold<=1:raise SystemExit('--threshold 必须在 0.80 到 1.0 之间')
    try:
        project=load_json(a.project);report=detect_duplicates(project,a.threshold);save_json(a.output,report)
        print(json.dumps({'output':a.output,'clips':report['clip_count'],'duplicate_pairs':report['duplicate_pair_count'],'groups':len(report['groups']),'automatic_removal':False},ensure_ascii=False,indent=2))
    except Exception as exc:print(f'错误: {exc}',file=sys.stderr);raise SystemExit(2)
if __name__=='__main__':main()
