// Diálogos de la fase 2: nueva captura (subida), experimento (simple/avanzado, validar, lanzar) y relanzar.
import { parseDocument, parse as parseYaml } from 'yaml';
import { PRESETS, QUALITY, GPUS, STAGES, QOS_MEM_G, defaultForm, buildConfig, toYaml, sbatchFromForm, needsSam3, memG, gresType, nextName } from '../lib/presets.mjs';
import { $, esc, api, toast, busy, bytes } from './util';

export type Ctx={state:()=>any,load:()=>Promise<void>,feature:(id:string)=>any};
const KINDS:any={drone_photos:['Fotos de dron','drone'],phone_photos:['Fotos de teléfono','phone'],video:['Video','video'],streetview_export:['Export de Street View',''],other:['Otra','otra']};
let ctx:Ctx;
const dialog=()=>$('form') as HTMLDialogElement;
const head=(eyebrow:string,title:string)=>`<header class="dialog-head"><div><div class="eyebrow">${esc(eyebrow)}</div><h2>${esc(title)}</h2></div><button class="close" data-act="close" aria-label="Cerrar">×</button></header>`;
function show(html:string){$('form-body').innerHTML=html;if(!dialog().open)dialog().showModal();}
export function initForms(context:Ctx){
  ctx=context;
  $('form-body').addEventListener('click',(e:any)=>{const t=e.target.closest('[data-act]');if(t)handlers[t.dataset.act]?.(t,e);});
  $('form-body').addEventListener('input',(e:any)=>inputHandler?.(e));
  $('form-body').addEventListener('change',(e:any)=>changeHandler?.(e));
  dialog().addEventListener('click',(e:any)=>{if(e.target===dialog())close();});
  // Esc también cierra: que no queden manejadores del diálogo anterior escuchando.
  dialog().addEventListener('close',()=>{inputHandler=changeHandler=null;});
}
function close(){dialog().close();inputHandler=changeHandler=null;}
const handlers:Record<string,(t:any,e:any)=>void>={close};
let inputHandler:any=null,changeHandler:any=null;

