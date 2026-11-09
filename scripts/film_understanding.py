#!/usr/bin/env python3
"""User-controlled, file-based AI film-understanding workflow.

No model is called here. Prompts and user-provided AI answers are exchanged
manually, so no local footage is silently uploaded.
"""
import argparse, hashlib, json, math
from datetime import datetime, timezone
from pathlib import Path


def now(): return datetime.now(timezone.utc).isoformat()
def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def save_new(path, content):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as f: f.write(content)
def save_json(path, data): save_new(path,json.dumps(data,ensure_ascii=False,indent=2)+'\n')
def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def init_session(scan_path, folder, segment_sec=300):
    scan_path=Path(scan_path).resolve(); scan=read(scan_path)
    duration=scan.get('media',{}).get('duration_sec')
    if type(duration) not in (int,float) or not math.isfinite(duration) or duration<=0: raise ValueError('扫描结果缺少有效影片时长')
    if type(segment_sec) not in (int,float) or not math.isfinite(segment_sec) or segment_sec<=0: raise ValueError('分段时长必须为正数')
    folder=Path(folder); folder.mkdir(parents=True,exist_ok=False)
    segments=[]; start=0.0
    while start<duration:
        end=min(float(duration),start+segment_sec)
        segments.append({'segment_id':f'S{len(segments)+1:04d}','start_sec':start,'end_sec':end})
        start=end
    meta={'version':'1.0','scan_path':str(scan_path),'scan_sha256':digest(scan_path),'duration_sec':duration,'segments':segments,'created_at':now()}
    save_json(folder/'session.json',meta)
    save_new(folder/'material-review.txt','请核对字幕/ASR、联系表和片源是否正确。审核是可选步骤；跳过不等于资料已审核。\n')
    return meta


def load_session(folder):
    folder=Path(folder); meta=read(folder/'session.json'); scan=Path(meta['scan_path'])
    if not scan.is_file() or digest(scan)!=meta['scan_sha256']: raise ValueError('扫描文件不存在或已改变；请重新建立会话')
    return folder,meta,read(scan)


def material_decision(folder, choice, note=''):
    folder,_,_=load_session(folder)
    if choice not in ('reviewed','skipped'): raise ValueError('审核选择无效')
    if choice=='reviewed' and not note.strip(): raise ValueError('已审核需说明检查内容')
    save_json(folder/'material-decision.json',{'choice':choice,'note':note.strip(),'time':now()})


def make_prompt(folder, segment_id, output):
    folder,meta,scan=load_session(folder)
    if not (folder/'material-decision.json').exists(): raise ValueError('请先选择审核材料或跳过')
    segment=next((s for s in meta['segments'] if s['segment_id']==segment_id),None)
    if segment is None: raise ValueError('分段不存在')
    a,b=segment['start_sec'],segment['end_sec']
    cues=[c for c in scan.get('cues',[]) if c.get('start_sec',c.get('time_sec',-1)) < b and c.get('end_sec',c.get('time_sec',-1)) >= a]
    frames=[f for f in scan.get('frame_manifest',[]) if a<=f.get('time_sec',-1)<b]
    material={'segment':segment,'subtitle_source':scan.get('subtitle_source'),'frame_manifest':frames,'cues':cues}
    prompt=('请理解指定时段的影片。字幕、采样帧只是定位线索；请结合提供的连续原片或片段。'
            '若未收到视频/画面，请明确说明无法确认的内容；不能凭字幕推断身份、动作或因果。'
            '输出时间线、可辨识人物、事件、对话、关系、疑点、证据时间及未覆盖画面。不要编造。\n'
            '请在使用本提示时另行提供相关连续原片、联系表和完整字幕；本程序不会自动上传。\n\n'
            +json.dumps(material,ensure_ascii=False,indent=2)+'\n')
    save_new(output,prompt)


