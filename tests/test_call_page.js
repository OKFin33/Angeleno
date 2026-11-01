// Exercise actual candidate page code with controlled delayed SDK/network events.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = process.argv[2] || require('node:path').join(__dirname, '../web/index.html');
if (!path) throw new Error('Pass the web/index.html path');
const html = fs.readFileSync(path, 'utf8');
const start = html.indexOf('    const MODEL_NAMES =');
const end = html.indexOf("    setStatus('connecting');\n    checkLiveKit();", start);
assert(start >= 0 && end > start);
const code = html.slice(start, end);
function harness(fetchOverride, options={}) {
  const rooms = [], states = [], controls = {}, transcripts = [], toasts = [];
  const timers = new Map(); let nextTimer = 0;
  const schedule = (fn, delay, kind) => {
    const id = ++nextTimer; timers.set(id, {fn, delay, kind, cancelled:false}); return id;
  };
  const cancel = id => {if(timers.has(id))timers.get(id).cancelled=true;};
  const node = tag => ({tag, children:[], hidden:true, textContent:'', attributes:{}, dataset:{},
    classList:{add(){},remove(){},toggle(){}},
    setAttribute(name,value){this.attributes[name]=String(value);},
    getAttribute(name){return this.attributes[name]??null;},
    set innerHTML(_){throw new Error('Source content must not become HTML');},
    appendChild(child){this.children.push(child);},replaceChildren(...children){this.children=children;}});
  const sourceCard=node('section');
  const transcript=node('p');
  const button = name => Object.assign(node('button'), {addEventListener(type,fn){controls[name]=fn;}});
  const elements={sourceCard,transcript,modelSelect:{value:'deepseek'},micButton:button('call'),muteButton:button('mute'),
    callLabel:node('span'),statusText:node('p'),timer:node('p'),toast:node('div'),remoteAudio:node('audio'),
    personaName:node('h1'),portraitLetter:node('span'),disclosure:node('p')};
  const events = Object.fromEntries(['Connected','Disconnected','ParticipantConnected','ParticipantAttributesChanged','ParticipantDisconnected','Reconnecting','Reconnected','TrackSubscribed','TrackUnsubscribed','ActiveSpeakersChanged','TranscriptionReceived','DataReceived'].map(x=>[x,x]));
  class Room {
    constructor(){this.handlers={};this.actualConnected=false;this.remoteParticipants=new Map();this.micStopped=false;this.localParticipant={identity:'synthetic',isMicrophoneEnabled:true,audioTrackPublications:new Map([['mic',{track:{mediaStreamTrack:{stop:()=>{this.micStopped=true;}}}}]]),setMicrophoneEnabled:async enabled=>{if(options.micError)throw new Error("synthetic microphone denied");this.localParticipant.isMicrophoneEnabled=enabled;}};rooms.push(this);}
    on(name,fn){this.handlers[name]=fn;}
    async connect(){this.actualConnected=true;this.handlers.Connected();}
    disconnect(){if(!this.actualConnected)return Promise.resolve();return new Promise(resolve=>this.finishDisconnect=()=>{this.actualConnected=false;this.handlers.Disconnected();resolve();});}
  }
  const defaultFetch = async url=>({ok:true,json:async()=>url.endsWith('/healthz')?{ready:true}:{token:'synthetic',livekit_url:'ws://synthetic'}});
  const ctx={document:{getElementById:id=>elements[id],createElement:node},window:{location:{origin:'http://synthetic'}},
    ...elements,remoteAudio:elements.remoteAudio,LivekitClient:{Room,RoomEvent:events,Track:{Kind:{Audio:'audio'}}},
    TextDecoder,URL,AbortController,setTimeout:(fn,delay)=>schedule(fn,delay,'timeout'),clearTimeout:cancel,
    setInterval:(fn,delay)=>schedule(fn,delay,'interval'),clearInterval:cancel,
    fetch:fetchOverride||defaultFetch,console:{...console,error(){}}};
  vm.createContext(ctx);vm.runInContext(code,ctx);
  // Exercise real DOM/state/timer functions; only the media graph is synthetic.
  ctx.ensureAudioGraph=()=>{}; ctx.connectMicToAnalyser=()=>{};
  const showToast=ctx.showToast, setStatus=ctx.setStatus, showTranscript=ctx.showTranscript;
  ctx.showToast=text=>{toasts.push(text);showToast(text);};
  ctx.setStatus=(...args)=>{states.push(args);setStatus(...args);};
  ctx.showTranscript=text=>{transcripts.push(text);showTranscript(text);};
  return {ctx,rooms,states,controls,transcripts,toasts,sourceCard,transcript,elements,timers,
    toastTimer:()=>[...timers.entries()].filter(([,t])=>t.kind==='timeout'&&t.delay===6000&&!t.cancelled).at(-1)?.[0],
    state:()=>vm.runInContext('({connected,connecting,roomPresent:room!==null,callGeneration})',ctx)};
}
async function originalRegressions(){
  const h=harness();await h.ctx.connect();
  const leaving=h.ctx.disconnect();
  assert.equal(h.rooms[0].micStopped,true,'hangup stops capture before async leave completes');
  const redial=h.ctx.connect();await Promise.resolve();
  assert.equal(h.rooms.length,1,'redial waits for previous SDK leave');
  h.rooms[0].finishDisconnect();await leaving;await redial;
  assert.equal(h.state().connected,true);assert.equal(h.state().roomPresent,true);
  h.rooms[0].handlers.Disconnected();
  h.rooms[0].handlers.ParticipantDisconnected({isAgent:true});
  assert.equal(h.state().connected,true,'old room callbacks must not clear new call');
  assert.equal(h.state().roomPresent,true);
  h.rooms[1].handlers.Disconnected();assert.equal(h.state().roomPresent,false,'current disconnect still clears current state');

  let healthDone;
  const g=harness(()=>new Promise(resolve=>{healthDone=resolve;}));
  g.ctx.checkLiveKit();vm.runInContext('connected=true; callGeneration+=1;',g.ctx);
  g.ctx.setStatus('connected','Jo 对话中');healthDone({json:async()=>({ready:true})});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(g.states.at(-1)[0],'connected','late idle health must not overwrite active state');

  let tokenDone;let requests=0;
  const q=harness(async url=>{requests++;return url.endsWith('/healthz')?{ok:true,json:async()=>({ready:true})}:new Promise(resolve=>{tokenDone=resolve;});});
  const dialing=q.ctx.connect();
  while(!tokenDone)await new Promise(resolve=>setImmediate(resolve));
  await q.ctx.disconnect();tokenDone({ok:true,json:async()=>({token:'late',livekit_url:'ws://synthetic'})});
  await dialing;assert.equal(q.rooms.length,0,'cancelled token request must not create a late room');
  assert.equal(q.state().connected,false);assert.equal(q.state().connecting,false);
  const m=harness(undefined,{micError:true}); await m.ctx.connect();
  assert.equal(m.state().connected,false,'microphone denial ends the call');
  assert.equal(m.rooms[0].micStopped,true);
  assert.match(m.states.at(-1)[1],/麦克风/); m.rooms[0].finishDisconnect();

  const p=harness(); await p.ctx.connect();
  p.ctx.remoteAudio.play=()=>Promise.reject(new Error('synthetic autoplay failure'));
  p.rooms[0].handlers.TrackSubscribed({kind:'audio',attach(){}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(p.state().connected,false,'failed playback ends the call');
  assert.match(p.states.at(-1)[1],/无法播放/); p.rooms[0].finishDisconnect();

  const u=harness(); await u.ctx.connect(); let muteDone;
  u.rooms[0].localParticipant.setMicrophoneEnabled=()=>new Promise(resolve=>{muteDone=resolve;});
  const muting=u.controls.mute(); const ending=u.ctx.disconnect();
  u.rooms[0].finishDisconnect(); await ending; await u.ctx.connect();
  const currentLabel=u.ctx.muteButton.textContent; muteDone(); await muting;
  assert.equal(u.ctx.muteButton.textContent,currentLabel,'late mute does not alter a redial');
  u.rooms[0].handlers.TranscriptionReceived([{text:'stale'}]);
  u.rooms[1].handlers.TranscriptionReceived([{text:'current'}]);
  assert.deepEqual(u.transcripts,['current'],'late transcript cannot enter new call');
  const oldCaptionEnd=u.ctx.disconnect(); u.rooms[1].finishDisconnect(); await oldCaptionEnd;
  assert.equal(u.transcript.textContent,'','hangup clears visible subtitles');
  await u.ctx.connect(); u.rooms[1].handlers.TranscriptionReceived([{text:'late prior call'}]);
  assert.equal(u.transcript.textContent,'','redial cannot display old subtitles');
  const c=harness(); await c.ctx.connect();
  const agent={identity:'agent-current',isAgent:true};
  const card={session_id:'session-current',request_id:'rq-1',phase:'checking',status:null,
    subject:'<img src=x onerror=alert(1)>',citations:[],conflicts:[]};
  const packet=(sequence, changes={}, envelope={})=>new TextEncoder().encode(JSON.stringify({
    type:'angeleno.source',recipient:'synthetic',sequence,card:{...card,...changes},...envelope}));
  const send=(data,who=agent,topic='angeleno.source',room=c.rooms[0])=>room.handlers.DataReceived(data,who,0,topic);
  const flat=n=>[n,...n.children.flatMap(flat)];
  send(packet(1)); assert.equal(c.sourceCard.hidden,false);
  assert(flat(c.sourceCard).some(n=>n.textContent===card.subject),'untrusted markup remains literal text');
  const valid={phase:'result',status:'supported',conclusion:'合成来源支持的结论',citations:[
    {name:'官方合成来源',url:'https://nhm.org/plan-your-visit',excerpt:'合成摘录'},
    {name:'恶意链接',url:'javascript:alert(1)'}]};
  send(packet(2,valid));
  assert.equal(flat(c.sourceCard).filter(n=>n.tag==='a').length,1,'only safe https links render');
  assert.equal(c.sourceCard.children.length,1,'result replaces the checking card');
  assert.deepEqual(c.transcripts,[],'source packets never become transcript or TTS text');
  const ev=harness(); await ev.ctx.connect();
  ev.rooms[0].handlers.DataReceived(packet(1,{phase:'result',status:'evidence',conclusion:null,citations:[{name:'新发现的网页',url:'https://example.org/exhibit',excerpt:'网页资料，并非已核实结论',claim_kind:'fetched_page'}]}),agent,0,'angeleno.source');
  assert.equal(flat(ev.sourceCard).find(n=>n.tag==='h2').textContent,'已取得资料');
  assert(!flat(ev.sourceCard).some(n=>n.textContent==='有依据'),'fetched page does not become a verified fact');
  assert(flat(ev.sourceCard).some(n=>n.textContent==='由搜索服务读取；最终地址和跳转过程未提供'),'proxy boundary is visible in actual source details');
  ev.rooms[0].handlers.DataReceived(packet(2,{phase:'result',status:'evidence',conclusion:null,citations:[{name:'搜索结果',url:'https://example.org/exhibit',excerpt:'摘要',claim_kind:'search_summary'}]}),agent,0,'angeleno.source');
  assert(flat(ev.sourceCard).some(n=>n.textContent==='搜索摘要，尚未读取正文'),'search summary is distinguished from fetched page');
  ev.rooms[0].handlers.DataReceived(packet(3,{phase:'result',status:'unavailable',conclusion:null,citations:[{name:'搜索结果',url:'https://example.org/exhibit',excerpt:'摘要',claim_kind:'search_summary'}]}),agent,0,'angeleno.source');
  assert(flat(ev.sourceCard).some(n=>n.textContent==='搜索摘要，页面未读到／未核验'),'failed fetch retains an explicitly unverified search reference');
  assert.equal(flat(ev.sourceCard).filter(n=>n.tag==='a').length,1,'fetch failure does not silently clear its search source');

  const shown=c.sourceCard.children[0];
  send(packet(1)); send(packet(3),{identity:'user-peer',isAgent:false});
  send(packet(3),agent,'unrelated'); send(packet(3,{}, {recipient:'other-user'}));
  send(packet(3,{session_id:'old-session'})); send(packet(3),{identity:'other-agent',isAgent:true});
  send(new TextEncoder().encode('not json')); send(packet(3,{status:'toString',phase:'result'}));
  assert.equal(c.sourceCard.children[0],shown,'stale, misbound and malformed packets have no effect');
  send(packet(4,{phase:'result',status:'cancelled',conclusion:'must not display',citations:[]}));
  assert(!flat(c.sourceCard).some(n=>n.textContent==='must not display'),'cancelled card has no conclusion');
  const cancelled=c.sourceCard.children[0]; send(packet(2,valid));
  assert.equal(c.sourceCard.children[0],cancelled,'late conclusion cannot replace cancellation');

  const endingCard=c.ctx.disconnect(); c.rooms[0].finishDisconnect(); await endingCard;
  assert.equal(c.sourceCard.hidden,false,'last card remains after hangup');
  await c.ctx.connect(); assert.equal(c.sourceCard.hidden,true,'redial clears last card');
  send(packet(5,valid)); assert.equal(c.sourceCard.hidden,true,'old-room packet cannot enter redial');
  send(packet(1,{...valid,session_id:'new-session'}),agent,'angeleno.source',c.rooms[1]);
  assert.equal(c.sourceCard.hidden,false,'new call can establish its own source session');
  send(packet(2,{session_id:'new-session'}),agent,'angeleno.source',c.rooms[1]);
  const cancelChecking=c.ctx.disconnect(); c.rooms[1].finishDisconnect(); await cancelChecking;
  assert.equal(flat(c.sourceCard).find(n=>n.tag==='h2').textContent,'已取消','hangup must not leave a checking indicator running');
  const t=harness(); await t.ctx.connect();
  const status=(sequence,state,extra={})=>new TextEncoder().encode(JSON.stringify({
    type:'angeleno.turn_status',recipient:'synthetic',session_id:'turn-session',request_id:'r',sequence,state,...extra}));
  const sendStatus=(packet,room=t.rooms[0],who=agent)=>room.handlers.DataReceived(packet,who,0,'angeleno.turn_status');
  sendStatus(status(1,'lookup_timeout'));
  assert.match(t.toasts[0],/超时/); assert.equal(t.state().connected,true,'deadline leaves call available');
  assert.equal(t.sourceCard.hidden,true,'ordinary status does not fabricate a source card');
  assert.deepEqual(t.transcripts,[],'timeout status is not speech');
  sendStatus(status(2,'started')); const fresh=t.states.at(-1);
  sendStatus(status(1,'response_timeout')); sendStatus(status(3,'response_timeout',{recipient:'other'}));
  sendStatus(status(3,'response_timeout',{session_id:'old-session'}));
  sendStatus(status(3,'response_timeout'),t.rooms[0],{identity:'peer',isAgent:false});
  sendStatus(status(3,'unexpected'));
  assert.equal(t.states.at(-1),fresh,'stale, misbound and malformed statuses cannot replace a new turn');
  assert.equal(t.toasts.length,1);
  const endingStatus=t.ctx.disconnect(); t.rooms[0].finishDisconnect(); await endingStatus;
  await t.ctx.connect(); sendStatus(status(4,'response_timeout'));
  assert.equal(t.toasts.length,1,'old room status cannot enter a redial');
  sendStatus(status(1,'response_timeout',{session_id:'new-session'}),t.rooms[1]);
  assert.equal(t.toasts.length,2,'new dial establishes its own turn status sequence');
  console.log('15 page regressions passed: 7 call lifecycle, 6 source card and 2 turn-status groups');
}

const agent={identity:'agent-current',isAgent:true};
const turnStatus=(session,sequence,state)=>new TextEncoder().encode(JSON.stringify({
  type:'angeleno.turn_status',recipient:'synthetic',session_id:session,request_id:'r',sequence,state}));
const sendTurn=(room,session,sequence,state)=>room.handlers.DataReceived(turnStatus(session,sequence,state),agent,0,'angeleno.turn_status');

async function transientReset(){
  const h=harness(); await h.ctx.connect();
  h.elements.timer.textContent='00:42';
  sendTurn(h.rooms[0],'old-session',1,'lookup_timeout');
  const oldTimer=h.toastTimer();
  assert.equal(h.elements.toast.hidden,false);
  const ending=h.ctx.disconnect();
  assert.equal(h.elements.toast.hidden,true,'hangup hides the previous timeout notice immediately');
  assert.equal(h.elements.toast.textContent,'','hangup clears the previous notice text');
  assert.equal(h.timers.get(oldTimer).cancelled,true,'hangup cancels the previous notice timer');
  const dialing=h.ctx.connect();
  assert.equal(h.elements.timer.textContent,'00:00','redial must not display the previous call duration');
  assert.equal(h.elements.toast.hidden,true,'connecting does not show the previous notice');
  h.rooms[0].finishDisconnect(); await ending; await dialing;
  console.log('PASS transient-reset: rapid timeout -> hangup -> redial clears notice, timer and old duration');
}

async function timerIsolation(){
  const h=harness(); await h.ctx.connect();
  sendTurn(h.rooms[0],'old-session',1,'lookup_timeout'); const old=h.toastTimer();
  const ending=h.ctx.disconnect(); h.rooms[0].finishDisconnect(); await ending;
  await h.ctx.connect(); sendTurn(h.rooms[1],'new-session',1,'response_timeout');
  const current=h.toastTimer(); const message=h.elements.toast.textContent;
  // Model callbacks already queued when clearTimeout ran, as well as old SDK events.
  h.timers.get(old).fn();
  sendTurn(h.rooms[0],'old-session',2,'lookup_timeout');
  assert.equal(h.elements.toast.hidden,false,'old callback cannot hide the new call notice');
  assert.equal(h.elements.toast.textContent,message,'old room cannot replace the new call notice');
  assert.equal(h.timers.get(current).cancelled,false,'old callback cannot cancel the new notice timer');
  sendTurn(h.rooms[1],'new-session',2,'lookup_timeout'); const replacement=h.toastTimer();
  h.timers.get(current).fn();
  assert.equal(h.elements.toast.hidden,false,'replaced timer cannot hide a newer notice in the same call');
  h.timers.get(replacement).fn();
  assert.equal(h.elements.toast.hidden,true,'the current notice still expires normally');
  console.log('PASS timer-isolation: old session/replaced callbacks preserve the current notice');
}

async function muteReset(){
  const h=harness(); await h.ctx.connect(); await h.controls.mute();
  assert.equal(h.ctx.muteButton.textContent,'取消静音');
  assert.equal(h.ctx.muteButton.getAttribute('aria-label'),'取消静音');
  const ending=h.ctx.disconnect(); h.rooms[0].finishDisconnect(); await ending;
  assert.equal(h.ctx.muteButton.hidden,true);
  assert.equal(h.ctx.muteButton.textContent,'静音');
  assert.equal(h.ctx.muteButton.getAttribute('aria-label'),'静音麦克风','hangup resets the accessible mute action');
  await h.ctx.connect();
  assert.equal(h.ctx.muteButton.hidden,false);
  assert.equal(h.rooms[1].localParticipant.isMicrophoneEnabled,true);
  assert.equal(h.ctx.muteButton.textContent,'静音');
  assert.equal(h.ctx.muteButton.getAttribute('aria-pressed'),'false');
  assert.equal(h.ctx.muteButton.getAttribute('aria-label'),'静音麦克风','redial visual and accessible action agree');
  await h.controls.mute();
  assert.equal(h.ctx.muteButton.getAttribute('aria-pressed'),'true');
  assert.equal(h.ctx.muteButton.getAttribute('aria-label'),'取消静音');
  console.log('PASS mute-reset: muted -> hangup -> redial restores visual/ARIA consistency');
}

async function audioMetrics(){
  const h=harness(); h.ctx.performance={now:()=>100};
  let calls=0;
  const track={getRTCStatsReport:async()=>{calls++;return new Map([['private-id',{
    type:'inbound-rtp',kind:'audio',packetsLost:3,jitter:.02,
    jitterBufferDelay:2,jitterBufferEmittedCount:100,concealedSamples:12,
    id:'must-not-record',address:'must-not-record',text:'must-not-record'}]]);}};
  h.ctx.startAudioMetrics(track);
  assert.equal(calls,0,'default off makes no RTC stats call');
  assert.equal(h.ctx.window.angelenoAudioMetrics,undefined);
  assert.equal([...h.timers.values()].filter(t=>t.delay===1000&&!t.cancelled).length,0);
  h.ctx.window.location.search='?audio_metrics=1';
  h.ctx.startAudioMetrics(track);await new Promise(setImmediate);
  const rows=h.ctx.window.angelenoAudioMetrics;
  assert.equal(rows[0].packetsLost,3);assert.equal(rows[0].jitter,.02);
  assert.equal(rows[0].jitter_buffer_mean_seconds,.02);
  assert(!JSON.stringify(rows).includes('must-not-record'));
  const timer=[...h.timers.values()].find(t=>t.delay===1000&&!t.cancelled);
  for(let i=0;i<125;i++)await timer.fn();
  assert.equal(rows.length,120,'bounded local numeric history');
  h.ctx.performance.now=()=>120100;
  await timer.fn();
  assert.equal(timer.cancelled,true,'observation stops at120s even if call remains open');
  h.ctx.performance.now=()=>100;
  let resolve;
  h.ctx.startAudioMetrics({getRTCStatsReport:()=>new Promise(r=>resolve=r)});
  const empty=h.ctx.window.angelenoAudioMetrics;
  h.ctx.clearCall();resolve(new Map([['x',{type:'inbound-rtp',kind:'audio',packetsLost:999}]]));
  await new Promise(setImmediate);
  assert.equal(empty.length,0,'late result after hangup cannot enter a later call');
  assert([...h.timers.values()].filter(t=>t.delay===1000).every(t=>t.cancelled));
  h.ctx.startAudioMetrics({getRTCStatsReport:async()=>{throw new Error('private-error');}});
  await new Promise(setImmediate);
  const unavailable=h.ctx.window.angelenoAudioMetrics[0];
  assert.equal(unavailable.stats_available,false);
  assert.equal(unavailable.packetsLost,undefined,'missing counters are not fake zeros');
  assert(!JSON.stringify(unavailable).includes('private-error'));
  h.ctx.stopAudioMetrics();
  console.log('PASS audio-metrics: opt-in/local numeric counters, bounded history, late-result isolation');
}

(async()=>{
  const groups={'original':originalRegressions,'transient-reset':transientReset,'timer-isolation':timerIsolation,'mute-reset':muteReset,'audio-metrics':audioMetrics};
  const selected=process.argv[3];
  if(selected){assert(groups[selected],`Unknown group ${selected}`);await groups[selected]();}
  else for(const run of Object.values(groups))await run();
})().catch(error=>{console.error(error);process.exitCode=1;});
