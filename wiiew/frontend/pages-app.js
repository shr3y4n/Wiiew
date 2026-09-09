const $=s=>document.querySelector(s);let ws=null,deferred=null,lastState=null;
const stored=localStorage.getItem('wiiew_api');
const API=()=>{
  let v = localStorage.getItem('wiiew_api') || localStorage.getItem('wiiew_backend_url');
  if (v && v.trim()) {
    v = v.trim().replace(/\/+$/, '');
    if (!v.startsWith('http://') && !v.startsWith('https://')) {
      v = 'https://' + v;
    }
    return v;
  }
  if (location.hostname.endsWith('github.io')) return '';
  return location.origin;
};
const apiPath=p=>{const b=API();if(!b)return p;return `${b}${p.startsWith('/')?'':'/'}${p}`};
const wsPath=()=>{
  const base=API();
  if(!base)return '';
  try{
    const u=new URL(base);
    u.protocol=u.protocol==='https:'?'wss:':'ws:';
    return `${u.origin}/ws/live`;
  }catch(err){
    console.error('[WS] Invalid backend URL:',err);
    return '';
  }
};
function setConn(ok){const e=$('#connection');e.classList.toggle('online',ok);e.innerHTML=`<i></i> ${ok?'Live':'Offline'}`}
function fmtAgo(v){if(v==null)return'—';if(v<5)return'just now';if(v<60)return`${Math.floor(v)}s ago`;return`${Math.floor(v/60)}m ago`}
function render(s){lastState=s;const r=s.room||{},p=s.phone||{},sen=s.sensor||{},sys=s.system||{};const hero=$('#hero'),title=$('#state-title'),text=$('#state-text'),label=$('#visual-label');hero.className='hero';
 if(!sys.armed){hero.classList.add('empty');title.textContent=r.sustained_presence?'Presence detected':'Monitoring paused';text.textContent='System is disarmed.';label.textContent=r.sustained_presence?'PRESENCE':'PAUSED'}
 else if(r.state==='PRESENCE_UNTRUSTED'||r.state==='PRESENCE_DETECTED_INTRUDER'){hero.classList.add('intruder');title.textContent='Someone is here';text.textContent='Someone has entered your room.';label.textContent='PRESENCE DETECTED'}
 else if(r.state==='PRESENCE_TRUSTED'||r.state==='PRESENCE_DETECTED_SUPPRESSED'){hero.classList.add('suppressed');title.textContent='You are home';text.textContent='Trusted phone present — alert suppressed.';label.textContent='TRUSTED DEVICE'}
 else if(r.state==='CHECKING_PRESENCE'||(r.raw_presence&&!r.sustained_presence)){hero.classList.add('empty');title.textContent='Checking…';text.textContent='Confirming a real presence before alerting you.';label.textContent='CONFIRMING'}
 else if(r.state==='SENSOR_OFFLINE'){hero.classList.add('empty');title.textContent='Sensor offline';text.textContent="Wiiew can't currently monitor the room.";label.textContent='OFFLINE'}
 else{hero.classList.add('empty');title.textContent='Room empty';text.textContent='Your room is quiet.';label.textContent='CLEAR'}
 const confirm=$('#confirm');if(r.raw_presence&&!r.sustained_presence&&sys.armed){confirm.hidden=false;const d=r.presence_duration_seconds||0,t=r.presence_threshold_seconds||15;$('#confirm-fill').style.width=`${Math.min(100,d/t*100)}%`;$('#confirm-time').textContent=`${Math.floor(d)}s / ${t}s`}else confirm.hidden=true;
 $('#sensor').textContent=sen.online?'Online':'Offline';$('#server').textContent=sen.online?'Online':'Offline';$('#sensor-dot').className=`dot ${sen.online?'online':''}`;$('#server-dot').className=`dot ${sen.online?'online':''}`;
 $('#phone').textContent=p.phone_state==='PHONE_PRESENT'?'Home':p.phone_state==='PHONE_MAYBE_AWAY'?'Sleeping':p.phone_state==='PHONE_AWAY'?'Away':(!p.configured?'Not set':p.is_home?'Home':'Away');
 $('#phone-dot').className=`dot ${p.is_home?'online':''}`;
 $('#arm').querySelector('span:nth-child(2)').textContent=sys.armed?'ARMED':'DISARMED';$('#arm').style.background=sys.armed?'linear-gradient(135deg,rgba(105,230,179,.14),rgba(105,230,179,.05))':'rgba(255,255,255,.035)';
  const mState=r.movement_state||(r.motion_level==='active'?'MOVEMENT_DETECTED':r.raw_presence?'STATIONARY':'NONE');
  const person=$('#person');
  if(person){
    person.classList.toggle('moving',mState==='MOVEMENT_DETECTED');
    person.classList.toggle('stationary',mState==='STATIONARY');
  }

  // Phase 3: Multi-node localization rendering
  const loc=s.localization||{};
  const chip=$('#loc-chip');
  if(chip){
    if(loc.state==='VALID_ESTIMATE'&&loc.valid&&loc.x!=null&&loc.y!=null){
      chip.textContent=`Pos: (${loc.x}m, ${loc.y}m) ±${Math.round((1-loc.confidence)*100)}cm`;
      chip.style.borderColor='rgba(105,230,179,.4)';
      chip.style.color='var(--green)';
    }else if(loc.state==='SINGLE_NODE'){
      chip.textContent='Single node · Coarse presence';
      chip.style.borderColor='var(--line)';
      chip.style.color='var(--muted)';
    }else if(loc.state==='LOW_CONFIDENCE'){
      chip.textContent='2 nodes · Insufficient for 2D position';
      chip.style.borderColor='rgba(243,197,107,.4)';
      chip.style.color='var(--amber)';
    }else{
      chip.textContent='No active nodes';
      chip.style.borderColor='var(--line)';
      chip.style.color='var(--dim)';
    }
  }

  // Render node beacons around perimeter
  const nMarkers=$('#node-markers');
  if(nMarkers&&loc.nodes?.length){
    const w=loc.room_width_m||4, d=loc.room_depth_m||5;
    nMarkers.innerHTML=loc.nodes.map(n=>{
      const left=Math.max(6, Math.min(94, (n.x/w)*100));
      const top=Math.max(6, Math.min(94, (n.y/d)*100));
      return `<div class="node-beacon ${n.active?'active':''}" style="left:${left}%;top:${top}%;" title="${n.name||n.node_id}"><small>${n.name?.split(' ')[0]||n.node_id}</small></div>`;
    }).join('');
  }

  // Zero Fabrication Policy: Position person ONLY if loc.valid is true
  if(person){
    if(loc.valid&&loc.x!=null&&loc.y!=null){
      const w=loc.room_width_m||4, d=loc.room_depth_m||5;
      person.style.left=`${Math.max(12, Math.min(88, (loc.x/w)*100))}%`;
      person.style.top=`${Math.max(12, Math.min(88, (loc.y/d)*100))}%`;
    }else{
      person.style.left='50%';
      person.style.top='49%';
    }
  }

  $('#last').textContent=`Last activity ${fmtAgo(r.last_activity_seconds_ago)}`;
  $('#motion').textContent=mState==='MOVEMENT_DETECTED'?'Movement detected':mState==='STATIONARY'?'Stationary':'Quiet';
  $('#motion').style.color=mState==='MOVEMENT_DETECTED'?'var(--red)':mState==='STATIONARY'?'var(--cyan)':'var(--text)';
  if($('#direction'))$('#direction').textContent='Dir: Unknown (single node)';
  $('#signal-meta').textContent=`${sen.rssi_dbm??'—'} dBm · ${sen.subcarriers?.length||0} tones`;
  const bars=$('#bars'),amps=sen.subcarriers||[];if(bars.children.length!==amps.slice(0,52).length){bars.innerHTML='';amps.slice(0,52).forEach(()=>{const i=document.createElement('i');bars.appendChild(i)})}const max=Math.max(1,...amps.slice(0,52));[...bars.children].forEach((b,i)=>b.style.height=`${Math.max(8,amps[i]/max*68)}px`)
}
async function status(){try{const r=await fetch(apiPath('/api/status'));if(!r.ok)throw 0;render(await r.json());setConn(true)}catch{setConn(false)}}
function connect(){const url=wsPath();if(!url){setConn(false);return}try{ws?.close()}catch{}ws=new WebSocket(url);ws.onopen=()=>setConn(true);ws.onmessage=e=>{try{render(JSON.parse(e.data))}catch{}};ws.onclose=()=>{setConn(false);setTimeout(connect,2500)};ws.onerror=()=>ws.close()}
async function events(){try{const r=await fetch(apiPath('/api/events'));const j=await r.json();const list=$('#events');if(!j.events?.length){list.innerHTML='<div class="muted">No events yet.</div>';return}list.innerHTML=j.events.slice(0,12).map(e=>`<div class="event ${e.type==='ALERT_TRIGGERED'||e.type==='ENTRY_DETECTED'?'alert':'safe'}"><b>${String(e.type||'EVENT').replaceAll('_',' ')}</b><span>${e.message||''} · ${e.time_iso||''}</span></div>`).join('')}catch{}}
$('#arm').onclick=async()=>{try{await fetch(apiPath('/api/arm'),{method:'POST'});status();events()}catch{}};$('#home').onclick=async()=>{try{await fetch(apiPath('/api/heartbeat'),{method:'POST'});status()}catch{}};$('#refresh').onclick=events;
window.addEventListener('beforeinstallprompt',e=>{e.preventDefault();deferred=e;$('#install').hidden=false});$('#install').onclick=async()=>{if(deferred){deferred.prompt();await deferred.userChoice;deferred=null;$('#install').hidden=true}};
const modal=$('#modal');
$('#settings').onclick=async()=>{
  modal.hidden=false;
  $('#api').value=localStorage.getItem('wiiew_api')||localStorage.getItem('wiiew_backend_url')||'';
  try{
    const res=await fetch(apiPath('/api/settings'));
    if(res.ok){
      const cfg=await res.json();
      if($('#phone-name')) $('#phone-name').value=cfg.trusted_phone_name||'My Phone';
      if($('#phone-ip')) $('#phone-ip').value=cfg.trusted_phone_ip||'';
      if($('#phone-mac')) $('#phone-mac').value=cfg.trusted_phone_mac||'';
      if($('#grace')){
        $('#grace').value=Math.round((cfg.phone_grace_period_seconds||180)/60);
        $('#grace-out').textContent=$('#grace').value;
      }
      if($('#debounce')){
        $('#debounce').value=cfg.presence_sustained_seconds||15;
        $('#debounce-out').textContent=$('#debounce').value;
      }
      return;
    }
  }catch(e){}
  if($('#phone-name')) $('#phone-name').value=localStorage.getItem('wiiew_phone_name')||'My Phone';
};
$('#close').onclick=()=>modal.hidden=true;
$('#debounce').oninput=e=>$('#debounce-out').textContent=e.target.value;
$('#grace').oninput=e=>$('#grace-out').textContent=e.target.value;
if($('#btn-discover-devices')){
  $('#btn-discover-devices').onclick=async()=>{
    const list=$('#discovered-list');
    if(!list)return;
    list.style.display='block';
    list.innerHTML='<div class="muted" style="font-size:11px;padding:6px 0;">Scanning home network for devices...</div>';
    try{
      const res=await fetch(apiPath('/api/devices/discover'));
      const data=await res.json();
      const devs=data.devices||[];
      if(!devs.length){
        list.innerHTML='<div class="muted" style="font-size:11px;padding:6px 0;">No other devices detected on LAN.</div>';
        return;
      }
      list.innerHTML=devs.map(d=>`
        <div style="padding:7px 9px;margin-bottom:4px;border:1px solid var(--line);border-radius:8px;background:rgba(255,255,255,.03);cursor:pointer;font-size:11px;display:flex;justify-content:space-between;align-items:center;" onclick="window.selectPhone('${d.ip}','${d.mac}','${(d.hostname||'').replace(/'/g,"\\'")}')">
          <div>
            <div style="font-weight:600;">${d.hostname||'Device'}</div>
            <div style="color:var(--dim);font-size:10px;">${d.ip} · ${d.mac}</div>
          </div>
          <span style="color:var(--cyan);font-weight:600;">Select</span>
        </div>
      `).join('');
    }catch(e){
      list.innerHTML='<div class="muted" style="font-size:11px;padding:6px 0;color:var(--red);">Scan error: '+e.message+'</div>';
    }
  };
}
window.selectPhone=(ip,mac,name)=>{
  if($('#phone-ip')) $('#phone-ip').value=ip;
  if($('#phone-mac')) $('#phone-mac').value=mac;
  if(name&&!name.includes('Device')&&$('#phone-name')) $('#phone-name').value=name;
  const list=$('#discovered-list');
  if(list) list.style.display='none';
};
$('#save').onclick=async()=>{
  let val=$('#api').value.trim().replace(/\/+$/,'');
  if(val&&!val.startsWith('http://')&&!val.startsWith('https://')){val='https://'+val;}
  if(val){localStorage.setItem('wiiew_api',val);localStorage.setItem('wiiew_backend_url',val);}
  else{localStorage.removeItem('wiiew_api');localStorage.removeItem('wiiew_backend_url');}
  const pName=($('#phone-name')?.value||'My Phone').trim();
  localStorage.setItem('wiiew_phone_name',pName);
  const payload={
    is_armed:lastState?.system?.armed??true,
    trusted_phone_name:pName,
    trusted_phone_ip:($('#phone-ip')?.value||'').trim(),
    trusted_phone_mac:($('#phone-mac')?.value||'').trim(),
    phone_grace_period_seconds:parseInt($('#grace')?.value||'3',10)*60,
    presence_sustained_seconds:parseFloat($('#debounce')?.value||'15')
  };
  try{
    await fetch(apiPath('/api/settings'),{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify(payload)
    });
  }catch(err){console.warn('[Settings] Save error:',err)}
  $('#settings-msg').textContent='Saved. Connecting…';
  modal.hidden=true;
  connect();
  status();
};
async function enablePush(){if(!('serviceWorker'in navigator)||!('PushManager'in window)){alert('Push notifications are not supported by this browser.');return}try{const reg=await navigator.serviceWorker.ready;const keyRes=await fetch(apiPath('/api/push/public-key'));const {publicKey}=await keyRes.json();const perm=await Notification.requestPermission();if(perm!=='granted')return;const sub=await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:base64ToUint8(publicKey)});await fetch(apiPath('/api/push/subscribe'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(sub)});$('#settings-msg').textContent='Notifications enabled.'}catch(e){$('#settings-msg').textContent='Could not enable notifications: '+e.message}}
function base64ToUint8(s){const pad='='.repeat((4-s.length%4)%4),raw=atob((s+pad).replace(/-/g,'+').replace(/_/g,'/'));return Uint8Array.from(raw,c=>c.charCodeAt(0))}$('#push').onclick=enablePush;
if('serviceWorker'in navigator)navigator.serviceWorker.register('./sw.js').catch(console.warn);
if(stored){}else if(location.hostname.endsWith('github.io'))setTimeout(()=>$('#modal').hidden=false,450);
status();events();connect();setInterval(status,10000);