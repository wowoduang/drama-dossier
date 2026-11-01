#!/usr/bin/env python3
"""Series Memory Engine v3.5: resumable cross-episode memory backed by SQLite.

Episode dossiers remain the source of truth. This database is a searchable,
rebuildable projection with explicit human-reviewed identity/storyline links.
"""
from __future__ import annotations
import argparse, hashlib, json, re, sqlite3, sys, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from dossier_v3 import load as load_dossier, migrate_to_v35, validate as validate_dossier
ENGINE_VERSION = "3.7.0"

def now() -> str: return datetime.now(timezone.utc).isoformat(timespec="seconds")
def uid(prefix: str) -> str: return f"{prefix}_{uuid.uuid4().hex[:12]}"
def norm(value: str) -> str: return re.sub(r"[\W_]+", "", value.casefold().strip(), flags=re.UNICODE)
def episode_number(episode_id: str, fallback: int = 0) -> int:
    m = re.search(r"(\d+)$", str(episode_id)); return int(m.group(1)) if m else fallback
def file_hash(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""): h.update(chunk)
    return h.hexdigest()
def connect(db_path: str|Path) -> sqlite3.Connection:
    p=Path(db_path); p.parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(str(p),timeout=30); c.row_factory=sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON"); c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA busy_timeout=30000")
    return c
