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
  const rooms = [], states = [], controls = {}, transcripts = [];
  const node = tag => ({tag, children:[], hidden:true, textContent:'',
    set innerHTML(_){throw new Error('Source content must not become HTML');},
    appendChild(child){this.children.push(child);},replaceChildren(...children){this.children=children;}});
  const sourceCard=node('section');
  const button = name => ({classList:{add(){},remove(){},toggle(){}},setAttribute(){},addEventListener(type,fn){controls[name]=fn;}});
  const events = Object.fromEntries(['Connected','Disconnected','ParticipantConnected','ParticipantAttributesChanged','ParticipantDisconnected','Reconnecting','Reconnected','TrackSubscribed','TrackUnsubscribed','ActiveSpeakersChanged','TranscriptionReceived','DataReceived'].map(x=>[x,x]));
  class Room {
    constructor(){this.handlers={};this.actualConnected=false;this.remoteParticipants=new Map();this.micStopped=false;this.localParticipant={identity:'synthetic',isMicrophoneEnabled:true,audioTrackPublications:new Map([['mic',{track:{mediaStreamTrack:{stop:()=>{this.micStopped=true;}}}}]]),setMicrophoneEnabled:async()=>{if(options.micError)throw new Error("synthetic microphone denied");}};rooms.push(this);}
    on(name,fn){this.handlers[name]=fn;}
    async connect(){this.actualConnected=true;this.handlers.Connected();}
    disconnect(){if(!this.actualConnected)return Promise.resolve();return new Promise(resolve=>this.finishDisconnect=()=>{this.actualConnected=false;this.handlers.Disconnected();resolve();});}
  }
  const defaultFetch = async url=>({ok:true,json:async()=>url.endsWith('/healthz')?{ready:true}:{token:'synthetic',livekit_url:'ws://synthetic'}});
  const ctx={document:{getElementById:id=>id==='sourceCard'?sourceCard:{value:'deepseek'},createElement:node},window:{location:{origin:'http://synthetic'}},modelSelect:{value:'deepseek'},micButton:button('call'),muteButton:button('mute'),callLabel:{},remoteAudio:{},LivekitClient:{Room,RoomEvent:events,Track:{Kind:{Audio:'audio'}}},TextDecoder,URL,AbortController,setTimeout:()=>1,clearTimeout(){},fetch:fetchOverride||defaultFetch,console:{...console,error(){}},ensureAudioGraph(){},setStatus:(...args)=>states.push(args),startTimer(){},stopTimer(){},disconnectMicFromAnalyser(){},connectMicToAnalyser(){},showTranscript(text){transcripts.push(text);},modelDisplayName:()=>"Jo"};
  vm.createContext(ctx);vm.runInContext(code,ctx);
  return {ctx,rooms,states,controls,transcripts,sourceCard,state:()=>vm.runInContext('({connected,connecting,roomPresent:room!==null,callGeneration})',ctx)};
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
  console.log('11 page regressions passed: 6 call lifecycle and 5 source card isolation/rendering/cancellation/redial/hangup groups');
})().catch(error=>{console.error(error);process.exitCode=1;});
