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
  const button = name => ({classList:{add(){},remove(){},toggle(){}},setAttribute(){},addEventListener(type,fn){controls[name]=fn;}});
  const events = Object.fromEntries(['Connected','Disconnected','ParticipantConnected','ParticipantAttributesChanged','ParticipantDisconnected','Reconnecting','Reconnected','TrackSubscribed','TrackUnsubscribed','ActiveSpeakersChanged','TranscriptionReceived'].map(x=>[x,x]));
  class Room {
    constructor(){this.handlers={};this.actualConnected=false;this.remoteParticipants=new Map();this.micStopped=false;this.localParticipant={identity:'synthetic',isMicrophoneEnabled:true,audioTrackPublications:new Map([['mic',{track:{mediaStreamTrack:{stop:()=>{this.micStopped=true;}}}}]]),setMicrophoneEnabled:async()=>{if(options.micError)throw new Error("synthetic microphone denied");}};rooms.push(this);}
    on(name,fn){this.handlers[name]=fn;}
    async connect(){this.actualConnected=true;this.handlers.Connected();}
    disconnect(){if(!this.actualConnected)return Promise.resolve();return new Promise(resolve=>this.finishDisconnect=()=>{this.actualConnected=false;this.handlers.Disconnected();resolve();});}
  }
  const defaultFetch = async url=>({ok:true,json:async()=>url.endsWith('/healthz')?{ready:true}:{token:'synthetic',livekit_url:'ws://synthetic'}});
  const ctx={document:{getElementById:()=>({value:'deepseek'})},window:{location:{origin:'http://synthetic'}},modelSelect:{value:'deepseek'},micButton:button('call'),muteButton:button('mute'),callLabel:{},remoteAudio:{},LivekitClient:{Room,RoomEvent:events,Track:{Kind:{Audio:'audio'}}},AbortController,setTimeout:()=>1,clearTimeout(){},fetch:fetchOverride||defaultFetch,console:{...console,error(){}},ensureAudioGraph(){},setStatus:(...args)=>states.push(args),startTimer(){},stopTimer(){},disconnectMicFromAnalyser(){},connectMicToAnalyser(){},showTranscript(text){transcripts.push(text);},modelDisplayName:()=>"Jo"};
  vm.createContext(ctx);vm.runInContext(code,ctx);
  return {ctx,rooms,states,controls,transcripts,state:()=>vm.runInContext('({connected,connecting,roomPresent:room!==null,callGeneration})',ctx)};
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
  console.log('6 page lifecycle regressions passed: redial/stale events, stale health, cancelled token, mic denial, playback failure, late mute/transcript');
})().catch(error=>{console.error(error);process.exitCode=1;});
