import * as service from '../../lib/service.mjs';

export const prerender=false;
const json=(data:any,status=200)=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json','Cache-Control':'no-store'}});
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
    if(Number(request.headers.get('content-length')||0)>16384)return json({error:'Solicitud demasiado grande'},413);
    const body=await request.json().catch(()=>({}));
    if(route==='health')return json(await service.health());
    if(route==='refresh')return json(await service.refresh({force:Boolean(body.force)}));
    if(route==='metrics')return json(await service.metrics(body.scene,body.experiment));
    if(route==='log')return json(await service.log(body.job,body.filtered));
    if(route==='link'){service.link(body.scene,body.manzana);return json({ok:true});}
    if(route==='rename'){service.rename(body.id,body.name);return json({ok:true});}
    if(route==='settings'){service.store.setting('auto',Boolean(body.auto));return json({ok:true});}
    return json({error:'No encontrado'},404);
  }catch(e:any){return json({error:e.message},400);}
}
