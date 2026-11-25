// Optional end-to-end check (E) of the dashboard and the DormCare website in headless Microsoft Edge.
//
//   node tests/browser/browser_check.mjs [path-to-python]
//
// Needs Node.js 22 or newer and Microsoft Edge. Not needed to run the project or the Python test suite.
// It uses its own ports, a temporary database and a temporary browser profile, and blocks external hosts.
import { spawn } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../', import.meta.url));
const py = process.argv[2] || process.env.DORMCARE_PYTHON || 'python';
const edgeExe = process.env.EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const [B, D, W, C] = [15600, 18080, 18000, 19333];
const dir = mkdtempSync(join(tmpdir(), 'dormcare-browser-'));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const run = (exe, args) => spawn(exe, args.map(String), { cwd: root, stdio: 'ignore', windowsHide: true });

let passed = 0; let failed = 0;
function ok(title, good) {
  if (good) passed += 1; else failed += 1;
  console.log(`[${good ? 'PASS' : 'FAIL'}] ${title}`);
}

const children = [
  run(py, ['-m', 'pubsub.dashboard', '--port', D, '--broker-port', B, '--data-dir', join(dir, 'data'), '--no-browser']),
  run(edgeExe, ['--headless=new', `--remote-debugging-port=${C}`, `--user-data-dir=${join(dir, 'profile')}`,
    '--no-first-run', '--disable-extensions', '--disable-background-networking',
    '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1', 'about:blank']),
];

// A page in a new browser context has its own storage, like a separate device. The browser connection that
// created the context stays open, because closing it could dispose of the context.
const keepOpen = [];
async function isolatedTarget(url) {
  const { webSocketDebuggerUrl } = await (await fetch(`http://127.0.0.1:${C}/json/version`)).json();
  const bws = new WebSocket(webSocketDebuggerUrl);
  await new Promise((r) => { bws.onopen = r; });
  keepOpen.push(bws);
  let id = 0; const waiting = new Map();
  bws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && waiting.has(m.id)) { waiting.get(m.id)(m.result); waiting.delete(m.id); } };
  const send = (method, params = {}) => new Promise((r) => { waiting.set(++id, r); bws.send(JSON.stringify({ id, method, params })); });
  const { browserContextId } = await send('Target.createBrowserContext');
  const { targetId } = await send('Target.createTarget', { url, browserContextId });
  return `ws://127.0.0.1:${C}/devtools/page/${targetId}`;
}

async function tab(url, { isolated = false } = {}) {
  const wsUrl = isolated ? await isolatedTarget(url)
    : (await (await fetch(`http://127.0.0.1:${C}/json/new?${url}`, { method: 'PUT' })).json()).webSocketDebuggerUrl;
  const ws = new WebSocket(wsUrl);
  await new Promise((r) => { ws.onopen = r; });
  let id = 0; const waiting = new Map(); const errors = [];
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.id) { waiting.get(m.id)(m.result); waiting.delete(m.id); }
    else if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.exception?.description);
  };
  const send = (method, params = {}) => new Promise((r) => { waiting.set(++id, r); ws.send(JSON.stringify({ id, method, params })); });
  await send('Runtime.enable');
  await sleep(2500);
  const ev = async (expression) => {
    const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    return r.exceptionDetails ? `EVAL ERROR ${r.exceptionDetails.exception?.description}` : r.result.value;
  };
  const until = async (expr, ms = 20000) => {
    const end = Date.now() + ms;
    while (Date.now() < end) { if ((await ev(expr)) === true) return true; await sleep(300); }
    return false;
  };
  const reload = async () => { await send('Page.reload'); await sleep(3500); };
  return { ev, until, reload, errors };
}

