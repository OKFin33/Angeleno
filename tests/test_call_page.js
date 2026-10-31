// Exercise actual candidate page code with controlled delayed SDK/network events.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = process.argv[2] || require('node:path').join(__dirname, '../web/index.html');
if (!path) throw new Error('Pass the web/index.html path');
const html = fs.readFileSync(path, 'utf8');
const start = html.indexOf('    const TOKEN_URL =');
const end = html.indexOf("    setStatus('connecting');\n    checkLiveKit();", start);
assert(start >= 0 && end > start);
const code = html.slice(start, end);
function harness(fetchOverride, options={}) {
  const rooms = [], states = [], controls = {}, transcripts = [], toasts = [];
  const node = tag => ({tag, children:[], hidden:true, textContent:'',
    set innerHTML(_){throw new Error('Source content must not become HTML');},
    appendChild(child){this.children.push(child);},replaceChildren(...children){this.children=children;}});
  const sourceCard=node('section');
  const transcript=node('p');
  const button = name => ({classList:{add(){},remove(){},toggle(){}},setAttribute(){},addEventListener(type,fn){controls[name]=fn;}});
  const events = Object.fromEntries(['Connected','Disconnected','ParticipantConnected','ParticipantAttributesChanged','ParticipantDisconnected','Reconnecting','Reconnected','TrackSubscribed','TrackUnsubscribed','ActiveSpeakersChanged','TranscriptionReceived','DataReceived'].map(x=>[x,x]));
  class Room {
    constructor(){this.handlers={};this.actualConnected=false;this.remoteParticipants=new Map();this.micStopped=false;this.localParticipant={identity:'synthetic',isMicrophoneEnabled:true,audioTrackPublications:new Map([['mic',{track:{mediaStreamTrack:{stop:()=>{this.micStopped=true;}}}}]]),setMicrophoneEnabled:async()=>{if(options.micError)throw new Error("synthetic microphone denied");}};rooms.push(this);}
    on(name,fn){this.handlers[name]=fn;}
    async connect(){this.actualConnected=true;this.handlers.Connected();}
    disconnect(){if(!this.actualConnected)return Promise.resolve();return new Promise(resolve=>this.finishDisconnect=()=>{this.actualConnected=false;this.handlers.Disconnected();resolve();});}
  }
  const defaultFetch = async url=>({ok:true,json:async()=>url.endsWith('/healthz')?{ready:true}:{token:'synthetic',livekit_url:'ws://synthetic'}});
  const ctx={document:{getElementById:id=>id==='sourceCard'?sourceCard:id==='transcript'?transcript:{value:'deepseek'},createElement:node},window:{location:{origin:'http://synthetic'}},modelSelect:{value:'deepseek'},micButton:button('call'),muteButton:button('mute'),callLabel:{},remoteAudio:{},LivekitClient:{Room,RoomEvent:events,Track:{Kind:{Audio:'audio'}}},TextDecoder,URL,AbortController,setTimeout:()=>1,clearTimeout(){},fetch:fetchOverride||defaultFetch,console:{...console,error(){}},ensureAudioGraph(){},setStatus:(...args)=>states.push(args),startTimer(){},stopTimer(){},disconnectMicFromAnalyser(){},connectMicToAnalyser(){},showTranscript(text){transcripts.push(text);transcript.textContent=text;},modelDisplayName:()=>"Jo"};
  vm.createContext(ctx);vm.runInContext(code,ctx);
  ctx.showToast=text=>toasts.push(text);
  return {ctx,rooms,states,controls,transcripts,toasts,sourceCard,transcript,state:()=>vm.runInContext('({connected,connecting,roomPresent:room!==null,callGeneration})',ctx)};
}
(async()=>{
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
})().catch(error=>{console.error(error);process.exitCode=1;});
