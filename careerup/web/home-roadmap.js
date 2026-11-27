// Home summary uses saved roadmap data and server-confirmed completion results only.
let homeScoreRoadId=null,homeScoreValue=null,homeScoreTarget=null,homeScoreFrame=null,homeScorePending=null;
function resetHomeRoadmap(){
    if(homeScoreFrame!==null)cancelAnimationFrame(homeScoreFrame);
    homeScoreFrame=null;homeScoreRoadId=null;homeScoreValue=null;homeScoreTarget=null;homeScorePending=null;closeScoreSpotlight();
    document.getElementById('home-score-feedback').hidden=true;paintHomeScore(null);
}
function paintHomeScore(value){
    homeScoreValue=value;
    const valid=Number.isFinite(value),score=valid?Math.min(100,Math.max(0,value)):0;
    document.getElementById('spotlight-score-value').textContent=valid?score.toFixed(1):'—';
    document.getElementById('spotlight-score-ring').style.strokeDashoffset=String(320.442*(1-score/100));
    document.getElementById('home-score-value').textContent=valid?score.toFixed(1):'—';
    document.getElementById('home-score-ring').style.strokeDashoffset=String(320.442*(1-score/100));
    document.getElementById('home-score-gauge').setAttribute('aria-label',valid?`매칭점수 ${score.toFixed(1)}점, 100점 만점`:'매칭점수 계산 대기');
}
function updateHomeScore(score,animate){
    const numeric=score===null || score===undefined?null:Number(score),target=Number.isFinite(numeric)?Math.min(100,Math.max(0,numeric)):null;
    if(!animate && homeScoreTarget===target)return;
    if(homeScoreFrame!==null)cancelAnimationFrame(homeScoreFrame);homeScoreFrame=null;
    const from=homeScoreValue;homeScoreTarget=target;
    if(!animate || !Number.isFinite(from) || target===null || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches){paintHomeScore(target);return;}
    const started=performance.now(),duration=2600;
    const frame=now=>{const t=Math.min(1,Math.max(0,(now-started)/duration)),eased=1-Math.pow(1-t,3);paintHomeScore(from+(target-from)*eased);if(t<1)homeScoreFrame=requestAnimationFrame(frame);else homeScoreFrame=null;};
    homeScoreFrame=requestAnimationFrame(frame);
}
function renderHomeRoadmap(){
    const el=id=>document.getElementById(id),road=currentRoadmap,a=typeof certificateData==='undefined'?null:certificateData?.adventure;
    if(!el('home-roadmap-title'))return;
    el('home-roadmap-empty').hidden=!!road;el('home-roadmap-details').hidden=!road;
    el('home-roadmap-title').textContent=road?`${road.company || '기업명 미기재'} 목표 커리어 로드맵`:'나의 목표 커리어 로드맵';
    el('home-roadmap-job').textContent=road?.title || '';
    if(homeScoreRoadId!==road?.road_id){resetHomeRoadmap();homeScoreRoadId=road?.road_id ?? null;}
    const report=typeof certificateReport!=='undefined' && certificateReport?.road_id===road?.road_id?certificateReport:null;
    const key=a?String(a.run_id || a.road_id)+':'+String(a.test_mode):null;
    const animate=!!road && !!report && homeScorePending===key;
    const scoreBefore=homeScoreValue;
    if(animate && Number.isFinite(scoreBefore) && Number.isFinite(report.score_100))openScoreSpotlight(scoreBefore,report.score_100,road);
    updateHomeScore(report?report.score_100:road?.score_100,animate);
    if(animate){
        homeScorePending=null;
        const before=scoreBefore,after=report.score_100,feedback=el('home-score-feedback');feedback.replaceChildren();feedback.hidden=false;
        const text=document.createElement('span');text.textContent='스펙 반영 후 목표 공고와 다시 매칭했어요. ';feedback.append(text);
        const delta=document.createElement('strong');
        delta.textContent=Number.isFinite(before) && Number.isFinite(after)?`${before.toFixed(1)} → ${after.toFixed(1)}점 (${after-before>=0?'+':''}${(after-before).toFixed(1)})`:'새 매칭점수를 확인해주세요.';feedback.append(delta);
    }else if(homeScorePending && certificateData?.report_pending){el('home-score-feedback').hidden=false;el('home-score-feedback').textContent='스펙 반영 완료 · 매칭점수 재계산 대기 중';}
    el('home-roadmap-reason').textContent=road?.reason || '';
    for(const [id,content,empty] of [['home-strengths',road?.strong,'로드맵에서 강점을 확인해보세요.'],['home-gaps',road?.weak,'현재 확인된 보완점이 없어요.']]){
        const list=el(id);list.replaceChildren();for(const text of ((content || '').split(',').map(x=>x.trim()).filter(Boolean).slice(0,3).length?(content || '').split(',').map(x=>x.trim()).filter(Boolean).slice(0,3):[empty])){const item=document.createElement('li');item.textContent=text;list.append(item);}
    }
    // The right-hand goals keep their existing selected curriculum, even if a new target is created.
    const selected=!!a,complete=!!a?.state?.final_at,progress=a?.progress;
    el('home-curriculum-kind').textContent=selected?(['project','contest'].includes(a.kind)?'프로젝트 · SELF CURRICULUM':'자격증 · SELF CURRICULUM'):'SELF CURRICULUM';
    el('home-curriculum-title').textContent=selected?(a.certificate_name || a.name):'셀프 커리큘럼을 선택해보세요.';
    el('home-curriculum-goal').textContent=selected?(a.final_goal || (a.phase===1?'1차 시험 합격':'자격증 취득')):'커리어 로드맵에서 나에게 맞는 자격증이나 프로젝트를 선택할 수 있어요.';
    const days=selected?certDateDiff(a.result_date,a.today):null;
    el('home-goal-due').textContent=selected?`${a.result_date} · ${complete?'최종목표 달성':days===0?'D-DAY':days>0?'D-'+days:'D+'+(-days)}`:'';
    const button=el('home-final-complete');button.hidden=!selected || complete;button.disabled=!selected || certBusy || !!certPendingTestClock || a.today<a.result_date || !a.mid_goals.every(g=>!!a.state[g.id+'_at']);
    button.textContent=a?.kind==='project'?'프로젝트 완성 확인':'최종 합격 확인';
    const percent=selected && Number.isFinite(progress?.percent)?Math.min(100,Math.max(0,progress.percent)):complete?100:0;
    el('home-progress-title').textContent=selected?(a.certificate_name || a.name):'선택한 커리큘럼의 목표 달성 현황을 보여드려요.';
    el('home-progress-value').textContent=`${Math.round(percent)}%`;el('home-progress-fill').style.width=percent+'%';el('home-progress').setAttribute('aria-valuenow',String(Math.round(percent)));
    el('home-progress-note').textContent=!selected?'셀프 커리큘럼 선택 후 시작됩니다.':!progress?'성취도 정보를 불러오고 있습니다.':complete?(['project','contest'].includes(a.kind) || a.qualification_awarded?'최종목표 달성 완료 · 마이페이지 스펙 반영 완료':'시험 합격 기록 완료'):progress.policy==='mid30_daily_fraction_v2'?`중기목표 ${progress.mid_completed}/${progress.mid_total}개 · ${progress.mid_points}% + 일일목표 ${Number(progress.daily_points).toFixed(1)}% / ${progress.daily_budget}%${a.test_mode?' · 날짜 테스트 중':''}`:`일일목표 완료 ${progress.completed_days}일 / 목표 ${progress.target_days}일`;
}

