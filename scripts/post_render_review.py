#!/usr/bin/env python3
"""Post-render technical review using FFprobe and FFmpeg black/silence detectors."""
from __future__ import annotations
import argparse,json,re,shutil,subprocess,sys
from pathlib import Path

def run(cmd):
    return subprocess.run(cmd,capture_output=True,text=True,check=True)
def inspect_video(path,black_min_duration=0.15,silence_threshold="-45dB",silence_min_duration=1.0):
    path=Path(path)
    if not path.is_file():raise FileNotFoundError(path)
    ffprobe=shutil.which("ffprobe");ffmpeg=shutil.which("ffmpeg")
    if not ffprobe or not ffmpeg:raise RuntimeError("需要 FFmpeg 和 FFprobe")
    meta=json.loads(run([ffprobe,"-v","error","-show_streams","-show_format","-of","json",str(path)]).stdout)
    streams=meta.get("streams",[])
    video=next((x for x in streams if x.get("codec_type")=="video"),None)
    audio=next((x for x in streams if x.get("codec_type")=="audio"),None)
    if not video:raise ValueError("成片没有视频流")
    duration=float(meta.get("format",{}).get("duration") or video.get("duration") or 0)
    cmd=[ffmpeg,"-hide_banner","-nostats","-i",str(path),"-vf",f"blackdetect=d={black_min_duration}:pix_th=0.10","-af",f"silencedetect=noise={silence_threshold}:d={silence_min_duration}","-f","null","-"]
    cp=subprocess.run(cmd,capture_output=True,text=True)
    log=cp.stderr
    blacks=[]
    for m in re.finditer(r"black_start:([0-9.]+)\s+black_end:([0-9.]+)\s+black_duration:([0-9.]+)",log):
        blacks.append({"start_sec":float(m.group(1)),"end_sec":float(m.group(2)),"duration_sec":float(m.group(3))})
    silence=[]
    starts=re.findall(r"silence_start:\s*([0-9.]+)",log)
    ends=re.findall(r"silence_end:\s*([0-9.]+)",log)
    for i,start in enumerate(starts):
        end=float(ends[i]) if i<len(ends) else duration
        silence.append({"start_sec":float(start),"end_sec":end,"duration_sec":max(0,end-float(start))})
    issues=[]
    if not audio:issues.append({"severity":"WARN","code":"NO_AUDIO_STREAM","message":"成片没有音轨"})
    for b in blacks:
        if b["duration_sec"]>=black_min_duration:
            issues.append({"severity":"WARN","code":"BLACK_SEGMENT","message":"检测到黑帧/黑场片段","segment":b})
    for s in silence:
        if s["duration_sec"]>=silence_min_duration:
            issues.append({"severity":"INFO","code":"SILENCE_SEGMENT","message":"检测到持续静音；可能是有意留白，需人工判断","segment":s})
    if cp.returncode!=0:issues.append({"severity":"ERROR","code":"FFMPEG_ANALYSIS_FAILED","message":log[-2000:]})
    return {"source":str(path.resolve()),"duration_sec":duration,"video":{"codec":video.get("codec_name"),"width":video.get("width"),"height":video.get("height"),"fps":video.get("r_frame_rate")},"audio":None if not audio else {"codec":audio.get("codec_name"),"sample_rate":audio.get("sample_rate"),"channels":audio.get("channels")},"black_segments":blacks,"silence_segments":silence,"issues":issues,"release_blockers":sum(x["severity"]=="ERROR" for x in issues),"technical_check":"PASS" if not any(x["severity"]=="ERROR" for x in issues) else "FAIL","semantic_review_required":True,"limitations":["黑帧检测不能判断是否为有意的黑场/转场","静音检测不能判断是否为有意的停顿","本报告不评判剧情正确性、表演质量或审美"]}
def main():
    p=argparse.ArgumentParser(description="对渲染成片执行技术复核")
    p.add_argument("video");p.add_argument("output");p.add_argument("--black-min-duration",type=float,default=.15);p.add_argument("--silence-threshold",default="-45dB");p.add_argument("--silence-min-duration",type=float,default=1.0)
    a=p.parse_args()
    try:
        report=inspect_video(a.video,a.black_min_duration,a.silence_threshold,a.silence_min_duration)
        out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True);tmp=out.with_suffix(out.suffix+".tmp");tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");tmp.replace(out)
        print(json.dumps({"output":a.output,"duration_sec":report["duration_sec"],"issues":len(report["issues"]),"technical_check":report["technical_check"]},ensure_ascii=False))
    except Exception as e:print(f"成片检查失败: {e}",file=sys.stderr);raise SystemExit(2)
if __name__=="__main__":main()
