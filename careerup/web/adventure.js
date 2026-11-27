// Server-authoritative certificate adventure. Test progress is isolated from the real state.
let certPendingTestClock=null;
let certCalendarMonth=null,completionCelebrationTimer=null;
let certificateData=null,certificateReport=null,certEpoch=0,certRequest=0,certAbort=null,certBusy=false,certBossStage=null;
function certEl(id){return document.getElementById(id);}
function resetCertificateAdventure(){
    if(typeof resetAdventureStart==='function')resetAdventureStart();
    closeCurriculumCompletion();certPendingTestClock=null;
    if(typeof resetLicenseCurriculum==='function')resetLicenseCurriculum();
    if(typeof resetProjectCurriculum==='function')resetProjectCurriculum();
    resetHomeRoadmap();certEpoch++;certRequest++;certAbort?.abort();certAbort=null;certBusy=false;certificateData=null;certificateReport=null;certCalendarMonth=null;
    certEl('cert-password').value='';certEl('cert-password-error').textContent='';certEl('cert-drawer-status').textContent='';certEl('cert-add-text').value='';certEl('cert-status').textContent='';certEl('cert-calendar-toggle').hidden=true;for(const id of ['cert-calendar','cert-password-dialog']){const dialog=certEl(id);if(dialog?.open)dialog.close();}
    renderCertificateAdventure();
}
async function loadCertificateAdventure(){
    if(certBusy)return;certAbort?.abort();const request=++certRequest,epoch=certEpoch,controller=new AbortController();certAbort=controller;
    try{
        const token=await matchingToken();if(epoch!==certEpoch || request!==certRequest)return;
        const data=await matchingResponse(await fetch('/api/adventure',{signal:controller.signal,headers:{Authorization:'Bearer '+token}}));
        if(epoch!==certEpoch || request!==certRequest)return;
        receiveCertificate(data);
    }catch(error){if(epoch===certEpoch && request===certRequest && error.name!=='AbortError')certEl('cert-status').textContent=error.message || '목표를 불러오지 못했습니다.';}
    finally{if(epoch===certEpoch && request===certRequest)certAbort=null;}
}
async function certificateAction(action,args={}){
    if(action==='start')return await beginAdventure({kind:'certificate',lc_id:Number(args.lc_id)});
    if(certBusy)return null;
    if(certPendingTestClock && !['test_on','test_off','test_reset'].includes(action)){certEl('cert-status').textContent='먼저 테스트 날짜를 다시 적용해주세요.';return null;}
    const testClock=action==='start' && certificateData?.adventure?.test_mode?{date:certificateData.adventure.today,time:certificateData.adventure.clock_time || '12:00'}:null;
    const finalWasComplete=!!certificateData?.adventure?.state.final_at;certBusy=true;certRequest++;certAbort?.abort();const epoch=certEpoch,controller=new AbortController();certAbort=controller;renderCertificateAdventure();
    certEl('cert-status').textContent=action==='boss' && args.stage==='final'?'완료 내용을 스펙에 반영하고 목표 공고의 매칭점수를 다시 계산하고 있습니다…':'저장하고 있습니다…';certEl('cert-password-error').textContent='';certEl('cert-drawer-status').textContent='';
    try{
        const token=await matchingToken();if(epoch!==certEpoch)return null;
        let data=await matchingResponse(await fetch('/api/adventure',{method:'POST',signal:controller.signal,headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify({action,args})}));
        if(epoch!==certEpoch)return null;
        if(testClock && (!data.adventure?.test_mode || data.adventure.today!==testClock.date || data.adventure.clock_time!==testClock.time)){
            certPendingTestClock=testClock;
            try{
                data=await matchingResponse(await fetch('/api/adventure',{method:'POST',signal:controller.signal,headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify({action:'test_on',args:testClock})}));
                if(epoch!==certEpoch)return null;
                certPendingTestClock=null;
            }catch(error){
                if(epoch!==certEpoch || error.name==='AbortError')return null;
                receiveCertificate(data);
                certEl('cert-status').textContent='회차는 선택됐지만 테스트 날짜 적용에 실패했습니다. 다시 적용한 뒤 진행해주세요.';
                const retry=document.createElement('button');retry.type='button';retry.className='cert-clock-retry';retry.textContent='테스트 날짜 다시 적용';retry.addEventListener('click',()=>void certificateAction('test_on',certPendingTestClock || testClock));certEl('cert-status').append(' ',retry);return null;
            }
        }
        if(['test_on','test_off','test_reset'].includes(action))certPendingTestClock=null;
        const newlyCompleted=action==='boss' && args.stage==='final' && !finalWasComplete && !!data.adventure?.state?.final_at;
        receiveCertificate(data,{animateScore:newlyCompleted,completionConfirmed:newlyCompleted});
        if(action==='boss'){
            const a=data.adventure,name=a.certificate_name || a.name;
            const text=args.stage==='final'?(['project','contest'].includes(a.kind)?'완료한 프로젝트 내용을 마이페이지에 반영했습니다.':a.qualification_awarded?name+'을 보유 자격증에 반영했습니다.':'시험 합격을 기록했습니다.'):'중기목표가 완료되었습니다.';
            showToast(text);
        }
        return data;
    }catch(error){if(epoch===certEpoch && error.name!=='AbortError'){const message=error.message || '목표 저장에 실패했습니다.';certEl('cert-status').textContent=message;certEl('cert-password-error').textContent=message;certEl('cert-drawer-status').textContent=message;}return null;}
    finally{if(epoch===certEpoch){certBusy=false;certAbort=null;renderCertificateAdventure();}}
}
function syncCompletedAdventureSpec(a,projectEntry){
    if(!a?.state?.final_at)return;
    if(['project','contest'].includes(a.kind)){
        const input=certEl('input-project-experience');
        if(!input || !projectEntry)return;
        const entries=input.value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
        if(!entries.includes(projectEntry.trim()))input.value=[input.value.trim(),projectEntry.trim()].filter(Boolean).join('\n');
    }else if(a.qualification_awarded){
        const input=certEl('input-certificates'),entry=a.credential_name || a.certificate_name || a.name;
        const values=input.value.split(',').map(x=>x.trim()).filter(Boolean);
        if(!values.includes(entry))input.value=[...values,entry].join(', ');
    }
}
function receiveCertificate(data,{animateScore=false,completionConfirmed=false}={}){
    if(animateScore)homeScorePending=String(data.adventure?.run_id || data.adventure?.road_id)+':'+String(data.adventure?.test_mode);
    certificateData=data;
    const storedEntry=typeof data.project_experience==='string' && data.project_experience.split(/\r?\n/).some(line=>line.trim()===data.project_experience_entry?.trim())?data.project_experience_entry:null;
    // Reads/polls must never resurrect an intentionally deleted or unsaved input.
    if(completionConfirmed)syncCompletedAdventureSpec(data.adventure,storedEntry);if(data.adventure)certCalendarMonth=data.adventure.today.slice(0,7);certEl('cert-status').textContent='';certEl('cert-calendar-toggle').hidden=false;
    if(data.report_pending)certificateReport=null;
    if(data.match_report){certificateReport=data.match_report;if(currentRoadmap?.road_id===certificateReport.road_id)renderCurrentRoadmap(currentRoadmap);}
    renderCertificateAdventure();
    if(completionConfirmed && (['project','contest'].includes(data.adventure?.kind)?!!storedEntry:!!data.adventure?.qualification_awarded))queueCurriculumCompletion(data.adventure);
}
function certDateDiff(end,start){return Math.round((Date.parse(end+'T00:00:00Z')-Date.parse(start+'T00:00:00Z'))/86400000);}
function certGoals(a){const main=a.state.main_goal;return [...(main?[{...main,fixed:true,main:true}]:[]),...(a.state.custom || []).map(g=>({...g,main:false}))];}
function renderCertificateAdventure(){
    const a=certificateData?.adventure;
    renderHomeRoadmap();certEl('cert-test-label').hidden=!a?.test_mode;
    certEl('cert-todos').replaceChildren();certEl('cert-midgoals').replaceChildren();
    certEl('cert-add-text').disabled=!a || certBusy || !!certPendingTestClock;certEl('cert-add-form').querySelector('button').disabled=!a || certBusy || !!certPendingTestClock;
    certEl('cert-calendar').querySelectorAll('[data-cert-write],input').forEach(el=>el.disabled=!a || certBusy);
    if(!a){
        const license=certificateData?.license;
        for(const [id,key] of [['cert-written-date','written_date'],['cert-practical-date','practical_date'],['cert-result-date','result_date']])certEl(id).value=license?.[key] || '';
        certEl('cert-test-date').value='';certEl('cert-real-today').textContent='커리큘럼을 시작하면 일정을 수정하고 날짜를 테스트할 수 있습니다.';certEl('cert-date-presets').replaceChildren();certEl('cert-report').replaceChildren();certEl('cert-report').hidden=true;
        certEl('adventure-quest-count').textContent='0 / 0';certEl('cert-day').textContent='커리큘럼을 선택하면 오늘의 목표가 표시됩니다.';certEl('cert-final-date').textContent='';renderCertificateCurriculum();return;}
    certEl('cert-written-date').closest('label').firstChild.textContent=['project','contest'].includes(a.kind)?'중기목표 1일':(a.mid_goals.find(g=>g.id==='written')?.title || '시험 완료').replace('완료','일');
    certEl('cert-practical-date').closest('label').firstChild.textContent=['project','contest'].includes(a.kind)?'중기목표 2일':'실기시험일';
    certEl('cert-result-date').closest('label').firstChild.textContent=['project','contest'].includes(a.kind)?(a.kind==='contest'?'공모전 최종목표일':'프로젝트 최종목표일'):'최종 합격발표일';
    certEl('cert-third-label').hidden=!a.mid_goals.some(g=>g.id==='third');certEl('cert-third-date').required=!!a.third_date;
    certEl('cert-practical-date').closest('label').hidden=!a.mid_goals.some(g=>g.id==='practical');certEl('cert-practical-date').required=!!a.practical_date;
    certEl('cert-midgoals-label').textContent=['project','contest'].includes(a.kind)?(a.kind==='contest'?'공모전 단계':'프로젝트 단계'):'시험 단계';
    certEl('cert-dates-form').querySelector('h3').textContent=['project','contest'].includes(a.kind)?(a.kind==='contest'?'내 공모전 일정':'내 프로젝트 일정'):'내 시험 일정';certEl('cert-dates-form').querySelector('button').textContent='일정 저장';
    const s=a.state;
    certEl('cert-test-label').textContent=`날짜 테스트 · ${a.today}`;
    certEl('cert-day').textContent=`${a.quest_day || a.today} · 오전 6시 초기화${a.test_mode?' · 테스트 기록':''}`;
    const goals=certGoals(a),done=goals.filter(g=>s.done[g.id]).length;certEl('adventure-quest-count').textContent=`${done} / ${goals.length}`;
    for(const goal of goals){
        const row=document.createElement('div');row.dataset.goalId=goal.id;row.className='cert-todo'+(goal.main?' main':'')+(s.done[goal.id]?' done':'');
        const check=document.createElement('input');check.type='checkbox';check.checked=!!s.done[goal.id];check.disabled=certBusy || !!certPendingTestClock || !!(goal.milestone && s[goal.id+'_at']);check.setAttribute('aria-label',goal.text+' 완료');check.addEventListener('change',()=>{if(goal.milestone){check.checked=!!s.done[goal.id];openCertificateBoss(goal.milestone);}else void certificateAction('toggle',{id:goal.id});});row.append(check);
        if(goal.fixed){const text=document.createElement('span');text.className='cert-todo-text';text.textContent=goal.text;const fixed=document.createElement('small');fixed.textContent='메인 · 고정';row.append(text,fixed);}
        else{const input=document.createElement('input');input.className='cert-todo-text';input.value=goal.text;input.maxLength=200;input.disabled=certBusy || !!certPendingTestClock;input.setAttribute('aria-label','사용자 일일목표 수정');input.addEventListener('change',()=>void certificateAction('edit',{id:goal.id,text:input.value}));const remove=document.createElement('button');remove.type='button';remove.textContent='×';remove.disabled=certBusy || !!certPendingTestClock;remove.setAttribute('aria-label','사용자 일일목표 삭제');remove.addEventListener('click',()=>void certificateAction('delete',{id:goal.id}));row.append(input,remove);}
        certEl('cert-todos').append(row);
    }
    for(const goal of a.mid_goals){
        const date=goal.id==='written'?a.written_date:goal.id==='third'?a.third_date:a.practical_date,completed=!!s[goal.id+'_at'],available=a.today>=date && a.mid_goals.slice(0,a.mid_goals.indexOf(goal)).every(g=>!!s[g.id+'_at']);
        const button=document.createElement('button');button.type='button';button.className='cert-mid'+(completed?' completed':'');button.disabled=certBusy || !!certPendingTestClock || completed || !available;
        const title=document.createElement('strong');title.textContent=`${completed?'✓ ':available?'':'🔒 '}${goal.title}`;
        const note=document.createElement('small');note.textContent=`${date} · ${completed?'달성 완료':available?'클릭하여 목표 달성 확인':'설정한 날짜부터 완료할 수 있습니다'}`;button.append(title,note);button.addEventListener('click',()=>openCertificateBoss(goal.id));
        const row=document.createElement('div');row.className='cert-mid-row';
        const edit=document.createElement('button');edit.type='button';edit.className='cert-date-edit';edit.textContent='날짜 변경';edit.disabled=certBusy;edit.setAttribute('aria-label',goal.title+' 날짜 변경');edit.addEventListener('click',()=>editCertificateSchedule(goal.id));row.append(button,edit);certEl('cert-midgoals').append(row);
    }
    certEl('cert-final-date').textContent=['project','contest'].includes(a.kind)?`최종 목표: ${a.result_date} · ${s.final_at?'달성 완료':'프로젝트를 완성한 뒤 최종목표를 완료해주세요.'}`:`최종 목표: ${a.result_date} 합격발표 · ${s.final_at?'달성 완료':'합격 후 최종목표를 완료해주세요.'}`;
    certEl('cert-third-date').value=a.third_date || '';
    for(const [id,value] of [['cert-written-date',a.written_date],['cert-practical-date',a.practical_date],['cert-result-date',a.result_date],['cert-test-date',a.today]])certEl(id).value=value;
    certEl('cert-test-time').value=a.clock_time || '12:00';
    certEl('cert-real-today').textContent=`실제 오늘: ${a.real_today} (한국 시간)`;
    certEl('cert-date-presets').replaceChildren();
    const prior=new Date(a.written_date+'T00:00:00Z');prior.setUTCDate(prior.getUTCDate()-1);
    const presets=['project','contest'].includes(a.kind)?[['첫 목표 전날',prior.toISOString().slice(0,10)],['중기목표 1일',a.written_date],['중기목표 2일',a.practical_date],['중기목표 3일',a.third_date],['최종목표일',a.result_date]]:[['필기 전날',prior.toISOString().slice(0,10)],['필기 당일',a.written_date],['실기 당일',a.practical_date],['합격발표일',a.result_date]];
    for(const [label,date] of presets.filter(([,date])=>!!date)){const button=document.createElement('button');button.type='button';button.textContent=label;button.disabled=certBusy;button.addEventListener('click',()=>void certificateAction('test_on',{date,time:certEl('cert-test-time').value || '12:00'}));certEl('cert-date-presets').append(button);}
    certEl('cert-test-exit').disabled=certBusy || !a.test_mode;certEl('cert-test-reset').disabled=certBusy || !a.test_mode;
    certEl('cert-dates-form').querySelector('button').disabled=certBusy;certEl('cert-test-apply').disabled=certBusy;
    const report=certEl('cert-report');report.replaceChildren();report.hidden=!certificateData?.report_pending;
    if(certificateData?.report_pending){const text=document.createElement('p');text.textContent='스펙은 저장되었습니다. 목표 공고의 점수를 다시 계산해주세요.';const retry=document.createElement('button');retry.type='button';retry.textContent='점수 다시 계산';retry.addEventListener('click',()=>void loadCertificateAdventure());report.append(text,retry);}
    renderCertificateMonth();renderCertificateCurriculum();
}
function routeChoiceIcon(index){
    // Lucide icons for certificates, contests and projects.
    const icon=document.createElement('i');icon.setAttribute('data-lucide',['badge-check','trophy','folder-code'][index]);icon.setAttribute('aria-hidden','true');
    return icon;
}
function renderCertificateCurriculum(){
    const container=certEl('roadmap-curriculum');if(!container)return;
    container.replaceChildren();
    const role={FE:'프론트엔드',BE:'백엔드',APP:'앱 개발',ERP:'시스템·ERP',OPS:'클라우드',SEC:'정보보안',DATA:'데이터',AIML:'AI·ML',QA:'QA',GAME:'게임',IOT:'임베디드·IoT',NET:'네트워크',ETC:'개발·IT'}[currentRoadmap?.job_code] || '개발·IT';
    const license=licenseCurriculum?.recommendations?.[0]?.certificate_name || (licenseLoading?'자격증을 고르고 있어요…':licenseError?'추천을 불러오지 못했어요':'자격증 학습');
    const selected=certificateData?.adventure && certificateData.adventure.road_id===currentRoadmap?.road_id && (licenseCurriculum?.recommendations?.[0]?.rounds || []).some(r=>r.lc_id===certificateData.adventure.lc_id);
    const project=typeof projectCurriculum!=='undefined'?projectCurriculum?.recommendations?.[0]:null;
    const projectBusy=typeof projectLoading!=='undefined' && projectLoading;
    const projectFailed=typeof projectError!=='undefined' && projectError;
    const choices=[
        {label:'자격증',route:'certificate',title:license,description:licenseCurriculum?.recommendations?.[0]?.reason || (licenseLoading?'보완점과 사용자 스펙을 바탕으로 가장 적합한 자격증을 찾고 있습니다.':'보완점에 직접 연결되는 자격증이 있는지 확인합니다.'),goal:licenseCurriculum?.recommendations?.[0]?`목표: ${license} 취득`:'보완점에 맞는 학습 목표를 확인합니다.'},
        {label:'공모전',route:'contest',title:`${role} 공모전`,description:'팀을 구성해 실제 서비스 아이디어를 구현하고 문제 해결 과정을 포트폴리오로 정리합니다.',goal:'목표: 공모전 1회 참가 · 결과물 정리'},
        {label:'프로젝트',route:'project',title:project?.project_name || (projectBusy?'프로젝트를 고르고 있어요…':projectFailed?'추천을 불러오지 못했어요':'프로젝트 학습'),description:project?.reason || (projectBusy?'보완할 기술에 해당하는 프로젝트를 고르고 있습니다.':'보완점에 연결되는 프로젝트를 확인합니다.'),goal:project?`예상 기간: ${project.total_days}일 · 중기목표 ${project.mid_goals.length}개`:'프로젝트 학습 목표를 확인합니다.'}
    ];
    for(const [index,item] of choices.entries()){
        const active=(index===1 && certificateData?.adventure?.kind==='contest' && certificateData.adventure.road_id===currentRoadmap?.road_id) || (index===0 && !!selected) || (index===2 && !!project && certificateData?.adventure?.kind==='project' && certificateData.adventure.road_id===currentRoadmap?.road_id && certificateData.adventure.project_id===project.project_id),button=document.createElement('button');button.type='button';button.className='route-choice roadmap-curriculum-card';button.dataset.route=item.route;button.dataset.curriculumIndex=index;button.disabled=index===2?projectBusy:certBusy;button.setAttribute('aria-pressed',String(active));
        const top=document.createElement('span');top.className='route-choice-top';
        const badge=document.createElement('span');badge.className='route-choice-badge';badge.textContent=item.label;
        const status=document.createElement('span');status.className='route-choice-status';status.textContent=active?'선택됨':'선택하기';top.append(badge,status);
        const heading=document.createElement('span');heading.className='route-choice-heading';
        const icon=document.createElement('span');icon.className='route-choice-icon';icon.append(routeChoiceIcon(index));
        const identity=document.createElement('span');identity.className='route-choice-identity';
        const title=document.createElement('span');title.className='route-choice-title';title.textContent=item.title;identity.append(title);heading.append(icon,identity);
        const description=document.createElement('span');description.className='route-choice-description';description.textContent=item.description;
        const goal=document.createElement('span');goal.className='route-choice-goal';goal.textContent=item.goal;
        button.append(top,heading,description,goal);button.addEventListener('click',()=>void selectCurriculum(index));
        if(index===0 || index===1 || index===2){const wrapper=document.createElement('article');wrapper.className='license-route-wrapper';wrapper.dataset.selected=String(active);wrapper.append(button);const footer=document.createElement('div');footer.className='license-route-footer'+(index===2?' project-route-footer':index===1?' contest-route-footer':'');if(index===0)footer.classList.add('certificate-route-footer');wrapper.append(footer);container.append(wrapper);}else container.append(button);
    }
    lucide.createIcons();if(typeof renderLicensePanel==='function')renderLicensePanel();
    if(typeof renderProjectPanel==='function')renderProjectPanel();if(typeof renderContestPanel==='function')renderContestPanel();
}
async function selectCurriculum(index){
    if(index===2){projectExpanded=!projectExpanded;renderProjectPanel();document.querySelector('.project-route-footer')?.scrollIntoView?.({behavior:'smooth',block:'nearest'});return;}
    if(index===1){await loadContest();return;}
    licenseExpanded=true;renderLicensePanel();document.querySelector('.license-route-footer')?.scrollIntoView?.({behavior:'smooth',block:'nearest'});
    if(!licenseCurriculum && !licenseLoading && !licenseError)await requestLicenseCurriculum(true,false);
}
function renderHomeAdventure(){renderCertificateAdventure();}
function renderCertificateMonth(){
    const a=certificateData?.adventure,selected=certEl('cert-test-date').value;
    if(!certCalendarMonth)certCalendarMonth=(selected || a?.today || new Date().toISOString().slice(0,10)).slice(0,7);
    const first=new Date(certCalendarMonth+'-01T00:00:00Z');
    if(!Number.isFinite(first.getTime()))return;
    certEl('cert-month-title').textContent=`${first.getUTCFullYear()}년 ${first.getUTCMonth()+1}월`;
    const grid=certEl('cert-month-grid');grid.replaceChildren();
    for(const day of ['일','월','화','수','목','금','토']){const label=document.createElement('small');label.textContent=day;grid.append(label);}
    for(let i=0;i<first.getUTCDay();i++)grid.append(document.createElement('span'));
    const last=new Date(first);last.setUTCMonth(last.getUTCMonth()+1);last.setUTCDate(0);
    for(let day=1;day<=last.getUTCDate();day++){
        const date=certCalendarMonth+'-'+String(day).padStart(2,'0'),button=document.createElement('button');button.type='button';button.textContent=day;button.disabled=!a || certBusy;button.setAttribute('aria-label',date);button.setAttribute('aria-pressed',String(selected===date));
        if(a && [a.written_date,a.practical_date,a.third_date,a.result_date].includes(date)){button.classList.add('cert-exam-day');button.title='시험 또는 합격발표 일정';}
        button.addEventListener('click',()=>{certEl('cert-test-date').value=date;renderCertificateMonth();});grid.append(button);
    }
}
function changeCertificateMonth(delta){const date=new Date(certCalendarMonth+'-01T00:00:00Z');date.setUTCMonth(date.getUTCMonth()+delta);certCalendarMonth=date.toISOString().slice(0,7);renderCertificateMonth();}
function editCertificateSchedule(stage){renderCertificateMonth();certEl('cert-calendar').showModal();const input=certEl(stage==='written'?'cert-written-date':stage==='practical'?'cert-practical-date':stage==='third'?'cert-third-date':'cert-result-date');input.focus();input.scrollIntoView?.({block:'center',behavior:'smooth'});}
function openCertificateBoss(stage){
    const a=certificateData?.adventure,isProject=a?.kind==='project';certBossStage=stage;
    certEl('cert-password-title').textContent=stage==='final'?(isProject?'프로젝트 최종목표 달성 확인':'자격증 합격 확인'):(a?.mid_goals.find(g=>g.id===stage)?.title || '목표 완료')+' · 완료 확인';
    certEl('cert-password').value='';certEl('cert-password').hidden=stage==='final';certEl('cert-password').required=stage!=='final';
    const due=stage==='written'?a?.written_date:stage==='practical'?a?.practical_date:a?.third_date;
    certEl('cert-password-description').textContent=isProject?(stage==='final'?('프로젝트를 완성하셨나요? 완료 확인 후 프로젝트 경험에 반영하고 매칭점수를 다시 계산합니다.'):'완료 비밀번호를 입력해주세요. (목표일의 월일 4자리: '+due?.slice(5).replace('-','')+')'):stage==='final'?('최종 합격을 확인하셨나요? 완료 확인 후 보유 자격증에 반영하고 매칭점수를 다시 계산합니다.'):'완료 비밀번호를 입력해주세요.';
    certEl('cert-password-error').textContent='';certEl('cert-password-dialog').showModal();if(stage!=='final')certEl('cert-password').focus();
}
document.addEventListener('DOMContentLoaded',()=>{
    certEl('cert-final-date-edit').addEventListener('click',()=>editCertificateSchedule('final'));
    certEl('cert-go-roadmap').addEventListener('click',()=>switchNavTab('roadmap'));
    certEl('home-final-complete').addEventListener('click',()=>openCertificateBoss('final'));
    certEl('cert-calendar-toggle').addEventListener('click',()=>{renderCertificateMonth();certEl('cert-calendar').showModal();});
    certEl('cert-month-prev').addEventListener('click',()=>changeCertificateMonth(-1));certEl('cert-month-next').addEventListener('click',()=>changeCertificateMonth(1));
    certEl('cert-test-date').addEventListener('change',()=>{if(certEl('cert-test-date').value)certCalendarMonth=certEl('cert-test-date').value.slice(0,7);renderCertificateMonth();});
    document.querySelector('[data-close-calendar]').addEventListener('click',()=>certEl('cert-calendar').close());
    certEl('cert-add-form').addEventListener('submit',async e=>{e.preventDefault();const text=certEl('cert-add-text').value;const data=await certificateAction('add',{text});if(data)certEl('cert-add-text').value='';});
    certEl('cert-dates-form').addEventListener('submit',e=>{e.preventDefault();void certificateAction('dates',{written:certEl('cert-written-date').value,practical:certEl('cert-practical-date').value,result:certEl('cert-result-date').value,...(certificateData?.adventure?.kind==='project'?{third:certEl('cert-third-date').value}:{})});});
    certEl('cert-test-apply').addEventListener('click',()=>void certificateAction('test_on',{date:certEl('cert-test-date').value,time:certEl('cert-test-time').value || '12:00'}));
    certEl('cert-test-reset').addEventListener('click',()=>void certificateAction('test_reset',{date:certEl('cert-test-date').value,time:certEl('cert-test-time').value || '12:00'}));
    certEl('cert-test-exit').addEventListener('click',()=>void certificateAction('test_off'));
    certEl('cert-password-cancel').addEventListener('click',()=>certEl('cert-password-dialog').close());
    certEl('cert-password-form').addEventListener('submit',async e=>{e.preventDefault();const result=await certificateAction('boss',{stage:certBossStage,password:certEl('cert-password').value});if(result){certEl('cert-password').value='';certEl('cert-password-dialog').close();}});
    renderCertificateAdventure();
    setInterval(()=>{if(!document.hidden && !certEl('cert-calendar').open && !certEl('cert-password-dialog').open && !document.activeElement?.matches('input,textarea') && certificateData?.adventure)void loadCertificateAdventure();},60000);
});

