#!/usr/bin/env python3
"""Spoiler-safe hybrid semantic retrieval over episode-local series memory.

This module deliberately does not expose global identity merges or global
storyline summaries in as-of mode: those can be informed by later episodes.
It uses Chinese character n-grams + BM25 and optional ChatGPT query expansion.
No future episode records are fetched when a cutoff is supplied.
"""
from __future__ import annotations
import argparse, json, math, re, sqlite3, sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from series_memory import connect
VERSION='3.8.0'

BUILTIN_EXPANSIONS={
    '背叛':['出卖','倒戈','反叛','背离','暗中勾结','欺骗','出卖信任'],
    '复仇':['报仇','报复','清算','反击','讨还','以牙还牙'],
    '救':['营救','解救','搭救','相救','保护','援助'],
    '死亡':['身亡','遇害','牺牲','假死','丧命'],
    '爱情':['相恋','爱慕','感情','情感','恋人','表白'],
    '阴谋':['计划','密谋','算计','布局','陷害','操纵'],
    '身份':['真实身份','伪装','冒名','身世','秘密身份'],
    '和解':['原谅','和好','释怀','冰释前嫌'],
    '冲突':['争执','对峙','争斗','矛盾','交锋'],
    '揭晓':['揭露','真相大白','发现秘密','曝光','揭穿'],
}

def _episode_rows(c,sid):
    return c.execute('SELECT episode_id,episode_number,title,duration_sec FROM episodes WHERE series_id=? ORDER BY episode_number,episode_id',(sid,)).fetchall()

def resolve_cutoff(c,sid,value):
    if value is None or str(value).strip()=='':
        raise ValueError('必须指定 --as-of 集数或集 ID；省略截止集数会有剧透风险')
    raw=str(value).strip()
    row=c.execute('SELECT episode_number,episode_id FROM episodes WHERE series_id=? AND episode_id=?',(sid,raw)).fetchone()
    if row:return int(row['episode_number']),row['episode_id']
    if raw.isdigit():
        n=int(raw)
        exists=c.execute('SELECT episode_id FROM episodes WHERE series_id=? AND episode_number<=? ORDER BY episode_number DESC LIMIT 1',(sid,n)).fetchone()
        if not exists: raise ValueError(f'数据库中没有第 {n} 集或更早的集数')
        return n,exists['episode_id']
    m=re.search(r'(\d+)$',raw)
    if m:
        n=int(m.group(1)); exists=c.execute('SELECT episode_id FROM episodes WHERE series_id=? AND episode_number=?',(sid,n)).fetchone()
        if exists:return n,exists['episode_id']
    raise ValueError(f'无法解析截止集数: {raw}')

def cjk_ngrams(text:str)->list[str]:
    out=[]
    for run in re.findall(r'[\u3400-\u9fff]+',text or ''):
        out.extend('U:'+ch for ch in run)
        if len(run)==1: out.append(run)
        else:
            out.extend(run[i:i+2] for i in range(len(run)-1))
            if len(run)>=3: out.extend(run[i:i+3] for i in range(len(run)-2))
    return out

def tokenize(text:str)->list[str]:
    text=(text or '').casefold()
    latin=re.findall(r'[a-z0-9][a-z0-9._-]*',text)
    cjk=cjk_ngrams(text)
    # Keep a few meaningful punctuation-delimited phrases for exact phrase boost.
    phrases=[x.strip() for x in re.split(r'[，。！？；：、,.!?;:\s]+',text) if len(x.strip())>=2]
    return latin+cjk+['P:'+x for x in phrases]

def _names_for_episode(c,sid,ep):
    rows=c.execute('SELECT local_character_id,canonical_name,aliases_json FROM character_mentions WHERE series_id=? AND episode_id=?',(sid,ep)).fetchall()
    mapping={}
    for r in rows:
        aliases=json.loads(r['aliases_json'] or '[]')
        mapping[r['local_character_id']]={'name':r['canonical_name'],'aliases':aliases}
    return mapping

