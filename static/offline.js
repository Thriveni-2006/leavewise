/* Pocket Pahunch: plans a trip in the browser from data/pack.json (no server, no internet).
   Covers the routes that have bus-GPS history; direct buses only. Same idea as the server model:
   wait + ride are simulated together, the plan uses the 90th percentile plus the learned safety buffer. */
const Offline=(function(){
  let P=null,IDX=null,NAMES=null;
  const WALK=1.25,DETOUR=1.3;
  async function load(){
    if(P)return P;
    try{const r=await fetch('/pack.json');if(!r.ok)throw 0;P=await r.json();index()}catch(e){P=null}
    return P;
  }
  function index(){
    IDX=new Map();NAMES=new Map();
    P.routes.forEach((rt,ri)=>rt.stops.forEach((s,pos)=>{
      if(!IDX.has(s[0]))IDX.set(s[0],[]);IDX.get(s[0]).push([ri,pos]);
      if(!NAMES.has(s[0]))NAMES.set(s[0],{id:s[0],name:s[1],lat:s[2],lng:s[3]});
    }));
  }
  const ready=()=>!!P;
  function stopById(id){return NAMES&&NAMES.get(id)}
  function search(q){
    if(!NAMES)return[];const k=q.toLowerCase();const out=[];
    for(const s of NAMES.values()){const n=s.name.toLowerCase();if(n.includes(k))out.push({...s,routes:IDX.get(s.id).length,st:n.startsWith(k)})}
    out.sort((a,b)=>(b.st-a.st)||(b.routes-a.routes));return out.slice(0,12);
  }
  function dayType(date){const w=new Date(date+'T00:00:00').getDay();return(w===0||w===6||P.holidays.includes(date))?'weekend':'weekday'}
  function hav(a,b,c,d){const p=Math.PI/180,x=Math.sin((c-a)*p/2)**2+Math.cos(a*p)*Math.cos(c*p)*Math.sin((d-b)*p/2)**2;return 12742000*Math.asin(Math.sqrt(x))}
  function lerpQ(q,u){const n=q.length,x=Math.max(0,Math.min(n-1,u*n-0.5)),k=Math.min(n-2,Math.floor(x)),f=x-k;return q[k]*(1-f)+q[k+1]*f}
  function rand(seed){let s=seed>>>0;return()=>{s=(s+0x6D2B79F5)>>>0;let t=Math.imul(s^(s>>>15),1|s);t^=t+Math.imul(t^(t>>>7),61|t);return((t^(t>>>14))>>>0)/4294967296}}
  /* typical ride seconds between stop i and j */
  function rideMed(rt,dtp,h,i,j){const sg=rt.tab[dtp][h].seg;let s=0;for(let k=i;k<j;k++)s+=sg[k];return s}
  /* wait + ride for a rider who reaches stop i at hour h; returns minutes like the server's estimate() */
  function estimate(rt,i,j,h,dtp,N=1500){
    const e=rt.tab[dtp][h],med=rideMed(rt,dtp,h,i,j),n=j-i;
    const f=Math.max(0,Math.min(1,(n-6)/Math.max(1,rt.span-6)));       // 0 = short ride, 1 = long ride
    const R=e.rs.map((x,k)=>x*(1-f)+e.rl[k]*f);
    const rnd=rand(7+h*31+i*7+j);const tot=new Float64Array(N);
    for(let k=0;k<N;k++)tot[k]=med*lerpQ(R,rnd())+lerpQ(e.w,rnd());
    tot.sort();const p=u=>tot[Math.min(N-1,Math.floor(u*N))];
    const buf=(P.buffer&&P.buffer[e.c])||0;
    return{ride_typical_min:med/60,ride_plan_min:med*lerpQ(R,.9)/60,wait_typical_min:lerpQ(e.w,.5)/60,wait_plan_min:lerpQ(e.w,.9)/60,
      total_typical_min:p(.5)/60,total_plan_min:p(.9)/60+buf,confidence:e.c,n_observations:e.n,level:e.lv}
  }
  const BASIS=['','same hour & day type','same day type, all hours','all days, all hours','timetable x city pulse','distance at 14 km/h'];
  function hhmm(d){return String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0')}
  function nearby(lat,lng,max){const out=[];for(const s of NAMES.values()){const d=hav(lat,lng,s.lat,s.lng);if(d<=max)out.push({id:s.id,d})}return out}
  function journeys(o,d,date,mode,time,top=3){
    if(!P)return null;
    const dtp=dayType(date),[H,M]=time.split(':').map(Number);
    const base=new Date(date+'T00:00:00');base.setHours(H,M,0,0);
    const so=nearby(o.lat,o.lng,800),sd=nearby(d.lat,d.lng,800);
    if(!so.length||!sd.length)return[];
    const wd=new Map(sd.map(x=>[x.id,x.d*DETOUR/WALK])),wo=new Map(so.map(x=>[x.id,x.d*DETOUR/WALK]));
    const hAt=ms=>new Date(ms).getHours();
    const startH=mode==='arrive'?hAt(base.getTime()-45*60000):H;
    const dest=new Map();
    for(const[b,w]of wd)for(const[ri,j]of IDX.get(b))(dest.get(ri)||dest.set(ri,[]).get(ri)).push([j,b,w]);
    const cand=new Map();
    for(const[a,w1]of wo)for(const[ri,i]of IDX.get(a))for(const[j,b,w2]of(dest.get(ri)||[]))if(j>i){
      const c=w1+w2+rideMed(P.routes[ri],dtp,startH,i,j);
      if(!cand.has(ri)||c<cand.get(ri)[0])cand.set(ri,[c,i,j,a,b,w1,w2]);
    }
    const ranked=[...cand.entries()].sort((x,y)=>x[1][0]-y[1][0]).slice(0,8);
    const res=[];
    for(const[ri,[c,i,j,a,b,w1,w2]]of ranked){
      const rt=P.routes[ri];let start=base.getTime();let e;
      if(mode==='arrive'){
        e=estimate(rt,i,j,hAt(base.getTime()-45*60000+w1*1000),dtp);
        start=base.getTime()-e.total_plan_min*60000-(w1+w2)*1000;
      }
      e=estimate(rt,i,j,hAt(start+w1*1000),dtp);
      if(mode==='arrive')start=base.getTime()-e.total_plan_min*60000-(w1+w2)*1000;
      const st=rt.stops,bs=st[i],as=st[j];
      const leg={route:rt.name,route_id:rt.id,direction:rt.dir,board_stop:bs[1],board_stop_id:bs[0],board_idx:i,board_lat:bs[2],board_lng:bs[3],
        alight_stop:as[1],alight_stop_id:as[0],alight_idx:j,alight_lat:as[2],alight_lng:as[3],
        ride_typical_min:+e.ride_typical_min.toFixed(1),ride_plan_min:+e.ride_plan_min.toFixed(1),
        wait_typical_min:+e.wait_typical_min.toFixed(1),wait_plan_min:+e.wait_plan_min.toFixed(1),
        confidence:e.confidence,basis:BASIS[e.level]||'',n_observations:e.n_observations,measured:true,live_n:0,live_factor:1,last_bus_ago_min:null,
        shape:st.slice(i,j+1).map(s=>[s[2],s[3]])};
      const d2d=x=>+(w1/60+w2/60+x).toFixed(1);
      const r={...leg,...e,legs:[leg],transfers:0,walk_to_stop_min:+(w1/60).toFixed(1),walk_from_stop_min:+(w2/60).toFixed(1),
        total_typical_min:+e.total_typical_min.toFixed(1),total_plan_min:+e.total_plan_min.toFixed(1),
        door_to_door_typical_min:d2d(e.total_typical_min),door_to_door_plan_min:d2d(e.total_plan_min),
        wait_basis:'observed gaps between buses (offline summary)',quiet_ride_min:null,
        on_time_rate:(P.calib&&P.calib[e.confidence])??null,offline:true};
      if(mode==='arrive'){r.leave_by=hhmm(new Date(base.getTime()-r.door_to_door_plan_min*60000));r.leave_typical=hhmm(new Date(base.getTime()-r.door_to_door_typical_min*60000))}
      else{r.arrive_typical=hhmm(new Date(base.getTime()+r.door_to_door_typical_min*60000));r.arrive_plan_for=hhmm(new Date(base.getTime()+r.door_to_door_plan_min*60000))}
      res.push(r);
    }
    res.sort((x,y)=>x.door_to_door_plan_min-y.door_to_door_plan_min);
    return res.slice(0,top);
  }
  return{load,ready,search,stopById,journeys,estimate,dayType,get pack(){return P}};
})();