function closeCurriculumCompletion(){
    clearTimeout(completionCelebrationTimer);completionCelebrationTimer=null;
    const dialog=certEl('curriculum-completion-dialog');if(dialog?.open)dialog.close();
}
function queueCurriculumCompletion(a){
    clearTimeout(completionCelebrationTimer);
    const epoch=certEpoch,run=a.run_id,mode=a.test_mode;
    const delay=window.matchMedia?.('(prefers-reduced-motion: reduce)').matches?1200:3200;
    completionCelebrationTimer=setTimeout(()=>{
        completionCelebrationTimer=null;
        if(epoch!==certEpoch || certificateData?.adventure?.run_id!==run || certificateData?.adventure?.test_mode!==mode)return;
        showCurriculumCompletion(a);
    },delay);
}
function showCurriculumCompletion(a){
    closeScoreSpotlight();
    for(const id of ['cert-password-dialog','cert-calendar']){const dialog=certEl(id);if(dialog?.open)dialog.close();}
    const dialog=certEl('curriculum-completion-dialog');
    certEl('curriculum-completion-detail').textContent=['project','contest'].includes(a.kind)?'완료한 프로젝트 경험을 마이페이지에 반영했습니다.':'취득한 자격증을 마이페이지에 반영했습니다.';
    if(dialog.open)dialog.close();dialog.showModal();
}
document.addEventListener('DOMContentLoaded',()=>{
    certEl('curriculum-completion-later').addEventListener('click',closeCurriculumCompletion);
    certEl('curriculum-completion-next').addEventListener('click',()=>{
        closeCurriculumCompletion();switchNavTab('roadmap');
        certEl('roadmap-curriculum')?.scrollIntoView?.({behavior:window.matchMedia?.('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'center'});
    });
});