def build_documents(c,sid,cutoff,include_unreviewed=False,viewpoint_character=None):
    docs=[]; excluded=defaultdict(int)
    eps=c.execute('SELECT episode_id,episode_number,title FROM episodes WHERE series_id=? ORDER BY episode_number',(sid,)).fetchall()
    allowed={r['episode_id']:int(r['episode_number']) for r in eps if int(r['episode_number'])<=cutoff}
    future=[r['episode_id'] for r in eps if int(r['episode_number'])>cutoff]
    for ep,num in allowed.items():
        names=_names_for_episode(c,sid,ep)
        payload_row=c.execute('SELECT payload_json FROM episode_payloads WHERE series_id=? AND episode_id=?',(sid,ep)).fetchone()
        payload=json.loads(payload_row['payload_json']) if payload_row else {}
        # Event documents are episode-local. Never attach global aliases from future episodes.
        for ev in c.execute('SELECT * FROM events WHERE series_id=? AND episode_id=? ORDER BY playback_time',(sid,ep)):
            if not include_unreviewed and ev['review_status']!='PASS': excluded['unreviewed_events']+=1; continue
            if ev['modality'] in {'imagined','hypothetical'}: excluded['non_actual_events']+=1; continue
            event_payload=json.loads(ev['payload_json'] or '{}')
            local_ids=[event_payload.get(k) for k in ('actor','patient','recipient','target') if event_payload.get(k)]
            # Resolve only episode-local IDs; never use globally merged identity IDs in spoiler-safe retrieval.
            local_names=[]
            for local_id in local_ids:
                mention=names.get(local_id)
                if mention: local_names.extend([mention['name']]+mention['aliases'])
            doc={'doc_id':f'EV:{ep}:{ev["event_id"]}','type':'event','episode_id':ep,'episode_number':num,'episode_title':next((x['title'] for x in eps if x['episode_id']==ep),''),'event_id':ev['event_id'],'playback_time':float(ev['playback_time']),'action':ev['action'],'quote':ev['quote'],'modality':ev['modality'],'certainty':ev['certainty'],'review_status':ev['review_status'],'evidence_ids':json.loads(ev['evidence_ids_json'] or '[]'),'local_character_ids':local_ids,'local_character_names':list(dict.fromkeys(local_names))}
            doc['text']=' '.join([doc['action'],doc['quote'],' '.join(doc['local_character_names']),doc['episode_title']])
            docs.append(doc)
        # Episode-local character identity only. Do not expose merged global IDs or future aliases.
        for r in c.execute('SELECT * FROM character_mentions WHERE series_id=? AND episode_id=?',(sid,ep)):
            if not include_unreviewed and r['review_status']!='PASS': excluded['unreviewed_character_mentions']+=1; continue
            aliases=json.loads(r['aliases_json'] or '[]')
            docs.append({'doc_id':f'CH:{ep}:{r["local_character_id"]}','type':'character','episode_id':ep,'episode_number':num,'local_character_id':r['local_character_id'],'name':r['canonical_name'],'aliases':aliases,'identity_status':r['identity_status'],'review_status':r['review_status'],'evidence_ids':json.loads(r['evidence_ids_json'] or '[]'),'text':' '.join([r['canonical_name'],*aliases])})
        # Use episode-local storyline mention text; global storyline descriptions may contain later reveals.
        for r in c.execute('SELECT * FROM storyline_mentions WHERE series_id=? AND episode_id=?',(sid,ep)):
            if not include_unreviewed and r['review_status']!='PASS': excluded['unreviewed_storyline_mentions']+=1; continue
            docs.append({'doc_id':f'SL:{ep}:{r["local_storyline_id"]}','type':'storyline','episode_id':ep,'episode_number':num,'local_storyline_id':r['local_storyline_id'],'title':r['title'],'summary':r['summary'],'status':r['status'],'review_status':r['review_status'],'event_ids':json.loads(r['event_ids_json'] or '[]'),'text':' '.join([r['title'],r['summary']])})
        for r in c.execute('SELECT * FROM thread_mentions WHERE series_id=? AND episode_id=?',(sid,ep)):
            if not include_unreviewed and r['review_status']!='PASS': excluded['unreviewed_thread_mentions']+=1; continue
            docs.append({'doc_id':f'TH:{ep}:{r["local_thread_id"]}','type':'thread','episode_id':ep,'episode_number':num,'local_thread_id':r['local_thread_id'],'description':r['description'],'status':r['status'],'first_event':r['first_event'],'review_status':r['review_status'],'evidence_ids':json.loads(r['evidence_ids_json'] or '[]'),'text':r['description']})
        # Local relations from the episode dossier avoid cross-episode identity leakage.
        for rel in payload.get('relations',[]) or []:
            if not include_unreviewed and rel.get('review_status')!='PASS': continue
            src=names.get(rel.get('source_character'),{}).get('name','')
            dst=names.get(rel.get('target_character'),{}).get('name','')
            txt=' '.join([src,dst,str(rel.get('type') or ''),str(rel.get('description') or '')])
            docs.append({'doc_id':f'REL:{ep}:{rel.get("relation_id","")}', 'type':'relation','episode_id':ep,'episode_number':num,'source_name':src,'target_name':dst,'relation_type':rel.get('type',''),'review_status':rel.get('review_status','UNREVIEWED'),'evidence_ids':rel.get('evidence_ids',[]),'text':txt})
        # Knowledge records are time-stamped. Only expose viewer state or selected character perspective.
        for k in c.execute('SELECT * FROM character_knowledge WHERE series_id=? AND episode_id=? ORDER BY playback_time',(sid,ep)):
            if k['review_status']!='PASS': excluded['unreviewed_knowledge']+=1; continue
            if k['subject_type']=='CHARACTER' and viewpoint_character and k['subject_global_id']!=viewpoint_character: continue
            if k['subject_type']=='CHARACTER' and not viewpoint_character: continue
            if k['subject_type'] not in {'VIEWER','CHARACTER'}: continue
            docs.append({'doc_id':f'KN:{ep}:{k["knowledge_id"]}','type':'knowledge','episode_id':ep,'episode_number':num,'playback_time':float(k['playback_time']),'subject_type':k['subject_type'],'subject_name':k['subject_name'],'proposition':k['proposition'],'state':k['state'],'review_status':k['review_status'],'event_id':k['event_id'],'evidence_ids':json.loads(k['evidence_ids_json'] or '[]'),'text':' '.join([k['subject_name'],k['proposition'],k['state']])})
    return docs,{'episodes_included':len(allowed),'future_episodes_excluded':len(future),'future_episode_ids_exposed':False,'excluded_by_status':dict(excluded)}

