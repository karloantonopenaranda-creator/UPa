'use strict';
// Marketplace UI layered over the original prototype's records and broker.
// Internal "manager" ids remain stable; the public role is Property owner.
(() => {
  const ROOT='assets/marketplace/';
  const img=(file)=>ROOT+file;
  const isOwner=()=>S.role==='manager';
  const myId=()=>isOwner()?'m1':S.persona;
  const stamp=()=>new Date().toISOString();
  const today=()=>new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Manila',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
  const labelTime=(at)=>new Date(at).toLocaleString('en-PH',{timeZone:'Asia/Manila',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
  const chatUI={account:null,messages:false,thread:null,headLimit:null};
  const profileUI={account:null,target:null,tab:'posts'};
  const draftKey=thread=>myId()+':'+thread;
  const chatHeadLimit=()=>matchMedia('(max-width:640px), (max-height:600px)').matches?3:5;
  const examples=[
    {id:'demo-ana',name:'Ana R.',age:23,job:'Architecture student',area:'Jaro',budget:4000,photo:'ana.jpg',bio:'Looking for a cozy place near Jaro. I like early coffee, a tidy kitchen, and a weekend grocery run with a housemate.',habits:['Early bird','Tidy spaces','Non-smoker'],published:true,demo:true},
    {id:'demo-miguel',name:'Miguel R.',age:25,job:'Graphic designer',area:'Mandurriao',budget:5000,photo:'miguel.jpg',bio:'Remote designer looking for a relaxed home with good Wi-Fi. Happy to cook together, equally happy to give you your space.',habits:['Works from home','Enjoys cooking','Quiet evenings'],published:true,demo:true},
    {id:'demo-james',name:'James D.',age:24,job:'Young professional',area:'Molo',budget:4500,photo:'james.jpg',bio:'New to Iloilo and looking for a place to settle in. I keep shared spaces clean and enjoy a good conversation.',habits:['Non-smoker','Weekend runner','Tidy spaces'],published:true,demo:true},
  ];
  const defaultFields=()=>[
    {id:'occupation',label:'Occupation or school',type:'text',required:true},
    {id:'movein',label:'Preferred move-in date',type:'date',required:true},
    {id:'stay',label:'Expected length of stay',type:'text',required:true},
  ];
  function ensure(){
    S.community ||= {};
    const c=S.community;
    for(const key of ['profiles','saved','votes','read','ads','requirements','views','dismissedHeads'])c[key] ||= {};
    c.posts ||= [];
    S.marketThreads ||= []; S.marketMessages ||= []; S.marketApplications ||= [];
    S.ui.socialDraft ||= {}; S.ui.socialFeed ||= 'all';
    for(const p of S.personas){
      c.profiles[p.id] ||= {id:p.id,name:p.name,job:'',area:'Jaro',budget:4500,age:21,bio:'',habits:[],published:false};
    }
    c.profiles.m1 ||= {id:'m1',name:S.manager.name,role:'manager',job:'',area:'',bio:'',habits:[],published:false};
    // Bring existing owner/tenant conversations into the new inbox without deleting originals.
    for(const [tenant,list] of Object.entries(S.chats||{})){
      if(!S.personas.some(p=>p.id===tenant))continue;
      const pair=['m1',tenant].sort(),threadId=pair.join('--');
      for(const old of list){
        const id='legacy-'+old.id;
        if(S.marketMessages.some(m=>m.id===id)||!Chat.visible(old,S.role))continue;
        if(!S.marketThreads.some(t=>t.id===threadId))S.marketThreads.push({id:threadId,a:pair[0],b:pair[1],kind:'property',context:'Existing property conversation'});
        const parsed=new Date(String(old.at).replace(' ','T')+':00+08:00');
        S.marketMessages.push({id,thread:threadId,from:old.from==='manager'?'m1':tenant,name:old.name,text:old.text,at:Number.isNaN(parsed.getTime())?stamp():parsed.toISOString()});
      }
    }
  }
  const profile=(id)=>S.community.profiles[id]||examples.find(p=>p.id===id);
  const displayName=(id)=>id==='m1'?S.manager.name:profile(id)?.name||S.marketThreads.find(t=>t.a===id||t.b===id)?.names?.[id]||'Property owner';
  const userProfile=()=>profile(myId());
  const profiles=()=>[...Object.values(S.community.profiles).filter(p=>p.id!=='m1'&&p.published),...examples];
  const requirements=(id)=>S.community.requirements[id]||defaultFields();
  const validUser=(id)=>id==='m1'||S.personas.some(p=>p.id===id);
  const canReadThread=(t)=>t&&(t.a===myId()||t.b===myId());
  const threads=()=>S.marketThreads.filter(canReadThread);
  const otherId=(thread)=>thread.a===myId()?thread.b:thread.a;
  const messages=(id)=>S.marketMessages.filter(m=>m.thread===id).sort((a,b)=>a.at.localeCompare(b.at));
  const threadUnread=t=>messages(t.id).filter(m=>m.from!==myId()&&m.at>(S.community.read[myId()+':'+t.id]||'')).length;
  function unread(){return threads().reduce((total,t)=>total+threadUnread(t),0);}
  const recentThreads=()=>threads().slice().sort((a,b)=>(messages(b.id).at(-1)?.at||'').localeCompare(messages(a.id).at(-1)?.at||''));
  const threadName=t=>t.names?.[otherId(t)]||displayName(otherId(t));
  const threadAvatar=t=>profileAvatar(profile(otherId(t))||{name:threadName(t)});
  const countBadge=count=>count?`<span class="social-count" aria-label="${count} unread">${count>99?'99+':count}</span>`:'';
  function markChatRead(thread){
    if(!thread||!threadUnread(thread))return;
    S.community.read[myId()+':'+thread.id]=[stamp(),messages(thread.id).at(-1)?.at||''].sort().at(-1);save();
  }
  const safeProfileImage=value=>typeof value==='string'&&/^data:image\/(?:jpeg|png|webp);base64,[A-Za-z0-9+/=]+$/.test(value)?value:'';
  const profilePhoto=p=>safeProfileImage(p?.avatarData)||(p?.photo?img(p.photo):'');
  function profileAvatar(p,size=''){const src=profilePhoto(p);return src?`<span class="avatar ${size}"><img src="${esc(src)}" alt=""></span>`:avatar(p?.name||'UPa',size);}
  const tags=(list)=>`<div class="tags">${list.map(t=>`<span class="tag">${esc(t)}</span>`).join('')}</div>`;
  const pill=(text,ic='')=>`<span class="pill">${ic?icon(ic):''}${esc(text)}</span>`;
  const btn=(label,act,data='',kind='primary')=>`<button class="button ${kind}" data-act="${act}" ${data}>${label}</button>`;
  const head=(title,sub,action='')=>`<div class="page-title"><div><h2>${esc(title)}</h2><p>${esc(sub)}</p></div>${action}</div>`;
  const whoLine=(name,note,photo='')=>`<div class="person-line">${photo?profileAvatar({name,photo}):avatar(name)}<div><strong>${esc(name)}</strong><small>${esc(note)}</small></div></div>`;
  Object.assign(ICONS,{
    heart:'<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z"/>',
    bookmark:'<path d="M6 3h12v18l-6-4-6 4Z"/>',
    leaf:'<path d="M20 3C7 1 1 11 6 17c6 6 16 0 14-14ZM4 21 16 8"/>',
    rewind:'<path d="M4 10a8 8 0 1 1 2 9M4 4v6h6"/>',
    chevron:'<path d="m8 10 4 4 4-4"/>',
    image:'<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8" cy="8" r="2"/><path d="m3 17 5-5 4 4 4-5 5 6"/>',
    globe:'<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a17 17 0 0 1 0 18 17 17 0 0 1 0-18Z"/>',
    camera:'<path d="M14 4h-4L8 7H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-4Z"/><circle cx="12" cy="13" r="4"/>',
    edit:'<path d="m16 3 5 5-12 12-6 1 1-6L16 3ZM13 6l5 5"/>',
  });

  // Account and route migration is additive; existing operational records stay intact.
  const adminAccount=ACCOUNTS.findIndex(a=>a.role==='admin');
  if(adminAccount>=0)ACCOUNTS.splice(adminAccount,1);
  if(Auth.user?.role==='admin')Auth.signOut();
  if(!['manager','tenant'].includes(Auth.loginRole))Auth.loginRole='tenant';
  ROLE_NAMES.manager='Property owner';
  const ownerTools=[['properties','Rooms & availability','bed'],['tenants','Tenant records','users'],['rent','Rent & payments','wallet'],['utilities','Utilities','zap'],['maintenance','Maintenance','wrench'],['announcements','Announcements','megaphone']];
  const tenantTools=[['mydorm','My home','home'],['bills','Bills & payments','wallet'],['tickets','Maintenance','wrench'],['notices','Announcements','megaphone'],['join','Join with a code','qr']];
  NAV.manager.menu=[['dashboard','Overview','grid'],['feed','Community','store'],['profile','My profile','users'],['advertisements','My advertisements','building'],['findtenants','Find tenants','users'],['applications','Applications','file',()=>pendingJoins().length],['messages','Messages','msg',unread]];
  NAV.manager.general=[...ownerTools,['explore','Find rentals','search']];
  NAV.tenant.menu=[['feed','Community','store'],['explore','Find rentals','search'],['roommates','Roommates','users'],['saved','Saved places','bookmark'],['applications','Applications','file'],['messages','Messages','msg',unread],['profile','My profile','users']];
  NAV.tenant.general=tenantTools;
  DEFAULT_VIEW.tenant='feed';

  const oldListings=publicListings;
  publicListings=function(){
    const original=oldListings();
    const extra=myProps().filter(p=>p.listed&&!original.some(l=>l.id===p.id)).map(p=>{
      const rooms=S.rooms.filter(r=>r.prop===p.id);
      return {id:p.id,prop:p.id,name:p.name,type:p.type,area:p.area,price:rooms.length?Math.min(...rooms.map(r=>r.price)):0,avail:beds(p.id).avail,manager:S.manager.name,deposit:p.deposit,amenities:p.amenities,rules:p.rules,rooms};
    });
    return [...original,...extra].map((l,index)=>({...l,...S.community.ads[l.id],owner:propById(l.id)?.owner||S.community.ads[l.id]?.owner||'external-'+l.id,photo:S.community.ads[l.id]?.photo||['living-room.jpg','studio.jpg','bedroom.jpg'][index%3],verified:propById(l.id)?.verification==='Verified'}));
  };
  function findListing(id){
    const live=publicListings().find(l=>l.id===id);if(live)return live;
    const p=propById(id);if(!p)return null;
    const rs=S.rooms.filter(r=>r.prop===id);return {...p,...S.community.ads[id],id,owner:p.owner,manager:S.manager.name,price:rs.length?Math.min(...rs.map(r=>r.price)):0,avail:beds(id).avail,photo:S.community.ads[id]?.photo||'living-room.jpg'};
  }
  function menuHtml(view,mobile=false){return NAV[S.role].menu.map(([id,label,ic,badge])=>`<${mobile?'span':'li'}><button class="${mobile?'nav-item ':''}${view===id?'active':''}" data-act="nav" data-to="${id}" ${view===id?'aria-current="page"':''}>${icon(ic)}<span>${label}</span>${badge?.()?`<span class="${mobile?'count':'badge'}">${badge()}</span>`:''}</button></${mobile?'span':'li'}>`).join('');}
  const brand=()=>{const tag=Auth.account()?'a':'div';return `<${tag} class="brand upa-brand" ${tag==='a'?'href="#feed" aria-label="UPa community"':''}><img class="upa-wordmark" src="assets/brand/upa-wordmark.png" alt="UPa" width="1961" height="802"></${tag}>`;};
  sidebarHtml=function(view){return `${brand()}<nav aria-label="Main navigation"><ul class="nav">${menuHtml(view)}</ul><details class="sidebar-tools" ${NAV[S.role].general.some(([id])=>id===view)?'open':''}><summary>${isOwner()?'Property tools':'My home tools'}</summary><ul class="nav">${NAV[S.role].general.map(([id,label,ic])=>`<li><button class="${id===view?'active':''}" data-act="nav" data-to="${id}">${icon(ic)}${label}</button></li>`).join('')}<li><button data-act="help">${icon('help')}Prototype guide</button></li></ul></details></nav><div class="sidebar-tip">${icon('leaf')}<h4>${isOwner()?'Your next tenant is here.':'Make yourself at home.'}</h4><p>${isOwner()?'Meet people who are looking for a place like yours.':'Tell owners about yourself and the home you are looking for.'}</p><button class="quiet" data-act="nav" data-to="${isOwner()?'findtenants':'profile'}">${isOwner()?'Explore tenant profiles':'Build your public profile'}</button></div><button class="sidebar-account" data-act="toggle-profile">${profileAvatar(userProfile())}<div><strong>${esc(actorName())}</strong><small>${ROLE_NAMES[S.role]}</small></div>${icon('chevron')}</button>`;};
  topbarHtml=function(){
    const notifications=myNotifs(),count=notifications.filter(n=>!n.read).length;
    let dropdown='';
    if(chatUI.messages)dropdown=messageDropdown();
    else if(S.ui.bell)dropdown=`<div class="dropdown" id="header-notifications"><h4>Notifications <button class="quiet" data-act="read-all">Mark all read</button></h4><div class="scroll">${notifications.length?notifications.slice(0,15).map(n=>`<button class="item ${n.read?'':'unread'}" data-act="open-notif" data-id="${esc(n.id)}">${icon('bell')}<span>${esc(n.text)}<small>${esc(n.at)}</small></span></button>`).join(''):empty('bell','All caught up','Your updates will appear here.')}</div></div>`;
    else if(S.ui.profileMenu)dropdown=`<div class="dropdown" id="header-account"><h4>${esc(actorName())}</h4><button class="item" data-act="nav" data-to="profile">${profileAvatar(userProfile())}<span>My profile</span></button>${isOwner()?`<button class="item" data-act="nav" data-to="advertisements">${icon('building')}Your advertisements</button>`:''}<button class="item" data-act="help">${icon('help')}Prototype guide</button><button class="item" data-act="logout">${icon('logout')}Log out</button></div>`;
    return `${brand()}<span class="app-location">${icon('pin')}Iloilo City</span><label class="search">${icon('search')}<input id="global-search" aria-label="Search rental properties" placeholder="Search places, neighborhoods…"></label><div class="topbar-actions social-header-actions"><button class="icon-btn social-action ${chatUI.messages?'is-open':''}" data-act="toggle-messages" aria-label="Messages${unread()?`, ${unread()} unread`:''}" aria-expanded="${chatUI.messages}" aria-controls="header-messages">${icon('msg')}${countBadge(unread())}</button><button class="icon-btn social-action ${S.ui.bell?'is-open':''}" data-act="toggle-bell" aria-label="Notifications${count?`, ${count} unread`:''}" aria-expanded="${!!S.ui.bell}" aria-controls="header-notifications">${icon('bell')}${countBadge(count)}</button><button class="profile social-account ${S.ui.profileMenu?'is-open':''}" data-act="toggle-profile" aria-label="Your account" aria-expanded="${!!S.ui.profileMenu}" aria-controls="header-account">${profileAvatar(userProfile())}<span class="account-chevron">${icon('chevron')}</span></button></div>${dropdown}`;
  };
  loginHtml=function(){const role=Auth.loginRole;return `<main class="login-layout"><section class="login-story">${brand()}<h2>Find a place.<br>Find your people.</h2><p>A space that fits your life. A roommate who shares your rhythm. Your next chapter starts with UPa.</p><img src="${img('living-room.jpg')}" alt="Sample furnished living room"></section><section class="login-card"><div><h1>Welcome to UPa</h1><p class="login-lede" style="margin-top:8px">Sign in to your community.</p></div><div class="role-switch login-roles" role="group" aria-label="Account type">${['tenant','manager'].map(r=>`<button type="button" data-act="login-role" data-role="${r}" class="${r===role?'on':''}" aria-pressed="${r===role}">${ROLE_NAMES[r]}</button>`).join('')}</div><form class="stack" data-form="login"><label class="field"><span>Username</span><input class="input" name="username" autocomplete="username" value="${esc(Auth.typed)}" required></label><label class="field"><span>Password</span><input class="input" type="password" name="password" autocomplete="current-password" required></label>${Auth.error?`<p class="login-error" role="alert">${esc(Auth.error)}</p>`:''}<button class="btn primary" type="submit">Sign in</button></form><div class="demo-account"><strong>Explore a demo account</strong><label class="field"><span>Demo account</span><select class="input" id="demo-account-select">${ACCOUNTS.filter(a=>a.role===role).map(a=>{const p=S.personas.find(p=>p.id===a.persona),t=S.tenants.find(t=>t.userId===a.persona&&t.status==='active');const label=a.role==='manager'?S.manager.name:p?.name||a.username;return `<option value="${esc(a.username)}">${esc(label)} · ${esc(a.role==='manager'?'Property owner':t?propById(t.prop)?.name||'Resident':'House hunter')}</option>`;}).join('')}</select></label><button class="btn sm outline" data-act="fill-demo" data-role="${role}">Fill demo credentials</button><p class="site-note">Passwords follow the demo username + 123.</p></div><p class="login-note">Browser-based prototype. Use sample details only. Stock photos illustrate sample listings and profiles.</p></section></main>`;};

  function savedIds(){return S.community.saved[myId()]||[];}
  function listingCard(l){return `<article class="rental-card"><div class="rental-photo"><img src="${img(l.photo)}" alt="Stock photo illustrating ${esc(l.name)}" loading="lazy">${pill(l.avail>0?`${l.avail} ${l.avail===1?'space':'spaces'} available`:'Fully occupied')}<button class="icon-button" data-act="social-save" data-id="${esc(l.id)}" aria-label="Save ${esc(l.name)}" aria-pressed="${savedIds().includes(l.id)}">${icon(savedIds().includes(l.id)?'check':'bookmark')}</button></div><div class="rental-info"><div class="price">${peso(l.price)}<small> / month</small></div><h3>${esc(l.name)}</h3><p class="small-text">${esc(l.area)}, Iloilo City</p>${tags((l.amenities||[]).slice(0,3))}<div class="rental-bottom">${whoLine(l.manager||'Property owner',l.type)}${btn('View place','listing-detail',`data-id="${esc(l.id)}"`,'secondary small')}</div></div></article>`;}
  function postAuthor(name,note,photo='',avatarData='',published=true){
    return `<div class="post-author">${profileAvatar({name,photo,avatarData})}<div><strong>${esc(name)}</strong><small>${esc(note)}<span class="post-visibility" aria-label="${published?'Public post':'Only you'}" title="${published?'Public post':'Only you'}">${icon(published?'globe':'lock')}</span></small></div></div>`;
  }
  function propertyPost(l){
    const saved=savedIds().includes(l.id);
    return `<article class="post surface property-post"><header class="post-byline">${postAuthor(l.manager||'Property owner','Property advertisement · For rent',profile(l.owner)?.photo,profile(l.owner)?.avatarData)}</header><p class="post-copy">${esc(l.description||`Looking for a place in ${l.area}? Explore ${l.name}, see the house details, and start a conversation with the owner.`)}</p><img class="post-photo" src="${img(l.photo)}" alt="Stock photograph illustrating this property"><div class="post-property"><div><h3>${esc(l.name)}</h3><p class="small-text">${esc(l.area)}, Iloilo City · ${esc(l.type)}</p></div><div class="price">${peso(l.price)}<small> / month</small></div></div><footer class="post-actions"><button class="post-action ${saved?'is-saved':''}" data-act="social-save" data-id="${esc(l.id)}" aria-pressed="${saved}">${icon('bookmark')}${saved?'Saved':'Save'}</button><button class="post-action" data-act="social-contact" data-listing="${esc(l.id)}">${icon('msg')}Inquire</button><button class="post-action" data-act="listing-detail" data-id="${esc(l.id)}">${icon('home')}View property</button></footer></article>`;
  }
  function profilePost(p,post){
    const owner=p.id==='m1',note=p.demo?'Demo tenant profile':post?labelTime(post.at):p.job||'Looking for a home';
    const preferences=owner?[p.job,p.location].filter(Boolean):[`Budget up to ${peso(p.budget)}`,p.area,...p.habits.slice(0,2)];
    return `<article class="post surface profile-post"><header class="post-byline">${postAuthor(p.name,note,p.photo,p.avatarData,p.published)}</header><div class="post-copy">${esc(post?.body||p.bio||(owner?'Property update.':'Looking for a rental property.'))}</div><div class="post-preferences">${tags(preferences)}<small>${p.demo?'Sample identity':owner?'Property owner':'Looking for a home'}</small></div><footer class="post-actions"><button class="post-action" data-act="social-person" data-id="${esc(p.id)}">${icon('users')}View profile</button>${p.id!==myId()&&(isOwner()||owner)?`<button class="post-action" data-act="social-contact" data-person="${esc(p.id)}">${icon('msg')}Inquire</button>`:''}${post?.user===myId()?`<button class="post-action" data-act="social-delete-post" data-id="${esc(post.id)}">${icon('x')}Delete post</button>`:''}</footer></article>`;
  }
  function feed(){
    const filter=S.ui.socialFeed,list=publicListings(),published=profiles();
    return `<div class="feed-layout community-feed"><div class="feed-main">${head('Find a place. Find your people.','Good spaces and good people, right here in Iloilo.')}<div class="composer surface"><div class="row">${profileAvatar(userProfile())}<button class="composer-trigger" data-act="social-compose">${isOwner()?'Share a space someone will love…':'Tell the community what you are looking for…'}</button></div><div class="composer-tools"><button data-act="social-compose">${icon('image')}${isOwner()?'Property advertisement':'Looking for a home'}</button>${!isOwner()?'<button data-act="nav" data-to="roommates">'+icon('users')+'Find a roommate</button>':''}</div></div><div class="feed-tabs">${[['all','For you'],['properties','Properties'],['people','People looking']].map(([id,label])=>`<button class="${filter===id?'active':''}" data-act="social-feed-filter" data-filter="${id}" aria-pressed="${filter===id}">${label}</button>`).join('')}</div>${filter!=='properties'?S.community.posts.filter(post=>profile(post.user)?.published).map(post=>profilePost(profile(post.user),post)).join(''):''}${filter!=='people'&&list[0]?propertyPost(list[0]):''}${filter!=='properties'?published.slice(0,3).map(p=>profilePost(p)).join(''):''}${filter!=='people'?list.slice(1,4).map(propertyPost).join(''):''}${!list.length&&filter==='properties'?empty('building','No properties advertised yet','Owner advertisements appear here when published.'):''}</div><aside class="feed-rail stack"><section class="roommate-promo"><div class="avatar-stack">${examples.map(p=>profileAvatar(p)).join('')}</div><h3>Better together.<br>Find your roommate.</h3><p>A shared space feels better when you share the same everyday habits.</p>${btn(isOwner()?'Meet prospective tenants':'Meet your people','nav',`data-to="${isOwner()?'findtenants':'roommates'}"`,'primary small')}</section><section class="rail-card surface"><h3 class="rail-title">A few places to call home</h3>${list.slice(1,3).map(l=>`<div class="rail-rental"><img src="${img(l.photo)}" alt="Stock rental interior"><div><h4>${esc(l.name)}</h4><p>${esc(l.area)}, Iloilo City</p><strong>${peso(l.price)} / month</strong><button class="quiet" data-act="listing-detail" data-id="${esc(l.id)}">View place</button></div></div>`).join('')}</section><section class="rail-card surface"><h3 class="rail-title">${isOwner()?'Make room for someone new.':'Put yourself out there.'}</h3><p class="site-note">${isOwner()?'Review interest, manage your application fields, and find your next tenant.':'Your public profile helps owners understand the home you are looking for.'}</p>${btn(isOwner()?'Your advertisements':'Your public profile','nav',`data-to="${isOwner()?'advertisements':'profile'}"`,'secondary full small')}</section></aside></div>`;
  }

  function rentalsView(saved=false){
    const ex=S.ui.ex;
    const list=publicListings().filter(l=>(!saved||savedIds().includes(l.id))&&(!ex.q||`${l.name} ${l.area} ${l.type}`.toLowerCase().includes(ex.q.toLowerCase()))&&(!ex.area||l.area===ex.area)&&(!ex.type||l.type===ex.type)&&(!ex.max||l.price<=Number(ex.max))&&(!ex.amen||l.amenities?.includes(ex.amen)));
    return head(saved?'Keep your favorite places close.':'Your next chapter starts here.','Find a space that fits your days, your budget, and you.')+`<form class="rental-filters" data-form="market-filter"><label class="filter-field"><span>Neighborhood</span><select name="area"><option value="">Anywhere in Iloilo</option>${AREAS.map(a=>`<option ${ex.area===a?'selected':''}>${esc(a)}</option>`).join('')}</select></label><label class="filter-field"><span>Monthly budget</span><select name="max"><option value="">Any budget</option>${[2000,3000,5000,10000].map(n=>`<option value="${n}" ${String(ex.max)===String(n)?'selected':''}>Up to ${peso(n)}</option>`).join('')}</select></label><label class="filter-field"><span>Your space</span><select name="type"><option value="">All spaces</option>${TYPES.map(t=>`<option ${ex.type===t?'selected':''}>${esc(t)}</option>`).join('')}</select></label><button class="button primary" type="submit">${icon('search')}Find a place</button></form><div class="section-title"><h3>${saved?'Saved places':'Spaces you might love'}</h3><span class="small-text">${list.length} ${list.length===1?'place':'places'}${ex.q?' for “'+esc(ex.q)+'”':''}</span></div><div class="rental-grid">${list.map(listingCard).join('')||`<div class="empty-state"><h3>${saved?'No saved places match':'No rentals match'}</h3><p>Save a property or try a different search and budget.</p>${btn('Explore all rentals','social-clear-filters')}</div>`}</div><p class="site-note">Property details are prototype data. Photos are stock illustrations.</p>`;
  }

  function profileDetails(p){
    const items=[];
    if(p.job)items.push({label:'Occupation',value:p.job,icon:'users'});
    if(p.education)items.push({label:'Education',value:p.education,icon:'file'});
    if(p.location)items.push({label:'Location',value:p.location,icon:'pin'});
    else if(p.area)items.push({label:'Preferred area',value:p.area+', Iloilo City',icon:'pin'});
    if(p.id!=='m1'&&p.budget>0)items.push({label:'Rental budget',value:'Up to '+peso(p.budget)+' / month',icon:'wallet'});
    for(const item of (p.details||[]).slice(0,6))if(item.label&&item.value)items.push({...item,icon:'info'});
    return items;
  }
  function profileIntro(p,own){
    return `<section class='profile-intro surface'><div class='profile-section-heading'><h2>${profileUI.tab==='about'?'About':'Intro'}</h2>${own?`<button class=quiet data-act=profile-edit aria-label='Edit profile details'>${icon('edit')}</button>`:''}</div>${p.bio?`<p class=profile-bio>${esc(p.bio)}</p>`:`<p class=profile-placeholder>${own?'Add a bio to tell people a little about yourself.':'No bio added yet.'}</p>`}${own?btn(p.bio?'Edit bio':'Add bio','profile-edit','data-focus="bio"','secondary full small'):''}<div class=profile-details>${profileDetails(p).map(d=>`<div class=profile-detail>${icon(d.icon)}<div><small>${esc(d.label)}</small><span>${esc(d.value)}</span></div></div>`).join('')||'<p class=profile-placeholder>No additional details shared.</p>'}</div>${p.habits?.length?`<div class=profile-habits><h3>Interests & everyday habits</h3>${tags(p.habits)}</div>`:''}${own?btn('Edit details','profile-edit','','secondary full small'):''}</section>`;
  }
  function profileView(){
    const id=profileUI.target||myId(),p=profile(id),own=id===myId();
    if(!p||(!own&&!p.published))return head('This profile is unavailable','The person may have made their profile private.')+btn('Back to community','nav','data-to="feed"','secondary');
    const posts=S.community.posts.filter(post=>post.user===id).slice().sort((a,b)=>b.at.localeCompare(a.at));
    const advertisements=id==='m1'?publicListings().filter(l=>l.owner===id):[];
    const cover=safeProfileImage(p.coverData),tab=profileUI.tab==='about'?'about':'posts';
    const timeline=posts.map(post=>profilePost(p,post)).join('')+advertisements.map(propertyPost).join('');
    return `<div class=profile-page id=profile-page><section class=profile-header><div class='profile-cover ${cover?'has-image':''}'>${cover?`<img src='${esc(cover)}' alt=''>`:''}${own?`<button class='button secondary small profile-cover-button' data-act=profile-pick-image data-kind=cover>${icon('camera')}${cover?'Change cover photo':'Add cover photo'}</button>`:''}</div><div class=profile-identity><div class=profile-avatar-wrap>${profileAvatar(p,'profile-avatar')}${own?`<button class=profile-photo-button data-act=profile-pick-image data-kind=avatar aria-label='Change profile photo' title='Change profile photo'>${icon('camera')}</button>`:''}</div><div class=profile-name><h1>${esc(p.name)}</h1><p>${id==='m1'?'Property owner':'Tenant'}${p.demo?' · Demo profile':''}</p><div class=profile-visibility>${pill(own&&!p.published?'Private profile':'Public profile',own&&!p.published?'lock':'globe')}</div></div><div class=profile-header-actions>${own?btn(icon('edit')+'Edit profile','profile-edit','','secondary small')+btn(icon('plus')+'Create a post','profile-compose','','primary small'):btn(icon('msg')+(isOwner()||id==='m1'?'Inquire':'Message'),'social-contact',`data-person='${esc(id)}'`,'primary small')}</div></div><div class=profile-tabs role=tablist aria-label='Profile sections'>${[['posts','Posts'],['about','About']].map(([key,label])=>`<button id='profile-tab-${key}' role=tab data-act=profile-tab data-tab='${key}' aria-selected='${tab===key}' aria-controls='profile-${key}-panel' tabindex='${tab===key?0:-1}'>${label}</button>`).join('')}</div>${own?'<input id=profile-cover-upload type=file accept="image/jpeg,image/png,image/webp" hidden><input id=profile-avatar-upload type=file accept="image/jpeg,image/png,image/webp" hidden>':''}</section><div class='profile-body ${tab==='about'?'profile-about-body':''}' id='profile-${tab}-panel' role=tabpanel aria-labelledby='profile-tab-${tab}'>${profileIntro(p,own)}${tab==='posts'?`<section class='profile-timeline community-feed' id=profile-timeline>${own?`<div class='composer surface'><div class=row>${profileAvatar(p)}<button class=composer-trigger data-act=profile-compose>Share an update with your community…</button></div><div class=profile-composer-note>${icon(p.published?'globe':'lock')}${p.published?'Posts appear on your profile and in the community.':'Publish your profile to share a post with the community.'}</div></div>`:''}<div class='profile-posts-heading surface'><h2>Posts</h2><span>${posts.length+advertisements.length} ${posts.length+advertisements.length===1?'post':'posts'}</span></div>${timeline||`<div class='profile-empty surface'>${empty('file',own?'No posts yet':'No posts to show',own?'Share an update to start your profile timeline.':'Public updates from this person will appear here.',own?btn('Create your first post','profile-compose','','primary small'):'')}</div>`}</section>`:''}</div></div>`;
  }
  function customDetailRow(detail={}){
    return `<div class=profile-detail-row><label class=field><span>Detail label</span><input class='input detail-label' maxlength=50 value='${esc(detail.label||'')}' placeholder='For example, Interests'></label><label class=field><span>Detail</span><input class='input detail-value' maxlength=200 value='${esc(detail.value||'')}' placeholder='What would you like to share?'></label><button type=button class=profile-detail-remove data-act=profile-detail-remove aria-label='Remove this detail'>${icon('x')}</button></div>`;
  }
  function editProfile(d={}){
    if(profileUI.target&&profileUI.target!==myId())return;
    const p=userProfile(),tenant=p.id!=='m1';
    withModalForm('Edit profile',`<p class=small-text>Add only the details you want to share on your public profile.</p><label class=field><span>Bio</span><textarea class=input name=bio maxlength=1000 placeholder='Tell people a little about yourself…'>${esc(p.bio)}</textarea></label><div class=form-grid><label class=field><span>Occupation or school (optional)</span><input class=input name=job maxlength=100 value='${esc(p.job)}'></label><label class=field><span>Education (optional)</span><input class=input name=education maxlength=100 value='${esc(p.education||'')}'></label><label class='field full'><span>Location (optional)</span><input class=input name=location maxlength=100 value='${esc(p.location||'')}' placeholder='City or neighborhood'></label>${tenant?`<label class=field><span>Age (18+ for roommate discovery)</span><input class=input type=number name=age min=18 max=100 value='${p.age}' required></label><label class=field><span>Maximum monthly budget (₱)</span><input class=input type=number name=budget min=1 max=1000000 value='${p.budget}' required></label><label class='field full'><span>Preferred neighborhood</span><select class=input name=area>${AREAS.map(area=>`<option ${area===p.area?'selected':''}>${area}</option>`).join('')}</select></label>`:''}<label class='field full'><span>Interests & everyday habits (optional, separate with commas)</span><input class=input name=habits maxlength=250 value='${esc((p.habits||[]).join(', '))}' placeholder='Reading, Early bird, Tidy spaces'></label></div><div class=profile-extra-details><div class=profile-section-heading><h3>Other details</h3><button type=button class=quiet data-act=profile-detail-add>${icon('plus')}Add detail</button></div><div id=profile-detail-rows>${(p.details||[]).map(customDetailRow).join('')}</div></div><label class=profile-published><input type=checkbox name=published ${p.published?'checked':''}>Publish my profile and posts to the community</label><p class=site-note>Your account email and rental application answers stay separate from this profile. Unpublishing hides this profile and its community posts.</p><div class=modal-foot><button type=button class='btn outline' data-act=close-modal>Cancel</button><button class='btn primary' type=submit>Save profile</button></div>`,'market-profile',`data-owner='${myId()}'`);
    if(d.focus==='bio')$('#modal [name=bio]')?.focus();
  }
  async function uploadProfileImage(input,kind){
    if(!Auth.account()||(profileUI.target&&profileUI.target!==myId()))return;
    const file=input.files?.[0];if(!file)return;
    const account=myId();input.value='';
    if(!['image/jpeg','image/png','image/webp'].includes(file.type)||file.size>5*1024*1024)return toast('Choose a JPG, PNG, or WebP image smaller than 5 MB.');
    try{
      const bitmap=await createImageBitmap(file);
      try{
        if(bitmap.width*bitmap.height>40000000)throw new Error('Choose an image smaller than 40 megapixels.');
        const limit=kind==='cover'?1600:640,scale=Math.min(1,limit/Math.max(bitmap.width,bitmap.height));
        const canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(bitmap.width*scale));canvas.height=Math.max(1,Math.round(bitmap.height*scale));
        const context=canvas.getContext('2d');context.fillStyle='#ffffff';context.fillRect(0,0,canvas.width,canvas.height);context.drawImage(bitmap,0,0,canvas.width,canvas.height);
        const data=canvas.toDataURL('image/jpeg',0.84);if(!Auth.account()||myId()!==account)return;
        const p=userProfile(),key=kind==='cover'?'coverData':'avatarData',previous=p[key];p[key]=data;
        try{localStorage.setItem(STORE_KEY,JSON.stringify(S));}catch{if(previous===undefined)delete p[key];else p[key]=previous;throw new Error('Browser storage is full. Choose a smaller image.');}
        render();toast(kind==='cover'?'Cover photo updated.':'Profile photo updated.');
      }finally{bitmap.close();}
    }catch(error){toast(error.message.startsWith('Choose ')||error.message.startsWith('Browser storage')?error.message:'This image could not be opened. Try a JPG, PNG, or WebP photo.');}
  }
  function findTenants(){return head('Meet people looking for a home.','Public introductions help you start a relevant conversation.')+`<div class="tenant-grid">${profiles().map(p=>`<article class="tenant-card">${profileAvatar(p,'large')}<h3>${esc(p.name)}</h3><p class="small-text">${esc(p.job||'Prospective tenant')}${p.demo?' · Demo profile':''}</p><p>${esc(p.bio)}</p><div class="tenant-budget"><span>Monthly budget</span><strong>Up to ${peso(p.budget)}</strong></div>${tags([p.area,...p.habits.slice(0,2)])}${btn('View profile','social-person',`data-id="${esc(p.id)}"`,'secondary full')}</article>`).join('')}</div>`;}

  function votes(){return S.community.votes[myId()]||[];}
  function decisionFor(user,target){return (S.community.votes[user]||[]).filter(v=>v.target===target).at(-1)?.side;}
  const mutual=(target)=>decisionFor(myId(),target)==='right'&&decisionFor(target,myId())==='right';
  function deck(){return profiles().filter(p=>p.id!==myId()&&!decisionFor(myId(),p.id)&&(!S.ui.roommateArea||S.ui.roommateArea===p.area));}
  function roommateView(){
    const p=deck()[0],me=userProfile();
    const interests=profiles().filter(p=>decisionFor(myId(),p.id)==='right');
    return `<div class="roommate-layout"><section class="roommate-intro"><h2>A shared home.<br>Your kind of people.</h2><p>Find someone whose everyday habits feel a little like yours.</p><div class="roommate-preferences"><h3>Your preferences</h3><label class="field"><span>Neighborhood</span><select class="input" id="roommate-area"><option value="">Anywhere in Iloilo</option>${AREAS.map(area=>`<option ${area===S.ui.roommateArea?'selected':''}>${area}</option>`).join('')}</select></label><div class="preference-line">${icon('wallet')}Budget up to ${peso(me.budget)}</div>${btn('Edit your profile','nav','data-to="profile"','secondary small')}</div></section><section class="swipe-deck" aria-label="Roommate profiles">${p?`<article class="swipe-card" id="roommate-card" data-person="${esc(p.id)}">${p.photo?`<img class="roommate-photo" src="${img(p.photo)}" alt="Stock portrait for this sample profile">`:`<div class="roommate-photo" style="display:grid;place-items:center;background:var(--g100)">${profileAvatar(p,'large')}</div>`}<div class="roommate-profile"><div class="roommate-name"><h3>${esc(p.name.split(' ')[0])}, ${p.age}</h3>${pill(p.demo?'Demo profile':'Public profile')}</div><p class="small-text">${esc(p.job)} · ${esc(p.area)}, Iloilo</p><p>${esc(p.bio)}</p>${tags(p.habits)}<div class="row between" style="margin-top:16px"><span class="small-text">Budget ${peso(p.budget)} / mo</span><button class="quiet" data-act="social-person" data-id="${esc(p.id)}">Full profile</button></div></div></article><div class="swipe-actions"><button class="swipe-action" data-act="roommate-undo" aria-label="Undo your last swipe" ${votes().length?'':'disabled'}>${icon('rewind')}</button><button class="swipe-action" data-act="roommate-swipe" data-id="${esc(p.id)}" data-side="left" aria-label="Pass on ${esc(p.name)}">${icon('x')}</button><button class="swipe-action like" data-act="roommate-swipe" data-id="${esc(p.id)}" data-side="right" aria-label="Interested in ${esc(p.name)}">${icon('heart')}</button></div><p class="swipe-hint">Swipe left to pass. Swipe right if you are interested.</p>`:empty('users','You are all caught up','Try another neighborhood or undo your last swipe.',btn('Undo last swipe','roommate-undo','','secondary small'))}</section><aside class="roommate-aside"><h3>Your interests</h3>${interests.map(person=>`<div class="match-preview">${profileAvatar(person)}<div><strong>${esc(person.name)}</strong><small>${mutual(person.id)?'Mutual interest':'Interest sent'}</small></div>${mutual(person.id)?`<button class="icon-button" data-act="social-contact" data-person="${esc(person.id)}" aria-label="Message ${esc(person.name)}">${icon('msg')}</button>`:''}</div>`).join('')||'<p class="small-text">People you swipe right on will appear here.</p>'}<p class="mutual-note">Roommate chat opens when both people express interest. Demo profiles never generate a pretend match.</p><div class="house-preview"><img src="${img('studio.jpg')}" alt="Stock apartment interior"><h4>A place for both of you.</h4><p>Explore spaces with room for a fresh start.</p>${btn('See rental spaces','nav','data-to="explore"','secondary small')}</div></aside></div><p class="site-note">${!me.published?'Publish your profile so other tenants can discover you. ':''}Example profiles use stock photos. Swipe decisions are stored in this prototype.</p>`;
  }

  function ownerDashboard(){
    const b=beds(),invoices=S.invoices.filter(i=>myProps().some(p=>p.id===tenantById(i.tenant)?.prop));
    const paid=sum(invoices,i=>i.paid||0),views=Object.entries(S.community.views).filter(([id])=>myProps().some(p=>p.id===id)).reduce((n,[,v])=>n+v,0);
    const requests=S.marketApplications.filter(a=>a.owner==='m1');
    const stats=[['Rent recorded',peso(paid),'Verified payments in your records','wallet'],['Occupancy',`${b.occ} <small>/ ${b.cap} beds</small>`,`${b.avail} beds available`,'bed'],['Listing views',String(views),'Views recorded in this browser','eye'],['Rental requests',String(requests.length),`${requests.filter(a=>applicationStatus(a)==='Pending').length} waiting for review`,'file']];
    const grouped=new Map();for(const i of invoices){const key=i.period||'Other';grouped.set(key,(grouped.get(key)||0)+(i.paid||0));}
    return head(`Welcome back, ${S.manager.name.split(' ')[0]}.`,'Your properties, people, and next steps in one place.',btn(icon('plus')+'Post a property','social-compose'))+`<div class="dashboard-stats">${stats.map(([label,value,note,ic])=>`<section class="stat"><div class="row"><span>${label}</span>${icon(ic)}</div><strong>${value}</strong><small>${note}</small></section>`).join('')}</div><div class="dashboard-grid"><section class="chart surface"><div class="section-title"><h3>Rent collection</h3>${btn('Payment records','nav','data-to="rent"','secondary small')}</div>${grouped.size?`<div class="collection-rows">${[...grouped].map(([period,amount])=>`<div class="occupancy-line"><div class="row between"><b>${esc(period)}</b><span>${peso(amount)}</span></div><div class="occupancy-bar"><span style="width:${amount?Math.max(2,amount/Math.max(...grouped.values())*100):0}%"></span></div></div>`).join('')}</div>`:empty('wallet','Your collection history starts here','Verified rent payments will populate this overview.',btn('Open rent & payments','nav','data-to="rent"','secondary small'))}</section><section class="occupancy surface"><h3>Space at a glance</h3><p class="small-text" style="margin-top:5px">Occupied beds by property</p>${myProps().map(p=>{const b=beds(p.id);return `<div class="occupancy-line"><div class="row"><b>${esc(p.name)}</b><span>${b.occ} / ${b.cap}</span></div><div class="occupancy-bar"><span style="width:${b.cap?b.occ/b.cap*100:0}%"></span></div></div>`;}).join('')}${btn('Rooms & availability','nav','data-to="properties"','secondary full small')}</section></div><div class="dashboard-bottom"><section class="request-list surface"><div class="section-title"><h3>People interested in your spaces</h3><button class="quiet" data-act="nav" data-to="applications">View applications</button></div>${requests.slice(0,3).map(a=>{const p=profile(a.tenant);return `<div class="request-item">${profileAvatar(p)}<div class="body"><strong>${esc(p?.name||a.name)}</strong><p>${esc(findListing(a.listing)?.name||'Property application')}</p></div>${btn('Review','social-review',`data-id="${a.id}"`,'secondary small')}</div>`;}).join('')||empty('file','No rental requests yet','Publish an advertisement so people can apply.')}</section><section class="roommate-promo"><h3>Your next tenant<br>could be looking now.</h3><p>Browse public tenant introductions and start a conversation.</p>${btn('Find tenants '+icon('arrow'),'nav','data-to="findtenants"','primary small')}</section></div>`;
  }

  function advertisements(){return head('Your property advertisements','Publish available spaces and choose what applicants need to complete.',btn(icon('plus')+'Post a property','social-compose'))+`<div class="listing-grid">${myProps().map(p=>{const l=findListing(p.id);return `<article class="rental-card"><div class="rental-photo"><img src="${img(l.photo)}" alt="Stock property illustration">${pill(p.listed?'Live':'Not published')}</div><div class="rental-info"><h3>${esc(p.name)}</h3><p class="small-text">${esc(p.area)} · ${beds(p.id).avail} available spaces</p><div class="row wrap" style="margin-top:15px">${btn('Edit advertisement','social-ad-edit',`data-id="${p.id}"`,'secondary small')}${btn('Application fields','social-requirements',`data-id="${p.id}"`,'secondary small')}${btn(p.listed?'Pause':'Publish','social-toggle-listing',`data-id="${p.id}"`,'primary small')}</div></div></article>`;}).join('')}</div><p class="site-note">Listing details and stock photos are prototype content. Unpublished listings do not appear in discovery.</p>`;}

  function applicationStatus(a){return a.joinId?S.joinRequests.find(j=>j.id===a.joinId)?.status||a.status:a.status;}
  function applications(){const list=S.marketApplications.filter(a=>isOwner()?a.owner==='m1':a.tenant===myId());return head(isOwner()?'Rental applications':'Your rental applications',isOwner()?'Review the applicant’s answers, then contact them or assign a room.':'Keep track of the requests you have sent.')+`<section class="request-list surface">${list.map(a=>{const p=profile(a.tenant),l=findListing(a.listing);return `<div class="request-item">${profileAvatar(p)}<div class="body"><strong>${esc(isOwner()?p?.name||a.name:l?.name||'Property application')}</strong><p>${esc(isOwner()?l?.name||'':p?.name||'')} · ${esc(labelTime(a.at))}</p>${chip(applicationStatus(a))}</div>${btn(isOwner()?'Review':'View request','social-review',`data-id="${a.id}"`,'secondary small')}</div>`;}).join('')||empty('file','No rental applications yet',isOwner()?'Applications appear here after tenants complete your fields.':'Find a place you like and complete the owner’s application fields.',btn(isOwner()?'Your advertisements':'Find rentals','nav',`data-to="${isOwner()?'advertisements':'explore'}"`,'secondary small'))}</section>${isOwner()&&pendingJoins().some(j=>!list.some(a=>a.joinId===j.id))?`<div class="surface surface-pad mt"><h3>Other join requests</h3><p class="site-note">Existing requests received through invite codes remain in your tenant records.</p>${btn('Open tenant records','nav','data-to="tenants"','secondary small')}</div>`:''}`;}

  function marketMessages(){
    const all=threads(),thread=all.find(t=>t.id===S.ui.marketThread)||all[0];
    if(!thread)return head('Good conversations start here.','Inquire about a property, a prospective tenant, or a mutual roommate match.')+`<section class="surface">${empty('msg','No conversations yet','Open a property or public profile and send an inquiry.',btn(isOwner()?'Find tenants':'Find rentals','nav',`data-to="${isOwner()?'findtenants':'explore'}"`))}</section>`;
    S.ui.marketThread=thread.id;const log=messages(thread.id),other=otherId(thread);
    S.community.read[myId()+':'+thread.id]=stamp();save();
    return head('Messages','Your property inquiries and roommate conversations.')+`<div class="messages-layout"><aside class="surface surface-pad"><h3>Conversations</h3>${all.map(t=>{const other=otherId(t),last=messages(t.id).at(-1);return `<button class="message-thread ${t.id===thread.id?'active':''}" data-act="market-thread" data-id="${t.id}">${profileAvatar(profile(other)||{name:t.names?.[other]||displayName(other)})}<span><strong>${esc(t.names?.[other]||displayName(other))}</strong><small>${esc(last?.text||t.context)}</small></span></button>`;}).join('')}</aside><section class="conversation"><header class="conversation-title">${whoLine(thread.names?.[other]||displayName(other),thread.context)}${!validUser(other)?'<p class="small-text" style="margin-top:8px">Demo recipient · conversation stored locally.</p>':''}</header><div class="message-log" id="market-message-log" aria-live="polite">${log.map(m=>`<div class="message-bubble ${m.from===myId()?'mine':''}">${esc(m.text)}<small>${esc(labelTime(m.at))}</small></div>`).join('')||empty('msg','Say hello','Ask a question or introduce yourself.')}</div><form class="message-compose" data-form="market-message" data-id="${thread.id}"><input id="market-message-input" name="text" value="${esc(S.ui.socialDraft[draftKey(thread.id)]||'')}" placeholder="Write a message…" maxlength="1000" aria-label="Message" required autocomplete="off"><button class="button primary" type="submit">${icon('send')}Send</button></form></section></div>`;
  }

  function messageDropdown(){
    const all=recentThreads();
    return `<section class="dropdown message-dropdown" id="header-messages" aria-label="Recent messages"><div class="message-dropdown-title"><h3>Messages</h3><button class="quiet" data-act="chat-inbox">Open inbox ${icon('arrow')}</button></div><div class="recent-conversations">${all.slice(0,8).map(t=>{const last=messages(t.id).at(-1),preview=last?`${last.from===myId()?'You: ':''}${last.text}`:t.context||'Start a conversation';return `<button class="chat-preview" data-act="chat-head-open" data-id="${esc(t.id)}">${threadAvatar(t)}<span class="chat-preview-copy"><strong>${esc(threadName(t))}</strong><small>${esc(preview)}</small></span>${countBadge(threadUnread(t))}</button>`;}).join('')||empty('msg','No conversations yet','Start an inquiry from a property or a public profile.')}</div><button class="chat-inbox-link" data-act="chat-inbox">See all conversations</button></section>`;
  }
  function chatPanel(thread){
    const log=messages(thread.id),name=threadName(thread);
    return `<section class="chat-panel" id="chat-panel" role="dialog" aria-labelledby="chat-panel-title"><header class="chat-panel-title">${threadAvatar(thread)}<div class="chat-panel-person"><h2 id="chat-panel-title">${esc(name)}</h2><p>${validUser(otherId(thread))?'Conversation': 'Demo conversation · saved locally'}</p></div><button class="chat-panel-action" data-act="chat-inbox" data-id="${esc(thread.id)}" aria-label="Open this conversation in Messages" title="Open in Messages">${icon('arrow')}</button><button class="chat-panel-action" data-act="chat-head-minimize" aria-label="Minimize chat" title="Minimize chat"><svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14"/></svg></button><button class="chat-panel-action" data-act="chat-head-dismiss" data-id="${esc(thread.id)}" aria-label="Close chat with ${esc(name)}" title="Close chat">${icon('x')}</button></header><div class="chat-panel-log" id="chat-panel-log" role="log" aria-label="Conversation with ${esc(name)}" aria-live="polite">${log.slice(-50).map(m=>`<div class="message-bubble ${m.from===myId()?'mine':''}">${esc(m.text)}<small>${esc(labelTime(m.at))}</small></div>`).join('')||'<p class="chat-panel-empty">Say hello or ask a question to start the conversation.</p>'}</div><form class="message-compose chat-head-compose" data-form="market-message" data-id="${esc(thread.id)}"><input id="chat-head-input" name="text" value="${esc(S.ui.socialDraft[draftKey(thread.id)]||'')}" placeholder="Write a message…" maxlength="1000" aria-label="Message ${esc(name)}" required autocomplete="off"><button class="button primary" type="submit" aria-label="Send message">${icon('send')}</button></form></section>`;
  }
  function renderChatHeads(){
    let layer=$('#social-chat-layer');
    if(!layer){layer=document.createElement('div');layer.id='social-chat-layer';layer.className='social-chat-layer';$('#shell').append(layer);}
    if(!Auth.account()){layer.innerHTML='';return;}
    const dismissed=S.community.dismissedHeads[myId()]||[],all=recentThreads().filter(t=>!dismissed.includes(t.id));
    chatUI.headLimit=chatHeadLimit();const shown=all.slice(0,chatUI.headLimit);
    const rail=shown.length?`<aside class="chat-head-rail" aria-label="Recent chats">${shown.map(t=>`<div class="chat-shortcut"><button class="chat-head ${chatUI.thread===t.id?'is-open':''}" data-act="chat-head-open" data-id="${esc(t.id)}" aria-label="Chat with ${esc(threadName(t))}${threadUnread(t)?`, ${threadUnread(t)} unread`:''}" aria-expanded="${chatUI.thread===t.id}" aria-controls="chat-panel">${threadAvatar(t)}${countBadge(threadUnread(t))}<span class="chat-head-name">${esc(threadName(t))}</span></button><button class="chat-head-dismiss" data-act="chat-head-dismiss" data-id="${esc(t.id)}" aria-label="Dismiss chat head for ${esc(threadName(t))}" title="Dismiss chat head">${icon('x')}</button></div>`).join('')}${all.length>shown.length?`<button class="chat-head chat-head-more" data-act="toggle-messages" aria-label="${all.length-shown.length} more conversations">+${all.length-shown.length}</button>`:''}</aside>`:'';
    const thread=threads().find(t=>t.id===chatUI.thread);
    layer.innerHTML=rail+(thread?chatPanel(thread):'');
    const log=$('#chat-panel-log');if(log)log.scrollTop=log.scrollHeight;
  }
  function openChatHead(id){
    const thread=threads().find(t=>t.id===id);if(!thread)return;
    S.community.dismissedHeads[myId()]=(S.community.dismissedHeads[myId()]||[]).filter(hidden=>hidden!==id);
    chatUI.thread=id;chatUI.messages=false;S.ui.bell=false;S.ui.profileMenu=false;commit();$('#chat-head-input')?.focus();
  }
  function minimizeChat(){
    const id=chatUI.thread;chatUI.thread=null;save();render();
    const launcher=[...document.querySelectorAll('.chat-head-rail [data-act=chat-head-open]')].find(button=>button.dataset.id===id)||$('#topbar [data-act=toggle-messages]');launcher?.focus();
  }

  Object.assign(VIEWS.manager,{feed,profile:profileView,dashboard:ownerDashboard,advertisements,findtenants:findTenants,applications,messages:marketMessages,explore:()=>rentalsView()});
  Object.assign(VIEWS.tenant,{feed,explore:()=>rentalsView(),saved:()=>rentalsView(true),roommates:()=>roommateView()+`<div class="roommate-mobile-interests">${btn('Your roommate interests','roommate-interests','','secondary full')}</div>`,profile:profileView,applications,messages:marketMessages});

  function formError(form,message){let box=form.querySelector('.form-error');if(!box){box=document.createElement('p');box.className='form-error';box.setAttribute('role','alert');form.prepend(box);}box.textContent=message;box.scrollIntoView({block:'nearest'});}
  function withModalForm(title,body,form,extra=''){openModal(esc(title),`<form class="stack" data-form="${form}" ${extra}>${body}</form>`,{wide:true});}
  function showPerson(id){const p=profile(id);if(!p||(!p.published&&id!==myId()))return toast('This profile is not public.');profileUI.target=id;profileUI.tab='posts';closeModal();go('profile');}
  function showListing(id){const l=findListing(id);if(!l)return toast('This property is no longer available.');if(!publicListings().some(p=>p.id===id)&&l.owner!==myId())return toast('This advertisement is not public.');S.community.views[id]=(S.community.views[id]||0)+1;save();openModal(esc(l.name),`<div class="photo hero-photo"><img src="${img(l.photo)}" alt="Stock photo illustrating this property"></div><div class="row between"><span class="pill">${esc(l.type)} · ${esc(l.area)}</span><strong class="price">${peso(l.price)}<small> / month</small></strong></div><p class="small muted">${l.avail} spaces available · Owner: ${esc(l.manager||'Property owner')}</p><p>${esc(l.description||'Explore the amenities and house rules, then contact the owner to arrange a viewing.')}</p>${tags(l.amenities||[])}<div class="field-row"><div><h4>Deposit & fees</h4><p class="site-note">${esc(l.deposit||'Ask the owner for details.')}</p></div><div><h4>House rules</h4><p class="site-note">${(l.rules||[]).map(esc).join('<br>')||'Confirm house rules with the owner.'}</p></div></div><h4>Before you apply</h4><p class="site-note">A short introduction${requirements(id).filter(f=>f.required).map(f=>', '+esc(f.label.toLowerCase())).join('')}. Application answers are visible to the owner, not on the public feed.</p><div class="modal-foot">${l.owner===myId()?btn('Edit advertisement','social-ad-edit',`data-id="${esc(id)}"`):btn('Inquire with owner','social-contact',`data-listing="${esc(id)}"`,'secondary')}${!isOwner()?btn('Apply to rent','social-apply',`data-id="${esc(id)}"`,'primary'):''}</div><p class="small-text">Prototype listing with a stock photograph. Verify the property and terms directly before renting.</p>`,{wide:true});}
  function fieldMarkup(field){const name='answer_'+field.id,required=field.required?'required':'',label=esc(field.label);return `<label class="field"><span>${label}${field.required?' (required)':''}</span>${field.type==='textarea'?`<textarea class="input" name="${name}" maxlength="1000" ${required}></textarea>`:`<input class="input" name="${name}" type="${field.type}" ${field.type==='file'?'accept="image/jpeg,image/png,application/pdf"':field.type==='number'?'min="0" max="1000000"':'maxlength="500"'} ${required}>`}${field.type==='file'?'<small>Prototype stores the filename only. Use sample files, not real personal documents.</small>':''}</label>`;}
  function applyForm(id){const l=publicListings().find(l=>l.id===id);if(isOwner()||!l)return;if(l.avail<=0)return toast('This property has no available spaces.');if(S.marketApplications.some(a=>a.tenant===myId()&&a.listing===id&&applicationStatus(a)==='Pending'))return toast('You already have a pending application for this property.');withModalForm('Request to rent',`<p class="small muted">Applying for <b>${esc(l.name)}</b>. Complete the owner’s fields before sending.</p><label class="field"><span>A short introduction (required)</span><textarea class="input" name="introduction" maxlength="1000" required>${esc(userProfile().bio)}</textarea></label>${requirements(id).map(fieldMarkup).join('')}<label class="check"><input type="checkbox" name="consent" required>I agree to share these application answers with this property owner.</label>${note('This prototype stores application answers in this browser. File fields keep filenames only. Use sample details.')}<div class="modal-foot"><button class="btn primary" type="submit">Send rental request</button></div>`,'market-apply',`data-listing="${esc(id)}"`);}
  function reviewApplication(id){const a=S.marketApplications.find(a=>a.id===id);if(!a||(a.owner!==myId()&&a.tenant!==myId()))return;const l=findListing(a.listing);openModal(isOwner()?'Review rental application':'Your rental application',`${whoLine(a.name,l?.name||'Property application')}${chip(applicationStatus(a))}<div class="application-detail">${a.answers.map(answer=>`<div class="application-answer"><b>${esc(answer.label)}</b><p>${esc(answer.value||'Not provided')}</p></div>`).join('')}</div><div class="modal-foot">${btn(isOwner()?'Contact applicant':'Message owner','social-contact',`data-${isOwner()?'person':'listing'}="${esc(isOwner()?a.tenant:a.listing)}"`,'secondary')}${isOwner()&&applicationStatus(a)==='Pending'?`${btn('Decline','social-decline',`data-id="${a.id}"`,'secondary')}${a.joinId?btn('Approve & assign room','approve-join',`data-id="${a.joinId}"`):''}`:''}</div>`,{wide:true});}
  function adForm(id){if(!isOwner())return;const p=propById(id)||myProps()[0];if(!p)return A['add-property']();const ad=S.community.ads[p.id]||{};withModalForm(id?'Edit property advertisement':'Post a property',`<label class="field"><span>Property</span><select class="input" name="prop" ${id?'disabled':''}>${propOptions(p.id)}</select></label>${id?`<input type="hidden" name="prop" value="${p.id}">`:''}<label class="field"><span>Public advertisement title</span><input class="input" name="title" maxlength="100" value="${esc(ad.name||p.name)}" required></label><label class="field"><span>Tell people about the property</span><textarea class="input" name="description" maxlength="1000" required>${esc(ad.description||'')}</textarea></label><label class="field"><span>Illustration photo</span><select class="input" name="photo">${[['living-room.jpg','Bright shared living room'],['studio.jpg','Furnished apartment'],['bedroom.jpg','Furnished bedroom']].map(([value,label])=>`<option value="${value}" ${ad.photo===value?'selected':''}>${label} (stock photo)</option>`).join('')}</select></label><label class="check"><input type="checkbox" name="listed" ${p.listed?'checked':''}>Publish this advertisement to the community</label><p class="small-text">Rent and availability come from your room records. Add a room with pricing before publishing.</p><div class="modal-foot">${btn('Create another property','add-property','','secondary')}<button class="btn primary" type="submit">Save advertisement</button></div>`,'market-ad');}
  function compose(){if(isOwner())return adForm();composeProfilePost('feed');}
  function composeProfilePost(origin='profile'){if(profileUI.target&&profileUI.target!==myId())return;profileUI.target=null;if(!userProfile().published){toast('Publish your profile before creating a community post.');go('profile');return editProfile({focus:'bio'});}withModalForm('Create a post',`<p class="small-text">Share an update with your community.</p><label class="field"><span>Your post</span><textarea class="input" name="body" maxlength="1000" required placeholder="What would you like to share?"></textarea></label><div class="modal-foot"><button class="btn primary" type="submit">Publish post</button></div>`,'market-post',`data-origin="${origin}"`);}
  function requirementRow(f){return `<div class="requirement-row" data-requirement-id="${esc(f.id)}"><input class="input req-label" aria-label="Field label" maxlength="100" value="${esc(f.label)}" required><select class="input req-type" aria-label="Field type">${[['text','Short text'],['textarea','Long text'],['date','Date'],['number','Number'],['file','File']].map(([value,label])=>`<option value="${value}" ${f.type===value?'selected':''}>${label}</option>`).join('')}</select><label class="check"><input class="req-required" type="checkbox" ${f.required?'checked':''}>Required</label><button type="button" class="icon-btn sm" data-act="requirement-remove" aria-label="Remove field">${icon('x')}</button></div>`;}
  function requirementsForm(id){const p=propById(id);if(!isOwner()||p?.owner!=='m1')return;withModalForm('Rental application fields',`<p class="small muted">${esc(p.name)}. A short introduction is always required. Choose up to 12 additional fields.</p><div id="requirement-rows">${requirements(id).map(requirementRow).join('')}</div>${btn(icon('plus')+'Add field','requirement-add','','secondary')}<div class="modal-foot"><button class="btn primary" type="submit">Save application fields</button></div>`,'market-requirements',`data-id="${id}"`);}

  function startThread(d){
    let target,name,context,kind='property';
    if(d.listing){const l=findListing(d.listing);if(!l)return;target=l.owner;name=l.manager||'Property owner';context=l.name;}
    else{
      const p=profile(d.person);
      const application=isOwner()?S.marketApplications.find(a=>a.owner===myId()&&a.tenant===d.person):null;
      if(!p||(!p.published&&!application))return toast('This public profile is unavailable.');
      const propertyConversation=isOwner()||p.id==='m1';
      target=p.id;name=p.name;context=application?findListing(application.listing)?.name||'Rental application':propertyConversation?'Rental inquiry':'Roommate conversation';kind=propertyConversation?'property':'roommate';
      if(kind==='roommate'&&!mutual(target))return toast('Both people must express interest before roommate chat opens.');
    }
    if(target===myId())return toast('This is your own property or profile.');
    const pair=[myId(),target].sort(),id=pair.join('--');
    let thread=S.marketThreads.find(t=>t.id===id);
    if(!thread){thread={id,a:pair[0],b:pair[1],kind,context,names:{[myId()]:actorName(),[target]:name}};S.marketThreads.unshift(thread);}
    S.ui.marketThread=id;closeModal();commit();go('messages');
  }
  const oldRender=render;
  render=function(){
    ensure();
    const account=Auth.account()?.username||null;
    $('#shell').classList.toggle('community-layout',!!account&&['feed','profile'].includes(currentView()));
    $('#shell').classList.toggle('profile-layout',!!account&&currentView()==='profile');
    if(profileUI.account!==account){profileUI.account=account;profileUI.target=null;profileUI.tab='posts';}
    if(currentView()!=='profile')profileUI.target=null;
    if(chatUI.account!==account){chatUI.account=account;chatUI.messages=false;chatUI.thread=null;}
    const active=document.activeElement,focused=['market-message-input','chat-head-input'].includes(active?.id)?{id:active.id,start:active.selectionStart,end:active.selectionEnd}:null;
    const focusRegion=active?.closest('.message-dropdown')?'#header-messages':active?.closest('#chat-panel')?'#chat-panel':active?.closest('.chat-head-rail')?'.chat-head-rail':active?.closest('.social-header-actions')?'#topbar':null;
    const focusedControl=focusRegion&&active.matches('[data-act]')?{region:focusRegion,act:active.dataset.act,id:active.dataset.id}:null;
    if(account){
      markChatRead(threads().find(t=>t.id===chatUI.thread));
      if(currentView()==='messages')markChatRead(threads().find(t=>t.id===S.ui.marketThread)||threads()[0]);
    }
    oldRender();
    const mobile=$('#mobile-nav');if(mobile&&Auth.account())mobile.innerHTML=`<div class="mobile-main-links">${menuHtml(currentView(),true)}</div><details class="mobile-home-tools"><summary>${isOwner()?'Property tools':'My home tools'}</summary><div>${NAV[S.role].general.map(([id,label,ic])=>`<button class="nav-item" data-act="nav" data-to="${id}">${icon(ic)}${label}</button>`).join('')}</div></details>`;
    const log=$('#market-message-log');if(log)log.scrollTop=log.scrollHeight;
    renderChatHeads();
    if(focused){const input=document.getElementById(focused.id);input?.focus();input?.setSelectionRange(focused.start,focused.end);}
    else if(account&&focusedControl){const target=[...document.querySelectorAll(focusedControl.region+' [data-act]')].find(button=>button.dataset.act===focusedControl.act&&button.dataset.id===focusedControl.id)||$('#topbar [data-act=toggle-messages]');target?.focus();}
  };
  const originalBell=A['toggle-bell'],originalProfile=A['toggle-profile'],originalNav=A.nav;
  Object.assign(A,{
    nav:d=>{if(d.to==='profile'){profileUI.target=null;profileUI.tab='posts';}originalNav(d);},
    'profile-edit':editProfile,
    'profile-compose':()=>composeProfilePost(),
    'profile-tab':d=>{if(!['posts','about'].includes(d.tab))return;profileUI.tab=d.tab;render();document.getElementById('profile-tab-'+d.tab)?.focus({preventScroll:true});},
    'profile-detail-add':()=>{const rows=$('#profile-detail-rows');if(!rows)return;if(rows.children.length>=6)return toast('You can add up to six extra details.');rows.insertAdjacentHTML('beforeend',customDetailRow());rows.lastElementChild.querySelector('input')?.focus();},
    'profile-detail-remove':(_d,button)=>button.closest('.profile-detail-row')?.remove(),
    'profile-pick-image':d=>{if(profileUI.target&&profileUI.target!==myId())return;if(['cover','avatar'].includes(d.kind))document.getElementById('profile-'+d.kind+'-upload')?.click();},
    'toggle-messages':()=>{chatUI.messages=!chatUI.messages;S.ui.bell=false;S.ui.profileMenu=false;render();$(chatUI.messages?'#header-messages button':'#topbar [data-act=toggle-messages]')?.focus();},
    'toggle-bell':()=>{chatUI.messages=false;originalBell();$('#topbar [data-act=toggle-bell]')?.focus();},
    'toggle-profile':()=>{chatUI.messages=false;originalProfile();$('#topbar [data-act=toggle-profile]')?.focus();},
    'chat-head-open':d=>openChatHead(d.id),
    'chat-head-minimize':minimizeChat,
    'chat-head-dismiss':d=>{if(!threads().some(t=>t.id===d.id))return;const hidden=new Set(S.community.dismissedHeads[myId()]||[]);hidden.add(d.id);S.community.dismissedHeads[myId()]=[...hidden];if(chatUI.thread===d.id)chatUI.thread=null;commit();$('#topbar [data-act=toggle-messages]')?.focus();},
    'chat-inbox':d=>{const all=threads(),thread=all.find(t=>t.id===d.id)||all.find(t=>t.id===S.ui.marketThread)||all[0];if(thread){S.ui.marketThread=thread.id;S.community.dismissedHeads[myId()]=(S.community.dismissedHeads[myId()]||[]).filter(id=>id!==thread.id);}chatUI.messages=false;chatUI.thread=null;save();go('messages');},
    'fill-demo':d=>{const form=$('#login form');if(!form)return;const selected=$('#demo-account-select')?.value;const account=ACCOUNTS.find(a=>a.role===d.role&&a.username===selected)||ACCOUNTS.find(a=>a.role===d.role);if(!account)return;form.elements.username.value=account.username;form.elements.password.value=account.password;form.elements.username.focus();},
    'social-compose':compose,'social-ad-edit':d=>adForm(d.id),'social-requirements':d=>requirementsForm(d.id),
    'social-feed-filter':d=>{S.ui.socialFeed=d.filter;commit();},
    'social-save':d=>{const set=new Set(savedIds());set.has(d.id)?set.delete(d.id):set.add(d.id);S.community.saved[myId()]=[...set];commit();toast(set.has(d.id)?'Place saved.':'Place removed from saved places.');},
    'social-clear-filters':()=>{S.ui.ex={q:'',type:'',area:'',max:'',amen:''};save();go('explore');},
    'social-person':d=>showPerson(d.id),'listing-detail':d=>showListing(d.id),'social-apply':d=>applyForm(d.id),
    'social-review':d=>reviewApplication(d.id),'social-contact':startThread,
    'market-thread':d=>{if(!canReadThread(S.marketThreads.find(t=>t.id===d.id)))return;S.ui.marketThread=d.id;S.community.dismissedHeads[myId()]=(S.community.dismissedHeads[myId()]||[]).filter(id=>id!==d.id);commit();},
    'social-delete-post':d=>{S.community.posts=S.community.posts.filter(p=>p.id!==d.id||p.user!==myId());commit();toast('Your post was deleted.');},
    'social-toggle-listing':d=>{const p=propById(d.id);if(!isOwner()||p?.owner!=='m1')return;if(!p.listed&&!S.rooms.some(r=>r.prop===p.id&&r.price>0))return toast('Add a priced room before publishing this property.');p.listed=!p.listed;commit();toast(p.listed?'Advertisement published.':'Advertisement paused.');},
    'social-decline':d=>{const a=S.marketApplications.find(a=>a.id===d.id);if(!isOwner()||a?.owner!=='m1')return;a.status='Rejected';if(a.joinId)A['reject-join']({id:a.joinId});closeModal();commit();toast('Rental request declined.');},
    'requirement-add':()=>{const rows=$('#requirement-rows');if(!isOwner()||!rows)return;if(rows.children.length>=12)return toast('Use up to 12 additional fields.');rows.insertAdjacentHTML('beforeend',requirementRow({id:uid('field'),label:'',type:'text',required:true}));},
    'requirement-remove':(d,el)=>el.closest('.requirement-row')?.remove(),
    'roommate-swipe':d=>{
      if(isOwner()||!['left','right'].includes(d.side)||!profile(d.id)?.published||d.id===myId())return;
      (S.community.votes[myId()] ||= []).push({target:d.id,side:d.side,at:stamp()});
      closeModal();save();const card=$('#roommate-card');card?.classList.add(d.side==='right'?'is-right':'is-left');
      setTimeout(()=>{render();toast(d.side==='right'?(mutual(d.id)?'You both expressed interest. You can now message each other.':'Interest saved. Roommate chat opens after mutual interest.'):'Passed. Here is another potential roommate.');},matchMedia('(prefers-reduced-motion: reduce)').matches?0:220);
    },
    'roommate-undo':()=>{if(isOwner())return;S.community.votes[myId()]?.pop();commit();toast('Last roommate decision undone.');},
    'roommate-interests':()=>{const interested=profiles().filter(p=>decisionFor(myId(),p.id)==='right');openModal('Your roommate interests',interested.map(p=>`<div class="request-item">${profileAvatar(p)}<div class="body"><strong>${esc(p.name)}</strong><p>${mutual(p.id)?'Mutual interest':'Waiting for mutual interest'}</p></div>${mutual(p.id)?btn('Message','social-contact',`data-person="${p.id}"`,'secondary small'):btn('Profile','social-person',`data-id="${p.id}"`,'secondary small')}</div>`).join('')||empty('heart','No interests yet','Swipe right on a profile that feels like a good fit.'));},
    help:()=>openModal('About this prototype',`<p>UPa brings property advertisements and public tenant introductions into one community. Tenants can discover rentals, complete owner-defined application fields, and find roommates. Owners can monitor records, advertise, and review applications.</p><p>Use the account menu to log out and switch accounts. Choose a demo account on the sign-in page and click <b>Fill demo credentials</b>. Owner: <b>manager / manager123</b>. Tenant usernames: <b>${ACCOUNTS.filter(a=>a.role==='tenant').map(a=>esc(a.username)).join(', ')}</b>; each password is its username followed by <b>123</b>. Luis and Bea start at Casa Jaro; Jerome starts at Molo. Miguel, Sofia, Carlo, Juan, and Ana can demonstrate applications.</p>${note('This is a local prototype. Public posts, profiles, settings, and matching decisions persist in this browser; they are not a deployed public network. Messages and application records can use the existing optional broker for known demo accounts. Files keep filenames only; use sample information.')}`),
  });

  Object.assign(F,{
    'market-filter':v=>{S.ui.ex={...S.ui.ex,area:v.area,max:v.max,type:v.type};commit();},
    'market-profile':(v,form)=>{
      if(!Auth.account()||form?.dataset.owner!==myId())return;
      const age=Number(v.age),budget=Number(v.budget),details=[];
      if(!isOwner()&&(!Number.isInteger(age)||age<18||age>100||!(budget>0&&budget<=1000000)||!AREAS.includes(v.area)))return formError(form,'Use an age of 18–100, a valid monthly budget, and a neighborhood.');
      for(const row of [...form.querySelectorAll('.profile-detail-row')].slice(0,6)){const label=row.querySelector('.detail-label').value.trim(),value=row.querySelector('.detail-value').value.trim();if(!label&&!value)continue;if(!label||!value)return formError(form,'Add both a label and a value for each extra detail.');details.push({label:label.slice(0,50),value:value.slice(0,200)});}
      Object.assign(userProfile(),{job:(v.job||'').trim().slice(0,100),education:(v.education||'').trim().slice(0,100),location:(v.location||'').trim().slice(0,100),bio:(v.bio||'').trim().slice(0,1000),details,habits:(v.habits||'').split(',').map(t=>t.trim()).filter(Boolean).slice(0,6).map(t=>t.slice(0,35)),published:!!v.published,...(!isOwner()?{age,budget,area:v.area}:{})});closeModal();commit();toast(v.published?'Public profile saved.':'Profile saved as a private draft.');
    },
    'market-post':(v,form)=>{if(!Auth.account()||!userProfile().published)return;if(!v.body?.trim())return formError(form,'Write something before publishing.');const origin=form?.dataset.origin;S.community.posts.unshift({id:uid('post'),user:myId(),body:v.body.trim().slice(0,1000),at:stamp()});closeModal();S.ui.socialFeed='all';profileUI.target=null;commit();go(origin==='profile'?'profile':'feed');toast('Your post was published.');},
    'market-ad':(v,form)=>{
      const p=propById(v.prop);if(!isOwner()||p?.owner!=='m1')return;
      if(!v.title?.trim()||!v.description?.trim())return formError(form,'Add a title and property description.');
      if(v.listed&&!S.rooms.some(r=>r.prop===p.id&&r.price>0))return formError(form,'Add a room with monthly pricing before publishing.');
      if(!['living-room.jpg','studio.jpg','bedroom.jpg'].includes(v.photo))return formError(form,'Choose a valid illustration photo.');
      S.community.ads[p.id]={name:v.title.trim().slice(0,100),description:v.description.trim().slice(0,1000),photo:v.photo,owner:'m1'};p.listed=!!v.listed;closeModal();commit();go('advertisements');toast('Advertisement saved.');
    },
    'market-requirements':(v,form)=>{
      const p=propById(form.dataset.id);if(!isOwner()||p?.owner!=='m1')return;
      const fields=[...form.querySelectorAll('.requirement-row')].map(row=>({id:row.dataset.requirementId,label:row.querySelector('.req-label').value.trim(),type:row.querySelector('.req-type').value,required:row.querySelector('.req-required').checked}));
      if(fields.length>12||fields.some(f=>!f.label||f.label.length>100||!['text','textarea','date','number','file'].includes(f.type))||new Set(fields.map(f=>f.label.toLowerCase())).size!==fields.length)return formError(form,'Give every field a different, nonempty label. Use up to 12 fields.');
      S.community.requirements[p.id]=fields;closeModal();commit();toast('Application fields saved.');
    },
    'market-apply':(v,form)=>{
      const l=publicListings().find(l=>l.id===form.dataset.listing);if(isOwner()||!l)return;
      if(l.avail<=0)return formError(form,'This property has no available spaces.');
      if(S.marketApplications.some(a=>a.tenant===myId()&&a.listing===l.id&&applicationStatus(a)==='Pending'))return formError(form,'You already have a pending request for this property.');
      if(!v.introduction?.trim()||!v.consent)return formError(form,'Write an introduction and agree to share your answers with the owner.');
      const answers=[{label:'Introduction',value:v.introduction.trim().slice(0,1000)}];
      for(const field of requirements(l.id)){
        const input=form.elements['answer_'+field.id];let value=field.type==='file'?(input?.files[0]?.name||''):String(v['answer_'+field.id]||'').trim();
        if(field.required&&!value)return formError(form,`Complete “${field.label}” before sending.`);
        if(value&&field.type==='number'&&(!Number.isFinite(Number(value))||Number(value)<0))return formError(form,`Enter a valid number for “${field.label}”.`);
        if(value&&field.type==='date'&&!/^\d{4}-\d{2}-\d{2}$/.test(value))return formError(form,`Enter a valid date for “${field.label}”.`);
        if(field.type==='file'&&input?.files[0]&&input.files[0].size>5*1024*1024)return formError(form,'Use a sample file smaller than 5 MB.');
        answers.push({label:field.label,value:value.slice(0,1000)});
      }
      const me=persona(),a={id:uid('application'),listing:l.id,owner:l.owner,tenant:myId(),name:me.name,answers,at:stamp(),status:'Pending',joinId:null};
      let join=null;if(l.owner==='m1'&&propById(l.id)){join={id:uid('j'),userId:myId(),name:me.name,email:me.email,prop:l.id,via:'Rental application',at:today(),note:answers[0].value,status:'Pending'};S.joinRequests.unshift(join);a.joinId=join.id;}
      S.marketApplications.unshift(a);
      if(l.owner==='m1')notify('manager',`Rental application from ${me.name} for ${l.name}`,'applications',null,{...(join?{joinRequests:[join]}:{}),marketApplications:[a]});
      closeModal();commit();go('applications');toast(l.owner==='m1'?'Rental request sent.':'Rental request saved in this prototype.');
    },
    'market-message':(v,form)=>{
      const thread=S.marketThreads.find(t=>t.id===form.dataset.id);if(!canReadThread(thread))return;
      const text=(v.text||'').trim();if(!text)return formError(form,'Write a message before sending.');
      if(thread.kind==='roommate'&&!mutual(otherId(thread)))return formError(form,'Roommate messaging requires mutual interest.');
      const message={id:uid('message'),thread:thread.id,from:myId(),name:actorName(),text:text.slice(0,1000),at:stamp()};S.marketMessages.push(message);S.ui.socialDraft[draftKey(thread.id)]='';
      const target=otherId(thread);
      if(validUser(target)){const role=target==='m1'?'manager':'tenant';notify(role,`Message from ${actorName()}`,'messages',target==='m1'?null:target,{marketThreads:[thread],marketMessages:[message]});}
      const floating=!!form.closest('#chat-panel');commit();$(floating?'#chat-head-input':'#market-message-input')?.focus();
    },
  });

  // Reuse the existing broker's typed record transport for actual demo accounts.
  RECORD_PARTS.answers={max:16,fields:{label:'s',value:'s'}};
  RECORD_RULES.marketThreads={fields:{id:'id',a:'id',b:'id',kind:'s',context:'s'},refs:r=>validUser(r.a)&&validUser(r.b)};
  RECORD_RULES.marketMessages={fields:{id:'id',thread:'id',from:'id',name:'s',text:'s',at:'s'},refs:r=>S.marketThreads.some(t=>t.id===r.thread&&(t.a===r.from||t.b===r.from))};
  RECORD_RULES.marketApplications={fields:{id:'id',listing:'id',owner:'id',tenant:'id',name:'s',answers:'answers',at:'s',status:'s',joinId:'id?'},refs:r=>validUser(r.tenant)&&r.owner==='m1'&&!!propById(r.listing)};
  document.addEventListener('input',event=>{if(['market-message-input','chat-head-input'].includes(event.target.id)){const thread=event.target.closest('form')?.dataset.id;if(thread)S.ui.socialDraft[draftKey(thread)]=event.target.value;}});
  document.addEventListener('change',event=>{if(event.target.id==='profile-cover-upload')uploadProfileImage(event.target,'cover');if(event.target.id==='profile-avatar-upload')uploadProfileImage(event.target,'avatar');});
  document.addEventListener('keydown',event=>{const tab=event.target.closest('[role=tab][data-act=profile-tab]');if(!tab||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();A['profile-tab']({tab:event.key==='Home'?'posts':event.key==='End'?'about':tab.dataset.tab==='posts'?'about':'posts'});});
  document.addEventListener('click',event=>{
    if(!chatUI.messages||event.target.closest('.message-dropdown,[data-act=toggle-messages]'))return;
    chatUI.messages=false;$('#header-messages')?.remove();const button=$('#topbar [data-act=toggle-messages]');button?.setAttribute('aria-expanded','false');button?.classList.remove('is-open');
  });
  document.addEventListener('keydown',event=>{
    if(event.key!=='Escape'||$('#modal').classList.contains('open'))return;
    if(chatUI.messages){chatUI.messages=false;render();$('#topbar [data-act=toggle-messages]')?.focus();}
    else if(chatUI.thread&&event.target.closest('#chat-panel'))minimizeChat();
  });
  document.addEventListener('change',event=>{if(event.target.id==='roommate-area'){S.ui.roommateArea=event.target.value;commit();}});
  window.addEventListener('resize',()=>{if(Auth.account()&&chatUI.headLimit!==chatHeadLimit())render();const log=$('#chat-panel-log');if(log)log.scrollTop=log.scrollHeight;});
  // Capture the common search shortcut before the legacy handler routes owners to tenant records.
  document.addEventListener('keydown',event=>{if(event.key==='Enter'&&event.target.id==='global-search'){event.preventDefault();event.stopImmediatePropagation();S.ui.ex.q=event.target.value.trim();save();go('explore');}},true);
  let swipeStart=null;
  document.addEventListener('pointerdown',event=>{const card=event.target.closest('#roommate-card');if(card&&!event.target.closest('button'))swipeStart={id:card.dataset.person,x:event.clientX,y:event.clientY};});
  document.addEventListener('pointerup',event=>{if(!swipeStart)return;const s=swipeStart;swipeStart=null;const dx=event.clientX-s.x,dy=event.clientY-s.y;if(Math.abs(dx)>75&&Math.abs(dx)>Math.abs(dy)*1.5)A['roommate-swipe']({id:s.id,side:dx>0?'right':'left'});});
  document.addEventListener('pointercancel',()=>{swipeStart=null;});
  // Make existing dialogs keyboard-contained without replacing their form handlers.
  document.addEventListener('keydown',event=>{if(event.key!=='Tab'||!$('#modal').classList.contains('open'))return;const nodes=[...$('#modal').querySelectorAll('button,input,textarea,select,a[href]')].filter(n=>!n.disabled&&n.offsetParent!==null);if(!nodes.length)return;const first=nodes[0],last=nodes.at(-1);if(!$('#modal').contains(document.activeElement)){event.preventDefault();(event.shiftKey?last:first).focus();}else if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}});
  $('#login').dataset.key='';
  ensure();render();
})();
