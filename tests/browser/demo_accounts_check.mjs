// Presentation accounts must work across independent browser storage contexts.
// node tests/browser/demo_accounts_check.mjs [python-executable] [project-root]
import {spawn} from 'node:child_process';
import {mkdtempSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';

const root=process.argv[3]||fileURLToPath(new URL('../../',import.meta.url));
const python=process.argv[2]||'python';
const temporary=mkdtempSync(join(tmpdir(),'upa-demo-accounts-'));
const W=18023,B=15623,C=19353;
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
  let version;
  for(let i=0;i<60;i++){try{
    await fetch('http://127.0.0.1:'+W+'/',{signal:AbortSignal.timeout(1000)});
    version=await(await fetch('http://127.0.0.1:'+C+'/json/version',{signal:AbortSignal.timeout(1000)})).json();break;
  }catch{await pause(100);}}
  if(!version)throw new Error('Isolated website/browser did not start');
  const browser=await connect(version.webSocketDebuggerUrl);
  const owner=await page(browser);
  check('nine public demo accounts and only two roles',await owner.ev("ACCOUNTS.length===9&&ACCOUNTS.filter(a=>a.role==='tenant').length===8&&ACCOUNTS.filter(a=>a.role==='manager').length===1&&!ACCOUNTS.some(a=>a.role==='admin')"));
  check('tenant demo picker lists all eight accounts',await owner.ev("document.querySelector('#demo-account-select')?.options.length===8"));
  const newUsers=['luis','bea','jerome','miguel','sofia','carlo'];
  for(const username of newUsers){
    await owner.ev("document.querySelector('#demo-account-select').value='"+username+"';document.querySelector('[data-act=fill-demo]').click()");
    check(username+' credentials fill correctly',await owner.ev("document.querySelector('[name=username]').value==='"+username+"'&&document.querySelector('[name=password]').value==='"+username+"123'"));
    await owner.ev("document.querySelector('[data-form=login]').requestSubmit()");
    check(username+' signs in as its own tenant',await owner.until("Auth.account()?.username==='"+username+"'&&S.persona==='u-"+username+"'&&Broker.state==='live'"));
    await owner.ev("go('profile')");
    check(username+' has an editable sample profile',await owner.ev("!!document.querySelector('[data-act=profile-edit]')&&!!S.community.profiles[S.persona].bio&&S.community.profiles[S.persona].published"));
    await owner.send('Page.reload');
    check(username+' survives reload with the correct session',await owner.until("typeof Auth!=='undefined'&&Auth.account()?.username==='"+username+"'&&S.persona==='u-"+username+"'&&Broker.state==='live'"));
    await owner.ev("A.logout()");
  }
  await owner.ev("Auth.loginRole='manager';render()");
  check('owner picker lists the owner account',await owner.ev("document.querySelector('#demo-account-select').options.length===1&&document.querySelector('#demo-account-select').value==='manager'"));
  await owner.login('manager','manager','manager123');
  await owner.ev("go('findtenants')");
  check('owner can find the new public profiles',await owner.ev("['Luis Mendoza','Bea Flores','Jerome Tan','Miguel Reyes','Sofia Garcia','Carlo Lim'].every(name=>document.querySelector('#content').textContent.includes(name))"));
  const luis=await page(browser),bea=await page(browser),jerome=await page(browser);
  await luis.login('tenant','luis','luis123');await bea.login('tenant','bea','bea123');await jerome.login('tenant','jerome','jerome123');
  check('new residents have their assigned property',await luis.ev("myTenancy()?.prop==='p1'")&&await bea.ev("myTenancy()?.prop==='p1'")&&await jerome.ev("myTenancy()?.prop==='p2'"));
  check('existing Juan and Ana stay available as house hunters',await owner.ev("ACCOUNTS.some(a=>a.username==='juan'&&a.password==='juan123')&&ACCOUNTS.some(a=>a.username==='ana'&&a.password==='ana123')&&!S.tenants.some(t=>['u-juan','u-ana'].includes(t.userId))"));
  await owner.ev("F.announce({prop:'p1',title:'Casa presentation notice',body:'For Casa Jaro residents'})");
  check('Casa notice reaches both new Casa residents',await luis.until("S.announcements.some(a=>a.title==='Casa presentation notice')")&&await bea.until("S.announcements.some(a=>a.title==='Casa presentation notice')"));
  check('Molo resident does not receive Casa notice',await jerome.ev("!S.announcements.some(a=>a.title==='Casa presentation notice')&&!S.notifs.some(n=>n.text.includes('Casa presentation notice'))"));
  await owner.ev("F.announce({prop:'p2',title:'Molo presentation notice',body:'For Molo residents'})");
  check('Molo notice reaches Jerome only',await jerome.until("S.announcements.some(a=>a.title==='Molo presentation notice')")&&await luis.ev("!S.announcements.some(a=>a.title==='Molo presentation notice')"));
  await luis.ev("go('tickets');A['new-ticket']();(()=>{const f=document.querySelector('[data-form=ticket]');f.elements.category.value='Internet';f.elements.title.value='Luis demo repair';f.elements.desc.value='Wi-Fi is unavailable';f.requestSubmit()})()");
  check('new resident maintenance request reaches owner',await owner.until("S.tickets.some(k=>k.title==='Luis demo repair'&&tenantById(k.tenant)?.userId==='u-luis')"));
  const ticketId=await owner.ev("S.tickets.find(k=>k.title==='Luis demo repair').id");
  await luis.ev("A.logout()");await pause(6200);
  for(const [status,note]of [['In progress','Checking the router'],['Resolved','Connection restored']]){
    await owner.ev("A.ticket({id:'"+ticketId+"'});F['ticket-update']({status:'"+status+"',note:'"+note+"'},document.querySelector('[data-form=ticket-update]'))");
  }
  await luis.login('tenant','luis','luis123');
  check('new subscriber receives missed maintenance updates',await luis.until("S.tickets.find(k=>k.id==='"+ticketId+"')?.status==='Resolved'"));
  check('both missed updates came from the broker queue',await luis.ev("S.notifs.filter(n=>n.text.includes('Luis demo repair')&&n.broker?.queued).length===2&&S.tickets.find(k=>k.id==='"+ticketId+"').history.length===3"));
  const carlo=await page(browser);await carlo.login('tenant','carlo','carlo123');
  check('house hunter starts without tenancy',await carlo.ev("!myTenancy()"));
  await carlo.ev("A['social-apply']({id:'p1'});(()=>{const f=document.querySelector('[data-form=market-apply]');f.elements.introduction.value='I am looking for a room for the next semester.';f.elements.answer_occupation.value='Student';f.elements.answer_movein.value='2026-12-01';f.elements.answer_stay.value='6 months';f.elements.consent.checked=true;f.requestSubmit()})()");
  check('new applicant sends completed rental requirements to owner',await owner.until("S.marketApplications.some(a=>a.tenant==='u-carlo'&&a.answers.some(x=>x.value==='Student'))"));
  await owner.ev("F.approve({room:'r103',start:TODAY},{dataset:{id:S.joinRequests.find(j=>j.userId==='u-carlo'&&j.status==='Pending').id}})");
  check('applicant receives approval across browser profiles',await carlo.until("myTenancy()?.prop==='p1'&&myTenancy()?.room==='r103'"));
  const legacy=await page(browser);
  await legacy.ev("(()=>{const s=seed();s.community={profiles:{'u-luis':{id:'u-luis',name:'Luis edited',bio:'Keep my bio',published:false}}};s.tenants=[{id:'keep-juan',userId:'u-juan',name:'Juan dela Cruz',email:'juan@example.test',prop:'p1',room:'r103',start:TODAY,status:'active'},{id:'keep-luis',userId:'u-luis',name:'Luis Mendoza',email:'luis@example.test',prop:'p2',room:'b2',start:TODAY,status:'active'}];s.tickets=[{id:'keep-ticket',tenant:'keep-juan',title:'Existing repair',category:'Internet',priority:'Medium',status:'Resolved',desc:'Keep this ticket',history:[]}];delete s.demoAccountsVersion;localStorage.setItem(STORE_KEY,JSON.stringify(s))})()");
  await legacy.send('Page.reload');await legacy.until("typeof S!=='undefined'&&!!document.querySelector('[data-act=fill-demo]')");
  check('migration preserves existing profile, tenancy and ticket',await legacy.ev("S.version===4&&S.community.profiles['u-luis'].bio==='Keep my bio'&&!S.community.profiles['u-luis'].published&&S.tenants.filter(t=>t.userId==='u-luis').length===1&&S.tenants.find(t=>t.userId==='u-luis').room==='b2'&&S.tenants.some(t=>t.id==='keep-juan')&&S.tickets.some(k=>k.id==='keep-ticket'&&k.status==='Resolved')"));
  await legacy.ev("S.tenants=S.tenants.filter(t=>t.userId!=='u-luis');S.community.profiles['u-sofia'].bio='My edited profile';S.community.profiles['u-sofia'].published=false;save()");
  await legacy.send('Page.reload');await legacy.until("typeof S!=='undefined'&&!!document.querySelector('[data-act=fill-demo]')");
  check('reload preserves edits and does not resurrect removed demo tenancy',await legacy.ev("!S.tenants.some(t=>t.userId==='u-luis')&&S.community.profiles['u-sofia'].bio==='My edited profile'&&!S.community.profiles['u-sofia'].published&&S.personas.filter(p=>p.id==='u-sofia').length===1"));
  await legacy.ev("(()=>{const s=seed();for(const r of s.rooms)r.beds=0;delete s.demoAccountsVersion;localStorage.setItem(STORE_KEY,JSON.stringify(s))})()");
  await legacy.send('Page.reload');await legacy.until("typeof S!=='undefined'&&!!document.querySelector('[data-act=fill-demo]')");
  check('demo preparation never overbooks full rooms',await legacy.ev("!S.tenants.some(t=>['u-luis','u-bea','u-jerome'].includes(t.userId))"));
  await legacy.ev("F.login({username:'luis',password:'wrong'})");
  check('incorrect credentials are rejected',await legacy.ev("!Auth.account()&&!!document.querySelector('.login-error')"));
  await legacy.login('tenant','sofia','sofia123');
  let resetSucceeded=true;
  try{await legacy.ev("A['do-reset']()");}catch{resetSucceeded=false;}
  check('demo reset retains the new account and restores its baseline',resetSucceeded&&await legacy.ev("Auth.account()?.username==='sofia'&&S.persona==='u-sofia'&&!!S.community.profiles['u-sofia']?.bio&&S.personas.length===8&&S.tenants.length===3"));
  check('no browser exceptions',errors.length===0);
  console.log(JSON.stringify({passed,errors}));
}catch(error){console.error(error.stack);process.exitCode=1;}
finally{for(const ws of sockets)ws.close();for(const child of children)child.kill();}