def _json(x: Any)->str: return json.dumps(x,ensure_ascii=False,separators=(",",":"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS series(series_id TEXT PRIMARY KEY,title TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS episodes(series_id TEXT NOT NULL,episode_id TEXT NOT NULL,episode_number INTEGER NOT NULL,title TEXT NOT NULL DEFAULT '',source_version TEXT NOT NULL DEFAULT '',duration_sec REAL NOT NULL DEFAULT 0,dossier_path TEXT NOT NULL,dossier_sha256 TEXT NOT NULL,schema_version TEXT NOT NULL,imported_at TEXT NOT NULL,event_count INTEGER NOT NULL DEFAULT 0,character_count INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(series_id,episode_id),FOREIGN KEY(series_id) REFERENCES series(series_id));
CREATE TABLE IF NOT EXISTS episode_payloads(series_id TEXT NOT NULL,episode_id TEXT NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(series_id,episode_id),FOREIGN KEY(series_id,episode_id) REFERENCES episodes(series_id,episode_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS global_characters(series_id TEXT NOT NULL,global_character_id TEXT NOT NULL,canonical_name TEXT NOT NULL,aliases_json TEXT NOT NULL DEFAULT '[]',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(series_id,global_character_id),FOREIGN KEY(series_id) REFERENCES series(series_id));
CREATE TABLE IF NOT EXISTS character_mentions(series_id TEXT NOT NULL,episode_id TEXT NOT NULL,local_character_id TEXT NOT NULL,global_character_id TEXT NOT NULL,canonical_name TEXT NOT NULL,aliases_json TEXT NOT NULL DEFAULT '[]',identity_status TEXT NOT NULL DEFAULT 'unknown',review_status TEXT NOT NULL DEFAULT 'UNREVIEWED',evidence_ids_json TEXT NOT NULL DEFAULT '[]',PRIMARY KEY(series_id,episode_id,local_character_id),FOREIGN KEY(series_id,episode_id) REFERENCES episodes(series_id,episode_id) ON DELETE CASCADE,FOREIGN KEY(series_id,global_character_id) REFERENCES global_characters(series_id,global_character_id));
CREATE TABLE IF NOT EXISTS events(series_id TEXT NOT NULL,episode_id TEXT NOT NULL,event_id TEXT NOT NULL,episode_number INTEGER NOT NULL,playback_time REAL NOT NULL,story_time TEXT NOT NULL DEFAULT '',actor_global_id TEXT,patient_global_id TEXT,recipient_global_id TEXT,target_global_id TEXT,action TEXT NOT NULL DEFAULT '',quote TEXT NOT NULL DEFAULT '',modality TEXT NOT NULL DEFAULT 'actual',certainty TEXT NOT NULL DEFAULT '',review_status TEXT NOT NULL DEFAULT 'UNREVIEWED',evidence_ids_json TEXT NOT NULL DEFAULT '[]',payload_json TEXT NOT NULL,PRIMARY KEY(series_id,episode_id,event_id),FOREIGN KEY(series_id,episode_id) REFERENCES episodes(series_id,episode_id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(series_id,episode_number,playback_time);
CREATE INDEX IF NOT EXISTS idx_events_actor ON events(series_id,actor_global_id,episode_number);
CREATE INDEX IF NOT EXISTS idx_events_patient ON events(series_id,patient_global_id,episode_number);
CREATE TABLE IF NOT EXISTS relations(series_id TEXT NOT NULL,episode_id TEXT NOT NULL,relation_id TEXT NOT NULL,source_global_id TEXT,target_global_id TEXT,relation_type TEXT NOT NULL DEFAULT '',valid_from_event TEXT,valid_to_event TEXT,status TEXT NOT NULL DEFAULT 'inferred',review_status TEXT NOT NULL DEFAULT 'UNREVIEWED',evidence_ids_json TEXT NOT NULL DEFAULT '[]',payload_json TEXT NOT NULL,PRIMARY KEY(series_id,episode_id,relation_id),FOREIGN KEY(series_id,episode_id) REFERENCES episodes(series_id,episode_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS global_storylines(series_id TEXT NOT NULL,global_storyline_id TEXT NOT NULL,title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(series_id,global_storyline_id),FOREIGN KEY(series_id) REFERENCES series(series_id));
CREATE TABLE IF NOT EXISTS storyline_mentions(series_id TEXT NOT NULL,episode_id TEXT NOT NULL,local_storyline_id TEXT NOT NULL,global_storyline_id TEXT NOT NULL,title TEXT NOT NULL,summary TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'ACTIVE',review_status TEXT NOT NULL DEFAULT 'UNREVIEWED',event_ids_json TEXT NOT NULL DEFAULT '[]',character_ids_json TEXT NOT NULL DEFAULT '[]',PRIMARY KEY(series_id,episode_id,local_storyline_id),FOREIGN KEY(series_id,episode_id) REFERENCES episodes(series_id,episode_id) ON DELETE CASCADE,FOREIGN KEY(series_id,global_storyline_id) REFERENCES global_storylines(series_id,global_storyline_id));
CREATE TABLE IF NOT EXISTS threads(series_id TEXT NOT NULL,episode_id TEXT NOT NULL,thread_id TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'OPEN',first_event TEXT,review_status TEXT NOT NULL DEFAULT 'UNREVIEWED',evidence_ids_json TEXT NOT NULL DEFAULT '[]',payload_json TEXT NOT NULL,PRIMARY KEY(series_id,episode_id,thread_id),FOREIGN KEY(series_id,episode_id) REFERENCES episodes(series_id,episode_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS global_threads(series_id TEXT NOT NULL,global_thread_id TEXT NOT NULL,description TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(series_id,global_thread_id),FOREIGN KEY(series_id) REFERENCES series(series_id));
CREATE TABLE IF NOT EXISTS thread_mentions(series_id TEXT NOT NULL,episode_id TEXT NOT NULL,local_thread_id TEXT NOT NULL,global_thread_id TEXT NOT NULL,description TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'OPEN',first_event TEXT,review_status TEXT NOT NULL DEFAULT 'UNREVIEWED',evidence_ids_json TEXT NOT NULL DEFAULT '[]',payload_json TEXT NOT NULL,PRIMARY KEY(series_id,episode_id,local_thread_id),FOREIGN KEY(series_id,episode_id) REFERENCES episodes(series_id,episode_id) ON DELETE CASCADE,FOREIGN KEY(series_id,global_thread_id) REFERENCES global_threads(series_id,global_thread_id));
CREATE TABLE IF NOT EXISTS thread_candidates(candidate_id TEXT PRIMARY KEY,series_id TEXT NOT NULL,left_episode_id TEXT NOT NULL,left_local_thread_id TEXT NOT NULL,right_episode_id TEXT NOT NULL,right_local_thread_id TEXT NOT NULL,match_key TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'NEEDS_REVIEW',created_at TEXT NOT NULL,UNIQUE(series_id,left_episode_id,left_local_thread_id,right_episode_id,right_local_thread_id),FOREIGN KEY(series_id) REFERENCES series(series_id));
CREATE TABLE IF NOT EXISTS identity_candidates(candidate_id TEXT PRIMARY KEY,series_id TEXT NOT NULL,left_episode_id TEXT NOT NULL,left_local_character_id TEXT NOT NULL,right_episode_id TEXT NOT NULL,right_local_character_id TEXT NOT NULL,match_key TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'NEEDS_REVIEW',created_at TEXT NOT NULL,UNIQUE(series_id,left_episode_id,left_local_character_id,right_episode_id,right_local_character_id),FOREIGN KEY(series_id) REFERENCES series(series_id));
CREATE TABLE IF NOT EXISTS storyline_candidates(candidate_id TEXT PRIMARY KEY,series_id TEXT NOT NULL,left_episode_id TEXT NOT NULL,left_local_storyline_id TEXT NOT NULL,right_episode_id TEXT NOT NULL,right_local_storyline_id TEXT NOT NULL,match_key TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'NEEDS_REVIEW',created_at TEXT NOT NULL,UNIQUE(series_id,left_episode_id,left_local_storyline_id,right_episode_id,right_local_storyline_id),FOREIGN KEY(series_id) REFERENCES series(series_id));
CREATE TABLE IF NOT EXISTS episode_causal_edges(series_id TEXT NOT NULL,episode_id TEXT NOT NULL,cause_id TEXT NOT NULL,from_event TEXT NOT NULL,to_event TEXT NOT NULL,edge_type TEXT NOT NULL,reasoning TEXT NOT NULL DEFAULT '',review_status TEXT NOT NULL DEFAULT 'UNREVIEWED',evidence_ids_json TEXT NOT NULL DEFAULT '[]',counter_evidence_ids_json TEXT NOT NULL DEFAULT '[]',payload_json TEXT NOT NULL,PRIMARY KEY(series_id,episode_id,cause_id),FOREIGN KEY(series_id,episode_id) REFERENCES episodes(series_id,episode_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS episode_story_arcs(series_id TEXT NOT NULL,episode_id TEXT NOT NULL,arc_id TEXT NOT NULL,title TEXT NOT NULL DEFAULT '',arc_type TEXT NOT NULL DEFAULT 'unclassified',segments_json TEXT NOT NULL DEFAULT '[]',event_ids_json TEXT NOT NULL DEFAULT '[]',storyline_ids_json TEXT NOT NULL DEFAULT '[]',evidence_ids_json TEXT NOT NULL DEFAULT '[]',semantic_status TEXT NOT NULL DEFAULT 'NEEDS_CHATGPT_REVIEW',review_status TEXT NOT NULL DEFAULT 'UNREVIEWED',score REAL NOT NULL DEFAULT 0,context_required TEXT NOT NULL DEFAULT 'high',payload_json TEXT NOT NULL,PRIMARY KEY(series_id,episode_id,arc_id),FOREIGN KEY(series_id,episode_id) REFERENCES episodes(series_id,episode_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS character_knowledge(series_id TEXT NOT NULL,episode_id TEXT NOT NULL,knowledge_id TEXT NOT NULL,episode_number INTEGER NOT NULL,subject_type TEXT NOT NULL,subject_global_id TEXT,subject_name TEXT NOT NULL DEFAULT '',proposition TEXT NOT NULL,state TEXT NOT NULL,playback_time REAL NOT NULL,event_id TEXT,evidence_ids_json TEXT NOT NULL DEFAULT '[]',review_status TEXT NOT NULL DEFAULT 'UNREVIEWED',payload_json TEXT NOT NULL,PRIMARY KEY(series_id,episode_id,knowledge_id),FOREIGN KEY(series_id,episode_id) REFERENCES episodes(series_id,episode_id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_knowledge_subject ON character_knowledge(series_id,subject_global_id,episode_number,playback_time);
CREATE TABLE IF NOT EXISTS cross_episode_causal_edges(edge_id TEXT PRIMARY KEY,series_id TEXT NOT NULL,from_episode_id TEXT NOT NULL,from_event_id TEXT NOT NULL,to_episode_id TEXT NOT NULL,to_event_id TEXT NOT NULL,edge_type TEXT NOT NULL,reasoning TEXT NOT NULL,evidence_refs_json TEXT NOT NULL DEFAULT '[]',counter_evidence_refs_json TEXT NOT NULL DEFAULT '[]',review_status TEXT NOT NULL DEFAULT 'UNREVIEWED',review_reason TEXT NOT NULL DEFAULT '',reviewer TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,FOREIGN KEY(series_id) REFERENCES series(series_id));
CREATE INDEX IF NOT EXISTS idx_cross_causal_from ON cross_episode_causal_edges(series_id,from_episode_id,from_event_id);
CREATE INDEX IF NOT EXISTS idx_cross_causal_to ON cross_episode_causal_edges(series_id,to_episode_id,to_event_id);
CREATE TABLE IF NOT EXISTS audit_log(audit_id TEXT PRIMARY KEY,series_id TEXT NOT NULL,action TEXT NOT NULL,object_ref TEXT NOT NULL,reason TEXT NOT NULL,reviewer TEXT NOT NULL DEFAULT '',before_json TEXT NOT NULL DEFAULT '{}',after_json TEXT NOT NULL DEFAULT '{}',timestamp TEXT NOT NULL,FOREIGN KEY(series_id) REFERENCES series(series_id));
CREATE TABLE IF NOT EXISTS ingest_files(series_id TEXT NOT NULL,path TEXT NOT NULL,sha256 TEXT NOT NULL DEFAULT '',episode_id TEXT,status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 1,last_error TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL,PRIMARY KEY(series_id,path),FOREIGN KEY(series_id) REFERENCES series(series_id));
"""
def init_db(db_path:str|Path,series_id:str,title:str)->None:
    c=connect(db_path)
    try:
        c.executescript(SCHEMA); stamp=now()
        c.execute("INSERT OR IGNORE INTO series VALUES(?,?,?,?)",(series_id,title,stamp,stamp))
        c.execute("UPDATE series SET title=?,updated_at=? WHERE series_id=?",(title,stamp,series_id))
        c.execute("INSERT OR REPLACE INTO meta VALUES('engine_version',?)",(ENGINE_VERSION,)); c.commit()
    finally:c.close()
def _global_for_local(c, sid, eid, local):
    if not local:return None
    r=c.execute("SELECT global_character_id FROM character_mentions WHERE series_id=? AND episode_id=? AND local_character_id=?",(sid,eid,local)).fetchone()
    return r[0] if r else None
def _record_ingest(c,sid,path,sha,eid,status,error=""):
    path=str(path.resolve()); old=c.execute("SELECT attempts FROM ingest_files WHERE series_id=? AND path=?",(sid,path)).fetchone()
    c.execute("INSERT OR REPLACE INTO ingest_files VALUES(?,?,?,?,?,?,?,?)",(sid,path,sha,eid,status,(old[0]+1 if old else 1),error[:2000],now()))
def refresh_candidates(c,sid):
    # Rebuild only pending suggestions so removed/replaced episode entities cannot leave stale queue rows.
    c.execute("DELETE FROM identity_candidates WHERE series_id=? AND status='NEEDS_REVIEW'",(sid,))
    c.execute("DELETE FROM storyline_candidates WHERE series_id=? AND status='NEEDS_REVIEW'",(sid,))
    c.execute("DELETE FROM thread_candidates WHERE series_id=? AND status='NEEDS_REVIEW'",(sid,))
    chars=c.execute("SELECT episode_id,local_character_id,canonical_name,aliases_json,global_character_id FROM character_mentions WHERE series_id=? ORDER BY episode_id,local_character_id",(sid,)).fetchall(); ca=0
    for i,a in enumerate(chars):
        na={norm(a[2])}|{norm(x) for x in json.loads(a[3] or '[]')}; na.discard('')
        for b in chars[i+1:]:
            if a[0]==b[0] or a[4]==b[4]:continue
            common=sorted(na & ({norm(b[2])}|{norm(x) for x in json.loads(b[3] or '[]')}))
            if not common:continue
            l,r=(a,b) if (a[0],a[1])<(b[0],b[1]) else (b,a)
            ca+=c.execute("INSERT OR IGNORE INTO identity_candidates VALUES(?,?,?,?,?,?,?,?,?)",(uid('IC'),sid,l[0],l[1],r[0],r[1],common[0],'NEEDS_REVIEW',now())).rowcount
    lines=c.execute("SELECT episode_id,local_storyline_id,title,global_storyline_id FROM storyline_mentions WHERE series_id=? ORDER BY episode_id,local_storyline_id",(sid,)).fetchall(); sa=0
    for i,a in enumerate(lines):
        key=norm(a[2])
        if not key:continue
        for b in lines[i+1:]:
            if a[0]==b[0] or a[3]==b[3] or key!=norm(b[2]):continue
            l,r=(a,b) if (a[0],a[1])<(b[0],b[1]) else (b,a)
            sa+=c.execute("INSERT OR IGNORE INTO storyline_candidates VALUES(?,?,?,?,?,?,?,?,?)",(uid('SC'),sid,l[0],l[1],r[0],r[1],key,'NEEDS_REVIEW',now())).rowcount
    threads=c.execute("SELECT episode_id,local_thread_id,description,global_thread_id FROM thread_mentions WHERE series_id=? ORDER BY episode_id,local_thread_id",(sid,)).fetchall(); ta=0
    for i,a in enumerate(threads):
        key=norm(a[2])
        if not key:continue
        for b in threads[i+1:]:
            if a[0]==b[0] or a[3]==b[3] or key!=norm(b[2]):continue
            l,r=(a,b) if (a[0],a[1])<(b[0],b[1]) else (b,a)
            ta+=c.execute("INSERT OR IGNORE INTO thread_candidates VALUES(?,?,?,?,?,?,?,?,?)",(uid('TC'),sid,l[0],l[1],r[0],r[1],key,'NEEDS_REVIEW',now())).rowcount
    return {"identity_candidates_added":ca,"storyline_candidates_added":sa,"thread_candidates_added":ta}

def import_dossier(db_path,series_id,dossier_path,replace=False):
    path=Path(dossier_path).resolve()
    if not path.is_file():raise FileNotFoundError(path)
    sha=file_hash(path); raw=load_dossier(path)
    errors=validate_dossier(raw)
    if errors:raise ValueError("档案结构校验失败: "+"; ".join(errors[:12]))
    data=migrate_to_v35(raw); ep=data.get('episode') or {}; eid=str(ep.get('episode_id') or '').strip()
    if not eid:raise ValueError('episode.episode_id 不能为空')
    c=connect(db_path)
    try:
        if not c.execute("SELECT 1 FROM series WHERE series_id=?",(series_id,)).fetchone():raise ValueError(f"数据库中不存在 series_id={series_id}；请先 init")
        old=c.execute("SELECT dossier_sha256 FROM episodes WHERE series_id=? AND episode_id=?",(series_id,eid)).fetchone()
        if old and old[0]==sha:
            _record_ingest(c,series_id,path,sha,eid,'SKIPPED_UNCHANGED'); c.commit()
            return {"status":"SKIPPED_UNCHANGED","episode_id":eid,"events":len(data.get('events',[]))}
        if old and not replace:raise ValueError(f"{eid} 已存在但文件内容变化；确认覆盖请加 --replace")
        c.execute("BEGIN IMMEDIATE")
        old_chars={r[0]:r[1] for r in c.execute("SELECT local_character_id,global_character_id FROM character_mentions WHERE series_id=? AND episode_id=?",(series_id,eid))}
        old_lines={r[0]:r[1] for r in c.execute("SELECT local_storyline_id,global_storyline_id FROM storyline_mentions WHERE series_id=? AND episode_id=?",(series_id,eid))}
        old_threads={r[0]:r[1] for r in c.execute("SELECT local_thread_id,global_thread_id FROM thread_mentions WHERE series_id=? AND episode_id=?",(series_id,eid))}
        if old:c.execute("DELETE FROM episodes WHERE series_id=? AND episode_id=?",(series_id,eid))
        if old and c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='series_visibility'").fetchone():
            for declaration in c.execute("SELECT * FROM series_visibility WHERE series_id=? AND review_status='PASS'", (series_id,)).fetchall():
                refs = json.loads(declaration['evidence_refs_json'])
                if declaration['effective_episode_id'] == eid or any(ref.split(':', 1)[0] == eid for ref in refs):
                    c.execute("UPDATE series_visibility SET review_status='NEED_EVIDENCE' WHERE series_id=? AND entity_type=? AND entity_id=?", (series_id, declaration['entity_type'], declaration['entity_id']))
                    c.execute("INSERT INTO audit_log VALUES(?,?,?,?,?,?,?,?,?)", (uid('AUD'), series_id, 'REOPEN_VISIBILITY_AFTER_EPISODE_REPLACE', declaration['entity_id'], '可见性证据分集已更新，需重新声明', '', _json(dict(declaration)), _json({'review_status':'NEED_EVIDENCE'}), now()))

        if old:
            stale=c.execute("SELECT edge_id FROM cross_episode_causal_edges WHERE series_id=? AND (from_episode_id=? OR to_episode_id=?) AND review_status='PASS'",(series_id,eid,eid)).fetchall()
            for edge_row in stale:
                edge_id=edge_row[0]
                c.execute("UPDATE cross_episode_causal_edges SET review_status='NEED_EVIDENCE',review_reason='端点分集档案已更新，需重新核对原片证据',updated_at=? WHERE series_id=? AND edge_id=?",(now(),series_id,edge_id))
                c.execute("INSERT INTO audit_log VALUES(?,?,?,?,?,?,?,?,?)",(uid('AUD'),series_id,'REOPEN_CAUSAL_AFTER_EPISODE_REPLACE',edge_id,'端点分集档案内容发生变化，旧因果审核不再自动沿用','', '{}', _json({'review_status':'NEED_EVIDENCE'}), now()))
        n=episode_number(eid,int(ep.get('episode_number') or 0))
        c.execute("INSERT INTO episodes VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(series_id,eid,n,str(ep.get('title') or ''),str(ep.get('source_version') or ''),float(ep.get('duration_sec') or 0),str(path),sha,str(data.get('schema_version') or ''),now(),len(data.get('events',[])),len(data.get('characters',[]))))
        c.execute("INSERT INTO episode_payloads VALUES(?,?,?)",(series_id,eid,_json(data)))
        for ch in data.get('characters',[]):
            lid=str(ch.get('character_id') or '')
            if not lid:continue
            gid=old_chars.get(lid) or uid('GCHR'); aliases=list(dict.fromkeys(str(x) for x in (ch.get('aliases') or []) if str(x).strip()))
            c.execute("INSERT OR IGNORE INTO global_characters VALUES(?,?,?,?,?,?)",(series_id,gid,str(ch.get('canonical_name') or lid),_json(aliases),now(),now()))
            c.execute("INSERT OR REPLACE INTO character_mentions VALUES(?,?,?,?,?,?,?,?,?)",(series_id,eid,lid,gid,str(ch.get('canonical_name') or lid),_json(aliases),str(ch.get('identity_status') or 'unknown'),str(ch.get('review_status') or 'UNREVIEWED'),_json(ch.get('evidence_ids') or [])))
        for ev in data.get('events',[]):
            event_id=str(ev.get('event_id') or '')
            if not event_id:continue
            c.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(series_id,eid,event_id,n,float(ev.get('playback_time') or 0),str(ev.get('story_time') or ''),_global_for_local(c,series_id,eid,ev.get('actor')),_global_for_local(c,series_id,eid,ev.get('patient')),_global_for_local(c,series_id,eid,ev.get('recipient')),_global_for_local(c,series_id,eid,ev.get('target')),str(ev.get('action') or ''),str(ev.get('quote') or ''),str(ev.get('modality') or 'actual'),str(ev.get('certainty') or ''),str(ev.get('review_status') or 'UNREVIEWED'),_json(ev.get('evidence_ids') or []),_json(ev)))
        for edge in data.get('causal_edges',[]):
            cid=str(edge.get('cause_id') or '')
            if cid:
                c.execute("INSERT INTO episode_causal_edges VALUES(?,?,?,?,?,?,?,?,?,?,?)",(series_id,eid,cid,str(edge.get('from_event') or ''),str(edge.get('to_event') or ''),str(edge.get('type') or 'POSSIBLE_CAUSE'),str(edge.get('reasoning') or ''),str(edge.get('review_status') or 'UNREVIEWED'),_json(edge.get('evidence_ids') or []),_json(edge.get('counter_evidence_ids') or []),_json(edge)))
        for arc in data.get('story_arcs',[]):
            aid=str(arc.get('arc_id') or '')
            if aid:
                c.execute("INSERT INTO episode_story_arcs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(series_id,eid,aid,str(arc.get('title') or ''),str(arc.get('arc_type') or 'unclassified'),_json(arc.get('segments') or []),_json(arc.get('event_ids') or []),_json(arc.get('storyline_ids') or []),_json(arc.get('evidence_ids') or []),str(arc.get('semantic_status') or 'NEEDS_CHATGPT_REVIEW'),str(arc.get('review_status') or 'UNREVIEWED'),float(arc.get('total_score') or 0),str(arc.get('context_required') or 'high'),_json(arc)))
        for ks in data.get('knowledge_states',[]):
            kid=str(ks.get('knowledge_id') or '')
            if not kid:continue
            subject_type=str(ks.get('subject_type') or 'CHARACTER')
            local_char=ks.get('character_id') if subject_type=='CHARACTER' else None
            subject_gid=_global_for_local(c,series_id,eid,local_char) if local_char else None
            c.execute("INSERT INTO character_knowledge VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(series_id,eid,kid,n,subject_type,subject_gid,str(ks.get('subject_name') or ''),str(ks.get('proposition') or ''),str(ks.get('state') or 'UNCERTAIN'),float(ks.get('playback_time') or 0),ks.get('event_id'),_json(ks.get('evidence_ids') or []),str(ks.get('review_status') or 'UNREVIEWED'),_json(ks)))
        for rel in data.get('relations',[]):
            rid=str(rel.get('relation_id') or '')
            if rid:c.execute("INSERT INTO relations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(series_id,eid,rid,_global_for_local(c,series_id,eid,rel.get('source_character')),_global_for_local(c,series_id,eid,rel.get('target_character')),str(rel.get('type') or ''),rel.get('valid_from_event'),rel.get('valid_to_event'),str(rel.get('status') or 'inferred'),str(rel.get('review_status') or 'UNREVIEWED'),_json(rel.get('evidence_ids') or []),_json(rel)))
        for line in data.get('storylines',[]):
            lid=str(line.get('storyline_id') or '')
            if not lid:continue
            gid=old_lines.get(lid) or uid('GSTL'); title=str(line.get('title') or lid); summary=str(line.get('summary') or '')
            c.execute("INSERT OR IGNORE INTO global_storylines VALUES(?,?,?,?,?,?)",(series_id,gid,title,summary,now(),now()))
            c.execute("INSERT INTO storyline_mentions VALUES(?,?,?,?,?,?,?,?,?,?)",(series_id,eid,lid,gid,title,summary,str(line.get('status') or 'ACTIVE'),str(line.get('review_status') or 'UNREVIEWED'),_json(line.get('event_ids') or []),_json(line.get('character_ids') or [])))
        for th in data.get('threads',[]):
            tid=str(th.get('thread_id') or '')
            if tid:
                desc=str(th.get('description') or '')
                gtid=old_threads.get(tid) or uid('GTHR')
                c.execute("INSERT OR IGNORE INTO global_threads VALUES(?,?,?,?,?)",(series_id,gtid,desc,now(),now()))
                c.execute("INSERT INTO thread_mentions VALUES(?,?,?,?,?,?,?,?,?,?)",(series_id,eid,tid,gtid,desc,str(th.get('status') or 'OPEN'),th.get('first_event'),str(th.get('review_status') or 'UNREVIEWED'),_json(th.get('evidence_ids') or []),_json(th)))
                c.execute("INSERT INTO threads VALUES(?,?,?,?,?,?,?,?,?)",(series_id,eid,tid,desc,str(th.get('status') or 'OPEN'),th.get('first_event'),str(th.get('review_status') or 'UNREVIEWED'),_json(th.get('evidence_ids') or []),_json(th)))
        _record_ingest(c,series_id,path,sha,eid,'IMPORTED'); c.execute("UPDATE series SET updated_at=? WHERE series_id=?",(now(),series_id)); c.commit()
        # Remove orphaned global entities after an episode replacement.
        c.execute("DELETE FROM global_characters WHERE series_id=? AND NOT EXISTS (SELECT 1 FROM character_mentions m WHERE m.series_id=global_characters.series_id AND m.global_character_id=global_characters.global_character_id)",(series_id,))
        c.execute("DELETE FROM global_storylines WHERE series_id=? AND NOT EXISTS (SELECT 1 FROM storyline_mentions m WHERE m.series_id=global_storylines.series_id AND m.global_storyline_id=global_storylines.global_storyline_id)",(series_id,))
        c.execute("DELETE FROM global_threads WHERE series_id=? AND NOT EXISTS (SELECT 1 FROM thread_mentions m WHERE m.series_id=global_threads.series_id AND m.global_thread_id=global_threads.global_thread_id)",(series_id,))
        refresh_candidates(c,series_id); c.commit()
        return {"status":"IMPORTED","episode_id":eid,"events":len(data.get('events',[])),"characters":len(data.get('characters',[])),"storylines":len(data.get('storylines',[]))}
    except Exception:c.rollback(); raise
    finally:c.close()

def _parse_ref(ref):
    if ':' not in ref:raise ValueError('本地引用格式应为 EPISODE_ID:LOCAL_ID，例如 EP001:CHR001')
    ep,lid=ref.split(':',1)
    if not ep or not lid:raise ValueError(f'无效本地引用: {ref}')
    return ep,lid

def link_characters(db_path,series_id,left_ref,right_ref,reason,reviewer=''):
    if not reason.strip():raise ValueError('合并身份必须填写审核理由')
    le,ll=_parse_ref(left_ref); re_,rl=_parse_ref(right_ref); c=connect(db_path)
    try:
        c.execute('BEGIN IMMEDIATE')
        a=c.execute("SELECT global_character_id,canonical_name,aliases_json FROM character_mentions WHERE series_id=? AND episode_id=? AND local_character_id=?",(series_id,le,ll)).fetchone()
        b=c.execute("SELECT global_character_id,canonical_name,aliases_json FROM character_mentions WHERE series_id=? AND episode_id=? AND local_character_id=?",(series_id,re_,rl)).fetchone()
        if not a or not b:raise ValueError('找不到指定的人物提及')
        keep,drop=a[0],b[0]
        if keep==drop:c.rollback(); return keep
        before={'left':dict(a),'right':dict(b)}
        c.execute("UPDATE character_mentions SET global_character_id=? WHERE series_id=? AND global_character_id=?",(keep,series_id,drop))
        for col in ('actor_global_id','patient_global_id','recipient_global_id','target_global_id'):
            c.execute(f"UPDATE events SET {col}=? WHERE series_id=? AND {col}=?",(keep,series_id,drop))
        c.execute("UPDATE character_knowledge SET subject_global_id=? WHERE series_id=? AND subject_global_id=?",(keep,series_id,drop))
        for col in ('source_global_id','target_global_id'):
            c.execute(f"UPDATE relations SET {col}=? WHERE series_id=? AND {col}=?",(keep,series_id,drop))
        aliases=set(json.loads(a[2] or '[]'))|set(json.loads(b[2] or '[]'))|{b[1]}; aliases.discard(a[1])
        c.execute("UPDATE global_characters SET aliases_json=?,updated_at=? WHERE series_id=? AND global_character_id=?",(_json(sorted(aliases)),now(),series_id,keep))
        c.execute("DELETE FROM global_characters WHERE series_id=? AND global_character_id=?",(series_id,drop))
        c.execute("UPDATE identity_candidates SET status='ACCEPTED' WHERE series_id=? AND ((left_episode_id=? AND left_local_character_id=? AND right_episode_id=? AND right_local_character_id=?) OR (left_episode_id=? AND left_local_character_id=? AND right_episode_id=? AND right_local_character_id=?))",(series_id,le,ll,re_,rl,re_,rl,le,ll))
        c.execute("INSERT INTO audit_log VALUES(?,?,?,?,?,?,?,?,?)",(uid('AUD'),series_id,'LINK_CHARACTER',f'{left_ref}={right_ref}',reason,reviewer,_json(before),_json({'global_character_id':keep}),now()))
        refresh_candidates(c,series_id)
        c.commit(); return keep
    except Exception:c.rollback(); raise
    finally:c.close()

def link_storylines(db_path,series_id,left_ref,right_ref,reason,reviewer=''):
    if not reason.strip():raise ValueError('合并剧情线必须填写审核理由')
    le,ll=_parse_ref(left_ref); re_,rl=_parse_ref(right_ref); c=connect(db_path)
    try:
        c.execute('BEGIN IMMEDIATE')
        a=c.execute("SELECT global_storyline_id,title FROM storyline_mentions WHERE series_id=? AND episode_id=? AND local_storyline_id=?",(series_id,le,ll)).fetchone()
        b=c.execute("SELECT global_storyline_id,title FROM storyline_mentions WHERE series_id=? AND episode_id=? AND local_storyline_id=?",(series_id,re_,rl)).fetchone()
        if not a or not b:raise ValueError('找不到指定的剧情线提及')
        keep,drop=a[0],b[0]
        if keep==drop:c.rollback(); return keep
        c.execute("UPDATE storyline_mentions SET global_storyline_id=? WHERE series_id=? AND global_storyline_id=?",(keep,series_id,drop))
        c.execute("DELETE FROM global_storylines WHERE series_id=? AND global_storyline_id=?",(series_id,drop))
        c.execute("UPDATE storyline_candidates SET status='ACCEPTED' WHERE series_id=? AND ((left_episode_id=? AND left_local_storyline_id=? AND right_episode_id=? AND right_local_storyline_id=?) OR (left_episode_id=? AND left_local_storyline_id=? AND right_episode_id=? AND right_local_storyline_id=?))",(series_id,le,ll,re_,rl,re_,rl,le,ll))
        c.execute("INSERT INTO audit_log VALUES(?,?,?,?,?,?,?,?,?)",(uid('AUD'),series_id,'LINK_STORYLINE',f'{left_ref}={right_ref}',reason,reviewer,'{}',_json({'global_storyline_id':keep}),now()))
        refresh_candidates(c,series_id)
        c.commit(); return keep
    except Exception:c.rollback(); raise
    finally:c.close()

def link_threads(db_path,series_id,left_ref,right_ref,reason,reviewer=''):
    if not reason.strip():raise ValueError('合并伏笔/线索必须填写审核理由')
    le,ll=_parse_ref(left_ref); re_,rl=_parse_ref(right_ref); c=connect(db_path)
    try:
        c.execute('BEGIN IMMEDIATE')
        a=c.execute("SELECT global_thread_id,description FROM thread_mentions WHERE series_id=? AND episode_id=? AND local_thread_id=?",(series_id,le,ll)).fetchone()
        b=c.execute("SELECT global_thread_id,description FROM thread_mentions WHERE series_id=? AND episode_id=? AND local_thread_id=?",(series_id,re_,rl)).fetchone()
        if not a or not b:raise ValueError('找不到指定的伏笔/线索提及')
        keep,drop=a[0],b[0]
        if keep==drop:c.rollback(); return keep
        c.execute("UPDATE thread_mentions SET global_thread_id=? WHERE series_id=? AND global_thread_id=?",(keep,series_id,drop))
        c.execute("DELETE FROM global_threads WHERE series_id=? AND global_thread_id=?",(series_id,drop))
        c.execute("UPDATE thread_candidates SET status='ACCEPTED' WHERE series_id=? AND ((left_episode_id=? AND left_local_thread_id=? AND right_episode_id=? AND right_local_thread_id=?) OR (left_episode_id=? AND left_local_thread_id=? AND right_episode_id=? AND right_local_thread_id=?))",(series_id,le,ll,re_,rl,re_,rl,le,ll))
        c.execute("INSERT INTO audit_log VALUES(?,?,?,?,?,?,?,?,?)",(uid('AUD'),series_id,'LINK_THREAD',f'{left_ref}={right_ref}',reason,reviewer,'{}',_json({'global_thread_id':keep}),now()))
        refresh_candidates(c,series_id)
        c.commit(); return keep
    except Exception:c.rollback(); raise
    finally:c.close()

def reject_candidate(db_path,series_id,candidate_id,kind,reason,reviewer=''):
    if not reason.strip():raise ValueError('驳回候选必须填写理由')
    table={'identity':'identity_candidates','storyline':'storyline_candidates','thread':'thread_candidates'}.get(kind)
    if not table:raise ValueError('kind 必须是 identity/storyline/thread')
    c=connect(db_path)
    try:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute(f"SELECT * FROM {table} WHERE series_id=? AND candidate_id=?",(series_id,candidate_id)).fetchone()
        if not row:raise ValueError('找不到候选')
        before=dict(row)
        c.execute(f"UPDATE {table} SET status='REJECTED' WHERE series_id=? AND candidate_id=?",(series_id,candidate_id))
        c.execute("INSERT INTO audit_log VALUES(?,?,?,?,?,?,?,?,?)",(uid('AUD'),series_id,'REJECT_CANDIDATE',f'{kind}:{candidate_id}',reason,reviewer,_json(before),_json({'status':'REJECTED'}),now()))
        refresh_candidates(c,series_id)
        c.commit(); return True
    except Exception:c.rollback(); raise
    finally:c.close()

def _role_for(e,gid):
    return ','.join(n for n,col in [('actor','actor_global_id'),('patient','patient_global_id'),('recipient','recipient_global_id'),('target','target_global_id')] if e[col]==gid)
def build_memory(c,sid):
    s=c.execute('SELECT * FROM series WHERE series_id=?',(sid,)).fetchone()
    if not s:raise ValueError(f'未知 series_id: {sid}')
    eps=[dict(r) for r in c.execute('SELECT * FROM episodes WHERE series_id=? ORDER BY episode_number,episode_id',(sid,))]
    chars=[]
    for ch in c.execute('SELECT * FROM global_characters WHERE series_id=? ORDER BY canonical_name',(sid,)):
        mentions=c.execute('SELECT * FROM character_mentions WHERE series_id=? AND global_character_id=? ORDER BY episode_id',(sid,ch['global_character_id'])).fetchall()
        evs=c.execute('SELECT * FROM events WHERE series_id=? AND (actor_global_id=? OR patient_global_id=? OR recipient_global_id=? OR target_global_id=?) ORDER BY episode_number,playback_time',(sid,ch['global_character_id'],ch['global_character_id'],ch['global_character_id'],ch['global_character_id'])).fetchall()
        chars.append({'global_character_id':ch['global_character_id'],'canonical_name':ch['canonical_name'],'aliases':json.loads(ch['aliases_json'] or '[]'),'mentions':[{'episode_id':m['episode_id'],'local_character_id':m['local_character_id'],'name':m['canonical_name'],'identity_status':m['identity_status'],'review_status':m['review_status'],'evidence_ids':json.loads(m['evidence_ids_json'] or '[]')} for m in mentions],'event_timeline':[{'episode_id':e['episode_id'],'event_id':e['event_id'],'episode_number':e['episode_number'],'playback_time':e['playback_time'],'role':_role_for(e,ch['global_character_id']),'action':e['action'],'quote':e['quote'],'modality':e['modality'],'certainty':e['certainty'],'review_status':e['review_status'],'evidence_ids':json.loads(e['evidence_ids_json'] or '[]')} for e in evs]})
    lines=[]
    for line in c.execute('SELECT * FROM global_storylines WHERE series_id=? ORDER BY title',(sid,)):
        ms=c.execute('SELECT * FROM storyline_mentions WHERE series_id=? AND global_storyline_id=? ORDER BY episode_id',(sid,line['global_storyline_id'])).fetchall()
        lines.append({'global_storyline_id':line['global_storyline_id'],'title':line['title'],'description':line['description'],'mentions':[{'episode_id':m['episode_id'],'local_storyline_id':m['local_storyline_id'],'summary':m['summary'],'status':m['status'],'review_status':m['review_status'],'event_ids':json.loads(m['event_ids_json'] or '[]'),'character_ids':json.loads(m['character_ids_json'] or '[]')} for m in ms]})
    rels=[]
    for r in c.execute('SELECT * FROM relations WHERE series_id=? ORDER BY episode_id,relation_id',(sid,)):
        x=dict(r); x['evidence_ids']=json.loads(x.pop('evidence_ids_json')); x['payload']=json.loads(x.pop('payload_json')); rels.append(x)
    threads=[]
    for th in c.execute('SELECT * FROM global_threads WHERE series_id=? ORDER BY description',(sid,)):
        ms=c.execute('SELECT * FROM thread_mentions WHERE series_id=? AND global_thread_id=? ORDER BY episode_id',(sid,th['global_thread_id'])).fetchall()
        threads.append({'global_thread_id':th['global_thread_id'],'description':th['description'],'mentions':[{'episode_id':m['episode_id'],'local_thread_id':m['local_thread_id'],'status':m['status'],'first_event':m['first_event'],'review_status':m['review_status'],'evidence_ids':json.loads(m['evidence_ids_json'] or '[]'),'payload':json.loads(m['payload_json'])} for m in ms]})
    event_index=[]
    for r in c.execute('SELECT * FROM events WHERE series_id=? ORDER BY episode_number,playback_time',(sid,)):
        x=dict(r); x['evidence_ids']=json.loads(x.pop('evidence_ids_json')); x['payload']=json.loads(x.pop('payload_json')); event_index.append(x)
    causal_edges=[]
    for r in c.execute('SELECT * FROM episode_causal_edges WHERE series_id=? ORDER BY episode_id,cause_id',(sid,)):
        x=dict(r); x['evidence_ids']=json.loads(x.pop('evidence_ids_json')); x['counter_evidence_ids']=json.loads(x.pop('counter_evidence_ids_json')); x['payload']=json.loads(x.pop('payload_json')); causal_edges.append(x)
    story_arcs=[]
    for r in c.execute('SELECT * FROM episode_story_arcs WHERE series_id=? ORDER BY episode_id,arc_id',(sid,)):
        x=dict(r); x['segments']=json.loads(x.pop('segments_json')); x['event_ids']=json.loads(x.pop('event_ids_json')); x['storyline_ids']=json.loads(x.pop('storyline_ids_json')); x['evidence_ids']=json.loads(x.pop('evidence_ids_json')); x['payload']=json.loads(x.pop('payload_json')); story_arcs.append(x)
    knowledge_states=[]
    for r in c.execute('SELECT * FROM character_knowledge WHERE series_id=? ORDER BY episode_number,playback_time',(sid,)):
        x=dict(r); x['evidence_ids']=json.loads(x.pop('evidence_ids_json')); x['payload']=json.loads(x.pop('payload_json')); knowledge_states.append(x)
    cross_edges=[]
    for r in c.execute('SELECT * FROM cross_episode_causal_edges WHERE series_id=? ORDER BY created_at',(sid,)):
        x=dict(r); x['evidence_refs']=json.loads(x.pop('evidence_refs_json')); x['counter_evidence_refs']=json.loads(x.pop('counter_evidence_refs_json')); cross_edges.append(x)
    evidence_index=[]
    character_states=[]
    character_state_changes=[]
    for row in c.execute('SELECT episode_id,payload_json FROM episode_payloads WHERE series_id=? ORDER BY episode_id',(sid,)):
        episode_payload=json.loads(row['payload_json'])
        for state in episode_payload.get('character_states',[]):
            character_states.append({**state, 'episode_id':row['episode_id']})
        for change in episode_payload.get('character_state_changes',[]):
            character_state_changes.append({**change, 'episode_id':row['episode_id']})
        for ev in episode_payload.get('evidence',[]):
            evidence_index.append({'episode_id':row['episode_id'],'evidence_id':ev.get('evidence_id'),'type':ev.get('type'),'source':ev.get('source'),'start_sec':ev.get('start_sec'),'end_sec':ev.get('end_sec'),'raw':ev.get('raw'),'observation':ev.get('observation'),'locator':ev.get('locator'),'review_status':ev.get('review_status','UNREVIEWED')})
    ic=[dict(r) for r in c.execute("SELECT * FROM identity_candidates WHERE series_id=? AND status='NEEDS_REVIEW' ORDER BY match_key",(sid,))]
    sc=[dict(r) for r in c.execute("SELECT * FROM storyline_candidates WHERE series_id=? AND status='NEEDS_REVIEW' ORDER BY match_key",(sid,))]
    tc=[dict(r) for r in c.execute("SELECT * FROM thread_candidates WHERE series_id=? AND status='NEEDS_REVIEW' ORDER BY match_key",(sid,))]
    audits=[]
    for a in c.execute('SELECT * FROM audit_log WHERE series_id=? ORDER BY timestamp',(sid,)):
        x=dict(a); x['before']=json.loads(x.pop('before_json')); x['after']=json.loads(x.pop('after_json')); audits.append(x)
    visibility=[]
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='series_visibility'").fetchone():
        for v in c.execute('SELECT * FROM series_visibility WHERE series_id=? ORDER BY effective_episode_number,entity_type,entity_id',(sid,)):
            x=dict(v);x['evidence_refs']=json.loads(x.pop('evidence_refs_json'));visibility.append(x)
    visibility_audits=[]
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='visibility_audit'").fetchone():
        for v in c.execute('SELECT * FROM visibility_audit WHERE series_id=? ORDER BY created_at',(sid,)):
            x=dict(v);x['before']=json.loads(x.pop('before_json'));x['after']=json.loads(x.pop('after_json'));visibility_audits.append(x)
    return {'engine_version':ENGINE_VERSION,'series':{'series_id':s['series_id'],'title':s['title']},'episodes':eps,'characters':chars,'storylines':lines,'relation_history':rels,'threads':threads,'events':event_index,'causal_edges':causal_edges,'story_arcs':story_arcs,'knowledge_states':knowledge_states,'character_states':character_states,'character_state_changes':character_state_changes,'cross_episode_causal_edges':cross_edges,'series_visibility':visibility,'visibility_audit':visibility_audits,'evidence_index':evidence_index,'identity_candidates':ic,'storyline_candidates':sc,'thread_candidates':tc,'audit_log':audits,'generated_at':now()}
def export_memory(db_path,sid,output):
    c=connect(db_path)
    try:p=build_memory(c,sid)
    finally:c.close()
    out=Path(output); out.parent.mkdir(parents=True,exist_ok=True); tmp=out.with_suffix(out.suffix+'.tmp')
    tmp.write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); tmp.replace(out); return p
def search(db_path,sid,query,limit=50):
    q=f'%{query.strip()}%'; c=connect(db_path)
    try:
        out=[]
        for r in c.execute('SELECT episode_id,event_id,episode_number,playback_time,action,quote,modality,review_status FROM events WHERE series_id=? AND (action LIKE ? OR quote LIKE ?) ORDER BY episode_number,playback_time LIMIT ?',(sid,q,q,limit)):out.append({'type':'event',**dict(r)})
        for r in c.execute('SELECT episode_id,local_character_id,global_character_id,canonical_name,aliases_json,review_status FROM character_mentions WHERE series_id=? AND (canonical_name LIKE ? OR aliases_json LIKE ?) ORDER BY episode_id LIMIT ?',(sid,q,q,limit)):out.append({'type':'character','episode_id':r['episode_id'],'local_character_id':r['local_character_id'],'global_character_id':r['global_character_id'],'canonical_name':r['canonical_name'],'aliases':json.loads(r['aliases_json'] or '[]'),'review_status':r['review_status']})
        for r in c.execute('SELECT episode_id,local_storyline_id,title,summary,status FROM storyline_mentions WHERE series_id=? AND (title LIKE ? OR summary LIKE ?) ORDER BY episode_id LIMIT ?',(sid,q,q,limit)):out.append({'type':'storyline',**dict(r)})
        for r in c.execute('SELECT episode_id,local_thread_id,description,status,first_event FROM thread_mentions WHERE series_id=? AND description LIKE ? ORDER BY episode_id LIMIT ?',(sid,q,limit)):out.append({'type':'thread',**dict(r)})
        for r in c.execute('SELECT episode_id,relation_id,relation_type,status,review_status FROM relations WHERE series_id=? AND (relation_type LIKE ? OR payload_json LIKE ?) ORDER BY episode_id LIMIT ?',(sid,q,q,limit)):out.append({'type':'relation',**dict(r)})
        return out[:limit]
    finally:c.close()
def _parse_event_ref(ref):
    if ':' not in ref:raise ValueError('事件引用格式应为 EPISODE_ID:EVENT_ID')
    ep,eid=ref.split(':',1)
    if not ep or not eid:raise ValueError(f'无效事件引用: {ref}')
    return ep,eid

def add_cross_causal_link(db_path,sid,from_ref,to_ref,edge_type,reasoning,evidence_refs=None):
    allowed={"CAUSES","ENABLES","BLOCKS","MOTIVATES","REVEALS","FOLLOWS","POSSIBLE_CAUSE"}
    if edge_type not in allowed:raise ValueError(f"edge_type 必须是 {sorted(allowed)}")
    if not reasoning.strip():raise ValueError("跨集因果候选必须填写推理说明")
    fe,fi=_parse_event_ref(from_ref); te,ti=_parse_event_ref(to_ref)
    if fe==te:raise ValueError("跨集因果边必须连接不同集数；同集因果请写入该集 dossier.causal_edges")
    if (fe,fi)==(te,ti):raise ValueError("事件不能指向自身")
    refs=list(evidence_refs or [])
    if not refs:raise ValueError("至少提供一条可回看证据引用，格式 EP001:EVD001")
    c=connect(db_path)
    try:
        endpoint_events={}
        for ep,eid in ((fe,fi),(te,ti)):
            row=c.execute("SELECT evidence_ids_json FROM events WHERE series_id=? AND episode_id=? AND event_id=?",(sid,ep,eid)).fetchone()
            if not row:raise ValueError(f"事件不存在: {ep}:{eid}")
            endpoint_events[ep]=set(json.loads(row[0] or '[]'))
        ref_episodes=set()
        for ref in refs:
            if ':' not in ref:raise ValueError(f"证据引用格式错误: {ref}")
            ep,evid=ref.split(':',1); row=c.execute("SELECT payload_json FROM episode_payloads WHERE series_id=? AND episode_id=?",(sid,ep)).fetchone()
            if not row or not any(x.get('evidence_id')==evid for x in json.loads(row[0]).get('evidence',[])):raise ValueError(f"证据不存在: {ref}")
            if ep not in endpoint_events or evid not in endpoint_events[ep]:raise ValueError(f"证据必须关联到因果边端点事件: {ref}")
            ref_episodes.add(ep)
        if ref_episodes!={fe,te}:raise ValueError("跨集因果边必须同时提供起点集和终点集的事件证据")
        edge_id=uid('XCAU'); stamp=now()
        c.execute("INSERT INTO cross_episode_causal_edges VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(edge_id,sid,fe,fi,te,ti,edge_type,reasoning,_json(refs),'[]','UNREVIEWED','','',stamp,stamp))
        c.execute("INSERT INTO audit_log VALUES(?,?,?,?,?,?,?,?,?)",(uid('AUD'),sid,'ADD_CROSS_CAUSAL_CANDIDATE',edge_id,reasoning,'','{}',_json({'from':from_ref,'to':to_ref,'type':edge_type,'evidence_refs':refs}),stamp))
        c.commit();return edge_id
    finally:c.close()

def review_cross_causal_link(db_path,sid,edge_id,status,reason,reviewer=''):
    if status not in {'PASS','NEED_EVIDENCE','INCORRECT','DISPUTED'}:raise ValueError('审核状态无效')
    if not reason.strip():raise ValueError('审核理由不能为空')
    c=connect(db_path)
    try:
        c.execute('BEGIN IMMEDIATE'); row=c.execute("SELECT * FROM cross_episode_causal_edges WHERE series_id=? AND edge_id=?",(sid,edge_id)).fetchone()
        if not row:raise ValueError(f'找不到跨集因果边: {edge_id}')
        before=dict(row); c.execute("UPDATE cross_episode_causal_edges SET review_status=?,review_reason=?,reviewer=?,updated_at=? WHERE series_id=? AND edge_id=?",(status,reason,reviewer,now(),sid,edge_id))
        after=dict(c.execute("SELECT * FROM cross_episode_causal_edges WHERE series_id=? AND edge_id=?",(sid,edge_id)).fetchone())
        c.execute("INSERT INTO audit_log VALUES(?,?,?,?,?,?,?,?,?)",(uid('AUD'),sid,'REVIEW_CROSS_CAUSAL_EDGE',edge_id,reason,reviewer,_json(before),_json(after),now())); c.commit();return after
    except Exception:c.rollback();raise
    finally:c.close()

def status(db_path,sid):
    c=connect(db_path)
    try:
        s=c.execute('SELECT * FROM series WHERE series_id=?',(sid,)).fetchone()
        if not s:raise ValueError(f'未知 series_id: {sid}')
        eps=c.execute('SELECT * FROM episodes WHERE series_id=? ORDER BY episode_number,episode_id',(sid,)).fetchall()
        ic=c.execute("SELECT COUNT(*) FROM identity_candidates WHERE series_id=? AND status='NEEDS_REVIEW'",(sid,)).fetchone()[0]
        sc=c.execute("SELECT COUNT(*) FROM storyline_candidates WHERE series_id=? AND status='NEEDS_REVIEW'",(sid,)).fetchone()[0]
        tc=c.execute("SELECT COUNT(*) FROM thread_candidates WHERE series_id=? AND status='NEEDS_REVIEW'",(sid,)).fetchone()[0]
        xc=c.execute("SELECT COUNT(*) FROM cross_episode_causal_edges WHERE series_id=? AND review_status IN ('UNREVIEWED','NEED_EVIDENCE','DISPUTED')",(sid,)).fetchone()[0]
        kc=c.execute('SELECT COUNT(*) FROM character_knowledge WHERE series_id=?',(sid,)).fetchone()[0]
        return {'series_id':sid,'title':s['title'],'episode_count':len(eps),'episodes':[{'episode_id':e['episode_id'],'episode_number':e['episode_number'],'title':e['title'],'events':e['event_count'],'characters':e['character_count'],'imported_at':e['imported_at']} for e in eps],'identity_candidates_pending':ic,'storyline_candidates_pending':sc,'thread_candidates_pending':tc,'cross_episode_causal_pending':xc,'knowledge_states':kc}
    finally:c.close()
def pending_candidates(db_path,sid):
    c=connect(db_path)
    try:
        causal=[]
        for r in c.execute("SELECT * FROM cross_episode_causal_edges WHERE series_id=? AND review_status IN ('UNREVIEWED','NEED_EVIDENCE','DISPUTED') ORDER BY created_at",(sid,)):
            x=dict(r);x['evidence_refs']=json.loads(x.pop('evidence_refs_json'));x['counter_evidence_refs']=json.loads(x.pop('counter_evidence_refs_json'));causal.append(x)
        return {'identity_candidates':[dict(r) for r in c.execute("SELECT * FROM identity_candidates WHERE series_id=? AND status='NEEDS_REVIEW' ORDER BY match_key",(sid,))],'storyline_candidates':[dict(r) for r in c.execute("SELECT * FROM storyline_candidates WHERE series_id=? AND status='NEEDS_REVIEW' ORDER BY match_key",(sid,))],'thread_candidates':[dict(r) for r in c.execute("SELECT * FROM thread_candidates WHERE series_id=? AND status='NEEDS_REVIEW' ORDER BY match_key",(sid,))],'cross_episode_causal_edges':causal}
    finally:c.close()
def batch_import(db_path,sid,folder,pattern='**/*.json',replace=False):
    files=sorted(p for p in Path(folder).glob(pattern) if p.is_file()); results=[]
    for p in files:
        try:
            obj=load_dossier(p)
            if not isinstance(obj,dict) or not isinstance(obj.get('episode'),dict) or 'events' not in obj or 'characters' not in obj:continue
            results.append({'path':str(p),**import_dossier(db_path,sid,p,replace)})
        except Exception as exc:
            c=connect(db_path)
            try:_record_ingest(c,sid,p,file_hash(p) if p.exists() else '',None,'FAILED',str(exc)); c.commit()
            finally:c.close()
            results.append({'path':str(p),'status':'FAILED','error':str(exc)})
    return {'scanned':len(files),'processed':len(results),'imported':sum(x.get('status')=='IMPORTED' for x in results),'skipped':sum(x.get('status')=='SKIPPED_UNCHANGED' for x in results),'failed':sum(x.get('status')=='FAILED' for x in results),'results':results}

def main():
    p=argparse.ArgumentParser(description='Drama Dossier Series Memory Engine v3.7'); sub=p.add_subparsers(dest='cmd',required=True)
    x=sub.add_parser('init',help='初始化全剧 SQLite 数据库'); x.add_argument('db'); x.add_argument('--series-id',required=True); x.add_argument('--title',required=True)
    x=sub.add_parser('import',help='导入一集 dossier JSON'); x.add_argument('db'); x.add_argument('dossier'); x.add_argument('--series-id',required=True); x.add_argument('--replace',action='store_true')
    x=sub.add_parser('batch-import',help='批量导入文件夹；重复运行会跳过未变化文件'); x.add_argument('db'); x.add_argument('folder'); x.add_argument('--series-id',required=True); x.add_argument('--pattern',default='**/*.json'); x.add_argument('--replace',action='store_true')
    x=sub.add_parser('status',help='查看全剧入库进度'); x.add_argument('db'); x.add_argument('--series-id',required=True)
    x=sub.add_parser('candidates',help='查看待人工确认的跨集身份/剧情线候选'); x.add_argument('db'); x.add_argument('--series-id',required=True)
    x=sub.add_parser('link-character',help='人工确认两集人物是同一人'); x.add_argument('db'); x.add_argument('left'); x.add_argument('right'); x.add_argument('--series-id',required=True); x.add_argument('--reason',required=True); x.add_argument('--reviewer',default='')
    x=sub.add_parser('link-storyline',help='人工确认两集剧情线属于同一条全剧剧情线'); x.add_argument('db'); x.add_argument('left'); x.add_argument('right'); x.add_argument('--series-id',required=True); x.add_argument('--reason',required=True); x.add_argument('--reviewer',default='')
    x=sub.add_parser('link-thread',help='人工确认两集伏笔/线索属于同一条全剧线索'); x.add_argument('db'); x.add_argument('left'); x.add_argument('right'); x.add_argument('--series-id',required=True); x.add_argument('--reason',required=True); x.add_argument('--reviewer',default='')
    x=sub.add_parser('reject-candidate',help='将误匹配候选标记为驳回并写入审计'); x.add_argument('db'); x.add_argument('candidate_id'); x.add_argument('--kind',choices=['identity','storyline','thread'],required=True); x.add_argument('--series-id',required=True); x.add_argument('--reason',required=True); x.add_argument('--reviewer',default='')
    x=sub.add_parser('add-causal-link',help='添加跨集因果候选（默认未审核）'); x.add_argument('db'); x.add_argument('from_ref'); x.add_argument('to_ref'); x.add_argument('--series-id',required=True); x.add_argument('--type',dest='edge_type',required=True); x.add_argument('--reasoning',required=True); x.add_argument('--evidence-ref',action='append',default=[])
    x=sub.add_parser('review-causal-link',help='审核跨集因果候选'); x.add_argument('db'); x.add_argument('edge_id'); x.add_argument('--series-id',required=True); x.add_argument('--status',choices=['PASS','NEED_EVIDENCE','INCORRECT','DISPUTED'],required=True); x.add_argument('--reason',required=True); x.add_argument('--reviewer',default='')
    x=sub.add_parser('search',help='跨集检索人物、事件和剧情线'); x.add_argument('db'); x.add_argument('query'); x.add_argument('--series-id',required=True); x.add_argument('--limit',type=int,default=50)
    x=sub.add_parser('export-memory',help='导出全剧记忆 JSON 供 ChatGPT 分析'); x.add_argument('db'); x.add_argument('output'); x.add_argument('--series-id',required=True)
    a=p.parse_args()
    try:
        if a.cmd=='init':init_db(a.db,a.series_id,a.title); print(f'已初始化: {a.db} ({a.series_id})')
        elif a.cmd=='import':print(json.dumps(import_dossier(a.db,a.series_id,a.dossier,a.replace),ensure_ascii=False,indent=2))
        elif a.cmd=='batch-import':print(json.dumps(batch_import(a.db,a.series_id,a.folder,a.pattern,a.replace),ensure_ascii=False,indent=2))
        elif a.cmd=='status':print(json.dumps(status(a.db,a.series_id),ensure_ascii=False,indent=2))
        elif a.cmd=='candidates':print(json.dumps(pending_candidates(a.db,a.series_id),ensure_ascii=False,indent=2))
        elif a.cmd=='link-character':print('已关联人物身份: '+link_characters(a.db,a.series_id,a.left,a.right,a.reason,a.reviewer))
        elif a.cmd=='link-storyline':print('已关联剧情线: '+link_storylines(a.db,a.series_id,a.left,a.right,a.reason,a.reviewer))
        elif a.cmd=='link-thread':print('已关联全剧伏笔: '+link_threads(a.db,a.series_id,a.left,a.right,a.reason,a.reviewer))
        elif a.cmd=='reject-candidate':reject_candidate(a.db,a.series_id,a.candidate_id,a.kind,a.reason,a.reviewer); print('候选已驳回并写入审计')
        elif a.cmd=='add-causal-link':print('已创建待审核跨集因果候选: '+add_cross_causal_link(a.db,a.series_id,a.from_ref,a.to_ref,a.edge_type,a.reasoning,a.evidence_ref))
        elif a.cmd=='review-causal-link':print(json.dumps(review_cross_causal_link(a.db,a.series_id,a.edge_id,a.status,a.reason,a.reviewer),ensure_ascii=False,indent=2))
        elif a.cmd=='search':print(json.dumps(search(a.db,a.series_id,a.query,a.limit),ensure_ascii=False,indent=2))
        elif a.cmd=='export-memory':
            m=export_memory(a.db,a.series_id,a.output); print(f"已导出 {a.output}: {len(m['episodes'])} 集，{len(m['characters'])} 个全剧人物实体，{len(m['storylines'])} 条剧情线")
    except Exception as exc:print(f'错误: {exc}',file=sys.stderr); raise SystemExit(2)
if __name__=='__main__':main()

