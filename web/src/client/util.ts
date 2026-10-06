// Utilidades compartidas por el panel (studio.ts) y los diálogos (forms.ts).
export const $=(id:string)=>document.getElementById(id) as any;
export const esc=(v:any)=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
export const STATUS:any={none:'Sin datos',data:'Con datos',partial:'Incompleto',queued:'En cola',running:'Corriendo',done:'Listo',failed:'Fallido'};
export const badge=(s:string,extra='')=>`<span class="badge s-${esc(s)}">${esc(STATUS[s]||s)}${extra?` · ${esc(extra)}`:''}</span>`;
export const pct=(v:any)=>v==null?'—':`${(100*v).toFixed(v<0.1?1:0)} %`;
export const bytes=(n:number)=>n>=1e9?`${(n/1e9).toFixed(1)} GB`:n>=1e6?`${(n/1e6).toFixed(0)} MB`:`${Math.round(n/1e3)} kB`;
export function duration(s:any){if(s==null)return '—';s=Math.round(s);const h=Math.floor(s/3600),m=Math.round(s%3600/60);return h?`${h} h ${m} min`:m?`${m} min`:`${s} s`;}
export function ago(t:number){const m=Math.round((Date.now()-t)/60000);return m<1?'hace un momento':m<60?`hace ${m} min`:m<1440?`hace ${Math.round(m/60)} h`:new Date(t).toLocaleDateString('es-PE',{day:'2-digit',month:'short'});}
let toastTimer:any;
export function toast(message:string,error=false){
  // Un <dialog> modal tapa todo lo demás (top layer): el aviso se muestra dentro del diálogo abierto.
  (document.querySelector('dialog[open]')||document.body).appendChild($('toast'));
  $('toast').textContent=message;$('toast').className=error?'error':'';$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,error?12000:7000);}
export async function api(route:string,body?:any){
  const response=await fetch('/api/'+route,body===undefined?{}:{method:'POST',body:JSON.stringify(body),headers:{'Content-Type':'application/json'}});
  const data=await response.json();if(!response.ok)throw Error(data.error||'No se pudo completar la operación.');return data;
}
/** Deshabilita el botón mientras corre `fn`, muestra el error si falla y recarga el estado al final. */
export async function busy(button:HTMLButtonElement|null|undefined,fn:()=>Promise<any>,after?:()=>Promise<any>){
  const label=button?.textContent;
  if(button){button.disabled=true;if(button.dataset.busy)button.textContent=button.dataset.busy;}
  try{return await fn();}catch(e:any){toast(e.message,true);}
  finally{if(button){button.disabled=false;button.textContent=label;}await after?.();}
}
