import L from 'leaflet';

const $=(id:string)=>document.getElementById(id) as any;
const esc=(v:any)=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
const STAGES=['frames','telemetry','masks','sfm','georef','undistort','dense','mesh','texture','metrics'];
const STATUS:any={none:'Sin datos',data:'Con datos',partial:'Incompleto',queued:'En cola',running:'Corriendo',done:'Listo',failed:'Fallido'};
const COLORS:any={none:'#b9bfb6',data:'#6f8fb3',partial:'#9b8fb0',queued:'#d19a2a',running:'#2f74c9',done:'#3f8f5a',failed:'#c8483c'};
const VIA:any={manual:'vínculo manual',export:'identificacion.json del export',nombre:'por nombre de escena',georef:'aprox. por GPS del georef (≤ 30 m)'};
const POLL=5*60_000;

let state:any={scenes:[],manzanas:{},jobs:[],names:{}},gis:any,lotsLayer:any=null,layer:any;
let view:any={kind:'overview'},assigning:string|null=null,toastTimer:any;
const layers=new Map<string,any>();

// ---------- utilidades ----------
const badge=(s:string,extra='')=>`<span class="badge s-${esc(s)}">${esc(STATUS[s]||s)}${extra?` · ${esc(extra)}`:''}</span>`;
const pct=(v:any)=>v==null?'—':`${(100*v).toFixed(v<0.1?1:0)} %`;
const bytes=(n:number)=>n>=1e9?`${(n/1e9).toFixed(1)} GB`:n>=1e6?`${(n/1e6).toFixed(0)} MB`:`${Math.round(n/1e3)} kB`;
function duration(s:any){if(s==null)return '—';s=Math.round(s);const h=Math.floor(s/3600),m=Math.round(s%3600/60);return h?`${h} h ${m} min`:m?`${m} min`:`${s} s`;}
function ago(t:number){const m=Math.round((Date.now()-t)/60000);return m<1?'hace un momento':m<60?`hace ${m} min`:m<1440?`hace ${Math.round(m/60)} h`:new Date(t).toLocaleDateString('es-PE',{day:'2-digit',month:'short'});}
const nameOf=(id:string)=>state.names[id]||'';
const feature=(id:string)=>gis.byId.get(id);
function toast(message:string,error=false){$('toast').textContent=message;$('toast').className=error?'error':'';$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,7000);}
async function api(route:string,body?:any){
  const response=await fetch('/api/'+route,body===undefined?{}:{method:'POST',body:JSON.stringify(body),headers:{'Content-Type':'application/json'}});
  const data=await response.json();if(!response.ok)throw Error(data.error||'No se pudo completar la operación.');return data;
}
async function action(fn:()=>Promise<any>,button?:HTMLButtonElement){
  if(button)button.disabled=true;
  try{await fn();}catch(e:any){toast(e.message,true);}finally{if(button)button.disabled=false;await load();}
}

// ---------- mapa ----------
const map=L.map('map',{zoomControl:false,preferCanvas:true,zoomSnap:.25}).setView([-12.145,-77.022],15);
L.control.zoom({position:'bottomright'}).addTo(map);
// El contenedor puede cambiar de tamaño después de crear el mapa (CSS tardío, panel, móvil).
new ResizeObserver(()=>map.invalidateSize()).observe($('map'));
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:20,attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'}).addTo(map);
const statusOf=(id:string)=>state.manzanas[id]?.status||'none';
function style(f:any){
  const id=f.properties.id,s=statusOf(id),selected=view.kind==='manzana'&&view.id===id;
  return {color:selected?'#111':s==='none'?'#68705f':COLORS[s],weight:selected?3:s==='none'?.8:1.6,fillColor:COLORS[s],fillOpacity:selected?.55:s==='none'?.22:.55};
}
async function loadGis(){
  const data=await (await fetch('/gis/manzanas.geojson')).json();
  gis={data,byId:new Map(data.features.map((f:any)=>[f.properties.id,f]))};
  layer=L.geoJSON(data,{style,onEachFeature:(f:any,l:any)=>{
    layers.set(f.properties.id,l);
    l.on('click',()=>clickManzana(f.properties.id));
    l.bindTooltip(()=>{const id=f.properties.id;return `<strong>${esc(nameOf(id)||f.properties.scene)}</strong><br>${esc(id)} · ${esc(STATUS[statusOf(id)])}`;},{sticky:true,direction:'top'});
  }}).addTo(map);
  map.invalidateSize();
  map.fitBounds(layer.getBounds(),{animate:false});
}
async function toggleLots(on:boolean){
  if(!on){lotsLayer?.remove();return;}
  if(!lotsLayer){const data=await (await fetch('/gis/lotes.geojson')).json();lotsLayer=L.geoJSON(data,{interactive:false,style:{color:'#555',weight:.4,fill:false,opacity:.6}});}
  lotsLayer.addTo(map);layer.bringToFront();
}
function focus(id:string){const l=layers.get(id);if(l)map.fitBounds(l.getBounds().pad(1.2),{maxZoom:18});}
function clickManzana(id:string){
  if(assigning){const scene=assigning;assigning=null;action(()=>api('link',{scene,manzana:id}).then(()=>toast(`${scene} → ${nameOf(id)||id}`)));view={kind:'manzana',id};return;}
  view={kind:'manzana',id};render();
}

