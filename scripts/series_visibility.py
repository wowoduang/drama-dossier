#!/usr/bin/env python3
"""Human-audited episode at which a cross-episode identity/link becomes known."""
from __future__ import annotations
import argparse,json,sqlite3,sys,uuid
from datetime import datetime,timezone
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from series_memory import connect

def now():return datetime.now(timezone.utc).isoformat(timespec="seconds")
def ensure_table(c):
    c.execute("CREATE TABLE IF NOT EXISTS series_visibility(series_id TEXT NOT NULL,entity_type TEXT NOT NULL,entity_id TEXT NOT NULL,effective_episode_id TEXT NOT NULL,effective_episode_number INTEGER NOT NULL,evidence_refs_json TEXT NOT NULL,reason TEXT NOT NULL,reviewer TEXT NOT NULL DEFAULT '',review_status TEXT NOT NULL DEFAULT 'PASS',created_at TEXT NOT NULL,PRIMARY KEY(series_id,entity_type,entity_id),FOREIGN KEY(series_id) REFERENCES series(series_id))")
    c.execute("CREATE TABLE IF NOT EXISTS visibility_audit(audit_id TEXT PRIMARY KEY,series_id TEXT NOT NULL,entity_type TEXT NOT NULL,entity_id TEXT NOT NULL,action TEXT NOT NULL,before_json TEXT NOT NULL,after_json TEXT NOT NULL,reason TEXT NOT NULL,reviewer TEXT NOT NULL,created_at TEXT NOT NULL)")

