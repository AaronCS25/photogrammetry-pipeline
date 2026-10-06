// Acceso a Khipu con los binarios del sistema (OpenSSH de Windows) y el alias `khipu` de ~/.ssh/config.
// Copiado de sam3d-barranco/web/src/lib/connector.mjs. La app nunca guarda credenciales.
import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import path from 'node:path';

export function execute(program, args, input='', timeout=90000) {
  return new Promise((resolve,reject)=>{
    const child=spawn(program,args,{shell:false,windowsHide:true});
    let out='',err='',settled=false;
    const timer=setTimeout(()=>{child.kill(); finish(Error('La conexión tardó demasiado. Revisa `ssh khipu` en una terminal.'));},timeout);
    function finish(error) { if(settled)return; settled=true;clearTimeout(timer); error ? reject(error) : resolve(out); }
    child.stdout.on('data',b=>{out+=b; if(out.length>10*1024*1024){child.kill();finish(Error('Respuesta remota demasiado grande.'));}});
    child.stderr.on('data',b=>{err=(err+b).slice(-8000);});
    child.on('error',e=>finish(e.code==='ENOENT'?Error(`No se encontró «${program}». Instala el cliente OpenSSH de Windows.`):e));
    child.on('close',code=>finish(code===0?null:Error(err.trim() || `El comando remoto terminó con código ${code}.`)));
    child.stdin.on('error',()=>{});
    child.stdin.end(input);
  });
}
export const options=['-o','BatchMode=yes','-o','ConnectTimeout=12','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=2','-o','StrictHostKeyChecking=yes'];
// Un solo ssh por consulta: el bridge Python va por stdin con la petición embebida en base64.
export async function remote(request, timeout=120000) {
  const bridge=readFileSync(path.resolve('scripts/remote_bridge.py'),'utf8');
  const encoded=Buffer.from(JSON.stringify(request)).toString('base64');
  const code=bridge+`\ntry:\n    print(json.dumps({'ok': True, 'data': dispatch(json.loads(base64.b64decode('${encoded}')))}))\nexcept Exception as exc:\n    print(json.dumps({'ok': False, 'error': str(exc)}))\n`;
  const raw=await execute('ssh',['-T',...options,process.env.KHIPU_HOST||'khipu','python3 -'],code,timeout);
  let response;
  try { response=JSON.parse(raw); } catch { throw Error('Khipu no devolvió una respuesta válida. Revisa la conexión SSH.'); }
  if(!response.ok) throw Error(response.error);
  return response.data;
}