def expansion_terms(query,expansion=None):
    terms=[query]
    for key in ('keywords','synonyms','related_phrases','entities'):
        vals=(expansion or {}).get(key,[]) if isinstance(expansion,dict) else []
        if isinstance(vals,str): vals=[vals]
        for x in vals:
            if isinstance(x,str) and x.strip(): terms.append(x.strip())
    # Small conservative domain lexicon. It affects retrieval only, never the returned facts.
    for key,vals in BUILTIN_EXPANSIONS.items():
        if key in query:
            terms.extend(vals)
    return list(dict.fromkeys(terms))

def rank_documents(docs,query,expansion=None,limit=30):
    qterms=Counter(t for phrase in expansion_terms(query,expansion) for t in tokenize(phrase))
    if not qterms:return []
    tokenized=[Counter(tokenize(d.get('text',''))) for d in docs]
    n=len(docs); avgdl=sum(sum(x.values()) for x in tokenized)/max(n,1)
    df=Counter()
    for tf in tokenized: df.update(tf.keys())
    ranked=[]
    for d,tf in zip(docs,tokenized):
        dl=sum(tf.values()); score=0.0
        for term,qf in qterms.items():
            f=tf.get(term,0)
            if not f: continue
            idf=math.log(1+(n-df[term]+0.5)/(df[term]+0.5))
            k1=1.2; b=.72
            bm=idf*(f*(k1+1))/(f+k1*(1-b+b*dl/max(avgdl,1)))
            weight=1.0 if term.startswith('P:') else 0.25 if term.startswith('U:') else 0.75
            score+=bm*weight*min(qf,2)
        raw=d.get('text','').casefold(); phrase=query.casefold().strip()
        if phrase and len(phrase)>=2 and phrase in raw: score+=2.5
        if score>0:
            out={k:v for k,v in d.items() if k!='text'}
            out['snippet']=d.get('text','')[:320]
            out['score']=round(score,4)
            ranked.append(out)
    ranked.sort(key=lambda x:(-x['score'],x.get('episode_number',0),x.get('playback_time',0),x['doc_id']))
    return ranked[:max(1,min(int(limit),500))]