// ============================================================ nueva captura
let rows:any[]=[],capture:any={};
function suggestScene(base:string){
  const s=ctx.state(),taken=new Set([...s.scenes.map((x:any)=>x.name),...s.datasets.map((d:any)=>d.scene)]);
  if(!taken.has(base))return base;
  for(const c of 'bcdefghijklmnop')if(!taken.has(`${base}_${c}`))return `${base}_${c}`;
  return `${base}_${Date.now().toString(36)}`;
}
export function openCapture(manzana:string){
  const p=ctx.feature(manzana).properties;
  capture={manzana,scene:suggestScene(p.scene),notes:''};
  rows=[{kind:'phone_photos',name:'phone',folder:'',scan:null}];
  renderCapture();
}
function scanSummary(r:any){
  const s=r.scan;if(!s)return '';
  const sm=s.summary||{},parts=[`<b>${s.count}</b> archivos · ${bytes(s.bytes)}`];
  if(sm.dims)parts.push(Object.entries(sm.dims).map(([d,n])=>`${d}: ${n}`).join(', '));
  if(sm.photos!=null)parts.push(`GPS ${sm.gps}/${sm.photos}`);
  if(sm.videos!=null)parts.push(`${sm.videos} video(s), ${sm.srt} .srt`);
  if(sm.views!=null)parts.push(`${sm.views} vistas`);
  if(sm.models&&Object.keys(sm.models).length)parts.push(esc(Object.keys(sm.models).join(', ')));
  return `<div class="scan">${parts.join(' · ')}
    ${s.groups?.length>1?`<div class="dim small">Se subirá como: ${s.groups.map((g:any)=>`<code>${esc((r.kind==='streetview_export'?'':r.name)+g.suffix)}/</code> (${g.count})`).join(', ')}</div>`:''}
    ${s.errors.map((w:string)=>`<div class="warn small">✕ ${esc(w)}</div>`).join('')}${s.warnings.map((w:string)=>`<div class="note small">! ${esc(w)}</div>`).join('')}</div>`;
}
function renderCapture(){
  const sv=rows.some(r=>r.kind==='streetview_export');
  const ready=rows.length&&rows.every(r=>r.scan&&!r.scan.errors.length);
  show(`${head(capture.manzana,'Nueva captura')}
    <p class="dim">Cada carpeta local es una <b>fuente</b> (subcarpeta de la escena en Khipu). Las fotos se suben tal cual, con su EXIF.
    Una escena = una captura de la manzana; otra visita = otra escena.</p>
    <div class="grid2 tight">
      <label>Escena en Khipu<input data-cap="scene" value="${esc(capture.scene)}" pattern="[a-zA-Z0-9][a-zA-Z0-9_\\-]{0,63}" required/></label>
      <label>Notas<input data-cap="notes" value="${esc(capture.notes)}" maxlength="500" placeholder="Fecha, luz, recorrido…"/></label>
    </div>
    <h3>Fuentes</h3>
    ${rows.map((r,i)=>`<div class="source-row">
      <div class="row-fields">
        <select data-row="${i}" data-field="kind">${Object.entries(KINDS).map(([k,[l]]:any)=>`<option value="${k}" ${k===r.kind?'selected':''}>${l}</option>`).join('')}</select>
        ${r.kind==='streetview_export'?'<span class="dim small fixed">a la raíz de la escena</span>':`<input data-row="${i}" data-field="name" value="${esc(r.name)}" class="short" aria-label="Nombre de la fuente" title="Subcarpeta en Khipu"/>`}
        <input data-row="${i}" data-field="folder" value="${esc(r.folder)}" placeholder="C:\\ruta\\a\\la\\carpeta" class="grow" aria-label="Carpeta local"/>
        <button class="button small" data-act="pick" data-row="${i}">Examinar…</button>
        <button class="button small" data-act="scan" data-row="${i}" data-busy="Analizando…">Analizar</button>
        ${rows.length>1?`<button class="button small ghost" data-act="remove-row" data-row="${i}" aria-label="Quitar fuente">×</button>`:''}
      </div>${scanSummary(r)}</div>`).join('')}
    ${sv?'':'<button class="button small" data-act="add-row">+ Añadir fuente</button>'}
    <div class="dialog-actions"><span class="dim small">${ready?'Listo para subir. Se puede cerrar el diálogo: la subida sigue en segundo plano.':'Analiza cada carpeta antes de subir.'}</span>
      <button class="button primary" data-act="create" data-busy="Creando…" ${ready?'':'disabled'}>Subir a Khipu ↗</button></div>`);
  inputHandler=(e:any)=>{const t=e.target;
    if(t.dataset.cap)capture[t.dataset.cap]=t.value;
    if(t.dataset.row!==undefined&&t.dataset.field!=='kind'){const r=rows[t.dataset.row];r[t.dataset.field]=t.value;if(t.dataset.field==='folder'&&r.scan){r.scan=null;}}};
  changeHandler=(e:any)=>{const t=e.target;
    if(t.dataset.field==='kind'){const r=rows[t.dataset.row];r.kind=t.value;r.name=KINDS[t.value][1];r.scan=null;if(t.value==='streetview_export')rows=[r];renderCapture();}};
}
handlers['add-row']=()=>{const used=new Set(rows.map(r=>r.kind));rows.push({kind:used.has('drone_photos')?'phone_photos':'drone_photos',name:used.has('drone_photos')?'phone':'drone',folder:'',scan:null});renderCapture();};
handlers['remove-row']=t=>{rows.splice(Number(t.dataset.row),1);renderCapture();};
handlers.pick=t=>busy(t,async()=>{const r=await api('pick-folder',{});if(r.folder){const row=rows[t.dataset.row];row.folder=r.folder;row.scan=null;renderCapture();}});
handlers.scan=t=>busy(t,async()=>{const r=rows[t.dataset.row];r.scan=await api('scan',{folder:r.folder,kind:r.kind});renderCapture();});
handlers.create=t=>busy(t,async()=>{
  const d=await api('dataset/create',{manzana:capture.manzana,scene:capture.scene,notes:capture.notes,sources:rows.map(({kind,name,folder})=>({kind,name,folder}))});
  await api('dataset/upload',{id:d.id});
  close();toast(`Subiendo ${d.scene}… el progreso aparece en el panel.`);
},ctx.load);

