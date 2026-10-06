// Fase 3: descarga de la malla, vistas previas de máscaras y comparación de experimentos.
import { $, esc, api, toast, busy, bytes, pct } from './util';

type Ctx={state:()=>any,load:()=>Promise<void>};
let ctx:Ctx;
export function initResults(context:Ctx){ctx=context;}

// ---------- malla ----------
export function meshBox(scene:string,experiment:string){
  const d=ctx.state().downloads.find((x:any)=>x.id===`${scene}__${experiment}`);
  const active=ctx.state().downloading.includes(`${scene}__${experiment}`);
  const p=d?.progress,ratio=p?.total?p.sent/p.total:0;
  const files=d?.files?.length?`<ul class="ds-sources">${d.files.map((f:any)=>`<li><code>${esc(f.name)}</code> ${bytes(f.size)}${f.done||d.status==='lista'?' <span class="ok">✓</span>':''}</li>`).join('')}</ul>`:'';
  if(active)return `<div class="bar"><i style="width:${(100*ratio).toFixed(1)}%"></i></div>
    <div class="dim small">${esc(d?.status||'preparando')} · ${bytes(p?.sent||0)} / ${bytes(p?.total||0)}${p?.current?` · ${esc(p.current)}`:''}</div>${files}`;
  if(d?.status==='lista')return `<p><span class="ok">Descargada</span> en <code>${esc(d.dir)}</code> · checksums verificados.</p>${files}
    <div class="actions"><button class="button small primary-outline" data-act-r="open" data-id="${esc(d.id)}">Abrir carpeta</button>
    <button class="button small ghost" data-act-r="download" data-scene="${esc(scene)}" data-exp="${esc(experiment)}">Volver a descargar</button></div>`;
  return `${d?.status==='error'?`<p class="warn small">${esc(d.error)}</p>`:''}
    <p class="dim small">Copia <code>scene_texture.*</code>, sus texturas y las métricas a <code>Descargas\\barranco_experiments\\${esc(scene)}__${esc(experiment)}\\</code> para abrir en Blender.</p>
    <button class="button small primary" data-act-r="download" data-scene="${esc(scene)}" data-exp="${esc(experiment)}">Descargar malla</button>`;
}

// ---------- vistas previas ----------
export function previewsBox(scene:string,experiment:string){
  return `<div id="previews-box"><p class="dim small">Magenta = píxeles que la máscara excluye de features, denso y textura. Se generan en Khipu con <code>pipeline previews</code> (contenedor de segmentación, unos segundos) y quedan en caché local.</p>
    <button class="button small" data-act-r="previews" data-scene="${esc(scene)}" data-exp="${esc(experiment)}" data-busy="Generando en Khipu…">Ver vistas previas</button></div>`;
}
function gallery(scene:string,experiment:string,index:any){
  const items=index.items||[];
  return `<div class="gallery">${items.map((i:any)=>`<a href="${esc(i.url)}" target="_blank" rel="noopener" class="thumb">
      <img src="${esc(i.url)}" alt="${esc(i.image)}" loading="lazy"/><span><b>${pct(i.masked_ratio)}</b> ${esc(i.source)} · ${esc(i.image)} <span class="dim">(${i.index+1}/${i.of})</span></span></a>`).join('')}</div>
    <p class="dim small">${items.length} de ${index.total_masks} máscaras · generadas ${esc(index.generated_at||'')}${index.reused?' (ya existían en Khipu)':''}
    · <button class="link" data-act-r="previews" data-force="1" data-scene="${esc(scene)}" data-exp="${esc(experiment)}" data-busy="Regenerando…">regenerar</button></p>`;
}

// ---------- comparación ----------
let cmp:any={};
function experimentsOf(manzana:string){
  return ctx.state().scenes.filter((s:any)=>s.manzana===manzana).flatMap((s:any)=>(s.experiments||[]).filter((e:any)=>!e.placeholder).map((e:any)=>({scene:s.name,experiment:e.name,status:e.status,mtime:e.mtime||0})));
}
export function openCompare(manzana:string,preset?:{scene:string,experiment:string}){
  const list=experimentsOf(manzana).sort((a:any,b:any)=>b.mtime-a.mtime);
  if(list.length<2){toast('Hacen falta al menos dos experimentos en esta manzana.',true);return;}
  const done=list.filter((e:any)=>e.status==='done');
  const first=preset||done[0]||list[0];
  const second=[...done,...list].find((e:any)=>e.scene!==first.scene||e.experiment!==first.experiment);
  cmp={manzana,list,a:first,b:second,result:null};
  renderCompare();
  run();
}
const key=(e:any)=>`${e.scene}|${e.experiment}`;
function renderCompare(){
  const opt=(sel:any)=>cmp.list.map((e:any)=>`<option value="${esc(key(e))}" ${key(e)===key(sel)?'selected':''}>${esc(e.scene)} / ${esc(e.experiment)}</option>`).join('');
  const r=cmp.result;
  const val=(v:any)=>v==null?'—':typeof v==='number'?v.toLocaleString('es-PE'):esc(v);
  $('form-body').innerHTML=`<header class="dialog-head"><div><div class="eyebrow">${esc(cmp.manzana)}</div><h2>Comparar experimentos</h2></div><button class="close" data-act-r="close" aria-label="Cerrar">×</button></header>
    <div class="grid2 tight"><label>A<select id="cmp-a">${opt(cmp.a)}</select></label><label>B<select id="cmp-b">${opt(cmp.b)}</select></label></div>
    ${r?`<table class="compare"><thead><tr><th></th><th>A · ${esc(cmp.a.experiment)}</th><th>B · ${esc(cmp.b.experiment)}</th></tr></thead><tbody>
      ${r.rows.map((row:any)=>`<tr><th>${esc(row.label)}</th><td class="${row.best==='a'?'best':''}">${val(row.a)}</td><td class="${row.best==='b'?'best':''}">${val(row.b)}</td></tr>`).join('')}
    </tbody></table><p class="dim small">En verde, el mejor valor cuando la métrica tiene un sentido claro (más registradas, menos error…). Que registre más no implica mejor malla: revisa también las vistas previas y la malla en Blender.</p>`:'<p class="dim">Cargando métricas…</p>'}`;
  if(!$('form').open)$('form').showModal();
}
async function run(){
  cmp.result=null;renderCompare();
  try{cmp.result=await api('compare',{a:cmp.a,b:cmp.b});}catch(e:any){toast(e.message,true);cmp.result={rows:[]};}
  renderCompare();
}

// ---------- eventos (delegados en document: el detalle y el formulario los comparten) ----------
document.addEventListener('click',(e:any)=>{
  const t=e.target.closest('[data-act-r]');if(!t)return;
  const d=t.dataset;
  if(d.actR==='close')$('form').close();
  else if(d.actR==='download')busy(t,()=>api('mesh/download',{scene:d.scene,experiment:d.exp}).then(()=>toast('Descargando malla…')),ctx.load);
  else if(d.actR==='open')busy(t,()=>api('mesh/open',{id:d.id}));
  else if(d.actR==='previews')busy(t,async()=>{
    const index=await api('previews',{scene:d.scene,experiment:d.exp,force:Boolean(d.force)});
    const box=$('previews-box');if(box)box.innerHTML=gallery(d.scene,d.exp,index);
  });
});
document.addEventListener('change',(e:any)=>{
  if(e.target.id!=='cmp-a'&&e.target.id!=='cmp-b')return;
  const [scene,experiment]=e.target.value.split('|');
  cmp[e.target.id==='cmp-a'?'a':'b']={scene,experiment};
  run();
});