def add_answer(folder, segment_id, answer_path, feedback=''):
    folder,meta,_=load_session(folder)
    if not (folder/'material-decision.json').exists(): raise ValueError('请先选择审核材料或跳过')
    if segment_id not in {s['segment_id'] for s in meta['segments']} | {'FILM'}: raise ValueError('分段不存在')
    answer=Path(answer_path).read_text(encoding='utf-8').strip()
    if not answer: raise ValueError('AI 结果为空')
    directory=folder/'segments'/segment_id; directory.mkdir(parents=True,exist_ok=True)
    rounds=sorted(directory.glob('round-*.json'))
    if rounds and not feedback.strip(): raise ValueError('继续分析须填写用户问题或修改意见')
    entry={'segment_id':segment_id,'round':len(rounds)+1,'feedback':feedback.strip(),'ai_answer':answer,'answer_sha256':hashlib.sha256(answer.encode()).hexdigest(),'time':now(),'review_status':'UNREVIEWED'}
    save_json(directory/f'round-{len(rounds)+1:04d}.json',entry)
    return entry


def followup_prompt(folder,segment_id,question,output):
    folder,meta,_=load_session(folder)
    if segment_id not in {s['segment_id'] for s in meta['segments']} | {'FILM'} or not question.strip(): raise ValueError('需要有效分段和用户问题')
    rounds=sorted((folder/'segments'/segment_id).glob('round-*.json'))
    if not rounds: raise ValueError('请先导入本段的 AI 结果')
    latest=read(rounds[-1]); prompt='请依据已有材料重新核查用户提出的问题；缺少视频证据时指出需要补看的时间段，不要编造。\n\n用户问题：'+question.strip()+'\n\n上一轮结果：\n'+latest['ai_answer']+'\n'
    save_new(output,prompt)


def status(folder):
    folder,meta,_=load_session(folder); rows=[]
    for segment in meta['segments']:
        rounds=sorted((folder/'segments'/segment['segment_id']).glob('round-*.json'))
        reviews=sorted((folder/'segments'/segment['segment_id']).glob('review-*.json'))
        review=read(reviews[-1]) if reviews else None
        current=read(rounds[-1]) if rounds else None
        rows.append({**segment,'rounds':len(rounds),'has_ai_result':bool(rounds),'satisfied':bool(review and current and review['round']==current['round'] and review['satisfied'])})
    final_rounds=sorted((folder/'segments'/'FILM').glob('round-*.json'))
    final_reviews=sorted((folder/'segments'/'FILM').glob('review-*.json'))
    final=read(final_rounds[-1]) if final_rounds else None
    decision=read(final_reviews[-1]) if final_reviews else None
    final_satisfied=bool(final and decision and decision['round']==final['round'] and decision['satisfied'])
    return {'film_report_rounds':len(final_rounds),'film_report_satisfied':final_satisfied,'material_decision':read(folder/'material-decision.json') if (folder/'material-decision.json').exists() else None,'segments':rows,'segments_with_result':sum(x['has_ai_result'] for x in rows),'all_segments_have_results':all(x['has_ai_result'] for x in rows),'all_satisfied':all(x['satisfied'] for x in rows),'coverage_note':'分段有结果仅表示收到文本，不证明AI看过全部画面或剧情完整准确。'}


def review_answer(folder,segment_id,satisfied,reason):
    folder,meta,_=load_session(folder)
    if segment_id not in {s['segment_id'] for s in meta['segments']} | {'FILM'} or not reason.strip(): raise ValueError('需要有效分段及审核说明')
    directory=folder/'segments'/segment_id; rounds=sorted(directory.glob('round-*.json'))
    if not rounds: raise ValueError('尚无 AI 结果')
    prior=sorted(directory.glob('review-*.json'))
    save_json(directory/f'review-{len(prior)+1:04d}.json',{'round':read(rounds[-1])['round'],'satisfied':satisfied,'reason':reason.strip(),'time':now()})


