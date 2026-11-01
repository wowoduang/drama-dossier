/* Browser/Node equivalent of continuity_review.py; never erase legacy history. */
(function(root){
'use strict';
const copy=x=>JSON.parse(JSON.stringify(x));
const statuses=['UNREVIEWED','PASS','INCORRECT','NEED_EVIDENCE','DISPUTED'];
const audit=['review_status','review_history','review_reason','reviewer','reviewed_at'];
function normalize(payload){
 const out=copy(payload);
 if(!Array.isArray(out.candidates))throw Error('candidates 必须是数组');
 const ids=out.candidates.map(c=>c.candidate_id);
 if(ids.some(x=>typeof x!=='string'||!x)||new Set(ids).size!==ids.length)throw Error('candidate_id 必须唯一且非空');
 if(!Object.hasOwn(out,'shots'))out.shots=[];if(!Array.isArray(out.shots))throw Error('shots 必须是数组');
 const sids=out.shots.map(s=>s.shot_id);
 if(new Set(sids).size!==sids.length||out.shots.some(s=>!s.shot_id||!Number.isFinite(s.start_sec)||!Number.isFinite(s.end_sec)||s.start_sec<0||s.end_sec<=s.start_sec))throw Error('镜头 ID/时间范围无效或重复');
 for(const c of out.candidates){if(!Object.hasOwn(c,'review_status'))c.review_status='UNREVIEWED';if(!Object.hasOwn(c,'review_history'))c.review_history=[];if(!statuses.includes(c.review_status)||!Array.isArray(c.review_history))throw Error('审核状态/历史无效');}
 out.version='4.2.0';out.candidate_count=ids.length;out.all_candidates_unreviewed=out.candidates.every(c=>c.review_status==='UNREVIEWED');return out;
}
function snapshot(payload,c){return copy({episode_id:payload.episode_id??null,candidate:Object.fromEntries(Object.entries(c).filter(([k])=>!audit.includes(k))),shots:(payload.shots||[]).filter(s=>(c.shot_ids||[]).includes(s.shot_id)).map(copy)});}
function review(payload,id,status,reason,reviewer,checked=false){
 if(!statuses.includes(status)||!reason.trim()||!reviewer.trim())throw Error('需要有效状态、审核理由和审核者');
 if(status==='PASS'&&!checked)throw Error('PASS 必须回看连续原片并确认全部风险');
 const out=normalize(payload),c=out.candidates.find(c=>c.candidate_id===id);if(!c)throw Error('候选不存在');
 const entry={previous_status:c.review_status,status,reason:reason.trim(),reviewer:reviewer.trim(),reviewed_at:new Date().toISOString(),continuous_source_checked:!!checked,snapshot:snapshot(out,c)};
 c.review_history.push(entry);Object.assign(c,{review_status:status,review_reason:entry.reason,reviewer:entry.reviewer,reviewed_at:entry.reviewed_at});
 out.all_candidates_unreviewed=out.candidates.every(c=>c.review_status==='UNREVIEWED');return out;
}
const api={normalize,snapshot,review};if(typeof module!=='undefined')module.exports=api;else root.ContinuityReview=api;
})(globalThis);
