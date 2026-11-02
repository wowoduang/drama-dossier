#!/usr/bin/env python3
"""Render multi-source cross-episode projects with FFmpeg."""
from __future__ import annotations
import argparse,json,shutil,subprocess,tempfile
from pathlib import Path
from typing import Any

def load_project(path):
    with open(path,encoding='utf-8') as f:
        d=json.load(f)
    if not isinstance(d,dict) or d.get('project_type') not in {'cross_episode_compilation','cross_episode_story_arc'}:
        raise ValueError('不是可渲染的跨集剪辑项目 (project_type=cross_episode_compilation/cross_episode_story_arc)')
    return d

def active_clips(project):
    from edit_versions import require_approved
    require_approved(project)
    out=[]
    for cl in project.get('clips',[]):
        if cl.get('enabled',True) is False:continue
        start,end=cl.get('start_sec'),cl.get('end_sec')
        src=cl.get('source_video')
        if not isinstance(start,(int,float)) or not isinstance(end,(int,float)) or start<0 or end<=start:
            raise ValueError(f"片段时间无效: {cl.get('clip_id','?')}")
        if not src or not Path(src).is_file():raise FileNotFoundError(f"片段源视频不存在: {src}")
        out.append(cl)
    if not out:raise ValueError('没有启用的剪辑片段')
    durations={}
    for cl in out:
        src=str(Path(cl['source_video']).resolve())
        if src not in durations:durations[src]=probe_duration(src)
        if float(cl['end_sec'])>durations[src]+0.10:
            raise ValueError(f"片段 {cl.get('clip_id','?')} 出点 {cl['end_sec']:.3f}s 超出源视频时长 {durations[src]:.3f}s: {src}")
    return out

def probe_duration(path):
    ffprobe=shutil.which('ffprobe')
    if not ffprobe:raise RuntimeError('未找到 ffprobe')
    cp=subprocess.run([ffprobe,'-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(path)],capture_output=True,text=True,check=True)
    return float(cp.stdout.strip())

def has_audio(path):
    ffprobe=shutil.which('ffprobe')
    if not ffprobe:raise RuntimeError('未找到 ffprobe')
    p=subprocess.run([ffprobe,'-v','error','-select_streams','a:0','-show_entries','stream=index','-of','csv=p=0',str(path)],capture_output=True,text=True,check=True)
    return bool(p.stdout.strip())

def build_filter(clips,source_indices,audio_flags,width=1920,height=1080,fps=30):
    parts=[]; inputs=[]
    for i,cl in enumerate(clips):
        idx=source_indices[str(Path(cl['source_video']).resolve())]
        start=float(cl['start_sec']);end=float(cl['end_sec']);duration=end-start
        parts.append(f"[{idx}:v:0]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS,scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps},setsar=1,format=yuv420p[v{i}]")
        inputs.append(f"[v{i}]")
        if audio_flags[str(Path(cl['source_video']).resolve())]:
            parts.append(f"[{idx}:a:0]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
        else:
            parts.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[a{i}]")
        inputs.append(f"[a{i}]")
    parts.append(''.join(inputs)+f"concat=n={len(clips)}:v=1:a=1[outv][outa]")
    return ';\n'.join(parts)

def build_command(project,output,crf=20,preset='medium',width=1920,height=1080,fps=30):
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:raise RuntimeError('未找到 ffmpeg')
    clips=active_clips(project)
    sources=[]
    for cl in clips:
        p=str(Path(cl['source_video']).resolve())
        if p not in sources:sources.append(p)
    indices={p:i for i,p in enumerate(sources)}
    flags={p:has_audio(p) for p in sources}
    filter_text=build_filter(clips,indices,flags,width,height,fps)
    cmd=[ffmpeg,'-hide_banner','-y']
    for src in sources:cmd += ['-i',src]
    cmd += ['-/filter_complex','FILTER_FILE','-map','[outv]','-map','[outa]','-c:v','libx264','-preset',preset,'-crf',str(crf),'-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-ar','48000','-ac','2','-movflags','+faststart',str(output)]
    return cmd,filter_text

def main():
    p=argparse.ArgumentParser(description='跨集多源剪辑项目 MP4 渲染器')
    p.add_argument('project');p.add_argument('output');p.add_argument('--crf',type=int,default=20);p.add_argument('--preset',default='medium');p.add_argument('--width',type=int,default=1920);p.add_argument('--height',type=int,default=1080);p.add_argument('--fps',type=int,default=30);p.add_argument('--dry-run',action='store_true')
    a=p.parse_args()
    if not 0<=a.crf<=51 or min(a.width,a.height,a.fps)<=0 or a.width%2 or a.height%2:raise SystemExit('编码参数无效；宽高必须为正偶数，fps 为正数，CRF 为 0-51')
    try:
        project=load_project(a.project);cmd,flt=build_command(project,a.output,a.crf,a.preset,a.width,a.height,a.fps)
        if a.dry_run:
            print('# filter graph\n'+flt+'\n# command\n'+' '.join(cmd));return
        out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile('w',suffix='.ffscript',encoding='utf-8',delete=False) as f:f.write(flt);fp=f.name
        try:subprocess.run([fp if x=='FILTER_FILE' else x for x in cmd],check=True)
        finally:Path(fp).unlink(missing_ok=True)
        print(f'跨集成片渲染完成: {out}')
    except Exception as e:raise SystemExit(f'渲染失败: {e}')
if __name__=='__main__':main()

