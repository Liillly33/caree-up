let contestState=null,contestKey=null,contestRequest=0,contestBusy=false,contestController=null;
function contestBasis(){return currentRoadmap?.road_id+'|'+(certificateData?.adventure?.test_mode?certificateData.adventure.today:new Date().toLocaleDateString('en-CA',{timeZone:'Asia/Seoul'}));}
async function loadContest(force=false){
 const road=currentRoadmap;if(!road || contestBusy)return;
 contestBusy=true;contestKey=contestBasis();contestController=new AbortController();const request=++contestRequest;
 try{const token=await matchingToken();const data=await matchingResponse(await fetch('/api/contests/recommendations?force='+force,{signal:contestController.signal,headers:{Authorization:'Bearer '+token}}));if(request===contestRequest && currentRoadmap?.road_id===road.road_id){contestState=data;}}
 catch(e){if(request===contestRequest && e.name!=='AbortError')contestState={error:e.message};}finally{if(request===contestRequest){contestBusy=false;contestController=null;renderContestPanel();}}
}
function renderContestPanel(){
 const panel=document.querySelector('.contest-route-footer');if(!panel)return;panel.replaceChildren();
 if(contestKey!==contestBasis() && !contestBusy){contestState=null;void loadContest();}
 const item=contestState?.contest;
 panel.parentElement.querySelector('.route-choice-title').textContent=item?.CT_NAME || '공모전';
 const p=document.createElement('p');p.textContent=contestBusy?'공모전을 확인하고 있습니다…':contestState?.error || (item?item.CT_NAME:'해당 채용공고에 맞는 공모전이 없어요.');panel.append(p);
 if(item){
  const summary=document.createElement('small');summary.textContent=(item.CT_SUMMARY || '')+' · 접수 '+item.CT_APPLY_START+' ~ '+item.CT_APPLY_END;panel.append(summary);
  const link=document.createElement('a');link.textContent='공모전 상세 보기';link.href=/^https?:\/\//.test(item.CT_URL || '')?item.CT_URL:'#';link.target='_blank';link.rel='noopener noreferrer';panel.append(link);
  const start=document.createElement('button');start.className='license-start';start.textContent='이 공모전으로 학습 시작';start.disabled=contestBusy || certBusy;start.onclick=()=>void beginAdventure({kind:'contest',contest_id:item.CT_ID});panel.append(start);
  const reroll=document.createElement('button');reroll.className='license-reroll';reroll.textContent='다른 공모전 추천받기';reroll.disabled=contestBusy;reroll.onclick=()=>void loadContest(true);panel.append(reroll);
 }
}