def search_series(db_path,series_id,query,as_of,expansion=None,viewpoint_character=None,include_unreviewed=False,limit=30):
    c=connect(db_path)
    try:
        cutoff,cutoff_id=resolve_cutoff(c,series_id,as_of)
        if viewpoint_character:
            has_visibility=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='series_visibility'").fetchone()
            visible=None
            if has_visibility:
                visible=c.execute("SELECT effective_episode_number,review_status FROM series_visibility WHERE series_id=? AND entity_type='character' AND entity_id=?",(series_id,viewpoint_character)).fetchone()
            if not visible or visible['review_status']!='PASS' or int(visible['effective_episode_number'])>cutoff:
                raise ValueError('该人物全剧身份尚未声明在截止集数前对观众可见；为防止剧透，不能按此全局人物 ID 检索角色视角。请使用已声明可见的人物身份，或不指定人物视角。')
        docs,guard=build_documents(c,series_id,cutoff,include_unreviewed,viewpoint_character)
        results=rank_documents(docs,query,expansion,limit)
        return {'engine_version':VERSION,'series_id':series_id,'query':query,'as_of':{'episode_number':cutoff,'episode_id':cutoff_id,'strict':True},'viewpoint_character':viewpoint_character,'spoiler_guard':guard,'retrieval':{'method':'BM25 + Chinese character n-grams + optional LLM expansion + conservative domain synonyms','embeddings_used':False,'reviewed_only':not include_unreviewed},'result_count':len(results),'results':results}
    finally:c.close()

def make_expansion_prompt(db_path,series_id,query,as_of,output,limit=40):
    # Build a safe, episode-local context sample for ChatGPT to propose query synonyms.
    base=search_series(db_path,series_id,query,as_of,limit=limit)
    c=connect(db_path)
    try:
        cutoff=base['as_of']['episode_number']
        episodes=[dict(r) for r in c.execute('SELECT episode_id,episode_number,title FROM episodes WHERE series_id=? AND episode_number<=? ORDER BY episode_number',(series_id,cutoff))]
    finally:c.close()
    context={'series_id':series_id,'as_of':base['as_of'],'allowed_episode_ids':[x['episode_id'] for x in episodes], 'candidate_results':base['results']}
    prompt='''# 剧情语义检索扩展任务\n\n请只根据下方截至指定集数的资料，为用户查询提出检索扩展词。不得推测或引用截止集数之后的剧情、人物身份、别名或结局。扩展词只用于在允许集数内检索，不是剧情事实。输出纯 JSON：{"keywords":[],"synonyms":[],"related_phrases":[],"entities":[]}。不要把未出现在上下文中的未来真相当作扩展词。\n\n用户查询：'''+query+'\n\n安全上下文：\n```json\n'+json.dumps(context,ensure_ascii=False,indent=2)+'\n```\n'
    Path(output).parent.mkdir(parents=True,exist_ok=True);Path(output).write_text(prompt,encoding='utf-8')
    return {'output':str(output),'as_of':base['as_of'],'candidate_context':len(base['results'])}

def main():
    p=argparse.ArgumentParser(description='全剧语义辅助检索与剧透安全过滤 v3.8')
    sub=p.add_subparsers(dest='cmd',required=True)
    x=sub.add_parser('search');x.add_argument('db');x.add_argument('query');x.add_argument('--series-id',required=True);x.add_argument('--as-of',required=True);x.add_argument('--expansion-json');x.add_argument('--viewpoint-character');x.add_argument('--include-unreviewed',action='store_true');x.add_argument('--limit',type=int,default=30);x.add_argument('--output')
    x=sub.add_parser('make-expansion-prompt');x.add_argument('db');x.add_argument('query');x.add_argument('output');x.add_argument('--series-id',required=True);x.add_argument('--as-of',required=True);x.add_argument('--limit',type=int,default=40)
    a=p.parse_args()
    try:
        if a.cmd=='search':
            expansion=json.loads(Path(a.expansion_json).read_text(encoding='utf-8')) if a.expansion_json else None
            result=search_series(a.db,a.series_id,a.query,a.as_of,expansion,a.viewpoint_character,a.include_unreviewed,a.limit)
            text=json.dumps(result,ensure_ascii=False,indent=2)
            if a.output:Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(text+'\n',encoding='utf-8');print(f'结果已保存: {a.output}（{result["result_count"]} 条）')
            else:print(text)
        else:print(json.dumps(make_expansion_prompt(a.db,a.series_id,a.query,a.as_of,a.output,a.limit),ensure_ascii=False,indent=2))
    except Exception as exc:print(f'错误: {exc}',file=sys.stderr);raise SystemExit(2)
if __name__=='__main__':main()
