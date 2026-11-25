// Maintenance records must travel across independent browser storage contexts.
// node tests/browser/maintenance_check.mjs [python-executable] [project-root]
import {spawn} from 'node:child_process';
import {mkdtempSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';

const root=process.argv[3]||fileURLToPath(new URL('../../',import.meta.url));
const python=process.argv[2]||'python';
const temporary=mkdtempSync(join(tmpdir(),'upa-maintenance-'));
const W=18017,B=15617,C=19347;
const children=[
  spawn(python,['-m','pubsub.webapp','--port',String(W),'--broker-port',String(B),'--data-dir',join(temporary,'broker'),'--no-browser'],{cwd:root,stdio:'ignore',windowsHide:true}),
  spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',['--headless=new',`--remote-debugging-port=${C}`,`--user-data-dir=${join(temporary,'edge')}`,'--no-first-run','--disable-extensions','--disable-background-networking','about:blank'],{stdio:'ignore',windowsHide:true}),
];
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const sockets=[],errors=[];let passed=0;
function check(name,condition){console.log(`[${condition?'PASS':'FAIL'}] ${name}`);if(!condition)throw new Error(name);passed++;}
async function connect(url){
  const ws=new WebSocket(url);sockets.push(ws);await new Promise(resolve=>ws.onopen=resolve);
  let id=0;const pending=new Map();
  ws.onmessage=event=>{const message=JSON.parse(event.data);if(message.id){const p=pending.get(message.id);if(!p)return;pending.delete(message.id);message.error?p.reject(new Error(JSON.stringify(message.error))):p.resolve(message.result);}else if(message.method==='Runtime.exceptionThrown')errors.push(message.params.exceptionDetails.exception?.description||message.params.exceptionDetails.text);};
  return (method,params={})=>new Promise((resolve,reject)=>{pending.set(++id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});
}
async function page(browser){
  const {browserContextId}=await browser('Target.createBrowserContext');
  const {targetId}=await browser('Target.createTarget',{url:'about:blank',browserContextId});
  const send=await connect(`ws://127.0.0.1:${C}/devtools/page/${targetId}`);
  const ev=async expression=>{const result=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(result.exceptionDetails)throw new Error(result.exceptionDetails.exception?.description||result.exceptionDetails.text);return result.result.value;};
  const until=async expression=>{for(let i=0;i<100;i++){if(await ev(expression))return true;await pause(100);}return false;};
  await send('Runtime.enable');await send('Page.enable');await send('Page.navigate',{url:`http://127.0.0.1:${W}/`});
  if(!await until("!!document.querySelector('[data-act=fill-demo]')"))throw new Error('Sign-in page did not load');
  return {send,ev,until,login:async(role,username,password)=>{await ev(`Auth.loginRole=${JSON.stringify(role)};F.login({username:${JSON.stringify(username)},password:${JSON.stringify(password)}})`);if(!await until("Broker.state==='live'"))throw new Error('Broker did not connect');}};
}

try{
  let version;for(let i=0;i<100;i++){try{await fetch(`http://127.0.0.1:${W}/`);version=await(await fetch(`http://127.0.0.1:${C}/json/version`)).json();break;}catch{await pause(100);}}
  if(!version)throw new Error('Isolated website/browser did not start');
  const browser=await connect(version.webSocketDebuggerUrl);
  const manager=await page(browser),tenant=await page(browser),unrelated=await page(browser);
  await manager.login('manager','manager','manager123');await tenant.login('tenant','juan','juan123');await unrelated.login('tenant','ana','ana123');
  await manager.ev("go('maintenance')");await pause(100);
  await tenant.ev("S.rooms.push({id:'ticket-room',prop:'p1',name:'New Room',beds:2,price:3000,reserved:0,unavailable:0});S.tenants.push({id:'ticket-tenant',userId:'u-juan',name:'Juan dela Cruz',email:'juan@example.test',phone:'',prop:'p1',room:'ticket-room',start:TODAY,status:'active'});save();go('tickets')");await pause(100);
  check('manager starts without tenant device records',await manager.ev("!tenantById('ticket-tenant')&&!roomById('ticket-room')"));
  await tenant.ev("A['new-ticket']();(()=>{const form=document.querySelector('[data-form=ticket]');form.elements.category.value='Internet';form.elements.priority.value='Medium';form.elements.title.value='No Internet';form.elements.desc.value='No Internet since yesterday';form.requestSubmit();})()");
  const arrived=await manager.until("S.tickets.some(k=>k.title==='No Internet')");
  if(!arrived)console.log('Receiving diagnostics:',await manager.ev("({notifications:S.notifs.map(n=>n.text),tenants:S.tenants.length,rooms:S.rooms.map(r=>r.id),tickets:S.tickets.length,broker:Broker.state})"));
  check('new ticket reaches manager without preexisting tenant/room records',arrived);
  check('manager board displays ticket and linked room',await manager.ev("document.querySelector('#content').textContent.includes('No Internet')&&document.querySelector('#content').textContent.includes('New Room')&&!!tenantById('ticket-tenant')"));
  const id=await tenant.ev("S.tickets.find(k=>k.title==='No Internet').id");
  check('unrelated tenant does not receive ticket',await unrelated.ev(`!S.tickets.some(k=>k.id==='${id}')`));
  await manager.ev(`A.ticket({id:'${id}'});(()=>{const form=document.querySelector('[data-form=ticket-update]');form.elements.status.value='In progress';form.elements.note.value='Router inspection scheduled';form.requestSubmit();})()`);
  check('manager update returns to tenant',await tenant.until(`S.tickets.find(k=>k.id==='${id}')?.status==='In progress'`));
  await tenant.ev(`S.tickets.find(k=>k.id==='${id}').status='Submitted';S.tickets.find(k=>k.id==='${id}').history=S.tickets.find(k=>k.id==='${id}').history.slice(0,1);A.ticket({id:'${id}'})`);
  check('submitted ticket offers a resend action',await tenant.ev("!!document.querySelector('#modal [data-act=ticket-resend]')"));
  await tenant.ev("document.querySelector('#modal [data-act=ticket-resend]').click()");
  await manager.until("S.notifs.some(n=>n.text.includes('resend'))");await pause(250);
  check('resending never resets manager progress or duplicates ticket',await manager.ev(`S.tickets.filter(k=>k.id==='${id}').length===1&&S.tickets.find(k=>k.id==='${id}').status==='In progress'`));
  await manager.ev(`S.tickets=S.tickets.filter(k=>k.id!=='${id}');S.tenants=S.tenants.filter(t=>t.id!=='ticket-tenant');S.rooms=S.rooms.filter(r=>r.id!=='ticket-room');save();render()`);
  await tenant.ev(`A['ticket-resend']({id:'${id}'})`);
  check('existing ticket can be recovered on a manager missing its records',await manager.until(`S.tickets.some(k=>k.id==='${id}')&&!!tenantById('ticket-tenant')&&!!roomById('ticket-room')`));
  await unrelated.ev(`A['ticket-resend']({id:'${id}'})`);await pause(150);
  check('another account cannot resend a ticket it does not own',await unrelated.ev("!S.tickets.some(k=>k.title==='No Internet')"));
  check('no browser exceptions',errors.length===0);
  console.log(JSON.stringify({passed,errors}));
}catch(error){console.error(error.stack);process.exitCode=1;}
finally{for(const ws of sockets)ws.close();for(const child of children)child.kill();}