def aggregate(folder, output):
    folder,meta,_=load_session(folder); progress=status(folder); sections=[]
    for item in progress['segments']:
        rounds=sorted((folder/'segments'/item['segment_id']).glob('round-*.json'))
        sections.append({'segment':item,'latest_ai_answer':read(rounds[-1])['ai_answer'] if rounds else None})
    reports=sorted((folder/'segments'/'FILM').glob('round-*.json'))
    save_json(output,{'version':'1.0','status':progress,'sections':sections,'latest_film_report':read(reports[-1])['ai_answer'] if reports else None,'limitations':['这是按时间排列的分段结果汇编，并非自动生成的整片综合分析。','满意度不代表剧情事实已核实；原始 AI 结果不得自动升级为 PASS。']})


def synthesis_prompt(folder, output):
    folder,meta,_=load_session(folder); progress=status(folder); sections=[]
    for item in progress['segments']:
        rounds=sorted((folder/'segments'/item['segment_id']).glob('round-*.json'))
        sections.append({'segment_id':item['segment_id'],'start_sec':item['start_sec'],'end_sec':item['end_sec'],'answer':read(rounds[-1])['ai_answer'] if rounds else None})
    prompt=('请仅依据以下分段结果，汇总整片剧情、人物、关系、时间线、伏笔及疑点，附证据时间码。'
            '缺失或未确认的段落必须单独列出；分段有回答不证明已看过所有画面。'
            '遇到矛盾请列明，不要补造。输出待人工审核的综合报告。\n\n'+json.dumps(sections,ensure_ascii=False,indent=2)+'\n')
    save_new(output,prompt)

def main():
    p=argparse.ArgumentParser(description='影片理解材料可选审核、AI 分段理解与多轮反馈（手动交换 AI 结果）'); sub=p.add_subparsers(dest='cmd',required=True)
    q=sub.add_parser('init');q.add_argument('scan');q.add_argument('folder');q.add_argument('--segment-sec',type=float,default=300)
    q=sub.add_parser('materials');q.add_argument('folder');q.add_argument('--choice',required=True,choices=['reviewed','skipped']);q.add_argument('--note',default='')
    q=sub.add_parser('prompt');q.add_argument('folder');q.add_argument('segment_id');q.add_argument('output')
    q=sub.add_parser('answer');q.add_argument('folder');q.add_argument('segment_id');q.add_argument('answer_file');q.add_argument('--feedback',default='')
    q=sub.add_parser('followup');q.add_argument('folder');q.add_argument('segment_id');q.add_argument('output');q.add_argument('--question',required=True)
    q=sub.add_parser('review');q.add_argument('folder');q.add_argument('segment_id');q.add_argument('--decision',choices=['satisfied','revise'],required=True);q.add_argument('--reason',required=True)
    q=sub.add_parser('status');q.add_argument('folder')
    q=sub.add_parser('aggregate');q.add_argument('folder');q.add_argument('output')
    q=sub.add_parser('synthesis-prompt');q.add_argument('folder');q.add_argument('output')
    a=p.parse_args()
    try:
        if a.cmd=='init': result=init_session(a.scan,a.folder,a.segment_sec)
        elif a.cmd=='materials': result=material_decision(a.folder,a.choice,a.note)
        elif a.cmd=='prompt': result=make_prompt(a.folder,a.segment_id,a.output)
        elif a.cmd=='answer': result=add_answer(a.folder,a.segment_id,a.answer_file,a.feedback)
        elif a.cmd=='followup': result=followup_prompt(a.folder,a.segment_id,a.question,a.output)
        elif a.cmd=='review': result=review_answer(a.folder,a.segment_id,a.decision=='satisfied',a.reason)
        elif a.cmd=='status': result=status(a.folder)
        elif a.cmd=='aggregate': result=aggregate(a.folder,a.output)
        else: result=synthesis_prompt(a.folder,a.output)
        if result is not None: print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,OSError) as e: raise SystemExit(str(e))
if __name__=='__main__':main()