// ---------- panel ----------
function stagesBar(exp:any){
  return `<div class="stages">${STAGES.map(s=>{const d=exp.stages?.[s];return `<span class="${d?'on':''}" title="${esc(s)}${d?` · ${duration(d.seconds)} · job ${esc(d.job||'?')}${d.host?' · '+esc(d.host):''}`:''}">${esc(s)}</span>`;}).join('')}</div>`;
}
function jobLine(j:any){
  if(!j)return '';
  const reason=j.reason&&j.reason!=='None'?` · ${j.reason}`:'';
  return `<button class="link job" data-log="${esc(j.id)}">job ${esc(j.id)}</button> <span class="dim">${esc(j.state||'')}${j.elapsed?' · '+esc(j.elapsed):''}${esc(reason)}${j.node&&j.node!=='None assigned'?' · '+esc(j.node):''}</span>`;
}
function experimentCard(scene:any,exp:any){
  const m=exp.metrics||{};
  const facts=[
    m.registered_images!=null?['Registradas',`${m.registered_images}/${m.frames_total??'?'} (${pct(m.registration_ratio)})`]:null,
    exp.fragments!=null?['Fragmentos',exp.fragments]:null,
    m.timings_total_seconds!=null?['Tiempo',duration(m.timings_total_seconds)]:null,
    m.georef||exp.origin?['Georef','sí']:null,
    m.mean_masked_ratio?['Máscara',Object.entries(m.mean_masked_ratio).map(([k,v]:any)=>`${k} ${pct(v)}`).join(', ')]:null,
    ['Textura',exp.textured?'sí':'no'],
  ].filter(Boolean) as any[];
  const notices=(exp.job?.notices||[]).map((n:string)=>`<li>${esc(n)}</li>`).join('');
  return `<article class="exp">
    <header><strong>${esc(exp.name)}</strong>${badge(exp.status,exp.status==='failed'?exp.job?.state:'')}</header>
    ${exp.placeholder?'':stagesBar(exp)}
    ${exp.placeholder?'':`<dl class="facts">${facts.map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl>`}
    ${m.notes?`<p class="notes">${esc(m.notes)}</p>`:''}
    ${exp.job?`<div class="jobline">${jobLine(exp.job)}${exp.jobs.length>1?` <span class="dim">(+${exp.jobs.length-1} anteriores)</span>`:''}</div>`:''}
    ${notices?`<ul class="notices">${notices}</ul>`:''}
    ${exp.placeholder?'':`<div class="actions"><button class="button small" data-detail="${esc(scene.name)}|${esc(exp.name)}">Detalle</button></div>`}
  </article>`;
}
function sceneCard(scene:any,{withLink=true}={}){
  const sources=scene.sources==null?'<span class="warn">Sin carpeta en datasets/raw</span>':scene.sources.length?scene.sources.map((s:any)=>`<span class="source">${esc(s.name)} <b>${s.files}</b> <span class="dim">${bytes(s.bytes)}</span></span>`).join(''):'<span class="dim">sin fuentes</span>';
  const via=scene.manzana?`<span class="dim small">${esc(VIA[scene.via]||'')}</span>`:'';
  const controls=withLink?`<div class="actions">
      <button class="button small" data-assign="${esc(scene.name)}">${scene.manzana?'Mover a otra manzana':'Asignar a manzana'}</button>
      ${scene.manzana?`<button class="button small ghost" data-unlink="${esc(scene.name)}">Quitar</button>`:''}
      ${scene.via==='manual'?`<button class="button small ghost" data-auto="${esc(scene.name)}">Detección automática</button>`:''}
    </div>`:'';
  const exps=scene.experiments||[];
  return `<section class="scene">
    <header><h3>${esc(scene.name)}</h3>${badge(scene.status)}</header>
    <div class="sources">${sources}</div>${via}
    ${scene.identification?`<p class="dim small">Export Street View: ${esc(scene.identification.name||'')} · ${esc(scene.identification.photos??'?')} fotos</p>`:''}
    ${exps.length?exps.map((e:any)=>experimentCard(scene,e)).join(''):'<p class="dim">Sin experimentos en outputs/.</p>'}
    ${controls}
  </section>`;
}
function renderManzana(id:string){
  const f=feature(id);if(!f){view={kind:'overview'};return renderOverview();}
  const p=f.properties,scenes=state.scenes.filter((s:any)=>s.manzana===id);
  return `<div class="crumbs"><button class="link" data-view="overview">← Barranco</button></div>
    <div class="head">
      <input class="title-input" id="rename" value="${esc(nameOf(id))}" placeholder="${esc(p.scene)} — ponle un nombre" maxlength="80" aria-label="Nombre de la manzana"/>
      ${badge(statusOf(id))}
    </div>
    <dl class="facts compact">
      <div><dt>Id GIS</dt><dd><code>${esc(id)}</code></dd></div>
      <div><dt>Escena propuesta</dt><dd><code>${esc(p.scene)}</code></dd></div>
      <div><dt>Lotes</dt><dd>${p.lotCount}${p.groupingValid?'':' <span class="warn">(agrupación inválida: lote suelto)</span>'}</dd></div>
      <div><dt>Área</dt><dd>${Math.round(p.area_m2).toLocaleString('es-PE')} m²</dd></div>
    </dl>
    <h2 class="section">Escenas en Khipu <span class="count">${scenes.length}</span></h2>
    ${scenes.length?scenes.map((s:any)=>sceneCard(s)).join(''):`<p class="empty">Aún no hay datos de esta manzana en Khipu. Para que una escena aparezca aquí, nómbrala <code>${esc(p.scene)}</code> (o <code>${esc(p.scene)}_…</code>), o asígnala desde la lista de escenas sin manzana.</p>`}`;
}
function renderScene(name:string){
  const s=state.scenes.find((x:any)=>x.name===name);if(!s){view={kind:'overview'};return renderOverview();}
  return `<div class="crumbs"><button class="link" data-view="overview">← Barranco</button>${s.manzana?` / <button class="link" data-manzana="${esc(s.manzana)}">${esc(nameOf(s.manzana)||feature(s.manzana)?.properties.scene||s.manzana)}</button>`:''}</div>${sceneCard(s)}`;
}
function renderOverview(){
  if(!state.lastSync)return `<div class="hero"><h1>Lo que ya existe en Khipu</h1>
    <p>Lista escenas de <code>datasets/raw/</code>, experimentos de <code>outputs/</code>, sus métricas y los jobs <code>photogram</code> de las últimas semanas. Solo lectura: no se escribe ni se lanza nada.</p>
    <p class="dim small">Usa <code>ssh khipu</code> con tu clave (BatchMode); la app no guarda credenciales.</p>
    <button class="button primary" id="first-sync">Conectar y listar Khipu</button></div>`;
  const scenes=state.scenes,unlinked=scenes.filter((s:any)=>!s.manzana);
  const exps=scenes.flatMap((s:any)=>s.experiments||[]);
  const active=state.jobs.filter((j:any)=>j.active);
  const recent=state.jobs.slice(0,12);
  const counts=Object.values(state.manzanas).reduce((a:any,m:any)=>{a[m.status]=(a[m.status]||0)+1;return a;},{});
  return `<h1>Barranco</h1>
    <div class="stats">
      <div><b>${Object.keys(state.manzanas).length}</b><span>manzanas con datos</span></div>
      <div><b>${scenes.length}</b><span>escenas</span></div>
      <div><b>${exps.filter((e:any)=>e.status==='done').length}<small>/${exps.length}</small></b><span>experimentos listos</span></div>
      <div><b>${active.length}</b><span>jobs activos</span></div>
    </div>
    ${Object.keys(counts).length?`<p class="dim small">${Object.entries(counts).map(([s,n])=>`${n} ${STATUS[s].toLowerCase()}`).join(' · ')}</p>`:''}
    <h2 class="section">Jobs recientes <span class="count">${state.jobs.length}</span></h2>
    ${recent.length?`<table class="jobs"><tbody>${recent.map((j:any)=>`<tr>
      <td><button class="link" data-log="${esc(j.id)}">${esc(j.id)}</button></td>
      <td>${j.scene?`<button class="link" data-scene="${esc(j.scene)}">${esc(j.scene)}</button>`:'<span class="dim">—</span>'}<div class="dim small">${esc(j.experiment||j.config||'')}</div></td>
      <td><span class="state st-${esc((j.state||'').toLowerCase())}">${esc(j.state||'?')}</span>${j.reason&&j.reason!=='None'?`<div class="dim small">${esc(j.reason)}</div>`:''}</td>
      <td class="dim small">${esc(j.elapsed||'')}</td></tr>`).join('')}</tbody></table>`:'<p class="dim">Sin jobs <code>photogram</code> en las últimas 3 semanas.</p>'}
    <h2 class="section">Escenas sin manzana <span class="count">${unlinked.length}</span></h2>
    <p class="dim small">Pruebas o escenas con nombre libre. Ábrela y asígnala a una manzana con un clic en el mapa.</p>
    <ul class="scene-list">${unlinked.map((s:any)=>`<li><button class="link" data-scene="${esc(s.name)}">${esc(s.name)}</button> ${badge(s.status)} <span class="dim small">${(s.experiments||[]).length} exp.</span></li>`).join('')}</ul>`;
}
function render(){
  const c=state.connection;
  $('connection').className='pill'+(c?.ok?' ok':c?' error':'');
  $('connection').innerHTML=`<i></i> ${c?.ok?'Khipu':c?'Sin conexión':'Sin conectar'}`;
  $('connection').title=c?.error||'';
  $('sync-time').textContent=state.lastSync?`listado ${ago(state.lastSync)}`:'';
  $('refresh').disabled=state.busy>0;
  $('auto').checked=state.auto;
  $('summary').textContent=c&&!c.ok?`Error de conexión: ${c.error}`:state.root?`Khipu: ${state.root}`:'Sin listado todavía.';
  const banner=assigning?`<div class="assigning">Haz clic en una manzana del mapa para asignar <b>${esc(assigning)}</b>. <button class="link" id="cancel-assign">Cancelar</button></div>`:'';
  $('panel').innerHTML=banner+(view.kind==='manzana'?renderManzana(view.id):view.kind==='scene'?renderScene(view.name):renderOverview());
  document.body.classList.toggle('is-assigning',Boolean(assigning));
  layer?.setStyle(style);
  $('search-list').innerHTML=[
    ...state.scenes.map((s:any)=>`<option value="${esc(s.name)}">escena</option>`),
    ...Object.entries(state.names).map(([id,n]:any)=>`<option value="${esc(n)}">${esc(id)}</option>`),
  ].join('');
}
$('legend').innerHTML=['none','data','queued','running','done','failed'].map(s=>`<span><i style="background:${COLORS[s]}"></i>${STATUS[s]}</span>`).join('');

// ---------- detalle de experimento y logs ----------
function table(rows:any[][]){return `<table class="kv">${rows.map(([k,v])=>`<tr><th>${esc(k)}</th><td>${v}</td></tr>`).join('')}</table>`;}
async function openDetail(sceneName:string,expName:string){
  const scene=state.scenes.find((s:any)=>s.name===sceneName),exp=scene?.experiments.find((e:any)=>e.name===expName);
  const base=`outputs/${sceneName}/${expName}`;
  $('detail-body').innerHTML=`<header class="dialog-head"><div><div class="eyebrow">${esc(sceneName)}</div><h2>${esc(expName)}</h2></div><button class="close" data-close>×</button></header><p class="dim">Cargando métricas desde Khipu…</p>`;
  $('detail').showModal();
  let data:any={};
  try{data=await api('metrics',{scene:sceneName,experiment:expName});}catch(e:any){data={error:e.message};}
  const m=data.metrics||{},sp=m.sparse||{},g=data.georef||m.georef,mk=m.masking;
  const timings=Object.entries(m.timings_seconds||{}).sort(([a],[b])=>STAGES.indexOf(a)-STAGES.indexOf(b)).map(([k,v])=>[k,duration(v)]);
  const artifacts=Object.entries(m.artifacts||{}).filter(([,v])=>v).map(([k,v]:any)=>[k,`<code>${esc(v.path)}</code> <span class="dim">${v.size_mb} MB</span>`]);
  const jobs=(exp?.jobs||[]).map((id:string)=>state.jobs.find((j:any)=>j.id===id)||{id});
  $('detail-body').innerHTML=`<header class="dialog-head"><div><div class="eyebrow">${esc(sceneName)}</div><h2>${esc(expName)} ${exp?badge(exp.status):''}</h2></div><button class="close" data-close>×</button></header>
    ${data.error?`<p class="warn">${esc(data.error)}</p>`:''}
    ${!data.metrics&&!data.error?'<p class="dim">Este experimento todavía no tiene <code>metrics/metrics.json</code>.</p>':''}
    <div class="grid2">
      <div><h3>SfM</h3>${table([
        ['Imágenes',`${sp.registered_images??'—'} / ${m.frames_total??'—'} registradas (${pct(sp.registration_ratio)})`],
        ['Por fuente',esc(Object.entries(m.frames_per_source||{}).map(([k,v])=>`${k}: ${v}`).join(', ')||'—')],
        ['Fragmentos',esc(exp?.fragments??'—')+(sp.model!=null?` <span class="dim">(métricas del modelo ${esc(sp.model)})</span>`:'')],
        ['Puntos 3D',esc(sp.points3d?.toLocaleString('es-PE')??'—')],
        ['Error reproy.',sp.mean_reprojection_error_px!=null?`${sp.mean_reprojection_error_px.toFixed(2)} px`:'—'],
        ['Denso',esc(m.dense_backend||'—')],
      ])}</div>
      <div><h3>Tiempos <span class="dim small">${duration(m.timings_total_seconds)}</span></h3>${timings.length?table(timings):'<p class="dim">—</p>'}</div>
    </div>
    ${g?`<h3>Georreferenciación</h3>${table([
      ['Origen ENU',g.enu_origin_gps?`${g.enu_origin_gps.lat?.toFixed(6)}, ${g.enu_origin_gps.lon?.toFixed(6)} <span class="dim">(${esc(g.enu_origin_gps.image)})</span>`:'—'],
      ['Cámaras',esc(g.cameras??'—')],['Desplazamiento',esc(JSON.stringify(g.enu_offset_m??'—'))],['ROI',esc(g.roi?JSON.stringify(g.roi):'—')],
    ])}`:''}
    ${mk?`<h3>Máscaras</h3>${table([['Backends',esc(JSON.stringify(mk.backends))],['% enmascarado',esc(Object.entries(mk.mean_masked_ratio||{}).map(([k,v])=>`${k}: ${pct(v)}`).join(', '))]])}`:''}
    ${artifacts.length?`<h3>Artefactos</h3>${table(artifacts)}`:''}
    ${exp?.textured?`<p class="dim small">Descarga manual (la fase 3 lo hará desde aquí):<br><code class="copy">scp "khipu:${esc(state.root)}/${esc(base)}/mvs/scene_texture*" .</code></p>`:''}
    <h3>Jobs</h3>
    ${jobs.length?`<ul class="joblist">${jobs.map((j:any)=>`<li>${jobLine(j)} <button class="link" data-log="${esc(j.id)}" data-filtered="1">log filtrado</button></li>`).join('')}</ul>`:'<p class="dim">Sin jobs conocidos (marcadores sin <code>slurm_job_id</code>).</p>'}
    <pre id="log-view" class="log" hidden></pre>`;
}
async function showLog(job:string,filtered:boolean){
  if(!$('detail').open){$('detail-body').innerHTML=`<header class="dialog-head"><div><div class="eyebrow">slurm-logs/photogram-${esc(job)}.out</div><h2>Job ${esc(job)}</h2></div><button class="close" data-close>×</button></header><pre id="log-view" class="log"></pre>`;$('detail').showModal();}
  const view=$('log-view');view.hidden=false;view.textContent='Leyendo log…';
  try{
    const r=await api('log',{job,filtered});
    view.innerHTML=`<div class="log-head">photogram-${esc(job)}.out · ${r.total} líneas · ${filtered?'filtrado ([etapa], ==, avisos y errores)':'últimas '+r.lines.length} <button class="link" data-log="${esc(job)}" ${filtered?'':'data-filtered="1"'}>${filtered?'ver cola completa':'ver filtrado'}</button></div>`+
      r.lines.map((l:string)=>`<span class="${/ERROR|Traceback|Error|Killed/.test(l)?'l-err':/ADVERTENCIA|AVISO/.test(l)?'l-warn':l.startsWith('[')||l.startsWith('==')?'l-stage':''}">${esc(l)}</span>`).join('\n');
    view.scrollTop=view.scrollHeight;
    view.scrollIntoView({block:'nearest'});
  }catch(e:any){view.textContent=e.message;}
}

// ---------- eventos ----------
document.addEventListener('click',(e:any)=>{
  const t=e.target.closest('button,[data-close]');if(!t)return;
  const d=t.dataset;
  if(d.close!==undefined){$('detail').close();return;}
  if(d.view==='overview'){view={kind:'overview'};render();}
  else if(d.manzana){view={kind:'manzana',id:d.manzana};focus(d.manzana);render();}
  else if(d.scene){const s=state.scenes.find((x:any)=>x.name===d.scene);if(s?.manzana){view={kind:'manzana',id:s.manzana};focus(s.manzana);}else view={kind:'scene',name:d.scene};render();}
  else if(d.detail){const [s,x]=d.detail.split('|');openDetail(s,x);}
  else if(d.log)showLog(d.log,Boolean(d.filtered));
  else if(d.assign){assigning=d.assign;render();}
  else if(d.unlink)action(()=>api('link',{scene:d.unlink,manzana:null}),t);
  else if(d.auto)action(()=>api('link',{scene:d.auto}),t);
  else if(t.id==='cancel-assign'){assigning=null;render();}
  else if(t.id==='first-sync')sync(true,t);
});
$('detail').addEventListener('click',(e:any)=>{if(e.target===$('detail'))$('detail').close();});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&assigning){assigning=null;render();}});
document.addEventListener('change',(e:any)=>{if(e.target.id==='rename'&&view.kind==='manzana')action(()=>api('rename',{id:view.id,name:e.target.value}));});
$('refresh').addEventListener('click',()=>sync(true,$('refresh')));
$('fit').addEventListener('click',()=>layer&&map.fitBounds(layer.getBounds(),{animate:false}));
$('show-lots').addEventListener('change',(e:any)=>toggleLots(e.target.checked));
$('auto').addEventListener('change',(e:any)=>action(()=>api('settings',{auto:e.target.checked})));
$('search-form').addEventListener('submit',(e:any)=>{
  e.preventDefault();const q=$('search').value.trim();if(!q)return;const ql=q.toLowerCase();
  const scene=state.scenes.find((s:any)=>s.name.toLowerCase()===ql);
  const named=Object.entries(state.names).find(([,n]:any)=>n.toLowerCase().includes(ql));
  const f=gis.data.features.find((f:any)=>f.properties.id.toLowerCase()===ql||f.properties.scene===ql||f.properties.lots.includes(q));
  if(scene){view=scene.manzana?{kind:'manzana',id:scene.manzana}:{kind:'scene',name:scene.name};if(scene.manzana)focus(scene.manzana);}
  else if(named||f){const id=named?named[0]:f.properties.id;view={kind:'manzana',id};focus(id);}
  else{toast('Sin coincidencias. Prueba con un id MZ-BAR-…, mz_xxxxxx, un nº de lote o una escena.',true);return;}
  render();
});

async function load(){try{state=await api('state');render();}catch(e:any){toast(e.message,true);}}
async function sync(force:boolean,button?:HTMLButtonElement){
  await action(async()=>{const r=await api('refresh',{force});if(r.skipped)toast('Listado reciente; espera unos segundos.');},button);
}
// Seguimiento: solo con jobs activos, pestaña visible y casilla marcada; un listado (un ssh) cada 5 min.
setInterval(()=>{
  if(state.auto&&document.visibilityState==='visible'&&state.jobs.some((j:any)=>j.active)&&Date.now()-(state.lastSync||0)>=POLL)sync(false);
},60_000);

loadGis().then(load);