let scoreSpotlightTimer=null;
function closeScoreSpotlight(){clearTimeout(scoreSpotlightTimer);scoreSpotlightTimer=null;const dialog=document.getElementById('score-spotlight');if(dialog?.open)dialog.close();}
function openScoreSpotlight(before,after,road){
    closeScoreSpotlight();
    for(const id of ['cert-password-dialog','cert-calendar']){const dialog=document.getElementById(id);if(dialog?.open)dialog.close();}
    document.getElementById('spotlight-company').textContent=road.company || '현재 목표 공고';
    document.getElementById('spotlight-before').textContent=before.toFixed(1)+'점';
    document.getElementById('spotlight-after').textContent=after.toFixed(1)+'점';
    const delta=after-before;
    document.getElementById('spotlight-delta').textContent=delta===0?'점수는 동일해요. 새로운 스펙이 반영되었습니다.':`실제 재계산 결과 ${delta>0?'+':''}${delta.toFixed(1)}점`;
    document.getElementById('score-spotlight').showModal();
    scoreSpotlightTimer=setTimeout(closeScoreSpotlight,window.matchMedia?.('(prefers-reduced-motion: reduce)').matches?1000:3000);
}
document.addEventListener('DOMContentLoaded',()=>document.getElementById('spotlight-close').addEventListener('click',closeScoreSpotlight));