def declare_visible(db,sid,entity_type,entity_id,episode_id,evidence_refs,reason,reviewer="",correct=False):
    if entity_type not in {"character","storyline","thread","causal_edge"}:raise ValueError("entity_type 必须为 character/storyline/thread/causal_edge")
    if not reason.strip():raise ValueError("必须填写何以在该集已可知的理由")
    refs=list(dict.fromkeys(evidence_refs or []))
    if not refs:raise ValueError("至少提供一条截止集或更早的证据引用")
    c=connect(db)
    try:
        ep=c.execute("SELECT episode_number FROM episodes WHERE series_id=? AND episode_id=?",(sid,episode_id)).fetchone()
        if not ep:raise ValueError(f"集数不存在: {episode_id}")
        entity_tables={'character':('global_characters','global_character_id'),'storyline':('global_storylines','global_storyline_id'),'thread':('global_threads','global_thread_id'),'causal_edge':('cross_episode_causal_edges','edge_id')}
        table,keycol=entity_tables[entity_type]
        if not c.execute(f"SELECT 1 FROM {table} WHERE series_id=? AND {keycol}=?",(sid,entity_id)).fetchone():
            raise ValueError(f"全剧实体不存在: {entity_type} {entity_id}")
        for ref in refs:
            if ":" not in ref:raise ValueError(f"证据引用格式错误: {ref}")
            e,evid=ref.split(":",1)
            row=c.execute("SELECT payload_json FROM episode_payloads WHERE series_id=? AND episode_id=?",(sid,e)).fetchone()
            evidence_item=next((x for x in json.loads(row[0]).get("evidence",[]) if x.get("evidence_id")==evid),None) if row else None
            if not evidence_item:
                raise ValueError(f"证据不存在: {ref}")
            if evidence_item.get("review_status")!="PASS":
                raise ValueError(f"可见性声明只能使用已审核 PASS 的证据: {ref}")
            n=c.execute("SELECT episode_number FROM episodes WHERE series_id=? AND episode_id=?",(sid,e)).fetchone()[0]
            if n>ep[0]:raise ValueError(f"可见性证据不能晚于声明集数: {ref}")
        # Require evidence to be tied to the declared entity, not merely any
        # arbitrary evidence item from an earlier episode.
        relevant=False
        for ref in refs:
            evid_ep,evid=ref.split(':',1)
            if entity_type=='character':
                relevant |= bool(c.execute("SELECT 1 FROM character_mentions WHERE series_id=? AND episode_id=? AND global_character_id=? AND (evidence_ids_json LIKE ? OR EXISTS (SELECT 1 FROM events e WHERE e.series_id=character_mentions.series_id AND e.episode_id=character_mentions.episode_id AND e.evidence_ids_json LIKE ? AND (e.actor_global_id=? OR e.patient_global_id=? OR e.recipient_global_id=? OR e.target_global_id=?)))",(sid,evid_ep,entity_id,f'%"{evid}"%',f'%"{evid}"%',entity_id,entity_id,entity_id,entity_id)).fetchone())
            elif entity_type=='storyline':
                row=c.execute("SELECT event_ids_json FROM storyline_mentions WHERE series_id=? AND episode_id=? AND global_storyline_id=?",(sid,evid_ep,entity_id)).fetchone()
                if row:
                    for event_id in json.loads(row[0] or '[]'):
                        ev=c.execute("SELECT evidence_ids_json FROM events WHERE series_id=? AND episode_id=? AND event_id=?",(sid,evid_ep,event_id)).fetchone()
                        if ev and evid in json.loads(ev[0] or '[]'): relevant=True; break
            elif entity_type=='thread':
                row=c.execute("SELECT evidence_ids_json,payload_json FROM thread_mentions WHERE series_id=? AND episode_id=? AND global_thread_id=?",(sid,evid_ep,entity_id)).fetchone()
                if row:
                    refs_here=set(json.loads(row[0] or '[]'))|set(json.loads(row[1] or '{}').get('resolution_evidence_ids',[]) or [])
                    relevant |= evid in refs_here
            elif entity_type=='causal_edge':
                row=c.execute("SELECT evidence_refs_json FROM cross_episode_causal_edges WHERE series_id=? AND edge_id=?",(sid,entity_id)).fetchone()
                relevant |= bool(row and ref in json.loads(row[0] or '[]'))
        if not relevant:raise ValueError("可见性证据必须与所声明的人物/剧情线/伏笔/因果边直接关联")
        ensure_table(c)
        before=c.execute("SELECT * FROM series_visibility WHERE series_id=? AND entity_type=? AND entity_id=?",(sid,entity_type,entity_id)).fetchone()
        requested={"effective_episode_id":episode_id,"effective_episode_number":int(ep[0]),"evidence_refs":refs}
        action="DECLARE_VISIBLE"
        if before and before["review_status"]=="PASS":
            previous=dict(before)
            previous_number=int(before["effective_episode_number"])
            requested_number=int(ep[0])
            if requested_number>previous_number:
                # Visibility is monotonic: later supporting evidence must not
                # revoke an already established earlier audience-knowledge point.
                action="DECLARE_VISIBLE_LATER_IGNORED"
                after={"requested":requested,"effective_retained":{"effective_episode_id":before["effective_episode_id"],"effective_episode_number":previous_number,"evidence_refs":json.loads(before["evidence_refs_json"])}}
                aid="VISAUD_"+uuid.uuid4().hex[:12]
                c.execute("INSERT INTO visibility_audit VALUES(?,?,?,?,?,?,?,?,?,?)",(aid,sid,entity_type,entity_id,action,json.dumps(previous,ensure_ascii=False),json.dumps(after,ensure_ascii=False),reason,reviewer,now()))
                c.commit()
                return {"entity_type":entity_type,"entity_id":entity_id,"effective_episode_id":before["effective_episode_id"],"effective_episode_number":previous_number,"evidence_refs":json.loads(before["evidence_refs_json"]),"review_status":before["review_status"],"declaration_recorded":True,"effective_retained":True}
            if requested_number==previous_number:
                action="DECLARE_VISIBLE_CONFIRMATION"
                after={"requested":requested,"effective_retained":{"effective_episode_id":before["effective_episode_id"],"effective_episode_number":previous_number,"evidence_refs":json.loads(before["evidence_refs_json"])}}
                aid="VISAUD_"+uuid.uuid4().hex[:12]
                c.execute("INSERT INTO visibility_audit VALUES(?,?,?,?,?,?,?,?,?,?)",(aid,sid,entity_type,entity_id,action,json.dumps(previous,ensure_ascii=False),json.dumps(after,ensure_ascii=False),reason,reviewer,now()))
                c.commit()
                return {"entity_type":entity_type,"entity_id":entity_id,"effective_episode_id":before["effective_episode_id"],"effective_episode_number":previous_number,"evidence_refs":json.loads(before["evidence_refs_json"]),"review_status":before["review_status"],"declaration_recorded":True,"effective_retained":True}
            if not correct:
                raise ValueError("新声明将把已知集数提前；如确属纠正，必须显式使用 --correct 并记录审计")
            action="CORRECT_VISIBLE_EARLIER"
        elif before:
            # Episode replacement or evidence invalidation reopens the declaration.
            # A fresh valid declaration at the same/later point may restore it.
            previous_number=int(before["effective_episode_number"])
            if int(ep[0])<previous_number and not correct:
                raise ValueError("新声明将把已知集数提前；如确属纠正，必须显式使用 --correct 并记录审计")
            action="REDECLARE_AFTER_INVALIDATION" if int(ep[0])>=previous_number else "CORRECT_VISIBLE_EARLIER"
        row=(sid,entity_type,entity_id,episode_id,int(ep[0]),json.dumps(refs,ensure_ascii=False),reason,reviewer,"PASS",now())
        c.execute("INSERT OR REPLACE INTO series_visibility VALUES(?,?,?,?,?,?,?,?,?,?)",row)
        aid="VISAUD_"+uuid.uuid4().hex[:12]
        after={"effective_episode_id":episode_id,"effective_episode_number":int(ep[0]),"evidence_refs":refs}
        c.execute("INSERT INTO visibility_audit VALUES(?,?,?,?,?,?,?,?,?,?)",(aid,sid,entity_type,entity_id,action,json.dumps(dict(before) if before else {},ensure_ascii=False),json.dumps(after,ensure_ascii=False),reason,reviewer,now()))
        c.commit();return {"entity_type":entity_type,"entity_id":entity_id,"effective_episode_id":episode_id,"effective_episode_number":int(ep[0]),"evidence_refs":refs,"review_status":"PASS","declaration_recorded":True,"effective_retained":False if before else True}
    finally:c.close()

def main():
    p=argparse.ArgumentParser(description="声明跨集身份/关系/伏笔/因果在何集已对观众可知")
    p.add_argument("db");p.add_argument("--series-id",required=True);p.add_argument("--entity-type",required=True,choices=["character","storyline","thread","causal_edge"]);p.add_argument("--entity-id",required=True);p.add_argument("--known-by-episode",required=True);p.add_argument("--evidence-ref",action="append",required=True);p.add_argument("--reason",required=True);p.add_argument("--reviewer",default="");p.add_argument("--correct",action="store_true",help="显式确认将可见性生效集数提前，并记录审计")
    a=p.parse_args()
    try:print(json.dumps(declare_visible(a.db,a.series_id,a.entity_type,a.entity_id,a.known_by_episode,a.evidence_ref,a.reason,a.reviewer,a.correct),ensure_ascii=False,indent=2))
    except Exception as e:print(f"错误: {e}",file=sys.stderr);raise SystemExit(2)
if __name__=="__main__":main()