try {
  await sleep(3500);
  const d = await tab(`http://127.0.0.1:${D}/`);
  const phone = (id) => `document.querySelector('.phone[data-id="${id}"]')`;
  const notice = (text) => d.ev(`[...document.querySelectorAll('.notice')].find(b => b.innerText.includes(${JSON.stringify(text)})).click()`);
  const crash = (id) => d.ev(`${phone(id)}.querySelector('.btn-crash').click()`);
  const start = (id) => d.ev(`${phone(id)}.querySelector('.btn-start').click()`);
  const shows = (id, text) => d.until(`${phone(id)}.querySelector('.inbox').textContent.includes(${JSON.stringify(text)})`);
  const waits = (id, n) => d.until(`document.querySelector('.slot[data-id="${id}"] .count').textContent === '${n}waiting'`);
  const brokerButton = () => d.ev(`document.querySelector('#btn-broker').click()`);

  await d.ev(`document.querySelector('#btn-demo').click()`);
  ok('E01 dashboard: four subscribers online', await d.until(`document.querySelectorAll('.phone.is-online').length === 4`));
  await notice('Fire safety');
  ok('E02 dashboard: topic filtering', await shows('tenant-juan', 'Fire safety')
    && !(await d.ev(`${phone('tenant-jerome')}.textContent.includes('Fire safety')`)));
  await notice('Welcome to our new');
  ok('E03 dashboard: filtering, other property', await shows('tenant-jerome', 'Welcome'));
  await notice('faucet in shared CR: acknowledged');
  ok('E04 dashboard: fan-out', await shows('tenant-juan', 'acknowledged') && await shows('caretaker', 'acknowledged'));
  await crash('tenant-juan');
  ok('E05 dashboard: subscriber crash', await d.until(`${phone('tenant-juan')}.classList.contains('is-off')`));
  await sleep(1500);
  for (const text of ['water interruption on', 'rent is due on Oct 15', 'shared CR: in progress']) { await notice(text); await sleep(1000); }
  ok('E06 dashboard: three queued', await waits('tenant-juan', 3));
  await start('tenant-juan');
  ok('E07 dashboard: catch-up', await d.until(`${phone('tenant-juan')}.querySelectorAll('.chip-CATCH-UP').length === 3`));
  await crash('tenant-jerome'); await sleep(1500);
  await notice('rent is due on Oct 10'); await waits('tenant-jerome', 1);
  await brokerButton(); await sleep(2500);
  ok('E08 dashboard: broker crash', await d.until(`document.querySelector('#broker-state').textContent.includes('stopped')`));
  await brokerButton(); await sleep(6000); await start('tenant-jerome');
  ok('E09 dashboard: broker recovery', await shows('tenant-jerome', 'Oct 10'));
  await d.ev(`document.querySelector('[data-fault="crash-before-ack"]').click()`); await sleep(4000);
  await notice('Pest control this');
  await d.until(`${phone('tenant-juan')}.classList.contains('is-off')`); await start('tenant-juan');
  ok('E10 dashboard: duplicate ignored', await shows('tenant-juan', 'Duplicate ignored'));
  await d.ev(`document.querySelector('[data-fault="lost-puback"]').click()`); await sleep(7000);
  await notice('electricity bill');
  ok('E11 dashboard: lost PUBACK', await d.until(`document.querySelector('.desk-result').textContent.includes('recognised the duplicate')`));

  const reset = async () => {
    await d.ev(`window.confirm = () => true; document.querySelector('#btn-reset').click()`); await sleep(2500);
    await d.ev(`document.querySelector('#btn-demo').click()`);
    await d.until(`document.querySelectorAll('.phone.is-online').length === 4`); await sleep(12000);
  };
  await reset();
  children.push(run(py, ['-m', 'pubsub.webapp', '--port', W, '--broker-port', B, '--no-broker', '--no-browser']));
  await sleep(3000);
  // The website opens on its sign-in page; ?role= only picks which role's tab is shown first.
  const submitLogin = (w, user, pass) => w.ev(`(() => { const f = document.querySelector('form[data-form="login"]');
    f.querySelector('[name=username]').value = ${JSON.stringify(user)};
    f.querySelector('[name=password]').value = ${JSON.stringify(pass)};
    f.requestSubmit(); })()`);
  const signIn = async (w, user, pass) => {
    await w.until(`!!document.querySelector('form[data-form="login"]')`);
    await submitLogin(w, user, pass);
    return w.until(`!document.querySelector('form[data-form="login"]') && !document.querySelector('#shell').hidden`);
  };
  const signedOut = `!!document.querySelector('form[data-form="login"]') && document.querySelector('#shell').hidden && !Broker.source`;
  const m = await tab(`http://127.0.0.1:${W}/?role=manager`);
  await signIn(m, 'manager', 'manager123');
  await sleep(4000);
  // The website starts empty: Juan joins Casa Jaro with the demo invite code and the manager approves him.
  const juanRequest = `S.joinRequests.find((j) => j.userId === 'u-juan' && j.status === 'Pending')`;
  const joinAndApproveJuan = async (tenantTab, managerTab) => {
    await tenantTab.ev(`F.join({ code: 'CJD-7K2Q', note: '' })`);
    if (await managerTab.until(`!!${juanRequest}`)) {
      await managerTab.ev(`F.approve({ room: 'r101', start: '2026-10-01', bill: 'on' }, { dataset: { id: ${juanRequest}.id } })`);
    }
    return tenantTab.until(`!!myTenancy()`);
  };
  const t = await tab(`http://127.0.0.1:${W}/?role=tenant`);
  await signIn(t, 'juan', 'juan123'); await sleep(3000);
  await joinAndApproveJuan(t, m); await sleep(2000);

  const announceThenViewAsJuan = async (title) => {
    await m.ev(`S.role = 'manager'; commit()`); await sleep(4000);
    await m.ev(`F.announce({ prop: 'p1', title: '${title}', body: 'x' })`);
    const onPhone = await shows('tenant-juan', title);
    await sleep(1500);
    await m.ev(`S.role = 'tenant'; S.persona = 'u-juan'; commit()`);
    const inBell = await m.until(`S.notifs.some(n => n.role === 'tenant' && n.text.includes('${title}'))`, 12000);
    return onPhone && inBell;
  };
  ok('E12 website: announcement reaches phone and bell', await announceThenViewAsJuan('Water tank cleaning Friday'));
  await reset();
  ok('E13 website: alert after a data reset is not taken for a duplicate', await announceThenViewAsJuan('Second rehearsal notice'));

  await m.ev(`S.role = 'manager'; commit()`); await sleep(3000);
  await t.reload(); await sleep(1000);  // Juan's window (signed in since the start) reconnects after the broker reset
  const hasAlert = `S.notifs.some(n => n.role === 'tenant' && n.text.includes('Two window notice'))`;
  await m.ev(`F.announce({ prop: 'p1', title: 'Two window notice', body: 'x' })`);
  ok('E14 website: live delivery between two windows', await t.until(hasAlert));
  await sleep(1500); await m.ev(`commit()`); await sleep(500); await t.reload();
  ok('E15 website: alert survives a save from the other window', await t.until(hasAlert, 6000));

  const visibleTo = (role, text) => `Chat.list('u-juan').some(x => x.text === '${text}' && Chat.visible(x, '${role}') && !x.failed && x.deliveredTo?.['${role}'])`;
  await t.ev(`Chat.send('u-juan', 'Is the Wi-Fi down?'); commit()`);
  const live = await m.until(visibleTo('manager', 'Is the Wi-Fi down?'));
  await sleep(2000); await m.reload(); await t.reload();
  ok('E16 website: chat survives reload in both windows', live
    && await m.until(visibleTo('manager', 'Is the Wi-Fi down?'), 6000) && await t.until(visibleTo('tenant', 'Is the Wi-Fi down?'), 6000));

  await brokerButton(); await sleep(3000);
  await m.ev(`Chat.send('u-juan', 'Sent while the broker was down'); commit()`);
  const marked = await m.until(`Chat.list('u-juan').some(x => x.text === 'Sent while the broker was down' && x.failed === true)`, 25000);
  await brokerButton();
  ok('E17 website: chat sent during a broker outage is re-sent', marked && await t.until(visibleTo('tenant', 'Sent while the broker was down'), 45000));

  await sleep(3000);
  await t.ev(`Broker.publish('manager.m1', { kind: 'announcement', id: '"><img src=x onerror="window.pwned=1">', prop: 'p1', title: 'Forged notice', date: '2026-10-14' })`);
  await m.until(`S.notifs.some(n => n.text.includes('Forged notice'))`);
  await m.ev(`go('announcements')`); await sleep(2000);
  ok('E18 website: broker payload cannot inject HTML', await m.ev(`!window.pwned && !document.querySelector('img[src="x"]')`) === true);

  const l = await tab(`http://127.0.0.1:${W}/?role=admin`);
  ok('E20 website: a visitor who has not signed in sees only the sign-in page', await l.until(signedOut));
  await submitLogin(l, 'admin', 'wrong-password'); await sleep(500);
  const wrongPassword = await l.until(`document.querySelector('.login-error')?.textContent.trim().length > 0`) && await l.ev(signedOut);
  await submitLogin(l, 'juan', 'juan123'); await sleep(500);  // a tenant account on the Admin tab
  ok('E21 website: a wrong password, or another role\'s account, is refused', wrongPassword === true && await l.ev(signedOut) === true);
  ok('E22 website: admin signs in to the admin view, with no way to switch role',
    await signIn(l, 'admin', 'admin123') && await l.ev(`S.role === 'admin' && !document.querySelector('.role-switch') && !document.querySelector('[data-act="role"]')`) === true);
  await l.ev(`document.querySelector('[data-act="toggle-profile"]').click()`); await sleep(300);
  await l.ev(`document.querySelector('[data-act="logout"]').click()`);
  ok('E23 website: signing out returns to the sign-in page and closes the live connection', await l.until(signedOut));
  await l.ev(`document.querySelector('[data-act="login-role"][data-role="tenant"]').click()`); await sleep(300);
  ok('E24 website: each tenant signs in as themselves, and other windows keep their own user',
    await signIn(l, 'ana', 'ana123') && await l.ev(`S.role === 'tenant' && S.persona === 'u-ana' && Broker.current().client === 'web-tenant-u-ana'`) === true
    && await t.ev(`S.role === 'tenant' && S.persona === 'u-juan'`) === true);

  // Records across devices: the manager, Juan and Ana each in a separate browser profile (separate storage).
  await t.ev(`A.logout()`); await sleep(1000);  // Juan's only other open window: his queued updates must wait for him (E26)
  const stub = `{ querySelector: () => ({ files: [] }) }`;  // stands in for a form with an empty file input
  const mgr = await tab(`http://127.0.0.1:${W}/?role=manager`, { isolated: true });
  await signIn(mgr, 'manager', 'manager123');
  const juan = await tab(`http://127.0.0.1:${W}/?role=tenant`, { isolated: true });
  await signIn(juan, 'juan', 'juan123');
  const ana = await tab(`http://127.0.0.1:${W}/?role=tenant`, { isolated: true });
  await signIn(ana, 'ana', 'ana123');
  await sleep(4000);
  await joinAndApproveJuan(juan, mgr);  // these profiles start empty too: Juan joins from his device, approved from the manager's

  await juan.ev(`F.ticket({ category: 'Plumbing', title: 'Shower drain blocked', desc: 'E25', priority: 'High' }, ${stub})`);
  const ticketOnMgr = await mgr.until(`S.tickets.some((k) => k.title === 'Shower drain blocked')`);
  await mgr.ev(`go('maintenance')`); await sleep(1500);
  ok('E25 devices: a tenant\'s repair request reaches the manager\'s Maintenance page',
    ticketOnMgr && await mgr.ev(`document.querySelector('#content').textContent.includes('Shower drain blocked')`) === true);

  const ticketId = await mgr.ev(`S.tickets.find((k) => k.title === 'Shower drain blocked')?.id ?? null`);
  await juan.ev(`A.logout()`); await sleep(1500);
  if (ticketId) await mgr.ev(`F['ticket-update']({ status: 'In progress', note: 'Plumber tomorrow' }, { dataset: { id: ${JSON.stringify(ticketId)} } })`);
  await sleep(2000);
  await signIn(juan, 'juan', 'juan123');
  ok('E26 devices: the manager\'s ticket update reaches the tenant, even after being signed out',
    !!ticketId && await juan.until(`S.tickets.find((k) => k.id === ${JSON.stringify(ticketId)})?.status === 'In progress'`));

  const bill = await juan.ev(`(() => { const i = S.invoices.find((x) => x.tenant === myTenancy()?.id && x.status === 'Unpaid'); return i ? { id: i.id, amount: invBalance(i) } : null; })()`);
  const billIs = (cond) => `(() => { const i = S.invoices.find((x) => x.id === ${JSON.stringify(bill?.id)}); return !!i && ${cond}; })()`;
  if (bill) await juan.ev(`F.pay({ inv: ${JSON.stringify(bill.id)}, amount: '${bill.amount}', method: 'GCash', ref: 'GC-E27', date: '2026-10-14' }, ${stub})`);
  const proofOnMgr = !!bill && await mgr.until(billIs(`i.status === 'Submitted for review' && i.payments.some((p) => p.ref === 'GC-E27')`));
  if (proofOnMgr) await mgr.ev(`A['decide-payment']({ id: ${JSON.stringify(bill.id)}, d: 'verify' })`);
  ok('E27 devices: a payment proof reaches the manager and the verification reaches the tenant',
    proofOnMgr && await juan.until(billIs(`i.status === 'Paid' && i.payments.some((p) => p.ref === 'GC-E27' && p.verified === true)`)));

  await ana.ev(`F.join({ code: 'CJD-7K2Q', note: 'E28' })`);
  const pending = `S.joinRequests.find((j) => j.userId === 'u-ana' && j.status === 'Pending')`;
  const reqId = await mgr.until(`!!${pending}`) ? await mgr.ev(`${pending}.id`) : null;
  if (reqId) await mgr.ev(`F.approve({ room: S.rooms.find((r) => r.prop === 'p1' && roomFree(r) > 0).id, start: '2026-10-14', bill: 'on' }, { dataset: { id: ${JSON.stringify(reqId)} } })`);
  ok('E28 devices: a join request reaches the manager and the approval makes the tenant a member',
    !!reqId && await ana.until(`myTenancy()?.prop === 'p1' && S.invoices.some((i) => i.tenant === myTenancy().id)`));

  const roomsBefore = await mgr.ev(`S.rooms.length`);
  await juan.ev(`Broker.publish('manager.m1', { kind: 'notification', title: 'Forged record', text: 'Forged record',
    records: { tickets: [{ id: 'forged-k', tenant: 'nobody', prop: 'p1', title: 'x', status: 5, history: 'no' },
                        { id: 'dangling-k', tenant: 'nobody', prop: 'p1', category: 'Other', title: 'Dangling', priority: 'Low',
                          status: 'Submitted', created: '2026-10-14', history: [] }],
               invoices: [{ id: 'bad id!', tenant: 't1' }], rooms: [{ id: 'r-x' }] } })`);
  const forgedAlert = await mgr.until(`S.notifs.some((n) => n.text === 'Forged record')`);
  ok('E29 devices: a forged record is ignored but its alert still shows', forgedAlert
    && await mgr.ev(`!S.tickets.some((k) => k.id === 'forged-k' || k.id === 'dangling-k') && !S.invoices.some((i) => i.id === 'bad id!')
      && S.rooms.length === ${roomsBefore}`) === true);

  // E30: the manager generates November's bills and records ₱300 cash on Juan's, which reaches Juan's device with the
  // payment. His device then goes back to an old copy of that bill (as after "Reset Demo Data") and he sends a proof.
  // The manager's cash payment must survive.
  await mgr.ev(`A['gen-bills']()`);
  const nov = await mgr.ev(`S.invoices.find((i) => i.tenant === S.tenants.find((x) => x.userId === 'u-juan')?.id && i.label === 'Rent – Nov 2026')?.id ?? null`);
  const sep = `S.invoices.find((x) => x.id === ${JSON.stringify(nov)})`;
  if (nov) await mgr.ev(`F.record({ inv: ${JSON.stringify(nov)}, amount: '300', method: 'Cash', ref: '', date: '2026-10-14' })`);
  const cashOnJuan = !!nov && await juan.until(`${sep}?.paid === 300`);
  await juan.ev(`(() => { const i = ${sep}; if (i) { i.payments = []; i.paid = 0; i.status = 'Unpaid'; save(); } })()`);
  if (nov) await juan.ev(`F.pay({ inv: ${JSON.stringify(nov)}, amount: '2500', method: 'GCash', ref: 'GC-E30', date: '2026-10-14' }, ${stub})`);
  ok('E30 devices: a proof sent from an old copy of a bill keeps the manager\'s newer payments', cashOnJuan
    && await mgr.until(`!!${sep}?.payments.some((p) => p.ref === 'GC-E30')`)
    && await mgr.ev(`(() => { const i = ${sep}; return i.paid === 300 && i.payments.some((p) => p.method === 'Cash' && p.verified === true)
      && i.status === 'Submitted for review'; })()`) === true);

  // E31: the manager adds a room that only the manager's device has, and approves Ana into it. (Ana's request is put
  // straight into the manager's data: the seed's only invite for Molo Bedspace Hub has expired.)
  await mgr.ev(`F.room({ name: 'Room 301', beds: '2', price: '3000', reserved: '0', unavailable: '0' }, { dataset: { prop: 'p2' } })`);
  const newRoom = await mgr.ev(`S.rooms.find((r) => r.name === 'Room 301' && r.prop === 'p2')?.id ?? null`);
  await mgr.ev(`S.joinRequests.unshift({ id: 'j-e31', userId: 'u-ana', name: 'Ana Robles', email: 'ana.robles@email.com', prop: 'p2',
    via: 'Invite code', at: '2026-10-14', note: '', status: 'Pending' }); save()`);
  if (newRoom) await mgr.ev(`F.approve({ room: ${JSON.stringify(newRoom)}, start: '2026-10-14', bill: 'on' }, { dataset: { id: 'j-e31' } })`);
  ok('E31 devices: an approval into a room added on the manager\'s device reaches the tenant whole', !!newRoom
    && await ana.until(`S.rooms.some((r) => r.id === ${JSON.stringify(newRoom)})
      && S.tenants.some((t) => t.userId === 'u-ana' && t.room === ${JSON.stringify(newRoom)} && S.invoices.some((i) => i.tenant === t.id))`));

  // E32: a fresh profile starts empty (houses, rooms and the invite code kept), and every page of every role works.
  const fresh = await tab(`http://127.0.0.1:${W}/?role=manager`, { isolated: true });
  const startsEmpty = await fresh.ev(`S.tenants.length === 0 && S.invoices.length === 0 && S.tickets.length === 0
    && S.joinRequests.length === 0 && S.announcements.length === 0 && S.notifs.length === 0 && S.audit.length === 0
    && S.properties.length === 3 && S.rooms.length === 8 && S.invites.some((i) => i.code === 'CJD-7K2Q' && i.status === 'Active')`);
  const visitAll = async (role, user, pass) => {
    await fresh.ev(`document.querySelector('[data-act="login-role"][data-role="${role}"]').click()`); await sleep(300);
    if (!await signIn(fresh, user, pass)) return false;
    const views = await fresh.ev(`[...NAV[S.role].menu, ...NAV[S.role].general].map((x) => x[0])`);
    for (const v of views) {
      await fresh.ev(`go('${v}')`); await sleep(350);
      // No NaN/undefined, and no leftover sample activity written into the page code.
      const fine = await fresh.ev(`document.querySelector('#content').children.length > 0
        && !/\\bNaN\\b|\\bundefined\\b|Fire safety inspection|Leaking faucet|Carla Bautista|Rhea Castillo|Andrea Villanueva/
          .test(document.querySelector('#content').innerText)`);
      if (fine !== true) { console.log(`       ${role} page "${v}" is blank or shows NaN, undefined or sample text (${fine})`); return false; }
    }
    await fresh.ev(`A.logout()`); await sleep(300);
    return views.length > 0;
  };
  const pagesWork = await visitAll('manager', 'manager', 'manager123') && await visitAll('tenant', 'juan', 'juan123')
    && await visitAll('admin', 'admin', 'admin123');
  ok('E32 website: starts empty but keeps the houses, rooms and invite code, and every page of every role works',
    startsEmpty === true && pagesWork && fresh.errors.length === 0);
  fresh.errors.forEach((e) => console.log(`       ${e}`));

  // E33: one shared browser (one laptop). Ana asks to join and logs out; the manager approves her, then renames the
  // room, changes its price and moves her out before she signs in again. The approval that waited at the broker for
  // her must not undo those changes. (Ana's other windows are signed out first, so the approval really waits.)
  await ana.ev(`A.logout()`); await l.ev(`A.logout()`); await sleep(1000);
  const shared = await tab(`http://127.0.0.1:${W}/?role=tenant`, { isolated: true });
  await signIn(shared, 'ana', 'ana123'); await sleep(2000);
  await shared.ev(`F.join({ code: 'CJD-7K2Q', note: '' })`); await sleep(500);
  await shared.ev(`A.logout()`); await sleep(500);
  await shared.ev(`document.querySelector('[data-act="login-role"][data-role="manager"]').click()`); await sleep(200);
  await signIn(shared, 'manager', 'manager123'); await sleep(2000);
  const anaReq = `S.joinRequests.find((j) => j.userId === 'u-ana' && j.status === 'Pending')`;
  const approvedAna = await shared.until(`!!${anaReq}`) && await shared.ev(`(() => {
    F.approve({ room: 'r103', start: '2026-10-14', bill: 'on' }, { dataset: { id: ${anaReq}.id } });
    const t = S.tenants.find((x) => x.userId === 'u-ana' && x.room === 'r103' && x.status === 'active');
    F.room({ name: 'Room 103 (renovated)', beds: '2', price: '4200', reserved: '0', unavailable: '0' }, { dataset: { id: 'r103', prop: 'p1' } });
    A['do-move-out']({ id: t.id });
    return !!t; })()`);
  await sleep(1500);
  await shared.ev(`A.logout()`); await sleep(500);
  await shared.ev(`document.querySelector('[data-act="login-role"][data-role="tenant"]').click()`); await sleep(200);
  await signIn(shared, 'ana', 'ana123');
  const lateApproval = await shared.until(`S.notifs.some((n) => n.userId === 'u-ana' && n.text.includes('was approved'))`);
  await sleep(1000);
  ok('E33 devices: an approval that waited at the broker does not undo the manager\'s later changes in a shared browser',
    approvedAna === true && lateApproval && await shared.ev(`(() => { const r = S.rooms.find((x) => x.id === 'r103');
      return r.name === 'Room 103 (renovated)' && r.price === 4200
        && !S.tenants.some((x) => x.userId === 'u-ana' && x.room === 'r103' && x.status === 'active'); })()`) === true);

  // E34: the broker keeps a user's subscriptions after a reset or move-out, so Juan's account is still subscribed to Casa
  // Jaro announcements. In a browser where Juan has no property, an announcement must be ignored.
  const outsider = await tab(`http://127.0.0.1:${W}/?role=tenant`, { isolated: true });
  await signIn(outsider, 'juan', 'juan123'); await sleep(3000);
  await mgr.ev(`F.announce({ prop: 'p1', title: 'E34 notice', body: 'x' })`);
  const memberGotIt = await juan.until(`S.announcements.some((a) => a.title === 'E34 notice')`);
  await sleep(2500);
  ok('E34 website: a tenant with no property ignores announcements the broker still sends them', memberGotIt
    && await outsider.ev(`!S.announcements.some((a) => a.title === 'E34 notice') && !S.notifs.some((n) => n.text.includes('E34 notice'))`) === true);

  const errors = [...d.errors, ...m.errors, ...t.errors, ...l.errors, ...mgr.errors, ...juan.errors, ...ana.errors, ...fresh.errors,
    ...shared.errors, ...outsider.errors];
  ok('E19 no uncaught page errors', errors.length === 0);
  errors.forEach((e) => console.log(`       ${e}`));
} catch (e) {
  failed += 1;
  console.log('[FAIL] harness error', e);
} finally {
  await fetch(`http://127.0.0.1:${D}/api/reset`, { method: 'POST' }).catch(() => {});
  try {
    const v = await (await fetch(`http://127.0.0.1:${C}/json/version`)).json();
    const ws = new WebSocket(v.webSocketDebuggerUrl);
    await new Promise((r) => { ws.onopen = r; });
    ws.send(JSON.stringify({ id: 1, method: 'Browser.close' }));
    await sleep(1000);
  } catch { /* the browser is already closed */ }
  children.forEach((child) => child.kill());
  console.log(`${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
}
