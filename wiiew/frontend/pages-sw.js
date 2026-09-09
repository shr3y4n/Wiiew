const CACHE='wiiew-v1';
const ASSETS=['./','./pages-index.html','./pages-style.css','./pages-app.js','./manifest.json'];
self.addEventListener('install',e=>e.waitUntil(caches.open(CACHE).then(c=>c.addAll(ASSETS)).then(()=>self.skipWaiting())));
self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));
self.addEventListener('fetch',e=>{if(e.request.method!=='GET')return;e.respondWith(caches.match(e.request).then(r=>r||fetch(e.request).then(x=>{const copy=x.clone();caches.open(CACHE).then(c=>c.put(e.request,copy));return x}).catch(()=>caches.match('./'))))});
self.addEventListener('push',e=>{let d={title:'Wiiew',body:'Someone has entered your room.'};try{if(e.data)d={...d,...e.data.json()}}catch{}e.waitUntil(self.registration.showNotification(d.title,{body:d.body,icon:'./icons/icon-192.png',badge:'./icons/badge-72.png',tag:d.tag||'wiiew-entry',renotify:false,data:d.data||{}}))});
self.addEventListener('notificationclick',e=>{e.notification.close();e.waitUntil(clients.matchAll({type:'window',includeUncontrolled:true}).then(cs=>{for(const c of cs)if('focus'in c)return c.focus();return clients.openWindow('./')}))});
