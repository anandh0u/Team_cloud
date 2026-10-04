#pragma once

// Served at GET /calibrate. Move each joint with a slider, then save the arm's position
// as a named pose. Talks only to this ESP32's own API.
static const char CALIBRATE_PAGE[] PROGMEM = R"rawliteral(<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Arm calibration</title>
<style>
 body{font-family:system-ui,sans-serif;margin:0;padding:14px;background:#111;color:#eee;max-width:720px}
 h1{font-size:1.2rem;margin:0 0 8px} h2{font-size:1rem;color:#aaa;margin:18px 0 8px}
 #stop{width:100%;padding:18px;font-size:1.3rem;font-weight:700;border:0;border-radius:12px;background:#dc2626;color:#fff}
 #stop.resume{background:#16a34a}
 #msg{min-height:1.3em;margin:8px 0;color:#fbbf24}
 .joint{background:#1f2937;border-radius:10px;padding:10px 12px;margin-bottom:8px}
 .joint label{display:flex;justify-content:space-between;font-weight:600}
 input[type=range]{width:100%;height:32px}
 .row{display:flex;gap:6px;flex-wrap:wrap}
 button.small{padding:10px 12px;border:0;border-radius:8px;background:#374151;color:#eee;font-size:.95rem}
 button.save{background:#2563eb;color:#fff}
 table{width:100%;border-collapse:collapse;font-size:.9rem} td,th{padding:6px 4px;border-bottom:1px solid #333;text-align:left}
 pre{background:#000;padding:10px;border-radius:8px;overflow-x:auto;font-size:.8rem}
 .ok{color:#86efac}
</style></head><body>
<h1>Arm calibration</h1>
<button id="stop">STOP</button>
<div id="msg"></div>
<h2>1. Move the arm</h2><div id="joints"></div>
<h2>2. Save the position as a pose</h2>
<div class="row" id="saves"></div>
<h2>Gripper</h2><div class="row">
 <button class="small save" onclick="saveGrip(true)">Save current as OPEN</button>
 <button class="small save" onclick="saveGrip(false)">Save current as CLOSED</button></div>
<h2>Poses</h2><table id="poses"></table>
<h2>For config.h (optional, to make it permanent in code)</h2><pre id="code"></pre>
<button class="small" onclick="resetCal()">Forget all saved calibration</button>
<script>
let st=null,dragging=false;
const $=id=>document.getElementById(id);
const msg=t=>$('msg').textContent=t||'';
async function post(path,body){
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
  const j=await r.json().catch(()=>({}));
  if(!r.ok||j.ok===false)msg(j.error||('HTTP '+r.status));else msg('');
  return j;
}
function build(){
  const names=[...st.joint_names,'gripper'];
  $('joints').innerHTML=names.map((n,i)=>{
    const lo=i<st.limits.length?st.limits[i][0]:0, hi=i<st.limits.length?st.limits[i][1]:180;
    return `<div class="joint"><label>${n}<span id="v${i}"></span></label>
     <input type="range" id="s${i}" min="${lo}" max="${hi}" step="1"
       onpointerdown="dragging=true" onpointerup="dragging=false"
       oninput="$('v${i}').textContent=this.value+'°'"
       onchange="dragging=false;post('/calibrate/jog',{joint:${i},angle:+this.value})"></div>`;}).join('');
  $('saves').innerHTML=st.poses.map(p=>`<button class="small save" onclick="savePose('${p}')">Save as ${p}</button>`).join('');
}
function render(){
  const ang=[...st.arm.angles,st.arm.gripper_angle];
  if(!dragging)ang.forEach((a,i)=>{const s=$('s'+i);if(s){s.value=a;$('v'+i).textContent=a+'°';}});
  $('stop').textContent=st.emergency_stop?'RESUME (arm is stopped)':'STOP';
  $('stop').className=st.emergency_stop?'resume':'';
  $('poses').innerHTML='<tr><th>Pose</th><th>Angles</th><th></th><th></th></tr>'+st.poses.map(p=>
    `<tr><td>${p}</td><td>${st.pose_angles[p].join(' / ')}</td><td>${st.calibrated[p]?'<span class="ok">saved</span>':'default'}</td>
     <td><button class="small" onclick="post('/arm/pose',{pose:'${p}'})">Go</button></td></tr>`).join('')+
    `<tr><td>gripper</td><td>open ${st.gripper_open_deg} / closed ${st.gripper_closed_deg}</td><td></td><td></td></tr>`;
  $('code').textContent='#define POSE_TABLE {  \\\n'+st.poses.map(p=>`  {"${p}", {${st.pose_angles[p].join(', ')}}},  \\`).join('\n')+
    '\n}\n#define GRIPPER_OPEN_DEG '+st.gripper_open_deg+'\n#define GRIPPER_CLOSED_DEG '+st.gripper_closed_deg;
}
async function poll(){
  try{const first=!st;st=await (await fetch('/status')).json();if(first)build();render();}catch(e){msg('connection lost');}
  setTimeout(poll,500);
}
async function savePose(p){const j=await post('/calibrate/save',{pose:p});if(j.ok)msg('Saved '+p);}
async function saveGrip(open){const j=await post('/calibrate/save',{gripper:open?'OPEN':'CLOSED'});if(j.ok)msg('Saved gripper '+(open?'OPEN':'CLOSED'));}
async function resetCal(){if(confirm('Forget every saved pose and use config.h again?'))await post('/calibrate/reset');}
$('stop').onclick=()=>post(st&&st.emergency_stop?'/resume':'/stop');
poll();
</script></body></html>)rawliteral";