// ============================================================ experimento
let ex:any={};
function scenesFor(manzana:string|null){
  const s=ctx.state();
  const names=[...s.scenes.filter((x:any)=>!manzana||x.manzana===manzana).map((x:any)=>x.name),...s.datasets.filter((d:any)=>!manzana||d.manzana===manzana).map((d:any)=>d.scene)];
  return [...new Set(names)];
}
function existingNames(scene:string){
  const s=ctx.state();
  return [...(s.scenes.find((x:any)=>x.name===scene)?.experiments||[]).map((e:any)=>e.name),...s.experimentsLocal.filter((e:any)=>e.scene===scene&&e.id!==ex.id).map((e:any)=>e.name)];
}
function presetFor(scene:string){
  const s=ctx.state(),srcs=[...(s.scenes.find((x:any)=>x.name===scene)?.sources||[]).map((x:any)=>x.name),...(s.datasets.find((d:any)=>d.scene===scene)?.sources||[]).map((x:any)=>x.kind==='streetview_export'?'fachadas':x.name)];
  const has=(n:string)=>srcs.some((x:string)=>x.startsWith(n));
  return has('fachadas')?'streetview':has('drone')&&has('phone')?'dron_telefono':has('drone')?'dron':has('video')?'video':'telefono';
}
/** opts: {manzana, scene?, draft? (experimento local), prefill? ({mode, form, yaml, sbatch, clone})} */
export function openExperiment(opts:any){
  const scenes=scenesFor(opts.manzana);
  const scene=opts.scene||opts.draft?.scene||scenes[0];
  if(!scene){toast('Primero sube una captura de esta manzana.',true);return;}
  const d=opts.draft,p=opts.prefill;
  ex={id:d?.id||null,manzana:opts.manzana,scene,mode:d?.mode||p?.mode||'simple',dirty:!d,validation:d?.validation||null,nameEdited:Boolean(d||p),
    sbatchEdited:Boolean(d||p?.sbatch),clone:d?.clone||p?.clone||null,source:p?.source||null};
  const preset=presetFor(scene);
  ex.form=d?.form||p?.form||defaultForm(preset,nextName(preset,existingNames(scene)));
  if(p?.form)ex.form={...p.form,name:nextName(p.form.name.replace(/_v\d+.*$/,''),existingNames(scene))};
  ex.yaml=d?.yaml||(p?.yaml?renameYaml(p.yaml):null);
  ex.sbatch=d?.sbatch||p?.sbatch||sbatchFromForm(ex.form);
  if(ex.mode==='simple')regenerate();
  renderExperiment();
}
function renameYaml(text:string){
  const doc=parseDocument(text),old=String(doc.getIn(['experiment','name'])||'exp');
  doc.setIn(['experiment','name'],nextName(old.replace(/_v\d+.*$/,''),existingNames(ex.scene)));
  doc.delete('scene');doc.delete('paths'); // config_resolved.yaml las trae; la app las fija
  return String(doc);
}
function regenerate(){
  try{ex.yaml=toYaml(buildConfig(ex.form),`Generado por photogrammetry-studio · preset ${ex.form.preset} (base: configs/experiments/${PRESETS[ex.form.preset].source})\nEscena: ${ex.scene}`);ex.yamlError=null;}
  catch(e:any){ex.yamlError=e.message;}
  if(!ex.sbatchEdited)ex.sbatch=sbatchFromForm(ex.form);
}
const field=(label:string,html:string,cls='')=>`<label class="${cls}">${label}${html}</label>`;
const check=(k:string,v:any,label:string)=>`<label class="check"><input type="checkbox" data-k="${k}" ${v?'checked':''}/> ${label}</label>`;
const numIn=(k:string,v:any,attrs='')=>`<input type="number" data-k="${k}" value="${esc(v??'')}" ${attrs}/>`;
function simpleForm(){
  const f=ex.form,m=f.masking,g=f.georef,r=f.resources;
  return `<div class="presets">${Object.entries(PRESETS).map(([k,p]:any)=>`<label class="preset ${f.preset===k?'on':''}"><input type="radio" name="preset" data-k="preset" value="${k}" ${f.preset===k?'checked':''}/><b>${esc(p.label)}</b><span>${esc(p.hint)}</span></label>`).join('')}</div>
    <div class="grid2 tight">
      ${field('Nombre del experimento',`<input data-k="name" value="${esc(f.name)}" pattern="[a-zA-Z0-9][a-zA-Z0-9_\\-]{0,63}"/>`)}
      ${field('Notas',`<input data-k="notes" value="${esc(f.notes)}" maxlength="300" placeholder="Qué se prueba"/>`)}
    </div>
    <fieldset><legend>${check('masking.enabled',m.enabled,'Máscaras')}</legend>
      <div class="inline">${check('masking.segformer',m.segformer,'SegFormer')}${check('masking.sam3',m.sam3,'SAM 3')}${check('masking.manual',m.manual,`manual (<code>${f.preset==='streetview'?'mascaras':'mask_overrides'}/</code>)`)}${m.sam3?check('masking.cableTiles',m.cableTiles,'mosaico 6×4 para cables'):''}</div>
      ${m.segformer?field('Clases SegFormer (Cityscapes)',`<input data-k="masking.classes" value="${esc(m.classes)}"/>`):''}
      ${m.sam3?field('Prompts SAM 3 (sustantivos simples)',`<input data-k="masking.prompts" value="${esc(m.prompts)}"/>`):''}
    </fieldset>
    <fieldset><legend>${check('georef.enabled',g.enabled,'Georreferenciación')}</legend>
      ${f.preset==='streetview'?'<p class="dim small">Posiciones desde <code>indice.csv</code>.</p>':''}
      <div class="inline">${field('Radio ROI (m)',numIn('georef.radius_m',g.radius_m,'min="5" max="300" placeholder="sin recorte"'))}${field('Altura ROI (m)',numIn('georef.height_m',g.height_m,'min="3" max="200" placeholder="automática"'))}</div>
    </fieldset>
    <fieldset><legend>Calidad</legend><div class="inline">${Object.entries(QUALITY).map(([k,q]:any)=>`<label class="check"><input type="radio" name="quality" data-k="quality" value="${k}" ${f.quality===k?'checked':''}/> ${q.label} <span class="dim small">(${q.maxImage}px, nivel ${q.resolution}${q.refine?`, refine ${q.refine}`:', sin refine'})</span></label>`).join('')}</div>
      ${f.preset==='video'?`<div class="inline">${field('fps',numIn('video.fps',f.video.fps,'min="0.2" max="10" step="0.1"'))}${field('Lado mayor (px)',numIn('video.longEdge',f.video.longEdge,'min="640" max="4096" placeholder="original"'))}</div>`:''}
    </fieldset>
    <fieldset><legend>Recursos</legend><div class="inline">
      ${field('GPU',`<select data-k="resources.gpu">${Object.entries(GPUS).map(([k,l])=>`<option value="${k}" ${r.gpu===k?'selected':''} ${k==='tesla'&&needsSam3(f)?'disabled':''}>${l}</option>`).join('')}</select>`)}
      ${field('Shards',numIn('resources.shards',r.shards,'min="1" max="32"'))}${field('CPUs',numIn('resources.cpus',r.cpus,'min="1" max="64"'))}
      ${field('RAM (G)',numIn('resources.mem',r.mem,'min="8" max="256"'))}${field('Horas',numIn('resources.hours',r.hours,'min="1" max="72"'))}</div>
    </fieldset>`;
}
function reuseBlock(){
  const exps=(ctx.state().scenes.find((x:any)=>x.name===ex.scene)?.experiments||[]).filter((e:any)=>!e.placeholder);
  if(!exps.length)return '';
  const c=ex.clone||{};
  return `<fieldset><legend>Reutilizar etapas de otro experimento</legend>
    <div class="inline">${field('Copiar de',`<select data-clone="from"><option value="">— no, empezar de cero —</option>${exps.map((e:any)=>`<option ${c.from===e.name?'selected':''}>${esc(e.name)}</option>`).join('')}</select>`)}
    ${field('Rehacer desde la etapa',`<select data-clone="fromStage">${STAGES.slice(1,-1).map(s=>`<option ${c.fromStage===s?'selected':''}>${s}</option>`).join('')}</select>`)}</div>
    <p class="dim small">Antes de lanzar se copia <code>outputs/${esc(ex.scene)}/&lt;origen&gt;/</code> a la carpeta nueva sin lo que rehacen las etapas siguientes (mvs/, undistorted/, metrics.json…). Solo tiene sentido si la configuración de las etapas reutilizadas no cambia.</p></fieldset>`;
}
function warnings(){
  const s=ctx.state(),out=[];
  const total=memG(ex.sbatch)+(s.activeMemG||0);
  if(total>QOS_MEM_G)out.push(`RAM: ${memG(ex.sbatch)}G pedidos + ${Math.round(s.activeMemG)}G de tus jobs activos = ${Math.round(total)}G > ${QOS_MEM_G}G del QOS. Quedará en cola (QOSMaxMemoryPerUser) hasta que terminen otros.`);
  let usesSam3=false;try{usesSam3=JSON.stringify(parseYaml(ex.yaml||'')?.masking||{}).includes('sam3');}catch{}
  if(usesSam3&&!['rtxa6000','a100'].includes(gresType(ex.sbatch)||''))out.push('SAM 3 exige A6000 o A100 (bf16): cambia --gres.');
  if(ex.yamlError)out.push(ex.yamlError);
  return out.map(w=>`<div class="note">! ${esc(w)}</div>`).join('');
}
function renderExperiment(){
  const scenes=scenesFor(ex.manzana);
  const v=ex.validation,canLaunch=v?.ok&&!ex.dirty;
  show(`${head(ex.manzana||'',ex.id?'Editar experimento':'Nuevo experimento')}
    <div class="grid2 tight">
      ${field('Escena',`<select id="ex-scene" ${ex.id?'disabled':''}>${scenes.map(s=>`<option ${s===ex.scene?'selected':''}>${esc(s)}</option>`).join('')}</select>`)}
      <div class="tabs" role="tablist"><button class="${ex.mode==='simple'?'on':''}" data-act="mode" data-mode="simple">Simple</button><button class="${ex.mode==='advanced'?'on':''}" data-act="mode" data-mode="advanced">Avanzado (YAML)</button></div>
    </div>
    ${ex.source?`<p class="dim small">Basado en <code>${esc(ex.source)}</code>.</p>`:''}
    <div id="ex-form">${ex.mode==='simple'?simpleForm():`<textarea id="ex-yaml-edit" spellcheck="false" rows="22">${esc(ex.yaml)}</textarea>`}
      ${reuseBlock()}
      ${field('Opciones de sbatch (SBATCH_OPTS)',`<input id="ex-sbatch" value="${esc(ex.sbatch)}" spellcheck="false"/>`)}
    </div>
    <div id="ex-warn">${warnings()}</div>
    ${ex.mode==='simple'?`<details class="yaml-preview"><summary>YAML generado</summary><pre id="ex-yaml">${esc(ex.yaml)}</pre></details>`:''}
    ${v?`<h3>Validación ${v.ok?'<span class="badge s-done">válida</span>':'<span class="badge s-failed">con errores</span>'}${ex.dirty?' <span class="dim small">(cambiaste algo: vuelve a validar)</span>':''}</h3><pre class="log small-log">${esc(v.output)}</pre>`:''}
    <div class="dialog-actions">
      ${ex.id?'<button class="button ghost" data-act="delete-exp">Eliminar borrador</button>':''}
      <span class="spacer"></span>
      <button class="button" data-act="save-exp" data-busy="Guardando…">Guardar borrador</button>
      <button class="button" data-act="validate-exp" data-busy="Validando en Khipu…">Validar</button>
      <button class="button primary" data-act="launch-exp" data-busy="Enviando…" ${canLaunch?'':'disabled'} title="${canLaunch?'':'Primero valida la configuración actual'}">Lanzar ↗</button>
    </div>`);
  inputHandler=(e:any)=>{
    const t=e.target;
    if(t.id==='ex-yaml-edit'){ex.yaml=t.value;markDirty();return;}
    if(t.id==='ex-sbatch'){ex.sbatch=t.value;ex.sbatchEdited=true;markDirty();return;}
    if(t.dataset.k&&t.type!=='radio'&&t.type!=='checkbox'&&t.tagName!=='SELECT'){setK(t);if(t.dataset.k==='name')ex.nameEdited=true;regenerate();markDirty();}
  };
  changeHandler=(e:any)=>{
    const t=e.target;
    if(t.id==='ex-scene'){ex.scene=t.value;const preset=presetFor(ex.scene);ex.form=defaultForm(preset,nextName(preset,existingNames(ex.scene)));ex.clone=null;regenerate();ex.dirty=true;renderExperiment();return;}
    if(t.dataset.clone){ex.clone={...(ex.clone||{fromStage:'georef'}),[t.dataset.clone]:t.value};if(!ex.clone.from)ex.clone=null;markDirty();return;}
    // Texto y números ya se aplicaron en 'input'; redibujar aquí (al perder el foco) borraría lo que se teclea en el campo siguiente.
    if(!t.dataset.k||(t.tagName==='INPUT'&&!['checkbox','radio'].includes(t.type)))return;
    if(t.dataset.k==='preset'){const name=ex.nameEdited?ex.form.name:nextName(t.value,existingNames(ex.scene));ex.form={...defaultForm(t.value,name),notes:ex.form.notes,resources:ex.form.resources};}
    else setK(t);
    regenerate();ex.dirty=true;renderExperiment();
  };
}
function setK(t:any){
  const path=t.dataset.k.split('.');let o=ex.form;
  for(const k of path.slice(0,-1))o=o[k]||={};
  o[path.at(-1)]=t.type==='checkbox'?t.checked:t.type==='number'?(t.value===''?null:Number(t.value)):t.value;
}
function markDirty(){
  ex.dirty=true;
  const pre=$('ex-yaml');if(pre)pre.textContent=ex.yaml;
  const s=$('ex-sbatch');if(s&&document.activeElement!==s)s.value=ex.sbatch;
  $('ex-warn').innerHTML=warnings();
  const launch=document.querySelector('[data-act="launch-exp"]') as HTMLButtonElement;if(launch)launch.disabled=true;
}
handlers.mode=t=>{
  const mode=t.dataset.mode;if(mode===ex.mode)return;
  if(mode==='simple'&&!confirm('Volver al modo simple descarta los cambios hechos a mano en el YAML. ¿Continuar?'))return;
  if(mode==='simple'&&!ex.form){toast('Este experimento no tiene formulario simple (viene de un YAML).',true);return;}
  ex.mode=mode;if(mode==='simple')regenerate();ex.dirty=true;renderExperiment();
};
async function saveExp(){
  const doc=await api('experiment/save',{id:ex.id,scene:ex.scene,manzana:ex.manzana,mode:ex.mode,form:ex.form,yaml:ex.mode==='advanced'?ex.yaml:undefined,sbatch:ex.sbatch,clone:ex.clone});
  ex.id=doc.id;ex.validation=doc.validation||null;ex.dirty=false;
  return doc;
}
handlers['save-exp']=t=>busy(t,async()=>{await saveExp();toast('Borrador guardado.');renderExperiment();},ctx.load);
handlers['validate-exp']=t=>busy(t,async()=>{await saveExp();ex.validation=await api('experiment/validate',{id:ex.id});ex.dirty=false;renderExperiment();},ctx.load);
handlers['launch-exp']=t=>{
  const c=ex.clone?`\nAntes se copiará ${ex.clone.from} (rehaciendo desde ${ex.clone.fromStage}).`:'';
  if(!confirm(`Lanzar ${ex.scene}/${ex.form?.name||'(YAML)'} en Khipu:\nSBATCH_OPTS="${ex.sbatch}"${c}`))return;
  busy(t,async()=>{const l=await api('experiment/launch',{id:ex.id});close();toast(l.job?`Job ${l.job} enviado.`:'Envío registrado; revisa su estado en el panel.');},ctx.load);
};
handlers['delete-exp']=t=>{if(confirm('¿Eliminar este borrador local?'))busy(t,async()=>{await api('experiment/delete',{id:ex.id});close();},ctx.load);};

