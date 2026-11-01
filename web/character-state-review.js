/* Equivalent dossier state-review snapshots for browser and Node contract tests. */
(function(root){
'use strict';
const copy=x=>JSON.parse(JSON.stringify(x));
const fields={character_states:'state_id',character_state_changes:'change_id'};
function snapshot(data,obj,collection){
 const states=new Map((data.character_states||[]).map(s=>[s.state_id,s]));
 const related=collection==='character_state_changes'?[obj.from_state_id,obj.to_state_id].filter(id=>states.has(id)).map(id=>states.get(id)):[];
 const objects=[obj,...related],collect=field=>new Set(objects.flatMap(o=>o[field]||[]));
 const evids=collect('evidence_ids'),eventids=collect('event_ids'),kids=collect('knowledge_ids');
 const events=(data.events||[]).filter(x=>eventids.has(x.event_id)),knowledge=(data.knowledge_states||[]).filter(x=>kids.has(x.knowledge_id));
 for(const item of [...events,...knowledge])for(const id of item.evidence_ids||[])evids.add(id);
 const charids=new Set(objects.flatMap(o=>[o.character_id,o.target_character_id]));
 return copy({episode:data.episode??null,object:obj,states:related,characters:(data.characters||[]).filter(x=>charids.has(x.character_id)),events,knowledge_states:knowledge,evidence:(data.evidence||[]).filter(x=>evids.has(x.evidence_id))});
}
function review(data,collection,id,status,reason,reviewer,sourceChecked){
 if(!fields[collection]||!['PASS','INCORRECT','NEED_EVIDENCE','DISPUTED'].includes(status))throw Error('审核对象或状态无效');
 if(!reason.trim()||!reviewer.trim())throw Error('需要审核理由和审核者');
 if(status==='PASS'&&!sourceChecked)throw Error('PASS 必须确认已核对连续原片');
 const out=copy(data),obj=(out[collection]||[]).find(o=>o[fields[collection]]===id);if(!obj)throw Error('对象不存在');
 if(!(obj.evidence_ids||[]).length)throw Error('请先补充证据 ID，再进行审核');
 const old=copy(obj);obj.review_status=status;out.review_history=out.review_history||[];
 out.review_history.push({review_id:'REV_'+(globalThis.crypto?.randomUUID?.()||String(Date.now())+Math.random()).replaceAll('-',''),object_id:id,collection,status,old_value:old,new_value:copy(obj),reason:reason.trim(),reviewer:reviewer.trim(),evidence_ids:obj.evidence_ids||[],timestamp:new Date().toISOString(),version:out.review_history.filter(r=>r.object_id===id).length+1,context_snapshot:snapshot(out,obj,collection),source_checked:!!sourceChecked});return out;
}
const api={snapshot,review};if(typeof module!=='undefined')module.exports=api;else root.CharacterStateReview=api;
})(globalThis);
