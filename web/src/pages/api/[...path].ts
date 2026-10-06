import * as service from '../../lib/service.mjs';

export const prerender=false;
const json=(data:any,status=200)=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json','Cache-Control':'no-store'}});
// Rutas POST → función del servicio (todas reciben el cuerpo JSON ya validado por tamaño y origen).
const POST:Record<string,(b:any)=>any>={
  health:()=>service.health(),
  refresh:b=>service.refresh({force:Boolean(b.force)}),
  metrics:b=>service.metrics(b.scene,b.experiment),
  log:b=>service.log(b.job,b.filtered),
  link:b=>{service.link(b.scene,b.manzana);return {ok:true};},
  rename:b=>{service.rename(b.id,b.name);return {ok:true};},
  settings:b=>{service.store.setting('auto',Boolean(b.auto));return {ok:true};},
  // fase 2
  scan:b=>service.scan(b.folder,b.kind),
  'pick-folder':()=>service.pickFolder(),
  'dataset/create':b=>service.createDataset(b),
  'dataset/delete':b=>{service.deleteDataset(b.id);return {ok:true};},
  'dataset/upload':b=>service.upload(b.id),
  'dataset/cancel':b=>service.cancelUpload(b.id),
  'experiment/save':b=>service.saveExperiment(b),
  'experiment/delete':b=>{service.deleteExperiment(b.id);return {ok:true};},
  'experiment/validate':b=>service.validateExperiment(b.id),
  'experiment/launch':b=>service.launchExperiment(b.id),
  'experiment/clone':b=>service.cloneExperiment(b),
  'launch/check':b=>service.checkLaunch(b.id),
  relaunch:b=>service.relaunch(b),
  cancel:b=>service.cancelJob(b.job),
};
export async function ALL({request,params,url}:any) {
  try {
    if(!['127.0.0.1','localhost','[::1]'].includes(url.hostname))return json({error:'Solo acceso local.'},403);
    const route=params.path||'';
    if(request.method==='GET'){
      if(route==='state')return json(service.state());
      return json({error:'No encontrado'},404);
    }
    if(request.method!=='POST')return json({error:'Método no permitido'},405);
    if(request.headers.get('origin')!==url.origin)return json({error:'Origen no permitido.'},403);
    if(Number(request.headers.get('content-length')||0)>131072)return json({error:'Solicitud demasiado grande'},413);
    const handler=POST[route];
    if(!handler)return json({error:'No encontrado'},404);
    return json(await handler(await request.json().catch(()=>({}))));
  }catch(e:any){return json({error:e.message},400);}
}