export async function cloneAsNew(manzana:string|null,scene:string,experiment:string,button?:HTMLButtonElement){
  await busy(button,async()=>{
    const r=await api('experiment/clone',{scene,experiment});
    openExperiment({manzana,scene,prefill:{...r,clone:{from:experiment,fromStage:'georef'}}});
  });
}

// ============================================================ relanzar
let rl:any={};
export function openRelaunch(scene:string,experiment:string){
  const s=ctx.state();
  const exp=s.scenes.find((x:any)=>x.name===scene)?.experiments?.find((e:any)=>e.name===experiment);
  const last=s.launches.find((l:any)=>l.scene===scene&&l.experiment===experiment);
  const job=s.jobs.find((j:any)=>(exp?.jobs||[]).includes(j.id)&&j.config);
  const firstMissing=STAGES.find(st=>!exp?.stages?.[st])||'texture';
  rl={scene,experiment,mode:'resume',fromStage:firstMissing,sbatch:last?.sbatch||'--gres=shard:rtxa6000:8 --cpus-per-task=16 --mem=64G --time=0-6:00:00',
    config:exp?.uiConfig?`datasets/raw/${scene}/_ui/${experiment}.yaml`:job?.config||null,result:null};
  renderRelaunch();
}
function renderRelaunch(){
  const r=rl.result;
  show(`${head(rl.scene,`Relanzar ${rl.experiment}`)}
    <p>YAML: ${rl.config?`<code>${esc(rl.config)}</code>`:'<span class="warn">desconocido (no hay log con cabecera): usa «Nueva versión».</span>'}</p>
    <label class="check"><input type="radio" name="rl-mode" value="resume" ${rl.mode==='resume'?'checked':''}/> <b>Reanudar</b> — mismo experimento; las etapas con marcador <code>.done</code> se omiten.</label>
    <label class="check"><input type="radio" name="rl-mode" value="from" ${rl.mode==='from'?'checked':''}/> <b>Repetir desde</b>
      <select id="rl-stage">${STAGES.map(s=>`<option ${s===rl.fromStage?'selected':''}>${s}</option>`).join('')}</select> <span class="dim small">(--from-stage --force: rehace esa etapa y las siguientes)</span></label>
    <label>Opciones de sbatch<input id="rl-sbatch" value="${esc(rl.sbatch)}" spellcheck="false"/></label>
    <p class="dim small">Primero se ejecuta <code>pipeline validate</code> en el maestro; si falla, no se envía nada.</p>
    ${r?.validation?`<h3>Validación ${r.validation.ok?'<span class="badge s-done">válida</span>':'<span class="badge s-failed">con errores</span>'}</h3><pre class="log small-log">${esc(r.validation.output)}</pre>`:''}
    <div class="dialog-actions"><span class="spacer"></span><button class="button primary" data-act="relaunch" data-busy="Validando y enviando…" ${rl.config?'':'disabled'}>Validar y relanzar ↗</button></div>`);
  inputHandler=(e:any)=>{if(e.target.id==='rl-sbatch')rl.sbatch=e.target.value;};
  changeHandler=(e:any)=>{if(e.target.name==='rl-mode')rl.mode=e.target.value;if(e.target.id==='rl-stage'){rl.fromStage=e.target.value;rl.mode='from';renderRelaunch();}};
}
handlers.relaunch=t=>busy(t,async()=>{
  rl.result=await api('relaunch',{scene:rl.scene,experiment:rl.experiment,fromStage:rl.mode==='from'?rl.fromStage:null,sbatch:rl.sbatch});
  if(rl.result.launch){close();toast(rl.result.launch.job?`Job ${rl.result.launch.job} enviado.`:'Envío registrado.');}
  else renderRelaunch();
},ctx.load);
