const {useState,useEffect,useRef,useCallback,useMemo}=React;
const RC=Recharts;

/* ─── AppLoader — single gradient loading screen (replaces old plain white loader) ── */
function AppLoader(){
  return html`<div style=${{
    position:'fixed',inset:0,zIndex:99998,
    background:'linear-gradient(135deg,#06040f 0%,#0a0618 40%,#060412 100%)',
    display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',
    fontFamily:"'Bricolage Grotesque',system-ui,sans-serif"
  }}>
    <div style=${{position:'absolute',inset:0,overflow:'hidden',pointerEvents:'none'}}>
      <div style=${{position:'absolute',width:400,height:400,borderRadius:'50%',background:'radial-gradient(circle,rgba(90,140,255,0.18) 0%,transparent 70%)',top:-120,left:-80,animation:'vwBoot-orb1 8s ease-in-out infinite'}}></div>
      <div style=${{position:'absolute',width:320,height:320,borderRadius:'50%',background:'radial-gradient(circle,rgba(168,85,247,0.15) 0%,transparent 70%)',bottom:-80,right:-60,animation:'vwBoot-orb2 10s ease-in-out infinite'}}></div>
    </div>
    <div style=${{position:'absolute',top:0,left:0,right:0,height:2,background:'linear-gradient(90deg,transparent,#5a8cff 25%,#a855f7 50%,#ec4899 75%,transparent)',boxShadow:'0 0 20px rgba(90,140,255,0.6)'}}></div>
    <div style=${{position:'relative',zIndex:1,textAlign:'center'}}>
      <div style=${{width:64,height:64,borderRadius:20,margin:'0 auto 20px',background:'linear-gradient(135deg,#5a8cff,#a855f7)',display:'flex',alignItems:'center',justifyContent:'center',boxShadow:'0 8px 32px rgba(90,140,255,0.5),0 0 0 1px rgba(255,255,255,0.1)'}}>
        <svg width="34" height="34" viewBox="0 0 64 64" fill="none">
          <circle cx="32" cy="32" r="8.5" fill="white"/>
          <circle cx="32" cy="11" r="5.5" fill="white" opacity=".9"/>
          <circle cx="51" cy="43" r="5.5" fill="white" opacity=".9"/>
          <circle cx="13" cy="43" r="5.5" fill="white" opacity=".9"/>
          <line x1="32" y1="16.5" x2="32" y2="23.5" stroke="white" strokeWidth="3.5" strokeLinecap="round"/>
          <line x1="46" y1="40" x2="40.5" y2="36.5" stroke="white" strokeWidth="3.5" strokeLinecap="round"/>
          <line x1="18" y1="40" x2="23.5" y2="36.5" stroke="white" strokeWidth="3.5" strokeLinecap="round"/>
        </svg>
      </div>
      <div style=${{fontSize:28,fontWeight:800,color:'#f5f5f7',letterSpacing:'-1.5px',marginBottom:6}}>VEWIT</div>
      <div style=${{fontSize:13,color:'rgba(174,174,178,0.6)',letterSpacing:'0.05em',marginBottom:36}}>AI-Powered Workspace</div>
      <div style=${{width:180,height:2,background:'rgba(255,255,255,0.06)',borderRadius:2,overflow:'hidden',margin:'0 auto'}}>
        <div style=${{height:'100%',borderRadius:2,background:'linear-gradient(90deg,#5a8cff,#a855f7,#ec4899)',animation:'vwBoot-bar 1.8s cubic-bezier(0.4,0,0.2,1) infinite'}}></div>
      </div>
    </div>
  </div>`;
}

const api={
  get:u=>fetch(u,{credentials:'include'}).then(r=>r.json()).catch(()=>({})), post:(u,b)=>fetch(u,{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}).then(r=>r.json()).catch(()=>({})), put:(u,b)=>fetch(u,{method:'PUT',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}).then(r=>r.json()).catch(()=>({})), del:u=>fetch(u,{method:'DELETE',credentials:'include'}).then(r=>r.json()).catch(()=>({})), upload:(u,fd)=>fetch(u,{method:'POST',credentials:'include',body:fd}).then(r=>r.json()).catch(()=>({})),
};

const STAGES={
  backlog:    {label:'Backlog', color:'#94a3b8',bg:'rgba(148,163,184,.13)'}, planning:   {label:'Planning', color:'var(--cy)',bg:'rgba(96,165,250,.13)'}, development:{label:'Dev', color:'#9b8ef4',bg:'rgba(167,139,250,.13)'}, code_review:{label:'Review', color:'#22d3ee',bg:'rgba(34,211,238,.13)'}, testing:    {label:'Testing', color:'var(--pu)',bg:'rgba(251,191,36,.13)'}, uat:        {label:'UAT', color:'#f472b6',bg:'rgba(244,114,182,.13)'}, release:    {label:'Release', color:'#fb923c',bg:'rgba(251,146,60,.13)'}, production: {label:'Production',color:'#34d399',bg:'rgba(52,211,153,.13)'}, completed:  {label:'Completed', color:'#4ade80',bg:'rgba(74,222,128,.13)'}, blocked:    {label:'Blocked', color:'var(--rd2)',bg:'rgba(248,113,113,.13)'},
};
const KCOLS=['backlog','planning','development','code_review','testing','uat','release','production','completed','blocked'];
const PRIS={critical:{label:'Critical',color:'var(--rd)',sym:'🔴'},high:{label:'High',color:'var(--rd2)',sym:'↑'},medium:{label:'Medium',color:'var(--pu)',sym:'→'},low:{label:'Low',color:'var(--cy)',sym:'↓'}};
const ROLES=['Admin','Manager','TeamLead','Developer','Tester','Viewer'];
const JOIN_ROLES=['Developer','Tester','Viewer']; // roles available when joining via invite code
const PAL=['#7c3aed','#2563eb','#059669','#d97706','#dc2626','#ec4899','#0891b2','#5a8cff'];
const fmtD=d=>{if(!d)return'—';try{return new Date(d).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'});}catch(e){return d;}};
const ago=iso=>{const m=Math.floor((Date.now()-new Date(iso))/60000);if(m<1)return'just now';if(m<60)return m+'m ago';if(m<1440)return Math.floor(m/60)+'h ago';return Math.floor(m/1440)+'d ago';};
const safe=a=>(Array.isArray(a)?a:[]);

function Av({u,size=32}){
  const imgSrc=(u&&u.avatar_data&&u.avatar_data.startsWith('data:image'))?u.avatar_data:
               (u&&u.avatar&&u.avatar.length>10&&u.avatar.startsWith('data:image'))?u.avatar:null;
  if(imgSrc){
    return html`<img src=${imgSrc} class="av" style=${{width:size,height:size,objectFit:'cover',borderRadius:'50%',border:'2px solid rgba(0,0,0,.06)'}}/>`;
  }
  const initials=(u&&u.avatar&&u.avatar.length<=4)?u.avatar:(u&&u.name?u.name.split(' ').map(w=>w[0]).join('').slice(0,2).toUpperCase():'?');
  return html`<div class="av" style=${{width:size,height:size,background:(u&&u.color)||'#2563eb',color:'#fff',fontSize:Math.max(9,Math.floor(size*.33))}}>
    ${initials}
  </div>`;
}
function SP({s}){
  const d=STAGES[s]||{label:s,color:'#94a3b8',bg:'rgba(148,163,184,.13)'};
  return html`<span class="badge" style=${{color:d.color,background:d.bg}}>${d.label}</span>`;
}
function PB({p}){
  const d=PRIS[p]||{label:p,color:'#94a3b8',sym:'·'};
  const isC=p==='critical';
  return html`<span class="badge" style=${{color:d.color,background:d.color+'22',boxShadow:isC?'0 0 6px '+d.color+'55':'none',animation:isC?'pulse 1.5s infinite':'none'}}>${d.sym} ${d.label}</span>`;
}
function Prog({pct,color}){
  return html`<div class="prog"><div class="progf" style=${{width:Math.min(100,Math.max(0,pct||0))+'%',background:color||'var(--ac)'}}></div></div>`;
}
class ErrorBoundary extends React.Component{
  constructor(p){super(p);this.state={err:null,info:null};}
  static getDerivedStateFromError(e){return{err:e};}
  componentDidCatch(e,info){this.setState({info});}
  render(){
    if(this.state.err)return html`
      <div style=${{padding:40,textAlign:'center',color:'var(--rd)',maxWidth:520,margin:'0 auto'}}>
        <div style=${{fontSize:32,marginBottom:12}}>⚠️</div>
        <div style=${{fontSize:15,fontWeight:700,color:'var(--tx)',marginBottom:8}}>Something went wrong</div>
        <div style=${{fontSize:12,color:'var(--rd)',fontFamily:'monospace',background:'rgba(248,113,113,.08)',padding:'10px 14px',borderRadius:8,marginBottom:16,textAlign:'left',wordBreak:'break-word',maxHeight:120,overflowY:'auto'}}>
          ${this.state.err.message}
        </div>
        <button class="btn bp" onClick=${()=>this.setState({err:null,info:null})}>Retry</button>
      </div>`;
    return this.props.children;
  }
}

/* ─── AuthScreen — Professional Tech Design ───────────────────────────────── */


/* ─── AuthScreen — Dark Magical Login ──────────────────────────────────────── */

/* ─── AuthScreen — Strava/Apple inspired rich design ─────────────────────── */

/* ─── AuthScreen — Apple iPhone 17 Pro Design Language ──────────────────── */
function AuthScreen({onLogin}){
  const _initTab=(()=>{try{const p=new URLSearchParams(window.location.search);return p.get('action')==='register'?'register':'login';}catch{return 'login';}})();
  const [tab,setTabRaw]=useState(_initTab);
  const [regMode,setRegMode]=useState('create');
  const [wsName,setWsName]=useState('');
  const [inviteCode,setInviteCode]=useState('');
  const [name,setName]=useState('');
  const [email,setEmail]=useState('');
  const [pw,setPw]=useState('');
  const [role,setRole]=useState('Developer');
  const [showPw,setShowPw]=useState(false);
  const [err,setErr]=useState('');
  const [phase,setPhase]=useState('idle');
  const [successMsg,setSuccessMsg]=useState('');
  const [totpStep,setTotpStep]=useState(false);
  const [totpUserId,setTotpUserId]=useState('');
  const [totpUserName,setTotpUserName]=useState('');
  const [totpToken,setTotpToken]=useState('');
  const canvasRef=useRef(null);
  const formRef=useRef(null);

  const setTab=(t)=>{
    setTabRaw(t);setEmail('');setPw('');setErr('');setName('');setWsName('');setInviteCode('');setPhase('idle');
    try{history.replaceState(null,'','/?action='+t);}catch{}
  };

  /* ── Inject CSS ── */
  useEffect(()=>{
    const id='vw-ap-css';
    if(document.getElementById(id))return;
    const s=document.createElement('style');
    s.id=id;
    s.textContent=`
      @import url('https://fonts.googleapis.com/css2?family=SF+Pro+Display:wght@300;400;500;600;700;800&family=Bricolage+Grotesque:opsz,wght@12..96,300;400;700;800&family=Inter:wght@300;400;500;600&display=swap');

      /* ── Apple keyframes ── */
      @keyframes ap-fadeUp{0%{opacity:0;transform:translateY(30px)}100%{opacity:1;transform:translateY(0)}}
      @keyframes ap-fadeIn{0%{opacity:0}100%{opacity:1}}
      @keyframes ap-scale{0%{opacity:0;transform:scale(0.88)}100%{opacity:1;transform:scale(1)}}
      @keyframes ap-slideDown{0%{opacity:0;transform:translateY(-14px)}100%{opacity:1;transform:translateY(0)}}
      @keyframes ap-spin{to{transform:rotate(360deg)}}
      @keyframes ap-pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:0.5;transform:scale(0.92)}}
      @keyframes ap-orb1{0%{transform:translate(0,0) scale(1)}25%{transform:translate(60px,-40px) scale(1.08)}50%{transform:translate(-30px,50px) scale(0.94)}75%{transform:translate(40px,20px) scale(1.04)}100%{transform:translate(0,0) scale(1)}}
      @keyframes ap-orb2{0%{transform:translate(0,0) scale(1)}33%{transform:translate(-50px,30px) scale(1.06)}66%{transform:translate(30px,-50px) scale(0.96)}100%{transform:translate(0,0) scale(1)}}
      @keyframes ap-orb3{0%,100%{transform:translate(0,0)}50%{transform:translate(25px,-35px)}}
      @keyframes ap-shimmer{0%{background-position:200% center}100%{background-position:-200% center}}
      @keyframes ap-float{0%,100%{transform:translateY(0) rotate(0deg)}50%{transform:translateY(-16px) rotate(1.5deg)}}
      @keyframes ap-ring{0%{transform:scale(1);opacity:0.6}100%{transform:scale(2.8);opacity:0}}
      @keyframes ap-check{0%{stroke-dashoffset:100}100%{stroke-dashoffset:0}}
      @keyframes ap-progress{0%{transform:scaleX(0);opacity:0}20%{opacity:1}100%{transform:scaleX(1);opacity:1}}
      @keyframes ap-grain{0%,100%{transform:translate(0,0)}20%{transform:translate(-1px,1px)}40%{transform:translate(1px,-1px)}60%{transform:translate(-1px,-1px)}80%{transform:translate(1px,1px)}}
      @keyframes ap-glow{0%,100%{box-shadow:0 0 40px rgba(90,140,255,0.25),0 0 80px rgba(168,85,247,0.12)}50%{box-shadow:0 0 60px rgba(90,140,255,0.4),0 0 120px rgba(168,85,247,0.2)}}
      @keyframes ap-line{0%{transform:translateX(-100%)}100%{transform:translateX(100vw)}}
      @keyframes ap-badge{0%{opacity:0;transform:scale(0.6) translateY(8px)}100%{opacity:1;transform:scale(1) translateY(0)}}

      /* ── Inputs ── */
      .ap-inp{
        width:100%;padding:14px 18px;border-radius:13px;font-size:15px;
        outline:none;font-family:'Inter',system-ui;letter-spacing:-0.1px;
        transition:all 0.25s cubic-bezier(0.4,0,0.2,1);box-sizing:border-box;
        background:rgba(255,255,255,0.07);
        border:1px solid rgba(255,255,255,0.12);
        color:#f5f5f7;
        backdrop-filter:blur(20px);
        -webkit-backdrop-filter:blur(20px);
      }
      .ap-inp:focus{
        border-color:rgba(90,140,255,0.7);
        background:rgba(90,140,255,0.06);
        box-shadow:0 0 0 4px rgba(90,140,255,0.15),0 0 24px rgba(90,140,255,0.08);
      }
      .ap-inp::placeholder{color:rgba(200,200,210,0.3)}
      .ap-inp option{background:#1c1c2e;color:#f5f5f7}
      .ap-inp select{-webkit-appearance:none}

      /* ── Buttons ── */
      .ap-btn-primary{
        width:100%;height:52px;border:none;cursor:pointer;
        border-radius:14px;font-size:16px;font-weight:700;
        letter-spacing:-0.3px;font-family:'Bricolage Grotesque','Inter',system-ui;
        background:linear-gradient(135deg,#5a8cff 0%,#a855f7 50%,#ec4899 100%);
        background-size:200% auto;
        color:#fff;
        box-shadow:0 8px 32px rgba(90,140,255,0.45),0 2px 8px rgba(168,85,247,0.3),inset 0 1px 0 rgba(255,255,255,0.2);
        transition:all 0.3s cubic-bezier(0.4,0,0.2,1);
        animation:ap-shimmer 4s linear infinite;
        position:relative;overflow:hidden;
      }
      .ap-btn-primary::before{
        content:'';position:absolute;inset:0;
        background:linear-gradient(135deg,rgba(255,255,255,0.15) 0%,transparent 50%);
        border-radius:inherit;pointer-events:none;
      }
      .ap-btn-primary:hover:not(:disabled){
        transform:translateY(-2px) scale(1.01);
        box-shadow:0 16px 48px rgba(90,140,255,0.55),0 4px 16px rgba(168,85,247,0.4),inset 0 1px 0 rgba(255,255,255,0.2);
        background-position:right center;
      }
      .ap-btn-primary:active:not(:disabled){transform:translateY(0) scale(0.99)}
      .ap-btn-primary:disabled{opacity:0.45;cursor:not-allowed;transform:none}

      .ap-tab{
        flex:1;height:38px;border:none;cursor:pointer;
        border-radius:10px;font-size:13.5px;font-weight:600;
        font-family:'Inter',system-ui;letter-spacing:-0.1px;
        transition:all 0.2s cubic-bezier(0.4,0,0.2,1);
        background:transparent;
      }
      .ap-tab-active{
        background:rgba(255,255,255,0.10);
        color:#f5f5f7;
        box-shadow:0 2px 8px rgba(0,0,0,0.25),inset 0 1px 0 rgba(255,255,255,0.1);
      }
      .ap-tab-inactive{color:rgba(200,200,210,0.4)}
      .ap-tab-inactive:hover{color:rgba(200,200,210,0.7)}

      .ap-link{background:none;border:none;cursor:pointer;font-family:'Inter',system-ui;transition:all 0.2s;padding:0}
      .ap-link:hover{opacity:0.7}

      /* ── Chips/pills ── */
      .ap-chip{
        display:inline-flex;align-items:center;gap:8px;
        padding:7px 16px;border-radius:100px;
        font-size:11.5px;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;
        font-family:'Inter',system-ui;
        backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);
        transition:all 0.2s;
      }
      .ap-feat-pill{
        display:inline-flex;align-items:center;gap:7px;
        padding:7px 14px;border-radius:100px;
        font-size:11.5px;font-weight:600;
        font-family:'Inter',system-ui;
        backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);
        transition:all 0.22s cubic-bezier(0.4,0,0.2,1);
        cursor:default;
      }
      .ap-feat-pill:hover{transform:translateY(-3px);filter:brightness(1.15)}
    `;
    document.head.appendChild(s);
  },[]);

  /* ── WebGL-like Canvas: Apple Deep Space ── */
  useEffect(()=>{
    const cv=canvasRef.current;if(!cv)return;
    const ctx=cv.getContext('2d');
    let raf,t=0;
    const resize=()=>{cv.width=cv.offsetWidth||720;cv.height=cv.offsetHeight||900;};
    resize();
    const ro=new ResizeObserver(resize);ro.observe(cv);

    // iPhone 17 Pro color palette: Deep Navy, Titanium Purple, Desert Titanium, Black Titanium
    const ORB_DEFS=[
      {cx:.18,cy:.22,rx:.38,ry:.42,r:255,g:100,b:255,a:.22,sp:.0014,ph:0},   // purple
      {cx:.75,cy:.18,rx:.36,ry:.40,r:90,g:140,b:255,a:.20,sp:.0018,ph:1.8},  // blue
      {cx:.5, cy:.7, rx:.34,ry:.36,r:236,g:72,b:153,a:.16,sp:.0012,ph:3.6},  // pink
      {cx:.1, cy:.8, rx:.28,ry:.30,r:160,g:100,b:255,a:.14,sp:.0022,ph:5.4}, // violet
      {cx:.9, cy:.65,rx:.25,ry:.28,r:50, g:200,b:255,a:.10,sp:.0016,ph:2.1}, // cyan
    ];
    const orbs=ORB_DEFS.map(o=>({...o}));

    // Fine particle field
    const pts=Array.from({length:55},()=>({
      x:Math.random(),y:Math.random(),
      vx:(Math.random()-.5)*.00012,vy:(Math.random()-.5)*.00010,
      ph:Math.random()*6.28,sp:.003+Math.random()*.008,
      r:Math.floor(Math.random()*3),
      ci:Math.floor(Math.random()*4)
    }));
    const PCOLS=[[90,140,255],[168,85,247],[236,72,153],[139,92,246]];

    // Scanning light lines — Apple product reveal style
    const scanLines=[
      {y:.35,sp:.00018,a:.18,w:1.2},
      {y:.62,sp:.00012,a:.12,w:.8},
      {y:.12,sp:.00022,a:.14,w:.6},
    ];

    const draw=()=>{
      const W=cv.width,H=cv.height;t+=.01;

      // Deep space base — Apple's signature gradient
      const base=ctx.createLinearGradient(0,0,W*.6,H);
      base.addColorStop(0,'#06040f');   // near-black with violet hint
      base.addColorStop(.3,'#0a0618'); // deep purple-black
      base.addColorStop(.6,'#04060f'); // deep blue-black
      base.addColorStop(1,'#080410'); // violet-black
      ctx.fillStyle=base;ctx.fillRect(0,0,W,H);

      // Subtle diagonal shimmer overlay — titanium sheen
      const ti=ctx.createLinearGradient(0,H*.3,W,H*.7);
      const tAlpha=(.025+Math.sin(t*.2)*.01);
      ti.addColorStop(0,`rgba(200,180,255,0)`);
      ti.addColorStop(.4,`rgba(200,180,255,${tAlpha})`);
      ti.addColorStop(.6,`rgba(180,200,255,${tAlpha*.7})`);
      ti.addColorStop(1,`rgba(200,180,255,0)`);
      ctx.fillStyle=ti;ctx.fillRect(0,0,W,H);

      // Animated color orbs — cinematic haze
      orbs.forEach(o=>{
        o.ph+=o.sp;
        const ox=(o.cx+Math.sin(o.ph*.7)*.12)*W;
        const oy=(o.cy+Math.cos(o.ph*.5)*.10)*H;
        const rw=o.rx*W*(1+Math.sin(o.ph*1.4)*.12);
        const rh=o.ry*H*(1+Math.cos(o.ph*1.6)*.10);
        const pulse=.65+Math.sin(o.ph*2.1)*.35;
        ctx.save();
        ctx.translate(ox,oy);ctx.scale(rw/Math.min(rw,rh),rh/Math.min(rw,rh));
        const g=ctx.createRadialGradient(0,0,0,0,0,Math.min(rw,rh));
        g.addColorStop(0,`rgba(${o.r},${o.g},${o.b},${o.a*pulse})`);
        g.addColorStop(.45,`rgba(${o.r},${o.g},${o.b},${o.a*pulse*.25})`);
        g.addColorStop(1,'rgba(0,0,0,0)');
        ctx.fillStyle=g;ctx.beginPath();ctx.arc(0,0,Math.min(rw,rh),0,6.28);ctx.fill();
        ctx.restore();
      });

      // Fine star/particle grid
      pts.forEach(p=>{
        p.x+=p.vx;p.y+=p.vy;p.ph+=p.sp;
        if(p.x<0)p.x=1;if(p.x>1)p.x=0;if(p.y<0)p.y=1;if(p.y>1)p.y=0;
        const [pr,pg,pb]=PCOLS[p.ci];
        const a=(.15+Math.sin(p.ph)*.1)*(p.r===0?.5:p.r===1?.7:1);
        ctx.fillStyle=`rgba(${pr},${pg},${pb},${a})`;
        ctx.beginPath();ctx.arc(p.x*W,p.y*H,p.r===0?.5:p.r===1?1:1.4,0,6.28);ctx.fill();
      });

      // Particle mesh lines
      ctx.lineWidth=.4;
      for(let i=0;i<pts.length;i++){
        for(let j=i+1;j<pts.length;j++){
          const dx=(pts[i].x-pts[j].x)*W,dy=(pts[i].y-pts[j].y)*H;
          const d=Math.sqrt(dx*dx+dy*dy);
          if(d<90){
            const [pr,pg,pb]=PCOLS[pts[i].ci];
            ctx.strokeStyle=`rgba(${pr},${pg},${pb},${.08*(1-d/90)})`;
            ctx.beginPath();ctx.moveTo(pts[i].x*W,pts[i].y*H);ctx.lineTo(pts[j].x*W,pts[j].y*H);ctx.stroke();
          }
        }
      }

      // Apple product scan lines — horizontal light sweeps
      scanLines.forEach(sl=>{
        sl.y+=sl.sp;if(sl.y>1.1)sl.y=-.1;
        const sy=sl.y*H;
        const sg=ctx.createLinearGradient(0,sy-20,0,sy+20);
        sg.addColorStop(0,'rgba(180,180,255,0)');
        sg.addColorStop(.5,`rgba(180,180,255,${sl.a})`);
        sg.addColorStop(1,'rgba(180,180,255,0)');
        ctx.fillStyle=sg;ctx.fillRect(0,sy-20,W,40);
        // Thin bright center line
        ctx.strokeStyle=`rgba(220,210,255,${sl.a*1.8})`;
        ctx.lineWidth=sl.w;
        ctx.beginPath();ctx.moveTo(0,sy);ctx.lineTo(W,sy);ctx.stroke();
      });

      // Subtle grid — Apple's fine dot grid
      ctx.save();
      for(let x=0;x<W;x+=44){
        for(let y=0;y<H;y+=44){
          const dx=x/W-.5,dy=y/H-.5;
          const dist=Math.sqrt(dx*dx+dy*dy);
          const da=Math.max(0,.08-dist*.1);
          ctx.fillStyle=`rgba(120,100,200,${da})`;
          ctx.beginPath();ctx.arc(x,y,.7,0,6.28);ctx.fill();
        }
      }
      ctx.restore();

      raf=requestAnimationFrame(draw);
    };
    draw();
    return()=>{ro.disconnect();cancelAnimationFrame(raf);};
  },[]);

  const go=async()=>{
    setErr('');setPhase('loading');
    if(tab==='login'){
      const r=await api.post('/api/auth/login',{email,password:pw});
      if(r.error){setErr(r.error);setPhase('error');setTimeout(()=>setPhase('idle'),350);}
      else if(r.totp_required){setTotpUserId(r.user_id);setTotpUserName(r.name);setTotpStep(true);setPhase('idle');}
      else{setSuccessMsg('Welcome back, '+r.name);setPhase('success');setTimeout(()=>onLogin(r),1900);}
    } else {
      if(!name||!email||!pw){setErr('All fields required.');setPhase('error');setTimeout(()=>setPhase('idle'),350);return;}
      if(regMode==='create'&&!wsName){setErr('Workspace name is required.');setPhase('error');setTimeout(()=>setPhase('idle'),350);return;}
      if(regMode==='join'&&!inviteCode){setErr('Enter the invite code.');setPhase('error');setTimeout(()=>setPhase('idle'),350);return;}
      const r=await api.post('/api/auth/register',{mode:regMode,workspace_name:wsName,invite_code:inviteCode,name,email,password:pw,role});
      if(r.error){setErr(r.error);setPhase('error');setTimeout(()=>setPhase('idle'),350);}
      else{setSuccessMsg('Welcome to VEWIT, '+r.name+'!');setPhase('success');setTimeout(()=>onLogin(r),1900);}
    }
  };

  const submitTotp=async()=>{
    const tok=totpToken.replace(/\s/g,'');
    if(tok.length!==6){setErr('Enter the 6-digit code.');return;}
    setErr('');setPhase('loading');
    const r=await api.post('/api/auth/totp/verify',{user_id:totpUserId,token:tok});
    if(r.error){setErr(r.error);setTotpToken('');setPhase('idle');}
    else{setSuccessMsg('Verified! Welcome back, '+totpUserName);setPhase('success');setTimeout(()=>onLogin(r),1800);}
  };

  const LBL=({children})=>html`<label style=${{display:'block',fontSize:11,fontWeight:700,letterSpacing:'.08em',textTransform:'uppercase',color:'rgba(180,170,210,0.55)',marginBottom:8}}>${children}</label>`;

  // ── LEFT PANEL (canvas + hero copy) ──
  const LEFT=html`
    <div style=${{position:'relative',width:'52%',flexShrink:0,minHeight:'100vh',overflow:'hidden'}}>
      <canvas ref=${canvasRef} style=${{position:'absolute',inset:0,width:'100%',height:'100%'}}></canvas>

      <!-- Top accent: iPhone 17 Pro titanium spectrum line -->
      <div style=${{position:'absolute',top:0,left:0,right:0,height:2,background:'linear-gradient(90deg,transparent 0%,#5a8cff 20%,#a855f7 50%,#ec4899 80%,transparent 100%)',zIndex:10,boxShadow:'0 0 20px rgba(90,140,255,0.6),0 0 40px rgba(168,85,247,0.3)'}}></div>

      <!-- Logo -->
      <div style=${{position:'absolute',top:28,left:32,zIndex:10,display:'flex',alignItems:'center',gap:11,animation:'ap-fadeIn 1s ease both'}}>
        <div style=${{width:36,height:36,borderRadius:11,background:'linear-gradient(135deg,#5a8cff,#a855f7)',display:'flex',alignItems:'center',justifyContent:'center',boxShadow:'0 4px 20px rgba(90,140,255,0.5)',flexShrink:0}}>
          <svg width="20" height="20" viewBox="0 0 64 64" fill="none">
            <circle cx="32" cy="32" r="8.5" fill="white"/>
            <circle cx="32" cy="11" r="5.5" fill="white" opacity=".9"/>
            <circle cx="51" cy="43" r="5.5" fill="white" opacity=".9"/>
            <circle cx="13" cy="43" r="5.5" fill="white" opacity=".9"/>
            <line x1="32" y1="16.5" x2="32" y2="23.5" stroke="white" stroke-width="3.5" stroke-linecap="round"/>
            <line x1="46" y1="40" x2="40.5" y2="36.5" stroke="white" stroke-width="3.5" stroke-linecap="round"/>
            <line x1="18" y1="40" x2="23.5" y2="36.5" stroke="white" stroke-width="3.5" stroke-linecap="round"/>
          </svg>
        </div>
        <span style=${{fontFamily:"'Bricolage Grotesque',system-ui",fontWeight:800,fontSize:17,color:'#f5f5f7',letterSpacing:'-0.6px'}}>VEWIT</span>
      </div>

      <!-- Center hero -->
      <div style=${{position:'absolute',top:'50%',left:'50%',transform:'translate(-50%,-50%)',textAlign:'center',zIndex:10,width:'84%',pointerEvents:'none'}}>

        <!-- Badge chip -->
        <div style=${{display:'flex',justifyContent:'center',marginBottom:22,animation:'ap-badge 0.8s 0.3s cubic-bezier(0.34,1.56,0.64,1) both',opacity:0}}>
          <span class="ap-chip" style=${{background:'rgba(90,140,255,0.12)',border:'1px solid rgba(90,140,255,0.28)',color:'rgba(160,185,255,0.95)'}}>
            <span style=${{width:6,height:6,borderRadius:'50%',background:'#5aff8c',boxShadow:'0 0 8px #5aff8c',display:'inline-block',animation:'ap-pulse 2.2s ease-in-out infinite'}}></span>
            AI-Powered · Now Live
          </span>
        </div>

        <!-- Hero headline — Apple's large, confident typography -->
        <h1 style=${{fontFamily:"'Bricolage Grotesque',system-ui",fontSize:'clamp(2rem,3.2vw,2.8rem)',fontWeight:800,lineHeight:1.06,letterSpacing:'-2px',color:'#f5f5f7',marginBottom:18,animation:'ap-fadeUp 0.9s 0.2s ease both',opacity:0}}>
          Built for teams<br/>
          <span style=${{background:'linear-gradient(135deg,#5a8cff 0%,#a855f7 45%,#ec4899 100%)',WebkitBackgroundClip:'text',WebkitTextFillColor:'transparent',backgroundClip:'text',backgroundSize:'200% auto',animation:'ap-shimmer 5s linear infinite'}}>
            that ship fast.
          </span>
        </h1>

        <p style=${{fontSize:14,color:'rgba(180,175,210,0.6)',lineHeight:1.85,letterSpacing:'-0.1px',animation:'ap-fadeUp 0.9s 0.35s ease both',opacity:0}}>
          Projects · AI Docs · Timeline<br/>Kanban · WebRTC · Analytics
        </p>
      </div>

      <!-- Feature pills — bottom bar -->
      <div style=${{position:'absolute',bottom:28,left:0,right:0,zIndex:10,padding:'0 20px',animation:'ap-fadeUp 0.9s 0.6s ease both',opacity:0}}>
        <!-- Frosted separator line -->
        <div style=${{height:'1px',background:'linear-gradient(90deg,transparent,rgba(255,255,255,0.08),transparent)',marginBottom:18}}></div>
        <div style=${{display:'flex',justifyContent:'center',gap:8,flexWrap:'wrap'}}>
          ${[
            {e:'📋',l:'Kanban',c:'rgba(90,140,255,0.18)',b:'rgba(90,140,255,0.35)',t:'rgba(160,190,255,0.9)'},
            {e:'🤖',l:'AI Studio',c:'rgba(168,85,247,0.18)',b:'rgba(168,85,247,0.35)',t:'rgba(200,160,255,0.9)'},
            {e:'📅',l:'Timeline',c:'rgba(52,199,89,0.15)',b:'rgba(52,199,89,0.35)',t:'rgba(130,230,160,0.9)'},
            {e:'📞',l:'Meet',c:'rgba(100,210,255,0.15)',b:'rgba(100,210,255,0.35)',t:'rgba(140,220,255,0.9)'},
            {e:'🎫',l:'Tickets',c:'rgba(236,72,153,0.15)',b:'rgba(236,72,153,0.35)',t:'rgba(255,150,200,0.9)'},
            {e:'📊',l:'Analytics',c:'rgba(255,159,10,0.15)',b:'rgba(255,159,10,0.35)',t:'rgba(255,200,130,0.9)'},
          ].map(f=>html`
            <span key=${f.l} class="ap-feat-pill" style=${{background:f.c,border:`1px solid ${f.b}`,color:f.t,boxShadow:`0 2px 12px ${f.c}`}}>
              <span style=${{fontSize:13}}>${f.e}</span>${f.l}
            </span>
          `)}
        </div>
      </div>
    </div>`;

  // ── RIGHT PANEL wrapper ──
  const RIGHT=(content)=>html`
    <div style=${{flex:1,minHeight:'100vh',overflowY:'auto',display:'flex',alignItems:'center',justifyContent:'center',padding:'48px 40px',
      background:'linear-gradient(160deg,#06040f 0%,#0a0618 40%,#060412 100%)',
      borderLeft:'1px solid rgba(255,255,255,0.05)'}}>
      <div style=${{width:'100%',maxWidth:400}}>
        ${content}
      </div>
    </div>`;

  // ── SUCCESS ──
  if(phase==='success') return html`
    <div style=${{display:'flex',width:'100vw',minHeight:'100vh',overflow:'hidden'}}>${LEFT}
    ${RIGHT(html`
      <div style=${{textAlign:'center',animation:'ap-scale 0.55s cubic-bezier(0.34,1.56,0.64,1) both'}}>
        <!-- Concentric ripple rings -->
        <div style=${{position:'relative',width:130,height:130,margin:'0 auto 36px'}}>
          <div style=${{position:'absolute',inset:0,borderRadius:'50%',border:'1.5px solid rgba(90,140,255,0.4)',animation:'ap-ring 1.4s 0s ease-out forwards'}}></div>
          <div style=${{position:'absolute',inset:0,borderRadius:'50%',border:'1.5px solid rgba(168,85,247,0.35)',animation:'ap-ring 1.4s 0.25s ease-out forwards'}}></div>
          <div style=${{position:'absolute',inset:0,borderRadius:'50%',border:'1.5px solid rgba(236,72,153,0.25)',animation:'ap-ring 1.4s 0.5s ease-out forwards'}}></div>
          <div style=${{position:'absolute',inset:18,borderRadius:'50%',background:'linear-gradient(135deg,rgba(90,140,255,0.15),rgba(168,85,247,0.15))',border:'1.5px solid rgba(90,140,255,0.4)',display:'flex',alignItems:'center',justifyContent:'center',backdropFilter:'blur(20px)'}}>
            <svg width="46" height="46" viewBox="0 0 56 56" fill="none">
              <polyline points="14,29 24,39 42,19" stroke="url(#sg)" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="100" stroke-dashoffset="100" style=${{animation:'ap-check 0.65s 0.45s cubic-bezier(0.4,0,0.2,1) forwards'}}/>
              <defs><linearGradient id="sg" x1="0" y1="0" x2="1" y2="0"><stop offset="0%" stop-color="#5a8cff"/><stop offset="100%" stop-color="#a855f7"/></linearGradient></defs>
            </svg>
          </div>
        </div>
        <h2 style=${{fontFamily:"'Bricolage Grotesque',system-ui",fontSize:26,fontWeight:800,color:'#f5f5f7',letterSpacing:'-1.5px',marginBottom:10}}>${successMsg}</h2>
        <p style=${{fontSize:14,color:'rgba(180,175,210,0.55)',marginBottom:32}}>Loading your workspace…</p>
        <!-- Apple-style thin progress bar -->
        <div style=${{height:2,background:'rgba(255,255,255,0.06)',borderRadius:2,overflow:'hidden',maxWidth:220,margin:'0 auto'}}>
          <div style=${{height:'100%',borderRadius:2,transformOrigin:'left',background:'linear-gradient(90deg,#5a8cff,#a855f7,#ec4899)',animation:'ap-progress 1.8s cubic-bezier(0.4,0,0.2,1) forwards'}}></div>
        </div>
      </div>
    `)}
    </div>`;

  // ── TOTP ──
  if(totpStep) return html`
    <div style=${{display:'flex',width:'100vw',minHeight:'100vh',overflow:'hidden'}}>${LEFT}
    ${RIGHT(html`
      <div style=${{animation:'ap-fadeUp 0.55s ease both'}}>
        <div style=${{width:54,height:54,borderRadius:16,background:'linear-gradient(135deg,#5a8cff,#a855f7)',display:'flex',alignItems:'center',justifyContent:'center',marginBottom:24,boxShadow:'0 6px 28px rgba(90,140,255,0.45)'}}>
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/><circle cx="12" cy="16" r="1" fill="white"/>
          </svg>
        </div>
        <h2 style=${{fontFamily:"'Bricolage Grotesque',system-ui",fontSize:26,fontWeight:800,color:'#f5f5f7',letterSpacing:'-1.5px',marginBottom:8}}>Two-Factor Verification</h2>
        <p style=${{fontSize:14,color:'rgba(175,170,210,0.6)',marginBottom:24,lineHeight:1.7}}>
          Hello <b style=${{color:'#a78bfa',fontWeight:700}}>${totpUserName}</b> — open your authenticator app and enter the 6-digit code.
        </p>
        <div style=${{background:'rgba(90,140,255,0.07)',border:'1px solid rgba(90,140,255,0.2)',borderRadius:13,padding:'12px 16px',marginBottom:24,display:'flex',gap:12,alignItems:'center'}}>
          <span style=${{fontSize:22}}>📱</span>
          <span style=${{fontSize:13,color:'rgba(180,175,210,0.8)',lineHeight:1.55}}>Use <b style=${{color:'#f5f5f7'}}>Google Authenticator</b> or <b style=${{color:'#f5f5f7'}}>Authy</b></span>
        </div>
        <div style=${{marginBottom:20}}>
          <${LBL}>Verification Code</${LBL}>
          <input class="ap-inp" style=${{height:64,textAlign:'center',fontSize:32,fontWeight:800,fontFamily:'monospace',letterSpacing:12,
            borderColor:totpToken.length===6?'rgba(90,140,255,0.7)':'rgba(255,255,255,0.12)',
            background:totpToken.length===6?'rgba(90,140,255,0.08)':'rgba(255,255,255,0.07)',
            boxShadow:totpToken.length===6?'0 0 0 4px rgba(90,140,255,0.15)':'none'}}
            value=${totpToken} placeholder="000000" maxLength=6 autoFocus
            onInput=${e=>setTotpToken(e.target.value.replace(/\D/g,'').slice(0,6))}
            onKeyDown=${e=>e.key==='Enter'&&submitTotp()}/>
        </div>
        ${err?html`<div style=${{display:'flex',gap:9,padding:'12px 15px',background:'rgba(255,69,58,0.08)',border:'1px solid rgba(255,69,58,0.22)',borderRadius:12,marginBottom:16,animation:'ap-slideDown 0.2s ease both'}}>
          <span>⚠️</span><span style=${{fontSize:13,color:'#ff6b6b',lineHeight:1.45}}>${err}</span>
        </div>`:null}
        <button class="ap-btn-primary" onClick=${submitTotp} disabled=${phase==='loading'||totpToken.length!==6}>
          ${phase==='loading'
            ?html`<span style=${{display:'inline-flex',alignItems:'center',gap:10}}><span style=${{width:16,height:16,border:'2px solid rgba(255,255,255,0.25)',borderTopColor:'#fff',borderRadius:'50%',animation:'ap-spin 0.7s linear infinite',display:'inline-block'}}></span>Verifying…</span>`
            :'Verify & Continue →'}
        </button>
        <div style=${{textAlign:'center',marginTop:16}}>
          <button class="ap-link" onClick=${()=>{setTotpStep(false);setTotpToken('');setErr('');setPhase('idle');}} style=${{color:'rgba(175,170,210,0.4)',fontSize:13}}>← Back to sign in</button>
        </div>
      </div>
    `)}
    </div>`;

  // ── MAIN FORM ──
  return html`
    <div style=${{display:'flex',width:'100vw',minHeight:'100vh',overflow:'hidden'}}>${LEFT}
    ${RIGHT(html`
      <div ref=${formRef} style=${{animation:'ap-fadeUp 0.65s ease both'}}>

        <!-- Wordmark -->
        <div style=${{display:'flex',alignItems:'center',gap:9,marginBottom:36}}>
          <div style=${{width:30,height:30,borderRadius:9,background:'linear-gradient(135deg,#5a8cff,#a855f7)',display:'flex',alignItems:'center',justifyContent:'center',boxShadow:'0 3px 14px rgba(90,140,255,0.45)'}}>
            <svg width="17" height="17" viewBox="0 0 64 64" fill="none"><circle cx="32" cy="32" r="8.5" fill="white"/><circle cx="32" cy="11" r="5.5" fill="white" opacity=".9"/><circle cx="51" cy="43" r="5.5" fill="white" opacity=".9"/><circle cx="13" cy="43" r="5.5" fill="white" opacity=".9"/><line x1="32" y1="16.5" x2="32" y2="23.5" stroke="white" stroke-width="3.5" stroke-linecap="round"/><line x1="46" y1="40" x2="40.5" y2="36.5" stroke="white" stroke-width="3.5" stroke-linecap="round"/><line x1="18" y1="40" x2="23.5" y2="36.5" stroke="white" stroke-width="3.5" stroke-linecap="round"/></svg>
          </div>
          <span style=${{fontFamily:"'Bricolage Grotesque',system-ui",fontWeight:800,fontSize:16,color:'#f5f5f7',letterSpacing:'-0.5px'}}>VEWIT</span>
        </div>

        <!-- Headline -->
        <h1 style=${{fontFamily:"'Bricolage Grotesque',system-ui",fontSize:'clamp(1.75rem,3vw,2.2rem)',fontWeight:800,letterSpacing:'-1.8px',lineHeight:1.08,color:'#f5f5f7',marginBottom:10}}>
          ${tab==='login'?'Sign in.':'Get started.'}
        </h1>
        <p style=${{fontSize:14,color:'rgba(175,170,210,0.5)',marginBottom:28,lineHeight:1.6}}>
          ${tab==='login'?'Access your VEWIT workspace.':'Create your team workspace.'}
        </p>

        <!-- Tab switcher — Apple segmented control -->
        <div style=${{display:'flex',background:'rgba(255,255,255,0.05)',borderRadius:13,padding:'3px',border:'1px solid rgba(255,255,255,0.07)',marginBottom:24,backdropFilter:'blur(20px)'}}>
          ${['login','register'].map(tp=>html`
            <button key=${tp} class=${'ap-tab '+(tab===tp?'ap-tab-active':'ap-tab-inactive')} onClick=${()=>setTab(tp)}>
              ${tp==='login'?'Sign In':'Create Account'}
            </button>`)}
        </div>

        <!-- Register mode picker -->
        ${tab==='register'?html`
          <div style=${{display:'flex',background:'rgba(255,255,255,0.03)',borderRadius:11,padding:'3px',border:'1px solid rgba(255,255,255,0.05)',marginBottom:18,animation:'ap-slideDown 0.22s ease both'}}>
            ${[['create','🏢 New Workspace'],['join','🔗 Join Workspace']].map(([m,l])=>html`
              <button key=${m} class=${'ap-tab '+(regMode===m?'ap-tab-active':'ap-tab-inactive')} style=${{height:33,fontSize:12,borderRadius:9}} onClick=${()=>setRegMode(m)}>${l}</button>`)}
          </div>
          ${regMode==='create'?html`
            <div style=${{marginBottom:16,animation:'ap-slideDown 0.2s ease both'}}>
              <${LBL}>Workspace Name</${LBL}>
              <input class="ap-inp" placeholder="e.g. Acme Corp" value=${wsName} onInput=${e=>setWsName(e.target.value)}/>
            </div>`:null}
          ${regMode==='join'?html`
            <div style=${{marginBottom:16,padding:'14px 16px',background:'rgba(90,140,255,0.06)',borderRadius:13,border:'1px solid rgba(90,140,255,0.18)',animation:'ap-slideDown 0.2s ease both'}}>
              <${LBL}>Invite Code</${LBL}>
              <input class="ap-inp" style=${{fontFamily:'monospace',letterSpacing:8,fontSize:20,textAlign:'center'}} placeholder="XXXXXXXX" value=${inviteCode} onInput=${e=>setInviteCode(e.target.value.toUpperCase())}/>
            </div>`:null}`:null}

        <!-- Fields -->
        <div style=${{display:'flex',flexDirection:'column',gap:14,marginBottom:6}}>
          ${tab==='register'?html`
            <div>
              <${LBL}>Full Name</${LBL}>
              <input class="ap-inp" placeholder="Alice Chen" value=${name} onInput=${e=>setName(e.target.value)}/>
            </div>`:null}

          <div>
            <${LBL}>Email Address</${LBL}>
            <input class="ap-inp" type="email" placeholder="you@company.com" value=${email}
              autoComplete="username" onInput=${e=>setEmail(e.target.value)} onKeyDown=${e=>e.key==='Enter'&&go()}/>
          </div>

          <div>
            <${LBL}>Password</${LBL}>
            <div style=${{position:'relative'}}>
              <input class="ap-inp" style=${{paddingRight:48}} type=${showPw?'text':'password'}
                placeholder="••••••••••" value=${pw} autoComplete="current-password"
                onInput=${e=>setPw(e.target.value)} onKeyDown=${e=>e.key==='Enter'&&go()}/>
              <button onClick=${()=>setShowPw(!showPw)}
                style=${{position:'absolute',right:14,top:'50%',transform:'translateY(-50%)',background:'none',border:'none',cursor:'pointer',color:'rgba(175,170,210,0.4)',fontSize:14,lineHeight:1,padding:2,transition:'color 0.2s'}}
                onMouseEnter=${e=>e.target.style.color='rgba(175,170,210,0.8)'}
                onMouseLeave=${e=>e.target.style.color='rgba(175,170,210,0.4)'}>
                ${showPw?'🙈':'👁'}
              </button>
            </div>
          </div>

          ${tab==='register'?html`
            <div>
              <${LBL}>Role</${LBL}>
              <select class="ap-inp" style=${{cursor:'pointer',backgroundImage:"url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 24 24' fill='none' stroke='%23aeaeb2' stroke-width='2.5'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E\")",backgroundRepeat:'no-repeat',backgroundPosition:'right 14px center',WebkitAppearance:'none',appearance:'none'}}
                value=${role} onChange=${e=>setRole(e.target.value)}>
                ${(regMode==='join'?JOIN_ROLES:ROLES).map(r=>html`<option key=${r}>${r}</option>`)}
              </select>
            </div>`:null}

          ${err?html`
            <div style=${{display:'flex',alignItems:'flex-start',gap:10,padding:'12px 16px',background:'rgba(255,69,58,0.08)',border:'1px solid rgba(255,69,58,0.22)',borderRadius:12,animation:'ap-slideDown 0.2s ease both'}}>
              <span style=${{fontSize:15,marginTop:1}}>⚠️</span>
              <span style=${{fontSize:13,color:'#ff7a76',lineHeight:1.5}}>${err}</span>
            </div>`:null}

          <!-- CTA — Apple-style shimmer gradient button -->
          <button class="ap-btn-primary" onClick=${go} disabled=${phase==='loading'} style=${{marginTop:4}}>
            ${phase==='loading'
              ?html`<span style=${{display:'inline-flex',alignItems:'center',gap:10,justifyContent:'center'}}>
                <span style=${{width:17,height:17,border:'2.5px solid rgba(255,255,255,0.25)',borderTopColor:'#fff',borderRadius:'50%',animation:'ap-spin 0.7s linear infinite',display:'inline-block'}}></span>
                <span>Please wait…</span>
              </span>`
              :html`<span>${tab==='login'?'Sign In':regMode==='create'?'Create Workspace':'Join Workspace'} →</span>`}
          </button>
        </div>

        <!-- Switch tab -->
        <p style=${{fontSize:13.5,color:'rgba(175,170,210,0.4)',textAlign:'center',marginTop:22,lineHeight:1.7}}>
          ${tab==='login'
            ?html`New to VEWIT? <button class="ap-link" onClick=${()=>setTab('register')} style=${{color:'#7e9fff',fontSize:13.5,fontWeight:600}}>Create account</button>`
            :html`Already have an account? <button class="ap-link" onClick=${()=>setTab('login')} style=${{color:'#7e9fff',fontSize:13.5,fontWeight:600}}>Sign in</button>`}
        </p>

        <!-- Help / legal -->
        <div style=${{marginTop:28,paddingTop:20,borderTop:'1px solid rgba(255,255,255,0.05)',display:'flex',justifyContent:'center',gap:20,flexWrap:'wrap'}}>
          <a href="mailto:support@vewit.in" style=${{fontSize:11.5,color:'rgba(160,150,200,0.45)',textDecoration:'none',transition:'color 0.2s',display:'flex',alignItems:'center',gap:5}}
            onMouseEnter=${e=>e.target.style.color='rgba(90,140,255,0.9)'} onMouseLeave=${e=>e.target.style.color='rgba(160,150,200,0.45)'}>
            🛟 support@vewit.in
          </a>
          <a href="mailto:ceo@vewit.in" style=${{fontSize:11.5,color:'rgba(160,150,200,0.45)',textDecoration:'none',transition:'color 0.2s',display:'flex',alignItems:'center',gap:5}}
            onMouseEnter=${e=>e.target.style.color='rgba(168,85,247,0.9)'} onMouseLeave=${e=>e.target.style.color='rgba(160,150,200,0.45)'}>
            🤝 ceo@vewit.in
          </a>
        </div>

      </div>
    `)}
    </div>`;
}


/* ─── TeamSidePanel ────────────────────────────────────────────────────────── */
function TeamSidePanel({cu,onClose,onSelectTeam,selectedTeam,teams,users,projects,tasks,onSetView,onReloadTeams,teamCtx,setTeamCtx,activeTeam}){
  const umap=safe(users).reduce((a,u)=>{a[u.id]=u;return a;},{});
  const [search,setSearch]=useState('');
  const [dashboard,setDashboard]=useState(null); // loaded team dashboard data
  const [loadingDash,setLoadingDash]=useState(false);

  useEffect(()=>{
    if(!selectedTeam){setDashboard(null);return;}
    setLoadingDash(true);
    api.get('/api/teams/'+selectedTeam+'/dashboard').then(d=>{
      setDashboard(d&&!d.error?d:null);
      setLoadingDash(false);
    }).catch(()=>setLoadingDash(false));
  },[selectedTeam]);

  const filtered=safe(teams).filter(t=>!search||t.name.toLowerCase().includes(search.toLowerCase()));

  /* ── Team drill-down dashboard ── */
  if(selectedTeam){
    const team=teams.find(t=>t.id===selectedTeam);
    if(!team)return null;
    const memberIds=JSON.parse(team.member_ids||'[]');
    const lead=umap[team.lead_id];
    const members=memberIds.map(id=>umap[id]).filter(Boolean);
    const sum=dashboard&&dashboard.summary;
    const memberStats=dashboard&&dashboard.member_stats||[];
    const teamProjects=dashboard&&dashboard.projects||[];

    return html`
      <div style=${{width:310,background:'var(--sf)',borderRight:'1px solid var(--bd)',display:'flex',flexDirection:'column',height:'100vh',flexShrink:0,overflow:'hidden'}}>
                <div style=${{padding:'12px 14px',borderBottom:'1px solid var(--bd)',display:'flex',alignItems:'center',gap:8,flexShrink:0}}>
          <button onClick=${()=>onSelectTeam(null)} style=${{background:'none',border:'none',cursor:'pointer',color:'var(--tx3)',fontSize:18,padding:'2px 6px',borderRadius:6,lineHeight:1}} title="Back">←</button>
          <div style=${{flex:1,minWidth:0}}>
            <div style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${team.name}</div>
            ${lead?html`<div style=${{fontSize:10,color:'var(--tx3)'}}>Lead: <b style=${{color:'var(--cy)'}}>${lead.name}</b></div>`:
              html`<div style=${{fontSize:10,color:'var(--tx3)'}}>${members.length} members</div>`}
          </div>
          ${teamCtx===team.id?html`
            <button onClick=${()=>setTeamCtx&&setTeamCtx('')}
              style=${{fontSize:10,padding:'4px 8px',borderRadius:7,border:'1px solid var(--ac)',background:'var(--ac)',color:'var(--ac-tx)',cursor:'pointer',fontWeight:700,flexShrink:0,whiteSpace:'nowrap'}}>
              ✓ Active
            </button>`:html`
            <button onClick=${()=>setTeamCtx&&setTeamCtx(team.id)}
              style=${{fontSize:10,padding:'4px 8px',borderRadius:7,border:'1px solid var(--ac)',background:'transparent',color:'var(--ac)',cursor:'pointer',fontWeight:700,flexShrink:0,whiteSpace:'nowrap'}}>
              Switch →
            </button>`}
          <button onClick=${onClose} style=${{background:'none',border:'none',cursor:'pointer',color:'var(--tx3)',fontSize:16,padding:'2px 6px'}} title="Close">✕</button>
        </div>
                <div style=${{display:'flex',gap:6,padding:'8px 12px',borderBottom:'1px solid var(--bd)',flexShrink:0}}>
          <button onClick=${()=>{setTeamCtx&&setTeamCtx(team.id);onSetView('projects');onClose();}}
            style=${{flex:1,padding:'6px 8px',borderRadius:7,border:'1px solid var(--bd)',background:'var(--sf2)',color:'var(--tx2)',cursor:'pointer',fontSize:11,fontWeight:600,transition:'all .12s'}}
            onMouseEnter=${e=>{e.currentTarget.style.borderColor='var(--ac)';e.currentTarget.style.color='var(--ac)';}}
            onMouseLeave=${e=>{e.currentTarget.style.borderColor='var(--bd)';e.currentTarget.style.color='var(--tx2)';}}>
            📁 Projects
          </button>
          <button onClick=${()=>{setTeamCtx&&setTeamCtx(team.id);onSetView('tasks');onClose();}}
            style=${{flex:1,padding:'6px 8px',borderRadius:7,border:'1px solid var(--bd)',background:'var(--sf2)',color:'var(--tx2)',cursor:'pointer',fontSize:11,fontWeight:600,transition:'all .12s'}}
            onMouseEnter=${e=>{e.currentTarget.style.borderColor='var(--ac)';e.currentTarget.style.color='var(--ac)';}}
            onMouseLeave=${e=>{e.currentTarget.style.borderColor='var(--bd)';e.currentTarget.style.color='var(--tx2)';}}>
            ☑ Tasks
          </button>
          <button onClick=${()=>{setTeamCtx&&setTeamCtx(team.id);onSetView('productivity');onClose();}}
            style=${{flex:1,padding:'6px 8px',borderRadius:7,border:'1px solid var(--bd)',background:'var(--sf2)',color:'var(--tx2)',cursor:'pointer',fontSize:11,fontWeight:600,transition:'all .12s'}}
            onMouseEnter=${e=>{e.currentTarget.style.borderColor='var(--ac)';e.currentTarget.style.color='var(--ac)';}}
            onMouseLeave=${e=>{e.currentTarget.style.borderColor='var(--bd)';e.currentTarget.style.color='var(--tx2)';}}>
            📊 Stats
          </button>
        </div>

        <div style=${{flex:1,overflowY:'auto'}}>
          ${loadingDash?html`<div style=${{textAlign:'center',padding:'40px 0',color:'var(--tx3)',fontSize:12}}>Loading...</div>`:null}

          ${!loadingDash&&sum?html`
                    <div style=${{display:'grid',gridTemplateColumns:'repeat(3,1fr)',borderBottom:'1px solid var(--bd)'}}>
            ${[
              {l:'Projects',v:sum.total_projects,c:'var(--ac)'}, {l:'Tasks',v:sum.total_tasks,c:'var(--tx)'}, {l:'Done',v:sum.completed,c:'var(--gn)'}, {l:'In Prog',v:sum.in_progress,c:'var(--cy)'}, {l:'Blocked',v:sum.blocked,c:'var(--rd)'}, {l:'Pending',v:sum.pending,c:'var(--am)'}, ].map((s,i)=>html`
              <div key=${i} style=${{textAlign:'center',padding:'10px 4px',borderRight:i%3<2?'1px solid var(--bd)':'none',borderBottom:i<3?'1px solid var(--bd)':'none'}}>
                <div style=${{fontSize:18,fontWeight:800,color:s.c,fontFamily:'monospace',lineHeight:1}}>${s.v}</div>
                <div style=${{fontSize:9,color:'var(--tx3)',marginTop:2,textTransform:'uppercase',letterSpacing:.4}}>${s.l}</div>
              </div>`)}
          </div>

                    <div style=${{padding:'10px 12px',borderBottom:'1px solid var(--bd)'}}>
            <div style=${{fontSize:10,fontWeight:700,color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.7,marginBottom:8}}>👥 Member Workload</div>
            ${memberStats.length===0?html`<div style=${{fontSize:11,color:'var(--tx3)',textAlign:'center',padding:'8px 0'}}>No tasks assigned yet</div>`:null}
            ${memberStats.map(m=>html`
              <div key=${m.id} style=${{display:'flex',alignItems:'center',gap:8,marginBottom:8,padding:'7px 8px',background:'var(--sf2)',borderRadius:8,border:'1px solid var(--bd)'}}>
                <${Av} u=${m} size=${28}/>
                <div style=${{flex:1,minWidth:0}}>
                  <div style=${{fontSize:11,fontWeight:600,color:'var(--tx)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${m.name}</div>
                  <div style=${{fontSize:9,color:'var(--tx3)'}}>${m.role}</div>
                </div>
                <div style=${{display:'flex',gap:5,fontSize:10,fontFamily:'monospace'}}>
                  <span style=${{color:'var(--gn)',fontWeight:700}} title="Completed">${m.completed}✓</span>
                  <span style=${{color:'var(--cy)'}} title="In Progress">${m.in_progress}⟳</span>
                  ${m.blocked>0?html`<span style=${{color:'var(--rd)',fontWeight:700}} title="Blocked">${m.blocked}✗</span>`:null}
                  ${m.overdue>0?html`<span style=${{color:'var(--am)',fontWeight:700}} title="Overdue">${m.overdue}!</span>`:null}
                </div>
              </div>`)}
          </div>

                    <div style=${{padding:'10px 12px'}}>
            <div style=${{fontSize:10,fontWeight:700,color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.7,marginBottom:8}}>📁 Projects (${teamProjects.length})</div>
            ${teamProjects.length===0?html`<div style=${{fontSize:11,color:'var(--tx3)',textAlign:'center',padding:'8px 0'}}>No projects yet</div>`:null}
            ${teamProjects.map(p=>{
              const pt=safe(tasks).filter(t=>t.project===p.id);
              const done=pt.filter(t=>t.stage==='completed').length;
              const pc=pt.length?Math.round(pt.reduce((a,t)=>a+(t.pct||0),0)/pt.length):(p.progress||0);
              return html`
                <div key=${p.id} style=${{padding:'8px 10px',borderRadius:8,border:'1px solid var(--bd)',marginBottom:6,background:'var(--sf2)',cursor:'pointer',borderLeft:'3px solid '+p.color,transition:'background .1s'}}
                  onClick=${()=>{onSetView('projects');onClose();}}
                  onMouseEnter=${e=>e.currentTarget.style.background='rgba(255,255,255,.06)'}
                  onMouseLeave=${e=>e.currentTarget.style.background='var(--sf2)'}>
                  <div style=${{fontSize:12,fontWeight:600,color:'var(--tx)',marginBottom:4,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${p.name}</div>
                  <div style=${{display:'flex',alignItems:'center',gap:6,marginBottom:4}}>
                    <div style=${{flex:1,height:3,background:'var(--bd)',borderRadius:100,overflow:'hidden'}}>
                      <div style=${{height:'100%',width:pc+'%',background:p.color,borderRadius:100}}></div>
                    </div>
                    <span style=${{fontSize:9,fontFamily:'monospace',color:'var(--tx3)'}}>${pc}%</span>
                  </div>
                  <div style=${{display:'flex',gap:8,fontSize:10}}>
                    <span style=${{color:'var(--tx3)'}}>${pt.length} tasks</span>
                    <span style=${{color:'var(--gn)'}}>${done} done</span>
                    <span style=${{color:'var(--am)'}}>${pt.length-done} open</span>
                  </div>
                </div>`;
            })}
          </div>`:null}

          ${!loadingDash&&!sum?html`<div style=${{textAlign:'center',padding:'40px 12px',color:'var(--tx3)',fontSize:12}}>
            <div style=${{fontSize:28,marginBottom:8}}>📊</div>
            No task data found for this team yet.<br/>Assign tasks to team members to see stats here.
          </div>`:null}
        </div>
      </div>`;
  }

  /* ── Team list cards ── */
  return html`
    <div style=${{width:240,background:'var(--sf)',borderRight:'1px solid var(--bd)',display:'flex',flexDirection:'column',height:'100vh',flexShrink:0}}>
      <div style=${{padding:'12px 14px',borderBottom:'1px solid var(--bd)',display:'flex',alignItems:'center',justifyContent:'space-between',flexShrink:0}}>
        <div>
          <span style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em'}}>👥 Teams</span>
          ${activeTeam?html`<div style=${{fontSize:10,color:'var(--ac)',marginTop:2}}>Viewing: <b>${activeTeam.name}</b></div>`:html`<div style=${{fontSize:10,color:'var(--tx3)',marginTop:2}}>All workspace data</div>`}
        </div>
        <div style=${{display:'flex',gap:5,alignItems:'center'}}>
          ${activeTeam?html`<button onClick=${()=>setTeamCtx&&setTeamCtx('')} style=${{fontSize:10,padding:'3px 8px',borderRadius:6,border:'1px solid var(--bd)',background:'transparent',color:'var(--tx3)',cursor:'pointer',whiteSpace:'nowrap'}}>× All</button>`:null}
          ${cu&&(cu.role==='Admin'||cu.role==='Manager')?html`
            <button title="Manage Teams" onClick=${()=>{onSetView('team');}}
              style=${{fontSize:10,padding:'3px 8px',borderRadius:6,border:'1px solid var(--ac)',background:'transparent',color:'var(--ac)',cursor:'pointer',whiteSpace:'nowrap',fontWeight:600}}>
              ⚙ Manage
            </button>`:null}
          <button onClick=${onClose} style=${{background:'none',border:'none',cursor:'pointer',color:'var(--tx3)',fontSize:16,padding:'2px 6px'}} title="Close">✕</button>
        </div>
      </div>
      <div style=${{padding:'8px 10px',borderBottom:'1px solid var(--bd)',flexShrink:0}}>
        <input class="inp" placeholder="Search teams..." value=${search}
          style=${{height:26,fontSize:11,width:'100%'}}
          onInput=${e=>setSearch(e.target.value)}/>
      </div>
      <div style=${{flex:1,overflowY:'auto',padding:'6px'}}>
        ${filtered.length===0?html`
          <div style=${{textAlign:'center',padding:'24px 8px',color:'var(--tx3)',fontSize:12}}>
            ${safe(teams).length===0?html`<div><div style=${{fontSize:28,marginBottom:6}}>🏷</div>No teams yet.${cu&&(cu.role==='Admin'||cu.role==='Manager')?html`<br/>Click <b>⚙ Manage</b> above to create teams.`:html`<br/>Ask your Admin to create teams.`}</div>`:'No teams match your search.'}
          </div>`:null}
        ${filtered.map(team=>{
          const memberIds=JSON.parse(team.member_ids||'[]');
          const lead=umap[team.lead_id];
          const members=memberIds.map(id=>umap[id]).filter(Boolean);
          const teamTasks=safe(tasks).filter(t=>{
            const byTeam=t.team_id===team.id;
            const byMember=t.assignee&&memberIds.includes(t.assignee);
            return byTeam||byMember;
          });
          const done=teamTasks.filter(t=>t.stage==='completed').length;
          const blocked=teamTasks.filter(t=>t.stage==='blocked').length;
          const teamProjs=new Set(teamTasks.map(t=>t.project).filter(Boolean)).size;
          return html`
            <div key=${team.id}
              style=${{padding:'10px 12px',borderRadius:10,border:'2px solid '+(teamCtx===team.id?'var(--ac)':'var(--bd)'),marginBottom:7,background:teamCtx===team.id?'rgba(90,140,255,.06)':'var(--sf2)',cursor:'pointer',transition:'all .12s'}}
              onClick=${()=>{
                onSelectTeam(team.id);
                setTeamCtx&&setTeamCtx(team.id);
              }}
              onMouseEnter=${e=>{e.currentTarget.style.background='rgba(255,255,255,.06)';e.currentTarget.style.borderColor='var(--ac)77';}}
              onMouseLeave=${e=>{e.currentTarget.style.background=teamCtx===team.id?'rgba(90,140,255,.06)':'var(--sf2)';e.currentTarget.style.borderColor=teamCtx===team.id?'var(--ac)':'var(--bd)';}}>
              <div style=${{display:'flex',alignItems:'center',gap:7,marginBottom:7}}>
                <div style=${{width:9,height:9,borderRadius:2,background:teamCtx===team.id?'var(--ac)':'var(--tx3)',flexShrink:0}}></div>
                <span style=${{fontSize:12,fontWeight:700,color:'var(--tx)',flex:1,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${team.name}</span>
                ${teamCtx===team.id?html`
                  <span style=${{fontSize:9,color:'var(--ac)',fontWeight:700,background:'rgba(90,140,255,.10)',padding:'2px 6px',borderRadius:4,flexShrink:0}}>ACTIVE</span>`:null}
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="var(--tx3)" strokeWidth="2.5" strokeLinecap="round"><polyline points="9 18 15 12 9 6"/></svg>
              </div>
              ${lead?html`<div style=${{fontSize:10,color:'var(--tx3)',marginBottom:6}}>Lead: <b style=${{color:'var(--cy)'}}>${lead.name}</b></div>`:null}
                            <div style=${{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:4,marginBottom:7}}>
                ${[['Tasks',teamTasks.length,'var(--tx)'],['Done',done,'var(--gn)'],['Proj',teamProjs,'var(--ac)']].map(([l,v,c])=>html`
                  <div key=${l} style=${{textAlign:'center',padding:'4px 2px',background:'var(--sf)',borderRadius:5,border:'1px solid var(--bd)'}}>
                    <div style=${{fontSize:13,fontWeight:700,color:c,fontFamily:'monospace',lineHeight:1}}>${v}</div>
                    <div style=${{fontSize:8,color:'var(--tx3)',marginTop:1,textTransform:'uppercase'}}>${l}</div>
                  </div>`)}
              </div>
              ${blocked>0?html`<div style=${{fontSize:10,color:'var(--rd)',fontWeight:600,marginBottom:6}}>⚠ ${blocked} blocked task${blocked!==1?'s':''}</div>`:null}
                            <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between'}}>
                <div style=${{display:'flex'}}>
                  ${members.slice(0,5).map((m,i)=>html`
                    <div key=${m.id} title=${m.name} style=${{marginLeft:i>0?-5:0,border:'1.5px solid var(--sf2)',borderRadius:'50%',zIndex:5-i}}>
                      <${Av} u=${m} size=${20}/>
                    </div>`)}
                  ${members.length>5?html`<span style=${{fontSize:9,color:'var(--tx3)',marginLeft:5,alignSelf:'center'}}>+${members.length-5}</span>`:null}
                </div>
                <span style=${{fontSize:9,color:'var(--tx3)'}}>${members.length} member${members.length!==1?'s':''}</span>
              </div>
            </div>`;
        })}
      </div>
    </div>`;
}

/* ─── Sidebar ─────────────────────────────────────────────────────────────── */
/* ─── QRCodeDisplay — client-side QR via QRCode.js CDN ─────────────────────── */
function QRCodeDisplay({otpauth,size}){
  const ref=useRef(null);
  const sz=size||200;
  useEffect(()=>{
    if(!ref.current||!otpauth)return;
    ref.current.innerHTML='';
    const render=()=>{
      if(window.QRCode){
        try{
          new window.QRCode(ref.current,{
            text:otpauth,width:sz,height:sz,
            colorDark:'#000000',colorLight:'#ffffff',
            correctLevel:window.QRCode.CorrectLevel.M
          });
          return;
        }catch(e){}
      }
      // Fallback: QR Server API (no lib needed)
      const img=document.createElement('img');
      img.src='https://api.qrserver.com/v1/create-qr-code/?size='+sz+'x'+sz+'&data='+encodeURIComponent(otpauth)+'&ecc=M&margin=8';
      img.style.cssText='width:'+sz+'px;height:'+sz+'px;display:block;image-rendering:pixelated;border-radius:4px';
      img.alt='QR Code';
      ref.current.appendChild(img);
    };
    if(window.QRCode) render();
    else {
      // Wait briefly for QRCode.js to load
      let tries=0;
      const t=setInterval(()=>{
        if(window.QRCode||tries>20){clearInterval(t);render();}
        tries++;
      },150);
    }
  },[otpauth,sz]);
  return html`<div ref=${ref} style=${{width:sz+'px',height:sz+'px',display:'inline-flex',alignItems:'center',justifyContent:'center'}}></div>`;
}

/* ─── PersonalTwoFAToggle — profile panel ─────────────────────────────────── */
function PersonalTwoFAToggle({cu,setCu}){
  const [configured,setConfigured]=useState(()=>!!(cu&&(cu.totp_configured||cu.totp_verified)));
  const [showSetup,setShowSetup]=useState(false);
  const [totpData,setTotpData]=useState(null);
  const [verifyToken,setVerifyToken]=useState('');
  const [verifying,setVerifying]=useState(false);
  const [resetting,setResetting]=useState(false);
  const [msg,setMsg]=useState('');
  const inpRef=useRef(null);

  const startSetup=async()=>{
    setMsg('');
    const r=await api.post('/api/auth/totp/setup',{});
    if(r.error){setMsg(r.error);return;}
    setTotpData(r);setShowSetup(true);setVerifyToken('');
    setTimeout(()=>{if(inpRef.current)inpRef.current.focus();},400);
  };

  const confirmSetup=async()=>{
    const tok=verifyToken.replace(/\s/g,'');
    if(tok.length!==6){setMsg('Enter the 6-digit code from your app.');return;}
    setVerifying(true);setMsg('');
    const r=await api.post('/api/auth/totp/verify-setup',{token:tok});
    setVerifying(false);
    if(r.error){setMsg(r.error);return;}
    setConfigured(true);setShowSetup(false);setTotpData(null);
    setCu&&setCu(prev=>({...prev,totp_configured:true,totp_verified:1}));
    setMsg('✓ Google Authenticator enabled!');
    setTimeout(()=>setMsg(''),3000);
  };

  const resetTotp=async()=>{
    if(!window.confirm('Remove Google Authenticator? You can re-enable it anytime.'))return;
    setResetting(true);
    const r=await api.post('/api/auth/totp/reset',{});
    setResetting(false);
    if(r.error){setMsg(r.error);return;}
    setConfigured(false);
    setCu&&setCu(prev=>({...prev,totp_configured:false,totp_verified:0}));
    setMsg('2FA removed');setTimeout(()=>setMsg(''),2000);
  };

  return html`
    <div>
      <div style=${{display:'flex',alignItems:'center',gap:10,marginBottom:(msg||showSetup)?8:0}}>
        <div style=${{flex:1}}>
          <div style=${{fontSize:12,fontWeight:700,color:'var(--tx)',display:'flex',alignItems:'center',gap:6}}>
            🔐 Authenticator App
            ${configured?html`<span style=${{fontSize:9,padding:'2px 7px',borderRadius:100,background:'rgba(74,222,128,0.15)',color:'#4ade80',fontWeight:700}}>ACTIVE</span>`:null}
          </div>
          <div style=${{fontSize:10,color:'var(--tx3)',marginTop:2}}>
            ${configured?'Protects your login with 2FA':'Not set up — add login security'}
          </div>
        </div>
        ${!configured?html`
          <button class="btn bp" style=${{padding:'5px 12px',fontSize:10,flexShrink:0,borderRadius:8}} onClick=${startSetup}>
            📱 Setup
          </button>`:html`
          <button class="btn brd" style=${{padding:'5px 10px',fontSize:10,flexShrink:0,borderRadius:8}} onClick=${resetTotp} disabled=${resetting}>
            ${resetting?'…':'Remove'}
          </button>`}
      </div>

      ${msg?html`<div style=${{fontSize:10,color:msg.startsWith('✓')?'#4ade80':'#f87171',fontWeight:600,padding:'3px 0'}}>${msg}</div>`:null}

      ${showSetup&&totpData?html`
        <div style=${{marginTop:10,padding:14,background:'var(--sf2)',borderRadius:12,border:'1px solid var(--bd)'}}>
          <div style=${{textAlign:'center',marginBottom:12}}>
            <div style=${{fontSize:11,fontWeight:700,color:'var(--tx)',marginBottom:8}}>📱 Scan with Google Authenticator</div>
            <div style=${{display:'inline-block',background:'white',padding:8,borderRadius:10,border:'2px solid var(--bd)',boxShadow:'0 4px 16px rgba(0,0,0,0.15)'}}>
              <${QRCodeDisplay} otpauth=${totpData.otpauth} size=${180}/>
            </div>
            <div style=${{fontSize:9,color:'var(--tx3)',marginTop:5}}>Tap + in Google Authenticator → Scan QR code</div>
          </div>

          <div style=${{marginBottom:10}}>
            <div style=${{fontSize:9,fontWeight:700,color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.6,marginBottom:4}}>Or enter key manually:</div>
            <div style=${{fontFamily:'monospace',fontSize:11,background:'var(--bg)',padding:'7px 10px',borderRadius:8,border:'1px solid var(--bd)',color:'var(--ac)',textAlign:'center',fontWeight:700,letterSpacing:3,userSelect:'all',wordBreak:'break-all'}}>${totpData.secret}</div>
          </div>

          <div style=${{fontSize:10,fontWeight:600,color:'var(--tx2)',marginBottom:6}}>Enter the 6-digit code from your app:</div>
          <input class="inp" ref=${inpRef} type="text" inputMode="numeric" pattern="[0-9]*"
            value=${verifyToken}
            onInput=${e=>setVerifyToken(e.target.value.replace(/\D/g,'').slice(0,6))}
            onKeyDown=${e=>e.key==='Enter'&&confirmSetup()}
            placeholder="000000"
            style=${{textAlign:'center',fontSize:24,fontWeight:700,fontFamily:'monospace',letterSpacing:10,marginBottom:8,height:52}}/>
          ${msg&&!msg.startsWith('✓')?html`<div style=${{fontSize:10,color:'#f87171',marginBottom:6,textAlign:'center'}}>${msg}</div>`:null}
          <div style=${{display:'flex',gap:7}}>
            <button class="btn bg" style=${{flex:1,justifyContent:'center',fontSize:11}} onClick=${()=>{setShowSetup(false);setTotpData(null);setMsg('');}}>Cancel</button>
            <button class="btn bp" style=${{flex:1,justifyContent:'center',fontSize:11}} onClick=${confirmSetup} disabled=${verifying||verifyToken.replace(/\s/g,'').length!==6}>
              ${verifying?html`<span class="spin"></span>`:'✓ Confirm & Enable'}
            </button>
          </div>
        </div>`:null}
    </div>`;
}

function Sidebar({cu,view,setView,onLogout,unread,dmUnread,col,setCol,wsName,dark,setDark,teams,users,projects,tasks,teamCtx,setTeamCtx,activeTeam,wsDmEnabled=true,onlineUsers=new Set()}){
  const fmtTime=s=>{const m=Math.floor(s/60);const sec=s%60;return m+':'+(sec<10?'0':'')+sec;};
  const isAdminManager=cu&&(cu.role==='Admin'||cu.role==='Manager');
  const baseView=(view||'dashboard').split(':')[0];

  const NAV_ICONS={
    dashboard:    html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/></svg>`, projects:     html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>`, tasks:        html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>`, messages:     html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`, tickets:      html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M2 9a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v1.5a1.5 1.5 0 0 0 0 3V15a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-1.5a1.5 1.5 0 0 0 0-3V9z"/><line x1="9" y1="7" x2="9" y2="17" strokeDasharray="2 2"/></svg>`, timeline:     html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/><line x1="8" y1="14" x2="10" y2="14"/><line x1="8" y1="18" x2="14" y2="18"/></svg>`, productivity: html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/><line x1="2" y1="20" x2="22" y2="20"/></svg>`, reminders:    html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`, team:         html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>`, dm:           html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>`,
    'ai-docs':    html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><circle cx="10" cy="13" r="2"/><path d="M20 21l-4.35-4.35"/></svg>`,
    timesheet:    html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="4" x2="9" y2="9"/><path d="M7 13h2l1 2 2-4 1 2h2"/></svg>`,
  };
  const adminNav=[
    {id:'dashboard', label:'Dashboard'}, {id:'projects', label:'Projects'}, {id:'tasks', label:'Kanban Board'}, {id:'messages', label:'Channels'}, {id:'dm', label:'Direct Messages'}, {id:'tickets', label:'Tickets'}, {id:'timeline', label:'Timeline Tracker'}, {id:'productivity',label:'Dev Productivity'}, {id:'reminders', label:'Reminders'}, {id:'team', label:'Team Management'}, {id:'ai-docs', label:'AI Docs', badge:'AI'}, {id:'timesheet', label:'Timesheet', badge:'New', hint:'Shift+L'}, ];
  const devNav=[
    {id:'dashboard', label:'Dashboard'}, {id:'projects', label:'Projects'}, {id:'tasks', label:'Kanban Board'}, {id:'messages', label:'Channels'}, {id:'dm', label:'Direct Messages'}, {id:'tickets', label:'Tickets'}, {id:'timeline', label:'Timeline'}, {id:'reminders', label:'Reminders'}, {id:'timesheet', label:'Timesheet'}, ];
  const navItems=(isAdminManager?adminNav:devNav).filter(it=>
    it.id!=='dm'||(wsDmEnabled||isAdminManager)
  );

  const themeIcon=dark
    ?html`<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>`
    :html`<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>`;

  const W=col?64:200; // collapsed=64px, expanded=200px

  return html`
    <aside style=${{
      width:W,minWidth:W,maxWidth:W,
      background:'linear-gradient(180deg,#0d0b1e 0%,#13112b 60%,#0d0b1e 100%)',
      display:'flex',flexDirection:'column',
      height:'100vh',flexShrink:0,overflow:'visible',
      borderRight:'1px solid rgba(90,94,247,0.18)',
      transition:'width .2s ease,min-width .2s ease,max-width .2s ease',
      position:'relative',
      boxShadow:'2px 0 32px rgba(10,8,30,0.45),inset -1px 0 0 rgba(90,94,247,0.08)'
    }}>

            <div style=${{
        padding:col?'14px 0':'12px 14px', display:'flex',alignItems:'center', gap:8,flexShrink:0, borderBottom:'1px solid rgba(90,94,247,0.12)', justifyContent:col?'center':'flex-start', minHeight:52
      }}>
        <div style=${{width:28,height:28,borderRadius:8,background:'linear-gradient(135deg,#5a5ef7,#a855f7)',display:'flex',alignItems:'center',justifyContent:'center',flexShrink:0,boxShadow:'0 2px 12px rgba(90,94,247,0.5),0 0 0 1px rgba(255,255,255,0.1)'}}>
          <svg width="14" height="14" viewBox="0 0 64 64" fill="none"><circle cx="32" cy="32" r="9" fill="white"/><circle cx="32" cy="11" r="6" fill="white" opacity=".9"/><circle cx="51" cy="43" r="6" fill="white" opacity=".9"/><circle cx="13" cy="43" r="6" fill="white" opacity=".9"/><line x1="32" y1="17" x2="32" y2="23" stroke="white" strokeWidth="3.5" strokeLinecap="round"/><line x1="46" y1="40" x2="40" y2="36" stroke="white" strokeWidth="3.5" strokeLinecap="round"/><line x1="18" y1="40" x2="24" y2="36" stroke="white" strokeWidth="3.5" strokeLinecap="round"/></svg>
        </div>
        ${!col?html`<div style=${{flex:1,minWidth:0}}>
          <div style=${{fontSize:12,fontWeight:700,color:'#ffffff',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${wsName||'VEWIT'}</div>
          ${activeTeam?html`<div style=${{fontSize:10,color:'var(--ac)',fontWeight:600,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',display:'flex',alignItems:'center',gap:4}}>
            ${!isAdminManager?html`<span style=${{color:'rgba(255,255,255,.3)',fontWeight:400}}>My Team</span>`:null}
            ${activeTeam.name}
          </div>`
          :html`<div style=${{fontSize:10,color:'rgba(203,213,225,0.6)'}}>Workspace</div>`}
        </div>`:null}
      </div>

            <nav style=${{flex:1,overflowY:'auto',padding:'8px 6px',display:'flex',flexDirection:'column',gap:2}}>
        ${navItems.map(it=>html`
          <button key=${it.id}
            title=${col?it.label:''}
            onClick=${()=>setView(it.id)}
            style=${{
              display:'flex',alignItems:'center', gap:col?0:10, width:'100%', padding:col?'10px 0':'9px 10px', borderRadius:9,border:'none',cursor:'pointer',
              background:baseView===it.id?'rgba(90,94,247,0.18)':'transparent',
              color:baseView===it.id?'#a5b4fc':'rgba(200,195,240,0.65)',
              fontSize:12,fontWeight:baseView===it.id?700:500,
              transition:'all .12s',textAlign:'left',
              borderLeft:baseView===it.id&&!col?'2px solid #818cf8':'2px solid transparent',
              justifyContent:col?'center':'flex-start', position:'relative',
              boxShadow:baseView===it.id?'inset 0 0 0 1px rgba(129,140,248,0.15),0 0 20px rgba(90,94,247,0.1)':'none'
            }}
            onMouseEnter=${e=>{if(baseView!==it.id){e.currentTarget.style.background='rgba(90,94,247,0.10)';e.currentTarget.style.color='#a5b4fc';}}}
            onMouseLeave=${e=>{if(baseView!==it.id){e.currentTarget.style.background='transparent';e.currentTarget.style.color='rgba(200,195,240,0.65)';}}}>
            <span style=${{flexShrink:0,width:col?'auto':18,display:'flex',alignItems:'center',justifyContent:'center',opacity:baseView===it.id?1:.8}}>${NAV_ICONS[it.id]||null}</span>
            ${!col?html`<span style=${{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',fontSize:12,flex:1}}>${it.label}</span>`:null}
            ${it.badge&&!col?html`<span style=${{fontSize:8,fontWeight:800,padding:'1px 5px',borderRadius:4,background:'linear-gradient(135deg,#5a5ef7,#a855f7)',color:'#fff',letterSpacing:'.04em',flexShrink:0}}>${it.badge}</span>`:null}
            ${it.id==='notifs'&&unread>0?html`<span style=${{
              position:'absolute',top:6,right:col?6:10, minWidth:16,height:16,borderRadius:8, background:'var(--rd)',color:'#fff', fontSize:9,fontWeight:700, display:'flex',alignItems:'center',justifyContent:'center', padding:'0 4px'
            }}>${unread>9?'9+':unread}</span>`:null}
            ${it.id==='dm'&&dmUnread.reduce((a,x)=>a+(x.cnt||0),0)>0?html`<span style=${{
              position:'absolute',top:6,right:col?6:10, minWidth:16,height:16,borderRadius:8, background:'var(--cy)',color:'#fff', fontSize:9,fontWeight:700, display:'flex',alignItems:'center',justifyContent:'center', padding:'0 4px'
            }}>${dmUnread.reduce((a,x)=>a+(x.cnt||0),0)}</span>`:null}
          </button>`)}

      </nav>

            <div style=${{padding:'8px 6px',borderTop:'1px solid rgba(90,94,247,0.12)',display:'flex',flexDirection:'column',gap:2,flexShrink:0}}>

        <button title=${dark?'Light Mode':'Dark Mode'} onClick=${()=>{setDark(d=>{const n=!d;try{localStorage.setItem('pf_dark',n?'1':'0');}catch{}return n;})}}
          style=${{display:'flex',alignItems:'center',gap:col?0:9,width:'100%',padding:col?'9px 0':'8px 10px',borderRadius:9,border:'none',cursor:'pointer',background:'transparent',color:'rgba(203,213,225,0.65)',transition:'all .12s',justifyContent:col?'center':'flex-start'}}
          onMouseEnter=${e=>{e.currentTarget.style.background='rgba(90,94,247,0.12)';e.currentTarget.style.color='#a5b4fc';}}
          onMouseLeave=${e=>{e.currentTarget.style.background='transparent';e.currentTarget.style.color='rgba(203,213,225,0.65)';}}>
          <span style=${{fontSize:15,flexShrink:0,width:col?'auto':18,display:'flex',alignItems:'center',justifyContent:'center'}}>${themeIcon}</span>
          ${!col?html`<span style=${{fontSize:12}}>${dark?'Light Mode':'Dark Mode'}</span>`:null}
        </button>
        ${(cu&&(cu.role==='Admin'||cu.role==='Manager'||cu.role==='TeamLead'))?html`
          <button title=${col?'Settings':''} onClick=${()=>setView('settings')}
            style=${{display:'flex',alignItems:'center',gap:col?0:9,width:'100%',padding:col?'9px 0':'8px 10px',borderRadius:9,border:'none',cursor:'pointer', background:baseView==='settings'?'rgba(37,99,235,0.18)':'transparent', color:baseView==='settings'?'var(--ac)':'rgba(255,255,255,.35)', transition:'all .12s',justifyContent:col?'center':'flex-start'}}
            onMouseEnter=${e=>{if(baseView!=='settings'){e.currentTarget.style.background='rgba(90,94,247,0.12)';e.currentTarget.style.color='#a5b4fc';}}}
            onMouseLeave=${e=>{if(baseView!=='settings'){e.currentTarget.style.background='transparent';e.currentTarget.style.color='rgba(255,255,255,.35)';}}}>
            <span style=${{fontSize:15,flexShrink:0,width:col?'auto':18,textAlign:'center'}}>⚙️</span>
            ${!col?html`<span style=${{fontSize:12}}>Settings</span>`:null}
          </button>`:null}
        <button title=${col?'Sign out':''} onClick=${onLogout}
          style=${{display:'flex',alignItems:'center',gap:col?0:9,width:'100%',padding:col?'9px 0':'8px 10px',borderRadius:9,border:'none',cursor:'pointer',background:'transparent',color:'rgba(203,213,225,0.55)',transition:'all .12s',justifyContent:col?'center':'flex-start'}}
          onMouseEnter=${e=>{e.currentTarget.style.background='rgba(239,68,68,.1)';e.currentTarget.style.color='#f87171';}}
          onMouseLeave=${e=>{e.currentTarget.style.background='transparent';e.currentTarget.style.color='rgba(203,213,225,0.55)';}}>
          <span style=${{fontSize:15,flexShrink:0,width:col?'auto':18,textAlign:'center'}}>↪</span>
          ${!col?html`<span style=${{fontSize:12}}>Sign out</span>`:null}
        </button>
      </div>
      <!-- Sidebar orb decoration -->
      ${!col?html`<div style=${{position:'absolute',bottom:100,left:-40,width:120,height:120,borderRadius:'50%',background:'radial-gradient(circle,rgba(90,94,247,0.12) 0%,transparent 70%)',pointerEvents:'none',zIndex:0}}></div>`:null}
      <button title=${col?'Expand sidebar':'Collapse sidebar'} onClick=${()=>setCol(c=>!c)}
        style=${{
          position:'absolute', left:col?64:200, top:'50%', transform:'translateY(-50%)', zIndex:200, width:14, height:40, background:'#0d0b1e', border:'1px solid rgba(90,94,247,0.2)', borderLeft:'none', borderRadius:'0 6px 6px 0', cursor:'pointer', display:'flex', alignItems:'center', justifyContent:'center', color:'rgba(255,255,255,.35)', transition:'left .2s ease, background .12s, color .12s', padding:0, }}
        onMouseEnter=${e=>{e.currentTarget.style.background='#1a1a1a';e.currentTarget.style.color='rgba(255,255,255,.8)';}}
        onMouseLeave=${e=>{e.currentTarget.style.background='#0f172a';e.currentTarget.style.color='rgba(148,163,184,0.5)';}}>
        <svg width="8" height="12" viewBox="0 0 8 12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          ${col
            ?html`<polyline points="2 2 6 6 2 10"/>`
            :html`<polyline points="6 2 2 6 6 10"/>`}
        </svg>
      </button>
    </aside>`;
}

/* ─── Header ──────────────────────────────────────────────────────────────── */
function Header({title,sub,dark,setDark,extra,cu,setCu,upcomingReminders,onViewReminders,notifs,onNotifClick,onMarkAllRead,onClearAll,activeTeam,teams,setTeamCtx}){
  const [showNP,setShowNP]=useState(false);
  const [showProfile,setShowProfile]=useState(false);
  const [uploadMsg,setUploadMsg]=useState('');
  const now=new Date();
  const todayStr=now.toLocaleDateString('en-US',{day:'numeric',month:'short'});
  const upcoming=safe(upcomingReminders).slice(0,4);
  const fmtT=dt=>{const d=new Date(dt);return d.getHours().toString().padStart(2,'0')+':'+d.getMinutes().toString().padStart(2,'0');};
  const unread=safe(notifs).filter(n=>!n.read).length;
  const NI={task_assigned:'✅',status_change:'🔄',comment:'💬',deadline:'⏰',dm:'📨',project_added:'📁',reminder:'🔔',call:'📞'};
  const NC={task_assigned:'var(--ac)',status_change:'var(--cy)',comment:'var(--pu)',deadline:'var(--am)',dm:'var(--cy)',project_added:'var(--gn)',reminder:'var(--am)',call:'#22c55e'};
  const npRef=useRef(null);
  const prRef=useRef(null);
  const prImgRef=useRef(null);
  useEffect(()=>{
    if(!showNP)return;
    const h=e=>{if(npRef.current&&!npRef.current.contains(e.target))setShowNP(false);};
    document.addEventListener('mousedown',h);return()=>document.removeEventListener('mousedown',h);
  },[showNP]);
  useEffect(()=>{
    if(!showProfile)return;
    const h=e=>{if(prRef.current&&!prRef.current.contains(e.target))setShowProfile(false);};
    document.addEventListener('mousedown',h);return()=>document.removeEventListener('mousedown',h);
  },[showProfile]);
  return html`
    <div style=${{flexShrink:0,background:'var(--sf)',borderBottom:'1px solid var(--bd)',position:'relative',zIndex:100,boxShadow:'0 1px 0 rgba(99,102,241,0.08),0 2px 12px rgba(15,14,23,0.05)'}}>
      <div style=${{padding:'0 18px',height:54,display:'flex',alignItems:'center',gap:10}}>
                <div style=${{display:'flex',alignItems:'center',gap:8,flexShrink:0,padding:'5px 14px 5px 10px',background:'#1e3a5f',borderRadius:100,cursor:'pointer',border:'1px solid rgba(37,99,235,0.25)',transition:'all .14s'}} onClick=${onViewReminders}>
          <svg width="13" height="13" viewBox="0 0 64 64" fill="none"><circle cx="32" cy="32" r="7" fill="#60a5fa"/><circle cx="32" cy="13" r="4" fill="#60a5fa" opacity="0.9"/><circle cx="48" cy="43" r="4" fill="#60a5fa" opacity="0.9"/><circle cx="16" cy="43" r="4" fill="#60a5fa" opacity="0.9"/><line x1="32" y1="17" x2="32" y2="25" stroke="#60a5fa" strokeWidth="2.5" strokeLinecap="round"/><line x1="44" y1="40" x2="38" y2="36" stroke="#5a8cff" strokeWidth="2.5" strokeLinecap="round"/><line x1="20" y1="40" x2="26" y2="36" stroke="#5a8cff" strokeWidth="2.5" strokeLinecap="round"/></svg>
          <span style=${{fontSize:11,fontWeight:700,color:'#bfdbfe',letterSpacing:'.3px'}}>Your Reminders</span>
          <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,.35)" strokeWidth="2" strokeLinecap="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
          <span style=${{fontSize:11,color:'#93c5fd',fontWeight:700}}>${todayStr}</span>
        </div>
                        <div style=${{flex:1,overflowX:'auto',scrollbarWidth:'none',msOverflowStyle:'none'}}>
          <div style=${{height:40,background:'#0f172a',borderRadius:100,display:'flex',alignItems:'center',padding:'0 14px',gap:0,position:'relative',minWidth:0,overflow:'hidden',border:'1px solid rgba(37,99,235,0.15)'}}>
            ${upcoming.length===0?html`
              <div style=${{display:'flex',alignItems:'center',gap:10,width:'100%',justifyContent:'center'}}>
                <span style=${{fontSize:11,color:'rgba(148,163,184,0.8)',fontStyle:'italic',letterSpacing:'.2px'}}>No reminders today</span>
                <button onClick=${onViewReminders} style=${{fontSize:10,padding:'3px 12px',height:22,borderRadius:100,background:'#1d4ed8',color:'#ffffff',border:'none',cursor:'pointer',fontWeight:700,letterSpacing:'.2px'}}>+ Add</button>
              </div>
            `:html`
              <div style=${{display:'flex',alignItems:'center',gap:0,width:'100%',overflowX:'auto',scrollbarWidth:'none',position:'relative'}}>
                <div style=${{position:'absolute',top:'50%',left:0,right:40,height:1,background:'linear-gradient(90deg,rgba(37,99,235,0.08) 0%,rgba(96,165,250,0.4) 55%,rgba(37,99,235,0.08) 100%)',transform:'translateY(-50%)',borderRadius:2,zIndex:0}}></div>
                ${upcoming.map((r,i)=>{
                  const isNow=Math.abs(new Date(r.remind_at)-new Date())<1800000;
                  const abbr=(r.task_title||'').split(' ').slice(0,2).join(' ');
                  const tStr=fmtT(r.remind_at);
                  return html`
                    <div key=${r.id} style=${{display:'flex',flexDirection:'column',alignItems:'center',marginRight:i<upcoming.length-1?28:0,flexShrink:0,position:'relative',zIndex:1,cursor:'pointer'}} onClick=${onViewReminders} title=${r.task_title}>
                      <div style=${{position:'relative'}}>
                        ${cu&&cu.avatar_data&&cu.avatar_data.startsWith('data:image')?
                          html`<img src=${cu.avatar_data} style=${{width:isNow?28:22,height:isNow?28:22,borderRadius:'50%',objectFit:'cover',border:isNow?'2px solid #22c55e':'2px solid rgba(90,140,255,.35)',boxShadow:isNow?'0 0 0 3px rgba(34,197,94,.2)':'none',transition:'all .18s'}}/>`:
                          html`<div style=${{width:isNow?28:22,height:isNow?28:22,borderRadius:'50%',background:isNow?'linear-gradient(135deg,#22c55e,#16a34a)':'linear-gradient(135deg,#3b82f6,#2563eb)',border:isNow?'2px solid #22c55e':'2px solid rgba(96,165,250,0.5)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:isNow?10:8,fontWeight:700,color:isNow?'#fff':'#fff',boxShadow:isNow?'0 0 0 3px rgba(34,197,94,.2)':'0 0 8px rgba(59,130,246,.3)',transition:'all .18s'}}>
                            ${(r.task_title||'?').charAt(0).toUpperCase()}
                          </div>`}
                        ${isNow?html`<div style=${{position:'absolute',bottom:-1,right:-1,width:7,height:7,borderRadius:'50%',background:'#22c55e',border:'1.5px solid #111',boxShadow:'0 0 4px #22c55e'}}></div>`:null}
                      </div>
                      <div style=${{display:'flex',flexDirection:'column',alignItems:'center',marginTop:1}}>
                        <span style=${{fontSize:8,fontWeight:700,color:isNow?'#22c55e':'var(--ac)',fontFamily:'monospace',lineHeight:1}}>${tStr}</span>
                        <span style=${{fontSize:7,color:'rgba(255,255,255,.35)',maxWidth:48,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',lineHeight:1.2}}>${abbr}</span>
                      </div>
                    </div>`;
                })}
                <button onClick=${onViewReminders} style=${{marginLeft:'auto',flexShrink:0,width:20,height:20,borderRadius:'50%',background:'var(--ac4)',border:'1px solid var(--ac3)',cursor:'pointer',color:'var(--ac)',fontSize:12,display:'flex',alignItems:'center',justifyContent:'center',fontWeight:700,lineHeight:1}} title="Manage reminders">+</button>
              </div>
            `}
          </div>
        </div>
        <div style=${{display:'flex',alignItems:'center',gap:8,flexShrink:0}}>
                    <div style=${{position:'relative'}} ref=${npRef}>
            <button style=${{width:34,height:34,borderRadius:'50%',border:'none',background:showNP?'var(--sf2)':'var(--sf)',boxShadow:showNP?'none':'var(--sh)',cursor:'pointer',display:'flex',alignItems:'center',justifyContent:'center',position:'relative',color:'var(--tx2)',transition:'all .15s'}}
              onClick=${()=>setShowNP(v=>!v)}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>
              ${unread>0?html`<div style=${{position:'absolute',top:-3,right:-3,width:15,height:15,borderRadius:'50%',background:'#ef4444',border:'2px solid var(--sf)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:8,fontWeight:700,color:'#fff'}}>${unread>9?'9+':unread}</div>`:null}
            </button>
            ${showNP?html`
              <div style=${{position:'fixed',top:58,right:14,width:350,maxHeight:460,background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:16,boxShadow:'0 12px 48px rgba(0,0,0,0.22)',zIndex:9500,overflow:'hidden',display:'flex',flexDirection:'column'}}>
                <div style=${{padding:'10px 13px 8px',borderBottom:'1px solid var(--bd)',display:'flex',justifyContent:'space-between',alignItems:'center',flexShrink:0}}>
                  <span style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em'}}>Notifications ${unread>0?html`<span style=${{color:'var(--ac)',fontSize:11}}>(${unread})</span>`:null}</span>
                  <div style=${{display:'flex',gap:5}}>
                    ${unread>0?html`<button class="btn bg" style=${{fontSize:10,padding:'2px 7px',height:20}} onClick=${onMarkAllRead}>✓ Mark all read</button>`:null}
                    <button class="btn brd" style=${{fontSize:10,padding:'2px 7px',height:20}} onClick=${()=>{onClearAll&&onClearAll();setShowNP(false);}}>Clear all</button>
                  </div>
                </div>
                <div style=${{overflowY:'auto',flex:1}}>
                  ${safe(notifs).length===0?html`<div style=${{textAlign:'center',padding:'20px 0',color:'var(--tx3)',fontSize:12}}>🔔 All caught up!</div>`:null}
                  ${safe(notifs).slice(0,25).map(n=>html`
                    <div key=${n.id} onClick=${()=>{onNotifClick&&onNotifClick(n);setShowNP(false);}}
                      style=${{display:'flex',gap:9,padding:'9px 13px',borderBottom:'1px solid var(--bd)',cursor:'pointer',background:n.read?'transparent':'rgba(29,78,216,.04)',borderLeft:n.read?'none':'2px solid rgba(29,78,216,.3)'}}>
                      <div style=${{width:26,height:26,borderRadius:7,background:(NC[n.type]||'var(--ac)')+'22',display:'flex',alignItems:'center',justifyContent:'center',fontSize:12,flexShrink:0}}>${NI[n.type]||'🔔'}</div>
                      <div style=${{flex:1,minWidth:0}}>
                        <p style=${{fontSize:12,color:'var(--tx)',fontWeight:n.read?400:600,lineHeight:1.35,marginBottom:2,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${n.content}</p>
                        <div style=${{display:'flex',gap:5,alignItems:'center'}}>
                          <span class="mono-10">${ago(n.ts)}</span>
                          ${n.type==='dm'?html`<span style=${{fontSize:9,fontWeight:700,color:'var(--cy)',background:'rgba(14,116,144,0.1)',borderRadius:4,padding:'1px 5px',letterSpacing:'.03em'}}>DM • click to reply</span>`:null}
                          ${n.type==='task_assigned'||n.type==='status_change'||n.type==='comment'?html`<span style=${{fontSize:9,fontWeight:600,color:'var(--ac)',background:'var(--ac3)',borderRadius:4,padding:'1px 5px'}}>→ Tasks</span>`:null}
                        </div>
                      </div>
                      ${!n.read?html`<div style=${{width:5,height:5,borderRadius:'50%',background:'var(--ac)',flexShrink:0,marginTop:5}}></div>`:null}
                    </div>`)}
                </div>
              </div>`:null}
          </div>
          ${cu?html`<div style=${{position:'relative'}} ref=${prRef}>
            <div style=${{display:'flex',alignItems:'center',gap:6,padding:'3px 9px 3px 3px',background:'var(--sf2)',borderRadius:20,border:'1px solid var(--bd)',cursor:'pointer',transition:'all .15s'}}
              onClick=${()=>setShowProfile(v=>!v)}
              onMouseEnter=${e=>{e.currentTarget.style.borderColor='var(--ac)';e.currentTarget.style.background='var(--sf)';}}
              onMouseLeave=${e=>{e.currentTarget.style.borderColor='var(--bd)';e.currentTarget.style.background='var(--sf2)';}}>
              <${Av} u=${cu} size=${24}/>
              <div style=${{lineHeight:1.2}}>
                <div style=${{fontSize:11,fontWeight:700,color:'var(--tx)'}}>${cu&&cu.name?cu.name.split(' ')[0]:''}</div>

              </div>
            </div>
            ${showProfile?html`
              <div style=${{position:'fixed',top:60,right:16,width:300,background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:18,boxShadow:'0 8px 40px rgba(0,0,0,.18)',zIndex:9500,overflow:'hidden'}}>
                <div style=${{padding:'20px 16px',background:'linear-gradient(135deg,rgba(29,78,216,.10),rgba(124,58,237,.05))',borderBottom:'1px solid var(--bd)',display:'flex',flexDirection:'column',alignItems:'center',gap:10}}>
                  <div style=${{position:'relative',cursor:'pointer'}} title="Click to change photo"
                    onClick=${e=>{e.stopPropagation();prImgRef.current&&prImgRef.current.click();}}>
                    ${(cu.avatar_data&&cu.avatar_data.startsWith('data:image'))?
                      html`<img src=${cu.avatar_data} style=${{width:68,height:68,borderRadius:'50%',objectFit:'cover',border:'3px solid var(--ac)',display:'block'}}/>`:
                      html`<div style=${{width:68,height:68,borderRadius:'50%',background:cu.color||'#2563eb',display:'flex',alignItems:'center',justifyContent:'center',fontSize:24,fontWeight:700,color:'#fff',border:'3px solid var(--ac)',boxShadow:'0 4px 16px rgba(29,78,216,.3)'}}>${cu.avatar||'?'}</div>`}
                    <div style=${{position:'absolute',bottom:2,right:2,width:22,height:22,borderRadius:'50%',background:'var(--ac)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:12,border:'2px solid var(--sf)',color:'#fff',pointerEvents:'none'}}>📷</div>
                  </div>
                  <input ref=${prImgRef} type="file" accept="image/*" style=${{display:'none'}} onChange=${async e=>{
                    const f=e.target.files[0];if(!f)return;
                    if(f.size>2*1024*1024){setUploadMsg('Image too large (max 2MB)');return;}
                    setUploadMsg('Uploading...');
                    const reader=new FileReader();
                    reader.onload=async ev=>{
                      const dataUrl=ev.target.result;
                      const res=await api.put('/api/users/'+cu.id,{avatar_data:dataUrl});
                      if(res&&res.id){
                        setCu&&setCu(prev=>({...prev,avatar_data:dataUrl}));
                        setUploadMsg('✓ Photo updated!');
                        setTimeout(()=>setUploadMsg(''),2500);
                      } else {
                        setUploadMsg('Upload failed. Try a smaller image.');
                      }
                    };
                    reader.readAsDataURL(f);
                  }}/>
                  <div style=${{textAlign:'center',width:'100%'}}>
                    <div style=${{fontSize:15,fontWeight:700,color:'var(--tx)',marginBottom:2}}>${cu.name}</div>
                    <div style=${{fontSize:11,color:'var(--tx3)',fontFamily:'monospace',marginBottom:4,wordBreak:'break-all'}}>${cu.email}</div>
                    <span style=${{display:'inline-block',padding:'3px 10px',borderRadius:20,fontSize:10,fontWeight:700,fontFamily:'monospace',background:'rgba(29,78,216,.12)',color:'var(--ac)',textTransform:'uppercase'}}>${cu&&cu.role||''}</span>
                    ${uploadMsg?html`<div style=${{marginTop:8,fontSize:11,color:uploadMsg.startsWith('✓')?'var(--gn)':'var(--rd)',fontFamily:'monospace'}}>${uploadMsg}</div>`:null}
                  </div>
                </div>
                <div style=${{padding:'12px 14px',borderBottom:'1px solid var(--bd)'}}>
                  <${PersonalTwoFAToggle} cu=${cu} setCu=${setCu}/>
                </div>
                <div style=${{padding:'10px 12px'}}>
                  <p style=${{fontSize:10,color:'var(--tx3)',textAlign:'center',marginBottom:8,fontFamily:'monospace'}}>Click avatar to change profile photo</p>
                  <button class="btn bg" style=${{width:'100%',justifyContent:'center',fontSize:12}} onClick=${()=>setShowProfile(false)}>Close</button>
                </div>
              </div>`:null}
          </div>`:null}
        </div>
      </div>
      <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',padding:'0 20px',height:42,borderTop:'1px solid var(--bd2)'}}>
        <div style=${{display:'flex',alignItems:'baseline',gap:10,minWidth:0}}>
          <h1 style=${{fontSize:15,fontWeight:700,color:'var(--tx)',letterSpacing:'-.2px',fontFamily:"'Space Grotesk',sans-serif",whiteSpace:'nowrap',flexShrink:0}}>${title}</h1>
          ${sub?html`<span style=${{color:'var(--tx2)',fontSize:11,fontWeight:500,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${sub}</span>`:null}
        </div>
        <div style=${{display:'flex',alignItems:'center',gap:7,flexShrink:0}}>${extra||null}</div>
      </div>
    </div>`;
}

/* ─── MemberPicker ────────────────────────────────────────────────────────── */
function MemberPicker({allUsers,selected,onChange}){
  return html`<div style=${{display:'flex',flexWrap:'wrap',gap:7,marginTop:4}}>
    ${safe(allUsers).map(u=>html`
      <button key=${u.id} class=${'chip'+(selected.includes(u.id)?' on':'')}
        onClick=${()=>onChange(selected.includes(u.id)?selected.filter(x=>x!==u.id):[...selected,u.id])}>
        <${Av} u=${u} size=${18}/><span>${u.name}</span>
        ${selected.includes(u.id)?html`<span style=${{color:'var(--ac2)',fontSize:11}}>✓</span>`:null}
      </button>`)}
  </div>`;
}

/* ─── FileAttachments ─────────────────────────────────────────────────────── */
function FileAttachments({taskId,projectId,readOnly}){
  const [files,setFiles]=useState([]);const [busy,setBusy]=useState(false);const [drag,setDrag]=useState(false);const ref=useRef(null);
  const load=useCallback(async()=>{
    const url=taskId?'/api/files?task_id='+taskId:projectId?'/api/files?project_id='+projectId:'';
    if(!url)return;const d=await api.get(url);setFiles(Array.isArray(d)?d:[]);
  },[taskId,projectId]);
  useEffect(()=>{load();},[load]);
  const upload=async fl=>{
    if(!fl||!fl.length)return;setBusy(true);
    for(let i=0;i<fl.length;i++){const fd=new FormData();fd.append('file',fl[i]);if(taskId)fd.append('task_id',taskId);if(projectId)fd.append('project_id',projectId);await api.upload('/api/files',fd);}
    await load();setBusy(false);
  };
  const del=async id=>{if(!window.confirm('Delete this file?'))return;await api.del('/api/files/'+id);setFiles(f=>f.filter(x=>x.id!==id));};
  const icon=m=>{if(!m)return'📄';if(m.startsWith('image/'))return'🖼';if(m.includes('pdf'))return'📕';if(m.includes('word'))return'📝';if(m.includes('sheet'))return'📊';if(m.includes('zip'))return'🗜';return'📄';};
  const sz=b=>b<1024?b+'B':b<1048576?+(b/1024).toFixed(1)+'KB':+(b/1048576).toFixed(1)+'MB';
  return html`<div style=${{display:'flex',flexDirection:'column',gap:10}}>
    ${!readOnly?html`<div class=${'drop-zone'+(drag?' over':'')} onClick=${()=>ref.current&&ref.current.click()}
      onDragOver=${e=>{e.preventDefault();setDrag(true);}} onDragLeave=${()=>setDrag(false)}
      onDrop=${e=>{e.preventDefault();setDrag(false);upload(e.dataTransfer.files);}}>
      ${busy?html`<span class="spin"></span><span style=${{marginLeft:8}}>Uploading...</span>`:
        html`<div style=${{fontSize:22,marginBottom:6}}>📎</div><div style=${{fontWeight:500}}>Click or drag to attach files</div><div style=${{fontSize:11,marginTop:3}}>Max 150 MB</div>`}
      <input ref=${ref} type="file" multiple style=${{display:'none'}} onChange=${e=>upload(e.target.files)}/></div>`:null}
    ${files.map(f=>html`
      <div key=${f.id} style=${{display:'flex',alignItems:'center',gap:10,padding:'9px 12px',background:'var(--sf2)',borderRadius:9,border:'1px solid var(--bd)'}}>
        <span style=${{fontSize:18}}>${icon(f.mime)}</span>
        <div style=${{flex:1,minWidth:0}}>
          <a href=${'/api/files/'+f.id} style=${{fontSize:13,color:'var(--ac2)',fontWeight:500,textDecoration:'none',display:'block',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${f.name}</a>
          <span class="mono-10">${sz(f.size)} · ${ago(f.ts)}</span>
        </div>
        ${!readOnly?html`<button class="btn brd" style=${{padding:'4px 9px',fontSize:11}} onClick=${()=>del(f.id)}>✕</button>`:null}
      </div>`)}
  </div>`;
}

/* ─── TaskModal ───────────────────────────────────────────────────────────── */
const TYPE_COLORS={task:'#1d4ed8',story:'#15803d',bug:'#b91c1c',epic:'#6d28d9',spike:'#b45309'};
const TYPE_BG={task:'rgba(29,78,216,0.10)',story:'rgba(21,128,61,0.10)',bug:'rgba(185,28,28,0.10)',epic:'rgba(109,40,217,0.10)',spike:'rgba(180,83,9,0.10)'};
const TYPE_BORDER={task:'rgba(29,78,216,0.2)',story:'rgba(21,128,61,0.2)',bug:'rgba(185,28,28,0.2)',epic:'rgba(109,40,217,0.2)',spike:'rgba(180,83,9,0.2)'};

function TaskModal({task,onClose,onSave,onDel,projects,users,cu,defaultPid,onSetReminder,teams,activeTeam}){
  const [title,setTitle]=useState((task&&task.title)||'');
  const [desc,setDesc]=useState((task&&task.description)||'');
  const [pid,setPid]=useState((task&&task.project)||defaultPid||(projects[0]&&projects[0].id)||'');
  const [teamId,setTeamId]=useState((task&&task.team_id)||((!task&&activeTeam)?activeTeam.id:'')||'');
  const [ass,setAss]=useState((task&&task.assignee)||'');
  const [pri,setPri]=useState((task&&task.priority)||'medium');
  const [stage,setStage]=useState((task&&task.stage)||'backlog');
  const [activeTab,setActiveTab]=useState('details'); // 'details' | 'activity'
  const [events,setEvents]=useState([]);
  const [evLoading,setEvLoading]=useState(false);
  useEffect(()=>{
    if(task&&task.id&&activeTab==='activity'){
      setEvLoading(true);
      api.get('/api/tasks/'+task.id+'/events').then(d=>{
        if(Array.isArray(d))setEvents(d);
        setEvLoading(false);
      });
    }
  },[task&&task.id,activeTab]);
  const [due,setDue]=useState((task&&task.due)||'');
  const [pct,setPct]=useState((task&&task.pct)||0);
  const [sprint,setSprint]=useState((task&&task.sprint)||'');
  const [cmts,setCmts]=useState(()=>{const r=task&&task.comments;if(!r)return[];if(Array.isArray(r))return r;try{return JSON.parse(r)||[];}catch{return [];}});
  const [nc,setNc]=useState('');
  const [tab,setTab]=useState('details');
  const [saving,setSaving]=useState(false);
  const [err,setErr]=useState('');
  const isEdit=!!(task&&task.id);
  const selectedTeam=safe(teams).find(t=>t.id===teamId);
  const teamMemberIds=selectedTeam?JSON.parse(selectedTeam.member_ids||'[]'):null;
  const assigneeOptions=teamMemberIds
    ? safe(users).filter(u=>teamMemberIds.includes(u.id))
    : safe(users);
  const FULL_EDIT_ROLES=['Admin','Manager','TeamLead'];
  const isAdminManagerTeamLead=cu&&FULL_EDIT_ROLES.includes(cu.role);
  const isAssignee=cu&&task&&task.assignee===cu.id;
  const canEditTask=isAdminManagerTeamLead||(!isEdit); // new tasks always editable
  const canUpdateStage=isAdminManagerTeamLead||isAssignee;
  const canDeleteTask=cu&&FULL_EDIT_ROLES.includes(cu.role);
  const [rmEnabled,setRmEnabled]=useState(false);
  // Subtasks
  const [subtasks,setSubtasks]=useState([]);
  const [newSubtask,setNewSubtask]=useState('');
  const [loadingSubtasks,setLoadingSubtasks]=useState(false);
  // Jira fields
  const [storyPoints,setStoryPoints]=useState((task&&task.story_points)||0);
  const [taskType,setTaskType]=useState((task&&task.task_type)||'task');
  const [taskLabels,setTaskLabels]=useState(()=>{const r=task&&task.labels;if(!r)return[];if(Array.isArray(r))return r;try{return JSON.parse(r)||[];}catch{return [];}});
  const [newLabel,setNewLabel]=useState('');
  const TASK_TYPES=['task','story','bug','epic','spike'];

  useEffect(()=>{
    if(isEdit&&tab==='subtasks'){
      setLoadingSubtasks(true);
      api.get('/api/tasks/'+task.id+'/subtasks').then(d=>{
        if(Array.isArray(d))setSubtasks(d);
        setLoadingSubtasks(false);
      }).catch(()=>setLoadingSubtasks(false));
    }
  },[isEdit,tab]);

  const addSubtask=async()=>{
    if(!newSubtask.trim()||!isEdit)return;
    const st=await api.post('/api/tasks/'+task.id+'/subtasks',{title:newSubtask.trim()});
    if(st&&st.id){setSubtasks(prev=>[...prev,st]);setNewSubtask('');}
  };
  const toggleSubtask=async(st)=>{
    await api.put('/api/subtasks/'+st.id,{done:st.done?0:1});
    setSubtasks(prev=>prev.map(s=>s.id===st.id?{...s,done:s.done?0:1}:s));
  };
  const delSubtask=async(sid)=>{
    await api.del('/api/subtasks/'+sid);
    setSubtasks(prev=>prev.filter(s=>s.id!==sid));
  };
  const [rmDate,setRmDate]=useState(()=>{
    const d=new Date();d.setDate(d.getDate()+(d.getHours()>=20?1:0));
    return d.toISOString().split('T')[0];
  });
  const [rmTime,setRmTime]=useState('16:00');
  const [rmMins,setRmMins]=useState(10);

  const addCmt=async()=>{
    if(!nc.trim())return;
    const newCmt={id:Date.now()+'',uid:cu&&cu.id,name:cu&&cu.name,text:nc.trim(),ts:new Date().toISOString()};
    const updated=[...cmts,newCmt];
    setCmts(updated);setNc('');
    if(task&&task.id){
      const payload={comments:updated};
      if(canEditTask){
        Object.assign(payload,{title:title.trim()||task.title,description:desc,project:pid,assignee:ass,priority:pri,stage,due,pct});
      } else {
        payload.stage=stage;payload.pct=pct;
      }
      await api.put('/api/tasks/'+task.id,payload);
    }
  };
  const save=async(opts={})=>{
    if(!title.trim()&&(!isEdit||canEditTask)){setErr('Title required.');return null;}
    setSaving(true);setErr('');
    let payload;
    if(isEdit&&canUpdateStage&&!canEditTask){
      payload={stage,pct};
    } else {
      payload={title:title.trim(),description:desc,project:pid,assignee:ass,priority:pri,stage,due,pct,comments:cmts,team_id:teamId,story_points:storyPoints,task_type:taskType,labels:taskLabels,sprint};
    }
    if(task&&task.id)payload.id=task.id;
    const result=await onSave(payload);
    setSaving(false);
    if(result&&result.error){setErr(result.error);return null;}
    if(!isEdit&&rmEnabled&&rmDate&&rmTime){
      const dt=new Date(rmDate+'T'+rmTime);
      const taskId=(result&&result.id)||'';
      await api.post('/api/reminders',{task_id:taskId,task_title:title.trim(),remind_at:dt.toISOString(),minutes_before:rmMins});
    }
    if(opts.keepOpen)return result;
    onClose();
    return result;
  };

  return html`
    <div class="ov" onClick=${e=>e.target===e.currentTarget&&onClose()}>
      <div class="mo fi">
        <div style=${{display:'flex',justifyContent:'space-between',alignItems:'flex-start',marginBottom:16}}>
          <div>
            <div style=${{display:'flex',alignItems:'center',gap:8,flexWrap:'wrap'}}>
              <!-- Type badge pill -->
              <span style=${{
                fontSize:10,fontWeight:800,padding:'3px 9px',borderRadius:5,textTransform:'uppercase',
                background:TYPE_BG[taskType||'task'],
                color:TYPE_COLORS[taskType||'task'],
                border:'1px solid '+TYPE_BORDER[taskType||'task'],
                display:'flex',alignItems:'center',gap:4
              }}>
                <span style=${{width:7,height:7,borderRadius:1,display:'inline-block',background:TYPE_COLORS[taskType||'task']}}></span>
                ${taskType||'task'}
              </span>
              <h2 style=${{fontSize:16,fontWeight:700,color:'var(--tx)',margin:0}}>
                ${isEdit
                  ? (tab==='subtasks'
                      ? 'Subtasks'
                      : canEditTask
                        ? 'Edit '+(taskType&&taskType!=='task'?taskType.charAt(0).toUpperCase()+taskType.slice(1):'Task')
                        : canUpdateStage?'Update Stage':'View Task')
                  : 'New '+(taskType&&taskType!=='task'?taskType.charAt(0).toUpperCase()+taskType.slice(1):'Task')}
              </h2>
              ${tab==='subtasks'?html`<span style=${{fontSize:11,color:'var(--tx3)',fontWeight:400}}>${title}</span>`:null}
            </div>
            ${isEdit?html`<span class="id-badge id-task">${task.id}</span>`:null}
            ${isEdit&&!canEditTask&&canUpdateStage?html`<div style=${{fontSize:11,color:'var(--am)',marginTop:3}}>You can update stage & progress as the assignee.</div>`:null}
            ${isEdit&&!canEditTask&&!canUpdateStage?html`<div style=${{fontSize:11,color:'var(--tx3)',marginTop:3}}>Read-only — you are not assigned to this task.</div>`:null}
          </div>
          <div style=${{display:'flex',gap:7}}>
            ${isEdit&&onDel&&canDeleteTask?html`<button class="btn brd" style=${{fontSize:12,padding:'6px 11px'}}
              onClick=${async()=>{if(window.confirm('Delete this task?')){await onDel(task.id);onClose();}}}>🗑</button>`:null}
            <button class="btn bg" style=${{padding:'7px 10px'}} onClick=${onClose}>✕</button>
          </div>
        </div>
        ${isEdit?html`
          <div style=${{display:'flex',gap:2,background:'var(--sf2)',borderRadius:9,padding:3,marginBottom:14,width:'fit-content',flexWrap:'wrap'}}>
            ${['details','subtasks','comments','files','activity'].map(t=>html`
              <button key=${t} class=${'tb'+(tab===t?' act':'')} onClick=${()=>setTab(t)} style=${{fontSize:11}}>
                ${t==='details'?'Details':t==='subtasks'?html`Subtasks${subtasks.length>0?html` <span style=${{background:'var(--ac)',color:'#fff',borderRadius:8,padding:'0 5px',fontSize:9}}>${subtasks.filter(s=>s.done).length}/${subtasks.length}</span>`:''}`:t==='comments'?'Comments'+(cmts.length?' ('+cmts.length+')':''):t==='activity'?'📋 Activity':'Files'}
              </button>`)}
          </div>`:null}

        ${tab==='details'?html`
          <div style=${{display:'grid',gap:12}}>
            ${!canEditTask&&!canUpdateStage?html`
              <div style=${{background:'var(--sf2)',borderRadius:10,padding:'12px 14px',border:'1px solid var(--bd)',display:'grid',gap:8}}>
                <div style=${{display:'flex',justifyContent:'space-between'}}><span class="tx3-11">Title</span><span style=${{fontSize:13,color:'var(--tx)',fontWeight:500}}>${title}</span></div>
                <div style=${{display:'flex',justifyContent:'space-between'}}><span class="tx3-11">Stage</span><${SP} s=${stage}/></div>
                <div style=${{display:'flex',justifyContent:'space-between'}}><span class="tx3-11">Priority</span><${PB} p=${pri}/></div>
                <div style=${{display:'flex',justifyContent:'space-between'}}><span class="tx3-11">Due</span><span style=${{fontSize:12,color:'var(--tx2)',fontFamily:'monospace'}}>${fmtD(due)}</span></div>
                <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center'}}><span class="tx3-11">Progress</span><span style=${{fontSize:12,color:'var(--ac)',fontWeight:700,fontFamily:'monospace'}}>${pct}%</span></div>
              </div>
            `:canUpdateStage&&!canEditTask?html`
              <div style=${{background:'var(--sf2)',borderRadius:10,padding:'12px 14px',border:'1px solid var(--bd)',display:'grid',gap:8,marginBottom:4}}>
                <div style=${{display:'flex',justifyContent:'space-between'}}><span class="tx3-11">Title</span><span style=${{fontSize:13,color:'var(--tx)',fontWeight:500}}>${title}</span></div>
                <div style=${{display:'flex',justifyContent:'space-between'}}><span class="tx3-11">Priority</span><${PB} p=${pri}/></div>
                <div style=${{display:'flex',justifyContent:'space-between'}}><span class="tx3-11">Due</span><span style=${{fontSize:12,color:'var(--tx2)',fontFamily:'monospace'}}>${fmtD(due)}</span></div>
              </div>
              <div><label class="lbl">Stage</label>
                <select class="sel" value=${stage} onChange=${e=>{
                  const ns=e.target.value;setStage(ns);
                  const ap=STAGE_PCT[ns];if(ap!==null&&ap!==undefined)setPct(ap);
                }}>
                  ${Object.entries(STAGES).map(([k,v])=>html`<option key=${k} value=${k}>${v.label}</option>`)}
                </select>
              </div>
              <div><label class="lbl">Completion: ${pct}%</label>
                <div style=${{display:'flex',alignItems:'center',gap:12}}>
                  <input type="range" min="0" max="100" value=${pct} style=${{flex:1,accentColor:'var(--ac)',cursor:'pointer'}} onChange=${e=>setPct(parseInt(e.target.value))}/>
                  <span style=${{fontSize:13,color:'var(--ac)',fontWeight:700,fontFamily:'monospace',width:34,textAlign:'right'}}>${pct}%</span>
                </div>
              </div>
            `:html`
              <div><label class="lbl">Title *</label>
                <input class="inp" placeholder="Task title..." value=${title} onInput=${e=>setTitle(e.target.value)}/></div>
              <div><label class="lbl">Description</label>
                <textarea class="inp" rows="3" placeholder="Describe the task..." onInput=${e=>setDesc(e.target.value)}>${desc}</textarea></div>
              <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr',gap:11}}>
                <div><label class="lbl">Project</label>
                  <select class="sel" value=${pid} onChange=${e=>setPid(e.target.value)}>
                    ${safe(projects).map(p=>html`<option key=${p.id} value=${p.id}>${p.name}</option>`)}
                  </select></div>
                <div><label class="lbl">Team <span style=${{fontWeight:400,color:'var(--tx3)',fontSize:10}}>(optional)</span></label>
                  <select class="sel" value=${teamId} onChange=${e=>{setTeamId(e.target.value);setAss('');}}>
                    <option value="">— No team —</option>
                    ${safe(teams).map(t=>html`<option key=${t.id} value=${t.id}>${t.name}</option>`)}
                  </select></div>
              </div>
              <div><label class="lbl">Assignee${teamId?html` <span style=${{fontWeight:400,color:'var(--ac)',fontSize:10}}>(from ${selectedTeam&&selectedTeam.name})</span>`:''}</label>
                  <select class="sel" value=${ass} onChange=${e=>setAss(e.target.value)}>
                    <option value="">Unassigned</option>
                    ${assigneeOptions.map(u=>html`<option key=${u.id} value=${u.id}>${u.name} (${u.role})</option>`)}
                  </select></div>
              <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:11}}>
                <div><label class="lbl">Priority</label>
                  <select class="sel" value=${pri} onChange=${e=>setPri(e.target.value)}>
                    ${Object.entries(PRIS).map(([k,v])=>html`<option key=${k} value=${k}>${v.sym} ${v.label}</option>`)}
                  </select></div>
                <div><label class="lbl">Stage</label>
                  <select class="sel" value=${stage} onChange=${e=>{
                    const ns=e.target.value;setStage(ns);
                    const ap=STAGE_PCT[ns];if(ap!==null&&ap!==undefined)setPct(ap);
                    if(!due&&ns!=='backlog'&&ns!=='blocked'){const days=STAGE_DAYS[ns];if(days>0)setDue(addDays(days));}
                  }}>
                    ${Object.entries(STAGES).map(([k,v])=>html`<option key=${k} value=${k}>${v.label}</option>`)}
                  </select></div>
                <div><label class="lbl">Due Date</label>
                  <input class="inp" type="date" value=${due} min="" onChange=${e=>setDue(e.target.value)} onFocus=${e=>{if(!e.target.value)e.target.value=new Date().toISOString().split('T')[0];}}/></div>
              <div><label class="lbl">Sprint</label><input class="inp" placeholder="e.g. Sprint 3" value=${sprint} onInput=${e=>setSprint(e.target.value)} disabled=${!canEditTask}/></div>
              </div>
              <div><label class="lbl">Completion: ${pct}%</label>
                <div style=${{display:'flex',alignItems:'center',gap:12}}>
                  <input type="range" min="0" max="100" value=${pct} style=${{flex:1,accentColor:'var(--ac)',cursor:'pointer'}} onChange=${e=>setPct(parseInt(e.target.value))}/>
                  <span style=${{fontSize:13,color:'var(--ac)',fontWeight:700,fontFamily:'monospace',width:34,textAlign:'right'}}>${pct}%</span>
                </div>
              </div>
            `}
            ${err?html`<div style=${{color:'var(--rd)',fontSize:12,padding:'7px 11px',background:'rgba(248,113,113,.07)',borderRadius:7}}>${err}</div>`:null}
            ${!isEdit?html`
              <div style=${{borderTop:'1px solid var(--bd)',paddingTop:12}}>
                <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:rmEnabled?12:0}}>
                  <div style=${{display:'flex',alignItems:'center',gap:8,cursor:'pointer'}} onClick=${()=>setRmEnabled(v=>!v)}>
                    <div style=${{width:36,height:20,borderRadius:10,background:rmEnabled?'var(--ac)':'var(--bd)',position:'relative',transition:'background .2s',flexShrink:0}}>
                      <div style=${{position:'absolute',top:2,left:rmEnabled?18:2,width:16,height:16,borderRadius:'50%',background:'#fff',transition:'left .2s',boxShadow:'0 1px 4px rgba(0,0,0,.2)'}}></div>
                    </div>
                    <span style=${{fontSize:12,fontWeight:600,color:'var(--tx)'}}>⏰ Set a reminder</span>
                    ${!rmEnabled?html`<span class="tx3-11">— get notified before this task is due</span>`:null}
                  </div>
                </div>
                ${rmEnabled?html`
                  <div style=${{background:'rgba(90,140,255,.06)',borderRadius:10,border:'1px solid rgba(99,102,241,.18)',padding:'12px 14px',display:'flex',flexDirection:'column',gap:10}}>
                    <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr',gap:10}}>
                      <div>
                        <label class="lbl" style=${{fontSize:10,marginBottom:3}}>Reminder Date</label>
                        <input class="inp" type="date" value=${rmDate} onChange=${e=>setRmDate(e.target.value)} min=${new Date().toISOString().split('T')[0]} onFocus=${e=>{if(!e.target.value)e.target.value=new Date().toISOString().split('T')[0];}} style=${{fontSize:12}}/>
                      </div>
                      <div>
                        <label class="lbl" style=${{fontSize:10,marginBottom:3}}>Reminder Time</label>
                        <input class="inp" type="time" value=${rmTime} onChange=${e=>setRmTime(e.target.value)} style=${{fontSize:12}}/>
                      </div>
                    </div>
                    <div>
                      <label class="lbl" style=${{fontSize:10,marginBottom:4}}>Notify me before</label>
                      <div style=${{display:'flex',gap:6,flexWrap:'wrap'}}>
                        ${[5,10,15,30,60].map(m=>html`<button key=${m} class=${'chip'+(rmMins===m?' on':'')} onClick=${()=>setRmMins(m)} style=${{fontSize:11,padding:'3px 11px'}}>${m<60?m+' min':'1 hr'}</button>`)}
                      </div>
                    </div>
                    <div style=${{fontSize:11,color:'var(--tx3)',display:'flex',alignItems:'center',gap:5}}>
                      <span>🔔</span>
                      <span>You'll be notified${rmMins>0?' '+rmMins+' min before':' at'} ${rmTime||'the set time'} on ${rmDate||'the selected date'} with sound.</span>
                    </div>
                  </div>
                `:null}
              </div>
            `:null}
            <div style=${{display:'flex',gap:9,justifyContent:'flex-end',paddingTop:6,borderTop:isEdit?'1px solid var(--bd)':'none'}}>
              <button class="btn bg" onClick=${onClose}>${isEdit&&!canEditTask&&!canUpdateStage?'Close':'Cancel'}</button>
              ${onSetReminder&&isEdit?html`<button class="btn bam" style=${{fontSize:12}} onClick=${async()=>{const r=await save({keepOpen:true});if(r!==null){onClose();onSetReminder({id:(task&&task.id)||r.id,title:title,due});}}}>⏰ Set Reminder</button>`:null}
              ${isEdit?html`<button class="btn bg" style=${{fontSize:12,color:'var(--ac)'}}
                onClick=${()=>{
                  // Navigate to Timesheet and pass pre-fill info via sessionStorage
                  try{sessionStorage.setItem('ts_prefill',JSON.stringify({project_id:project||'',task_id:(task&&task.id)||'',task_title:title}));}catch{}
                  onClose();
                  // Dispatch custom event so App can navigate + open form
                  window.dispatchEvent(new CustomEvent('vw:logtime'));
                }}>⏱ Log Time</button>`:null}
              ${(!isEdit||canEditTask||canUpdateStage)?html`<button class="btn bp" onClick=${save} disabled=${saving}>${saving?html`<span class="spin"></span>`:(isEdit?'Save Changes':'Create Task')}</button>`:null}
            </div>
          </div>`:null}

        ${tab==='comments'?html`
          <div style=${{display:'flex',flexDirection:'column',gap:10}}>
            ${cmts.length>0?html`<div style=${{display:'flex',flexDirection:'column',gap:8,maxHeight:240,overflowY:'auto'}}>
              ${cmts.map((c,i)=>{
                const au=safe(users).find(u=>u.id===c.uid);
                return html`<div key=${i} style=${{display:'flex',gap:9,padding:'9px 12px',background:'var(--sf2)',borderRadius:9,border:'1px solid var(--bd)'}}>
                  <${Av} u=${au} size=${24}/>
                  <div style=${{flex:1}}>
                    <div style=${{display:'flex',gap:7,alignItems:'center',marginBottom:3}}>
                      <span style=${{fontSize:12,fontWeight:600,color:'var(--tx)'}}>${(au&&au.name)||'?'}</span>
                      <span class="mono-10">${ago(c.ts)}</span>
                    </div>
                    <p style=${{fontSize:13,color:'var(--tx2)',lineHeight:1.5}}>${c.text}</p>
                  </div>
                </div>`;})}
            </div>`:null}
            <div style=${{display:'flex',gap:8}}>
              <input class="inp" style=${{flex:1}} placeholder="Add a comment..." value=${nc}
                onInput=${e=>setNc(e.target.value)} onKeyDown=${e=>e.key==='Enter'&&addCmt()}/>
              <button class="btn bp" onClick=${addCmt}>Post</button>
            </div>
            <div style=${{display:'flex',gap:9,justifyContent:'flex-end',paddingTop:6,borderTop:'1px solid var(--bd)'}}>
              <button class="btn bg" onClick=${onClose}>Close</button>
              ${onSetReminder&&isEdit?html`<button class="btn bg" style=${{color:'var(--am)'}} onClick=${async()=>{const r=await save({keepOpen:true});if(r!==null){onClose();onSetReminder({id:(task&&task.id),title:title,due});}}}>⏰ Remind</button>`:null}
              <button class="btn bp" onClick=${save} disabled=${saving}>${saving?html`<span class="spin"></span>`:'Save'}</button>
            </div>
          </div>`:null}

        ${tab==='subtasks'?html`
          <div style=${{display:'flex',flexDirection:'column',gap:10}}>
            <!-- Story Points + Task Type row -->
            <div style=${{display:'flex',gap:10,flexWrap:'wrap'}}>
              <div style=${{flex:1,minWidth:140}}>
                <label class="lbl">Task Type</label>
                <select class="sel" value=${taskType} onChange=${e=>setTaskType(e.target.value)} disabled=${!canEditTask}>
                  ${TASK_TYPES.map(t=>html`<option key=${t} value=${t}>${t.charAt(0).toUpperCase()+t.slice(1)}</option>`)}
                </select>
              </div>
              <div style=${{flex:1,minWidth:140}}>
                <label class="lbl">Story Points</label>
                <select class="sel" value=${storyPoints} onChange=${e=>setStoryPoints(parseInt(e.target.value))} disabled=${!canEditTask}>
                  ${[0,1,2,3,5,8,13,21].map(p=>html`<option key=${p} value=${p}>${p===0?'—':p+' pt'+(p>1?'s':'')}</option>`)}
                </select>
              </div>
            </div>
            <!-- Labels -->
            <div>
              <label class="lbl">Labels</label>
              <div style=${{display:'flex',gap:6,flexWrap:'wrap',marginBottom:6}}>
                ${taskLabels.map((lbl,i)=>html`
                  <span key=${i} style=${{display:'inline-flex',alignItems:'center',gap:4,padding:'3px 9px',background:'var(--ac3)',color:'var(--ac)',borderRadius:100,fontSize:11,fontWeight:600}}>
                    ${lbl}
                    ${canEditTask?html`<button onClick=${()=>setTaskLabels(prev=>prev.filter((_,j)=>j!==i))} style=${{background:'none',border:'none',cursor:'pointer',color:'var(--ac)',fontSize:12,lineHeight:1,padding:0}}>×</button>`:null}
                  </span>`)}
              </div>
              ${canEditTask?html`
                <div style=${{display:'flex',gap:6}}>
                  <input class="inp" style=${{flex:1,fontSize:12}} placeholder="Add label..." value=${newLabel}
                    onInput=${e=>setNewLabel(e.target.value)}
                    onKeyDown=${e=>{if(e.key==='Enter'&&newLabel.trim()){setTaskLabels(p=>[...p,newLabel.trim()]);setNewLabel('');}}}/>
                  <button class="btn bg" style=${{fontSize:12,padding:'5px 12px'}} onClick=${()=>{if(newLabel.trim()){setTaskLabels(p=>[...p,newLabel.trim()]);setNewLabel('');}}} >+</button>
                </div>`:null}
            </div>
            <!-- Subtasks list -->
            <div>
              <label class="lbl">Subtasks ${subtasks.length>0?html`<span style=${{color:'var(--tx3)',fontWeight:400}}>(${subtasks.filter(s=>s.done).length}/${subtasks.length} done)</span>`:null}</label>
              ${loadingSubtasks?html`<div class="spin" style=${{margin:'10px auto'}}></div>`:null}
              ${subtasks.length>0?html`
                <div style=${{display:'flex',flexDirection:'column',gap:4,marginBottom:8}}>
                  ${subtasks.map(st=>html`
                    <div key=${st.id} style=${{display:'flex',alignItems:'center',gap:8,padding:'7px 10px',background:'var(--sf2)',borderRadius:8,border:'1px solid var(--bd)',transition:'all .15s'}}>
                      <input type="checkbox" checked=${!!st.done} onChange=${()=>toggleSubtask(st)}
                        style=${{width:15,height:15,accentColor:'var(--ac)',cursor:'pointer',flexShrink:0}}/>
                      <span class="id-badge id-subtask" style=${{fontSize:9}}>${st.id.slice(0,8)}</span>
                      <span style=${{flex:1,fontSize:13,color:'var(--tx)',textDecoration:st.done?'line-through':'none',opacity:st.done?.55:1}}>${st.title}</span>
                      ${st.done?html`<span style=${{fontSize:10,color:'var(--gn)',fontWeight:600}}>Done</span>`:null}
                      <button onClick=${()=>delSubtask(st.id)} style=${{background:'none',border:'none',cursor:'pointer',color:'var(--rd2)',fontSize:14,lineHeight:1,padding:'0 2px',opacity:.6}}
                        onMouseEnter=${e=>e.currentTarget.style.opacity=1}
                        onMouseLeave=${e=>e.currentTarget.style.opacity=.6}>×</button>
                    </div>`)}
                </div>`:null}
              <!-- Add subtask input -->
              <div style=${{display:'flex',gap:6}}>
                <input class="inp" style=${{flex:1,fontSize:12}} placeholder="Add a subtask..." value=${newSubtask}
                  onInput=${e=>setNewSubtask(e.target.value)}
                  onKeyDown=${e=>{if(e.key==='Enter')addSubtask();}}/>
                <button class="btn bg" style=${{fontSize:12,padding:'5px 14px'}} onClick=${addSubtask} disabled=${!newSubtask.trim()}>+ Add</button>
              </div>
              ${subtasks.length>0?html`
                <div style=${{display:'flex',gap:8,marginTop:8}}>
                  <${Prog} pct=${Math.round(subtasks.filter(s=>s.done).length*100/subtasks.length)} color="var(--ac)"/>
                </div>`:null}
            </div>
          </div>`:null}
        ${tab==='files'&&isEdit?html`<${FileAttachments} taskId=${task.id} readOnly=${cu&&cu.role==='Viewer'}/>`:null}
      </div>
    </div>`;
}

/* ─── ProjectDetail ───────────────────────────────────────────────────────── */
function ProjectDetail({project,allTasks,allUsers,cu,onClose,onReload,onSetReminder,teams,activeTeam}){
  const [tab,setTab]=useState('tasks');const [edit,setEdit]=useState(false);
  const [name,setName]=useState(project.name||'');const [desc,setDesc]=useState(project.description||'');
  const [tDate,setTDate]=useState(project.target_date||'');const [color,setColor]=useState(project.color||'#5a8cff');
  const [members,setMembers]=useState(()=>{try{return safe(JSON.parse(project.members||'[]'));}catch{return [];}});const [saving,setSaving]=useState(false);
  const [showNew,setShowNew]=useState(false);const [editTask,setEditTask]=useState(null);
  const [projTeamId,setProjTeamId]=useState((project.team_id)||'');

  const handleTeamChange=useCallback((tid)=>{
    setProjTeamId(tid);
    if(!tid)return;
    const team=safe(teams).find(t=>t.id===tid);
    if(!team)return;
    const teamMids=JSON.parse(team.member_ids||'[]');
    setMembers(prev=>{
      const merged=[...prev];
      teamMids.forEach(mid=>{if(!merged.includes(mid))merged.push(mid);});
      return merged;
    });
  },[teams]);

  const projTasks=useMemo(()=>safe(allTasks).filter(t=>t.project===project.id),[allTasks,project.id]);
  const projUsers=useMemo(()=>safe(members).map(id=>safe(allUsers).find(u=>u.id===id)).filter(Boolean),[members,allUsers]);
  const done=projTasks.filter(t=>t.stage==='completed').length;
  const pc=projTasks.length?Math.round(projTasks.reduce((a,t)=>a+(t.pct||0),0)/projTasks.length):(project.progress||0);
  const stageGroups=KCOLS.map(s=>({s,tasks:projTasks.filter(t=>t.stage===s)})).filter(g=>g.tasks.length>0);

  const saveEdit=async()=>{
    setSaving(true);
    await api.put('/api/projects/'+project.id,{name,description:desc,target_date:tDate,color,members,team_id:projTeamId});
    await onReload();setSaving(false);setEdit(false);
  };
  const delProject=async()=>{if(!window.confirm('Delete project and all its tasks? Cannot be undone.'))return;await api.del('/api/projects/'+project.id);await onReload();onClose();};
  const saveTask=async p=>{
    let r;
    if(p.id&&allTasks.find(t=>t.id===p.id))r=await api.put('/api/tasks/'+p.id,p);
    else r=await api.post('/api/tasks',{...p,project:project.id});
    await onReload();
    return r;
  };
  const delTask=async id=>{await api.del('/api/tasks/'+id);await onReload();};

  return html`
    <div class="ov" onClick=${e=>e.target===e.currentTarget&&onClose()}>
      <div class="mo mo-xl fi" style=${{height:'90vh',display:'flex',flexDirection:'column',padding:0,overflow:'hidden'}}>

        <div style=${{padding:'20px 24px 0',flexShrink:0}}>
          <div style=${{display:'flex',alignItems:'flex-start',justifyContent:'space-between',marginBottom:14}}>
            <div style=${{display:'flex',alignItems:'center',gap:11}}>
              <div style=${{width:11,height:11,borderRadius:3,background:edit?color:project.color,flexShrink:0,marginTop:4}}></div>
              ${edit?html`<input class="inp" style=${{fontSize:17,fontWeight:700,padding:'4px 8px'}} value=${name} onInput=${e=>setName(e.target.value)}/>`:
                      html`<h2 style=${{fontSize:18,fontWeight:700,color:'var(--tx)'}}>${project.name}</h2>`}
            </div>
            <div style=${{display:'flex',gap:7,flexShrink:0}}>
              ${cu&&cu.role!=='Viewer'&&!edit?html`<button class="btn bg" style=${{fontSize:12,padding:'7px 12px'}} onClick=${()=>setEdit(true)}>✏ Edit</button>`:null}
              ${edit?html`<button class="btn bg" onClick=${()=>setEdit(false)}>Cancel</button><button class="btn bp" onClick=${saveEdit} disabled=${saving}>${saving?html`<span class="spin"></span>`:'Save'}</button>`:null}
              ${cu&&(cu.role==='Admin'||cu.role==='Manager')&&!edit?html`<button class="btn brd" style=${{fontSize:12,padding:'7px 12px'}} onClick=${delProject}>🗑</button>`:null}
              <button class="btn bg" style=${{padding:'7px 10px'}} onClick=${onClose}>✕</button>
            </div>
          </div>
          ${edit?html`
            <div style=${{display:'flex',flexDirection:'column',gap:11,marginBottom:12}}>
              <textarea class="inp" rows="2" value=${desc} onInput=${e=>setDesc(e.target.value)}>${desc}</textarea>
              <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr',gap:11}}>
                <div><label class="lbl">Target Date</label><input class="inp" type="date" value=${tDate} onChange=${e=>setTDate(e.target.value)} onFocus=${e=>{if(!e.target.value){e.target.value=new Date().toISOString().split('T')[0];}}}/></div>
                <div><label class="lbl">Color</label>
                  <div style=${{display:'flex',gap:7,flexWrap:'wrap',marginTop:4}}>
                    ${PAL.map(c=>html`<button key=${c} onClick=${()=>setColor(c)} style=${{width:26,height:26,borderRadius:6,background:c,border:'3px solid '+(color===c?'#fff':'transparent'),cursor:'pointer',transform:color===c?'scale(1.15)':'none'}}></button>`)}
                  </div>
                </div>
              </div>
                            <div>
                <label class="lbl">Assign to Team <span style=${{fontWeight:400,color:'var(--tx3)',fontSize:10}}>(auto-adds team members)</span></label>
                <select class="sel" value=${projTeamId} onChange=${e=>handleTeamChange(e.target.value)}>
                  <option value="">— No team —</option>
                  ${safe(teams).map(t=>html`<option key=${t.id} value=${t.id}>${t.name} (${JSON.parse(t.member_ids||'[]').length} members)</option>`)}
                </select>
              </div>
              <div><label class="lbl">Members</label><${MemberPicker} allUsers=${allUsers} selected=${members} onChange=${setMembers}/></div>
            </div>
            <div style=${{height:1,background:'var(--bd)',marginBottom:12}}></div>`:html`
            <p style=${{color:project.description?'var(--tx2)':'var(--tx3)',fontSize:13,marginBottom:11,lineHeight:1.55,fontStyle:project.description?'normal':'italic'}}>${project.description||'No description added yet.'}</p>
            <div style=${{display:'flex',alignItems:'center',gap:18,marginBottom:10}}>
              <div style=${{flex:1}}><${Prog} pct=${pc} color=${project.color}/></div>
              <span style=${{fontSize:11,color:'var(--tx2)',fontFamily:'monospace',fontWeight:700}}>${pc}%</span>
              <span style=${{fontSize:11,color:'var(--tx3)',fontFamily:'monospace'}}>Due ${fmtD(project.target_date)}</span>
            </div>
            <div style=${{display:'flex',alignItems:'center',gap:14,marginBottom:12}}>
              <span style=${{fontSize:12,color:'var(--tx2)'}}><b style=${{color:'var(--tx)'}}>${projTasks.length}</b> tasks · <b style=${{color:'var(--gn)'}}>${done}</b> done · <b style=${{color:'var(--am)'}}>${projTasks.length-done}</b> open</span>
              <div style=${{display:'flex',alignItems:'center',gap:8}}>
                ${(()=>{const pt=safe(teams).find(t=>t.id===(project.team_id||projTeamId));return pt?html`<span style=${{fontSize:10,color:'var(--ac)',background:'rgba(90,140,255,.10)',border:'1px solid rgba(90,140,255,.25)',padding:'2px 8px',borderRadius:5,fontWeight:600}}>👥 ${pt.name}</span>`:null;})()}
                <div style=${{display:'flex'}}>
                  ${projUsers.slice(0,7).map((m,i)=>html`<div key=${m.id} title=${m.name} style=${{marginLeft:i>0?-8:0,border:'2px solid var(--sf)',borderRadius:'50%',zIndex:7-i}}><${Av} u=${m} size=${24}/></div>`)}
                </div>
              </div>
            </div>`}
          <div style=${{display:'flex',gap:2,background:'var(--sf2)',borderRadius:10,padding:3,width:'fit-content',marginBottom:12}}>
            ${[['tasks','☑ Tasks'],['files','📎 Files'],['members','👥 Members']].map(([id,lbl])=>html`
              <button key=${id} class=${'tb'+(tab===id?' act':'')} onClick=${()=>setTab(id)}>${lbl}</button>`)}
          </div>
          <div style=${{height:1,background:'var(--bd)'}}></div>
        </div>

        <div style=${{flex:1,overflowY:'auto',padding:'16px 24px'}}>
          ${tab==='tasks'?html`
            <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:14}}>
              <span style=${{fontSize:13,color:'var(--tx2)'}}>${projTasks.length} task${projTasks.length!==1?'s':''}</span>
              ${cu&&cu.role!=='Viewer'?html`<button class="btn bp" style=${{fontSize:12,padding:'7px 13px'}} onClick=${()=>setShowNew(true)}>+ Add Task</button>`:null}
            </div>
            ${projTasks.length===0?html`<div style=${{textAlign:'center',padding:'48px 0',color:'var(--tx3)',fontSize:13}}><div style=${{fontSize:28,marginBottom:10}}>📋</div>No tasks yet. Click "+ Add Task" to get started.</div>`:null}
            ${stageGroups.map(g=>{
              const si=STAGES[g.s]||{label:g.s,color:'#94a3b8'};
              return html`<div key=${g.s} style=${{marginBottom:18}}>
                <div style=${{display:'flex',alignItems:'center',gap:8,marginBottom:8}}>
                  <div style=${{width:8,height:8,borderRadius:2,background:si.color}}></div>
                  <span style=${{fontSize:11,fontWeight:700,color:'var(--tx2)',textTransform:'uppercase',letterSpacing:.5,fontFamily:'monospace'}}>${si.label}</span>
                  <span style=${{fontSize:10,color:'var(--tx3)',background:'var(--bd)',padding:'1px 6px',borderRadius:4,fontFamily:'monospace'}}>${g.tasks.length}</span>
                </div>
                ${g.tasks.map(tk=>{
                  const au=safe(allUsers).find(u=>u.id===tk.assignee);
                  return html`<div key=${tk.id} class="tkc" style=${{marginBottom:7,display:'flex',gap:10,alignItems:'center'}} onClick=${()=>setEditTask(tk)}>
                    <div style=${{flex:1,minWidth:0}}>
                      <div style=${{display:'flex',gap:7,alignItems:'center',marginBottom:4}}><span class="id-badge id-task">${tk.id}</span><${PB} p=${tk.priority}/></div>
                      <div style=${{fontSize:13,fontWeight:500,color:'var(--tx)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${tk.title}</div>
                      ${tk.pct>0?html`<div style=${{marginTop:5}}><${Prog} pct=${tk.pct} color=${si.color}/></div>`:null}
                    </div>
                    <div style=${{display:'flex',flexDirection:'column',alignItems:'flex-end',gap:5,flexShrink:0}}>
                      ${au?html`<${Av} u=${au} size=${24}/>`:html`<div style=${{width:24,height:24,borderRadius:'50%',background:'var(--bd)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:10,color:'var(--tx3)'}}>?</div>`}
                      ${tk.due?html`<span class="mono-10">${fmtD(tk.due)}</span>`:null}
                    </div>
                  </div>`;
                })}
              </div>`;
            })}`:null}
          ${tab==='files'?html`<${FileAttachments} projectId=${project.id} readOnly=${cu&&cu.role==='Viewer'}/>`:null}
          ${tab==='activity'?html`
          <div style=${{padding:'4px 0'}}>
            ${evLoading?html`<div style=${{textAlign:'center',padding:20}}><span class="spin"></span></div>`
            :events.length===0?html`
              <div style=${{textAlign:'center',padding:'32px 16px',color:'var(--tx3)'}}>
                <div style=${{fontSize:28,marginBottom:8}}>📋</div>
                <div style=${{fontSize:12}}>No activity yet. Changes to stage, assignee, and comments will appear here.</div>
              </div>`
            :html`<div style=${{display:'flex',flexDirection:'column',gap:2}}>
              ${events.map((ev,i)=>{
                const evIcons={stage_change:'🔄',assigned:'👤',created:'✨',comment:'💬'};
                const evColors={stage_change:'var(--ac)',assigned:'var(--pu)',created:'var(--gn)',comment:'var(--am)'};
                const icon=evIcons[ev.event_type]||'📌';
                const color=evColors[ev.event_type]||'var(--tx3)';
                const label=(()=>{
                  if(ev.event_type==='stage_change')return html`moved stage <b>${ev.old_val||'—'}</b> → <b style=${{color:'var(--gn)'}}>${ev.new_val}</b>`;
                  if(ev.event_type==='assigned')return html`assigned to <b>${ev.new_val}</b>`;
                  if(ev.event_type==='comment')return html`added a comment`;
                  return html`${ev.event_type.replace(/_/g,' ')}`;
                })();
                return html`<div key=${i} style=${{display:'flex',gap:10,alignItems:'flex-start',padding:'8px 4px',borderBottom:'1px solid var(--bd)'}}>
                  <div style=${{width:26,height:26,borderRadius:'50%',background:color+'22',display:'flex',alignItems:'center',justifyContent:'center',fontSize:13,flexShrink:0}}>${icon}</div>
                  <div style=${{flex:1,minWidth:0}}>
                    <span style=${{fontSize:12,fontWeight:600,color:'var(--tx)'}}>${ev.user_name||'Someone'}</span>
                    <span style=${{fontSize:12,color:'var(--tx2)',marginLeft:4}}>${label}</span>
                    <div style=${{fontSize:10,color:'var(--tx3)',marginTop:2}}>${ev.ts?new Date(ev.ts).toLocaleString():''}</div>
                  </div>
                </div>`;
              })}
            </div>`}
          </div>`:null}
          ${tab==='members'?html`
            <div style=${{display:'flex',flexDirection:'column',gap:8}}>
              <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:6}}>
                <span style=${{color:'var(--tx2)',fontSize:13}}>${projUsers.length} members</span>
                ${cu&&cu.role!=='Viewer'?html`<button class="btn bg" style=${{fontSize:12,padding:'7px 12px'}} onClick=${()=>{setEdit(true);setTab('tasks');}}>Edit Members</button>`:null}
              </div>
              ${projUsers.map(m=>html`<div key=${m.id} style=${{display:'flex',alignItems:'center',gap:12,padding:'11px 14px',background:'var(--sf2)',borderRadius:10,border:'1px solid var(--bd)'}}>
                <${Av} u=${m} size=${36}/>
                <div style=${{flex:1}}><div style=${{fontSize:13,fontWeight:600,color:'var(--tx)'}}>${m.name}</div><div style=${{fontSize:11,color:'var(--tx3)',fontFamily:'monospace'}}>${m.email}</div></div>
                <span class="badge" style=${{background:'var(--ac)22',color:'var(--ac2)'}}>${m.role}</span>
              </div>`)}
            </div>`:null}
        </div>
      </div>

      ${showNew?html`<${TaskModal} task=${null} onClose=${()=>setShowNew(false)} onSave=${saveTask} projects=${[project]} users=${projUsers.length?projUsers:allUsers} cu=${cu} defaultPid=${project.id} onSetReminder=${onSetReminder} teams=${teams||[]} activeTeam=${activeTeam}/>`:null}
      ${editTask?html`<${TaskModal} task=${editTask} onClose=${()=>setEditTask(null)} onSave=${saveTask} onDel=${delTask} projects=${[project]} users=${projUsers.length?projUsers:allUsers} cu=${cu} defaultPid=${project.id} onSetReminder=${onSetReminder} teams=${teams||[]}/>`:null}
    </div>`;
}

/* ─── ProjectsView ────────────────────────────────────────────────────────── */
function ProjectsView({projects,tasks,users,cu,reload,onSetReminder,teams,activeTeam,initialProjectId,onClearInitial}){
  const [showNew,setShowNew]=useState(false);const [detail,setDetail]=useState(null);

  // Open project from initialProjectId prop OR directly from URL path /projects/<id>
  useEffect(()=>{
    if(safe(projects).length===0) return;
    // Check URL for project id first
    try{
      const parts=window.location.pathname.split('/');
      if(parts[1]==='projects'&&parts[2]){
        const urlProject=safe(projects).find(proj=>proj.id===parts[2]);
        if(urlProject){setDetail(urlProject);return;}
      }
    }catch(e){}
    // Fall back to prop
    if(initialProjectId){
      const p=safe(projects).find(proj=>proj.id===initialProjectId);
      if(p){setDetail(p);onClearInitial&&onClearInitial();}
    }
  },[initialProjectId,projects.length]); // re-run when projects load
  const [name,setName]=useState('');const [desc,setDesc]=useState('');
  const [sDate,setSDate]=useState('');const [tDate,setTDate]=useState('');
  const [color,setColor]=useState('#2563eb');const [members,setMembers]=useState([]);const [err,setErr]=useState('');
  const [search,setSearch]=useState('');
  const [sortBy,setSortBy]=useState('newest');
  const [viewMode,setViewMode]=useState('grid');
  const [projTeam,setProjTeam]=useState('');

  useEffect(()=>{if(detail){
    const fresh=safe(projects).find(p=>p.id===detail.id);if(fresh)setDetail(fresh);
    // Push clean URL with project id
    try{
      const slug=detail.id;
      history.pushState(null,'','/projects/'+slug);
      document.title='VEWIT — '+detail.name+' | Projects';
    }catch(e){}
  } else {
    // Back to /projects when detail closes
    try{
      if(window.location.pathname.startsWith('/projects/')){
        history.pushState(null,'','/projects');
        document.title='VEWIT — Projects | AI-Powered Team Collaboration';
      }
    }catch(e){}
  }},[detail]);
  useEffect(()=>{if(activeTeam)setProjTeam(activeTeam.id);},[activeTeam]);

  // Handle browser back/forward within projects
  useEffect(()=>{
    const onPop=()=>{
      const parts=window.location.pathname.split('/');
      if(parts[1]==='projects'&&parts[2]){
        const p=safe(projects).find(proj=>proj.id===parts[2]);
        if(p){setDetail(p);return;}
      }
      if(window.location.pathname==='/projects'||window.location.pathname==='/projects/'){
        setDetail(null);
      }
    };
    window.addEventListener('popstate',onPop);
    return()=>window.removeEventListener('popstate',onPop);
  },[projects]);

  const create=async()=>{
    if(!name.trim()){setErr('Project name required.');return;}setErr('');
    try{
      let mems=members.includes(cu.id)?members:[cu.id,...members];
      if(projTeam){
        const team=teams.find(t=>t.id===projTeam);
        if(team){
          const teamMids=JSON.parse(team.member_ids||'[]');
          teamMids.forEach(mid=>{if(!mems.includes(mid))mems.push(mid);});
        }
      }
      const newProj=await api.post('/api/projects',{name:name.trim(),description:desc,startDate:sDate,targetDate:tDate,color,members:mems,team_id:projTeam||''});
      if(newProj&&newProj.error){setErr(newProj.error);return;}
      if(!newProj||!newProj.id){setErr('Failed to create project. Please try again.');return;}
      setShowNew(false);setName('');setDesc('');setSDate('');setTDate('');setColor('#2563eb');setMembers([]);setProjTeam('');
      await new Promise(r=>setTimeout(r,300));
      await reload();
      setTimeout(()=>reload(),1000);
    }catch(e){setErr('Error creating project: '+(e.message||'Unknown error'));}
  };

  const filteredProjects=useMemo(()=>{
    let rows=[...safe(projects)];
    if(search.trim()){const q=search.toLowerCase();rows=rows.filter(p=>p.name.toLowerCase().includes(q)||(p.description||'').toLowerCase().includes(q));}
    rows.sort((a,b)=>{
      if(sortBy==='newest') return new Date(b.created||0)-new Date(a.created||0);
      if(sortBy==='oldest') return new Date(a.created||0)-new Date(b.created||0);
      if(sortBy==='name')   return a.name.localeCompare(b.name);
      if(sortBy==='progress'){
        const getP=proj=>{const pt=safe(tasks).filter(t=>t.project===proj.id);return pt.length?Math.round(pt.reduce((s,t)=>s+(t.pct||0),0)/pt.length):(proj.progress||0);};
        return getP(b)-getP(a);
      }
      if(sortBy==='tasks') return safe(tasks).filter(t=>t.project===b.id).length-safe(tasks).filter(t=>t.project===a.id).length;
      return 0;
    });
    return rows;
  },[projects,search,sortBy,tasks]);

  return html`
    <div class="fi" style=${{height:'100%',overflow:'hidden',display:'flex',flexDirection:'column'}}>

            <div style=${{flexShrink:0,padding:'10px 16px',borderBottom:'1px solid var(--bd)',display:'flex',alignItems:'center',gap:8,flexWrap:'wrap',background:'var(--bg)'}}>
                <div style=${{position:'relative',flex:'1',minWidth:140,maxWidth:280}}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
            style=${{position:'absolute',left:8,top:'50%',transform:'translateY(-50%)',color:'var(--tx3)',pointerEvents:'none'}}>
            <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
          </svg>
          <input class="inp" style=${{paddingLeft:26,height:28,fontSize:12}} placeholder="Search projects..."
            value=${search} onInput=${e=>setSearch(e.target.value)}/>
        </div>
        <span style=${{fontSize:11,color:'var(--tx3)',whiteSpace:'nowrap',flexShrink:0}}>${filteredProjects.length} of ${safe(projects).length}</span>

                <div style=${{display:'flex',background:'var(--sf2)',borderRadius:7,padding:2,gap:1,flexShrink:0}}>
          ${[['newest','🕐 Newest'],['oldest','🕐 Oldest'],['name','🔤 Name'],['progress','📊 Progress'],['tasks','📋 Tasks']].map(([k,lbl])=>html`
            <button key=${k} class=${'tb'+(sortBy===k?' act':'')} style=${{fontSize:10,padding:'2px 7px'}}
              onClick=${()=>setSortBy(k)}>${lbl}</button>`)}
        </div>

                <div style=${{display:'flex',background:'var(--sf2)',borderRadius:7,padding:2,gap:1,flexShrink:0}}>
          <button class=${'tb'+(viewMode==='grid'?' act':'')} style=${{fontSize:12,padding:'2px 8px'}}
            onClick=${()=>setViewMode('grid')} title="Card view">⊞</button>
          <button class=${'tb'+(viewMode==='compact'?' act':'')} style=${{fontSize:12,padding:'2px 8px'}}
            onClick=${()=>setViewMode('compact')} title="Compact list">☰</button>
        </div>

        ${search?html`<button class="btn bg" style=${{fontSize:11,padding:'3px 8px',flexShrink:0}}
          onClick=${()=>setSearch('')}>✕</button>`:null}

        ${cu&&cu.role!=='Viewer'&&cu.role!=='Developer'&&cu.role!=='Tester'?html`
          <button class="btn bp" style=${{marginLeft:'auto',whiteSpace:'nowrap',flexShrink:0}}
            onClick=${()=>setShowNew(true)}>+ New Project</button>`:null}
      </div>

            <div style=${{flex:1,minHeight:0,overflowY:'auto',padding:'12px 16px'}}>

        ${filteredProjects.length===0?html`
          <div style=${{textAlign:'center',padding:'60px 0',color:'var(--tx3)'}}>
            <div style=${{fontSize:40,marginBottom:12}}>🔍</div>
            <div style=${{fontSize:14,fontWeight:600,color:'var(--tx2)',marginBottom:6}}>${search?`No projects match "${search}"`:'No projects yet'}</div>
            ${search?html`<button class="btn bg" style=${{fontSize:12}} onClick=${()=>setSearch('')}>Clear search</button>`:null}
          </div>`:null}

                ${viewMode==='grid'&&filteredProjects.length>0?html`
          <div style=${{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(275px,1fr))',gap:12}}>
            ${filteredProjects.map(p=>{
              const pt=safe(tasks).filter(t=>t.project===p.id);
              const done=pt.filter(t=>t.stage==='completed').length;
              const pc=pt.length?Math.round(pt.reduce((a,t)=>a+(t.pct||0),0)/pt.length):(p.progress||0);
              const mems=safe((()=>{try{return JSON.parse(p.members||'[]');}catch{return[];}})()).map(id=>safe(users).find(u=>u.id===id)).filter(Boolean);
              const fmtShort=d=>{if(!d)return '';const dt=new Date(d);return dt.toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'numeric'});};
              const daysWorked=p=>{
                const s=p.start_date?new Date(p.start_date):null;
                const e=p.target_date?new Date(p.target_date):null;
                if(!s||!e)return null;
                const today=new Date();today.setHours(0,0,0,0);s.setHours(0,0,0,0);e.setHours(0,0,0,0);
                const worked=Math.max(0,Math.round((Math.min(today,e)-s)/86400000));
                const total=Math.max(1,Math.round((e-s)/86400000));
                return {worked,total};
              };
              return html`
                <div key=${p.id} class="card"
                  style=${{cursor:'pointer',transition:'all .15s',borderTop:'3px solid '+p.color,padding:'14px'}}
                  onClick=${()=>setDetail(p)}
                  onMouseEnter=${e=>{e.currentTarget.style.transform='translateY(-2px)';e.currentTarget.style.boxShadow='var(--sh)';}}
                  onMouseLeave=${e=>{e.currentTarget.style.transform='';e.currentTarget.style.boxShadow='';}}>
                  <div style=${{display:'flex',alignItems:'flex-start',justifyContent:'space-between',marginBottom:7}}>
                    <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em',flex:1,marginRight:6,lineHeight:1.3}}>${p.name}</h3>
                    <span class="badge" style=${{background:p.color+'22',color:p.color,flexShrink:0,fontSize:9}}>${pt.length} tasks</span>
                  </div>
                  <p style=${{fontSize:11,color:p.description?'var(--tx2)':'var(--tx3)',lineHeight:1.5,marginBottom:9,display:'-webkit-box',WebkitLineClamp:2,WebkitBoxOrient:'vertical',overflow:'hidden',fontStyle:p.description?'normal':'italic'}}>${p.description||'Add a description…'}</p>
                  <div style=${{marginBottom:9}}>
                    <div style=${{display:'flex',justifyContent:'space-between',marginBottom:3}}>
                      <span style=${{fontSize:9,color:'var(--tx3)',fontWeight:600,textTransform:'uppercase',letterSpacing:'.5px'}}>Progress</span>
                      <span style=${{fontSize:9,color:'var(--tx2)',fontFamily:'monospace',fontWeight:700}}>${pc}%</span>
                    </div>
                    <${Prog} pct=${pc} color=${p.color}/>
                  </div>
                  <div style=${{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:5,marginBottom:9}}>
                    ${[['Tasks',pt.length,'var(--tx)'],['Done',done,'var(--gn)'],['Open',pt.length-done,'var(--am)']].map(([l,v,c])=>html`
                      <div key=${l} style=${{textAlign:'center',padding:'6px 4px',background:'var(--sf2)',borderRadius:7,border:'1px solid var(--bd2)'}}>
                        <div style=${{fontSize:15,fontWeight:700,color:c}}>${v}</div>
                        <div style=${{fontSize:8,color:'var(--tx3)',marginTop:1,textTransform:'uppercase',letterSpacing:'.5px'}}>${l}</div>
                      </div>`)}
                  </div>
                  <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
                    <div style=${{display:'flex'}}>
                      ${mems.slice(0,5).map((m,i)=>html`<div key=${m.id} title=${m.name} style=${{marginLeft:i>0?-6:0,border:'2px solid var(--sf)',borderRadius:'50%',zIndex:5-i}}><${Av} u=${m} size=${20}/></div>`)}
                    </div>
                    <div style=${{display:'flex',alignItems:'center',gap:5}}>
                      ${(()=>{const pt=safe(teams).find(t=>t.id===p.team_id);return pt?html`<span style=${{fontSize:9,color:'var(--tx2)',background:'var(--sf2)',border:'1px solid var(--bd)',padding:'1px 6px',borderRadius:4,fontWeight:600}}>${pt.name}</span>`:
                        cu&&(cu.role==='Admin'||cu.role==='Manager')&&safe(teams).length>0?html`<select style=${{fontSize:9,padding:'1px 4px',borderRadius:4,border:'1px solid var(--bd)',background:'var(--sf2)',color:'var(--tx3)',cursor:'pointer'}}
                          value="" onChange=${async e=>{if(!e.target.value)return;await api.post('/api/projects/bulk-assign-team',{team_id:e.target.value,project_ids:[p.id]});reload();}}
                          onClick=${e=>e.stopPropagation()}>
                          <option value="">+ Team</option>
                          ${safe(teams).map(t=>html`<option key=${t.id} value=${t.id}>${t.name}</option>`)}
                        </select>`:null;})()}
                      <div style=${{display:'flex',flexDirection:'column',alignItems:'flex-end',gap:2}}>
                        <span style=${{fontSize:9,color:'var(--tx3)'}}>
                          ${p.start_date?fmtShort(p.start_date)+' – ':''}${fmtShort(p.target_date)||'No date'}
                        </span>
                        ${(()=>{const dw=daysWorked(p);return dw?html`
                          <span style=${{fontSize:9,fontWeight:600,color:dw.worked>=dw.total?'var(--am)':'var(--cy)'}}>
                            ${dw.worked} / ${dw.total} days
                          </span>`:null;})()}
                      </div>
                    </div>
                  </div>
                </div>`;
            })}
          </div>`:null}

        ${viewMode==='compact'&&filteredProjects.length>0?html`
          <div style=${{display:'flex',flexDirection:'column',gap:3}}>
                        <div style=${{display:'grid',gridTemplateColumns:'1fr 90px 50px 50px 50px 90px',gap:8,padding:'4px 12px', fontSize:9,fontWeight:700,color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.5}}>
              <span>Project</span><span>Progress</span><span style=${{textAlign:'center'}}>Tasks</span>
              <span style=${{textAlign:'center'}}>Done</span><span style=${{textAlign:'center'}}>Open</span>
              <span style=${{textAlign:'right'}}>End Date</span>
            </div>
            ${filteredProjects.map(p=>{
              const pt=safe(tasks).filter(t=>t.project===p.id);
              const done=pt.filter(t=>t.stage==='completed').length;
              const pc=pt.length?Math.round(pt.reduce((a,t)=>a+(t.pct||0),0)/pt.length):(p.progress||0);
              return html`
                <div key=${p.id}
                  style=${{display:'grid',gridTemplateColumns:'1fr 90px 50px 50px 50px 90px',gap:8, alignItems:'center',padding:'8px 12px', background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:8, cursor:'pointer',transition:'background .1s',borderLeft:'3px solid '+p.color}}
                  onClick=${()=>setDetail(p)}
                  onMouseEnter=${e=>e.currentTarget.style.background='var(--sf2)'}
                  onMouseLeave=${e=>e.currentTarget.style.background='var(--sf)'}>
                                    <div style=${{minWidth:0}}>
                    <div style=${{fontSize:12,fontWeight:600,color:'var(--tx)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${p.name}</div>
                    <div style=${{fontSize:10,color:'var(--tx3)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',marginTop:1}}>${p.description||'—'}</div>
                  </div>
                                    <div style=${{display:'flex',alignItems:'center',gap:5}}>
                    <div style=${{flex:1,height:4,background:'var(--bd)',borderRadius:100,overflow:'hidden'}}>
                      <div style=${{height:'100%',width:pc+'%',background:p.color,borderRadius:100}}></div>
                    </div>
                    <span style=${{fontSize:9,fontFamily:'monospace',color:'var(--tx3)',flexShrink:0,minWidth:24}}>${pc}%</span>
                  </div>
                                    <div style=${{textAlign:'center',fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em'}}>${pt.length}</div>
                  <div style=${{textAlign:'center',fontSize:13,fontWeight:700,color:'var(--gn)'}}>${done}</div>
                  <div style=${{textAlign:'center',fontSize:13,fontWeight:700,color:'var(--am)'}}>${pt.length-done}</div>
                                    <div style=${{fontSize:9,color:'var(--tx3)',fontFamily:'monospace',textAlign:'right'}}>
                    ${p.target_date?new Date(p.target_date).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'2-digit'}):'—'}
                  </div>
                </div>`;
            })}
          </div>`:null}
      </div>

      ${showNew?html`
        <div class="ov" onClick=${e=>e.target===e.currentTarget&&setShowNew(false)}>
          <div class="mo fi" style=${{maxWidth:520}}>
            <div style=${{display:'flex',justifyContent:'space-between',marginBottom:18}}>
              <h2 style=${{fontSize:17,fontWeight:700,color:'var(--tx)'}}>New Project</h2>
              <button class="btn bg" style=${{padding:'7px 10px'}} onClick=${()=>setShowNew(false)}>✕</button>
            </div>
            <div style=${{display:'flex',flexDirection:'column',gap:12}}>
              <div><label class="lbl">Project Name *</label>
                <input class="inp" placeholder="E.g. Google SecOps Integration" value=${name} onInput=${e=>setName(e.target.value)}/></div>
              <div><label class="lbl">Description</label>
                <textarea class="inp" rows="3" placeholder="What is this project about?" onInput=${e=>setDesc(e.target.value)}>${desc}</textarea></div>
              <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr',gap:11}}>
                <div><label class="lbl">Start Date</label>
                  <input class="inp" type="date" value=${sDate} onChange=${e=>setSDate(e.target.value)} onFocus=${e=>{if(!e.target.value)e.target.value=new Date().toISOString().split('T')[0];}}/></div>
                <div><label class="lbl">End Date</label>
                  <input class="inp" type="date" value=${tDate} onChange=${e=>setTDate(e.target.value)} onFocus=${e=>{if(!e.target.value){e.target.value=new Date().toISOString().split('T')[0];}}}/></div>
              </div>
              <div><label class="lbl">Color</label>
                <div style=${{display:'flex',gap:7,flexWrap:'wrap',marginTop:4}}>
                  ${PAL.map(c=>html`<button key=${c} onClick=${()=>setColor(c)}
                    style=${{width:26,height:26,borderRadius:6,background:c, border:'3px solid '+(color===c?'#fff':'transparent'), cursor:'pointer',transform:color===c?'scale(1.15)':'none'}}></button>`)}
                </div>
              </div>
              <div><label class="lbl">Add Members</label>
                <${MemberPicker} allUsers=${users} selected=${members} onChange=${setMembers}/></div>
              ${cu&&(cu.role==='Admin'||cu.role==='Manager')&&teams.length>0?html`
              <div><label class="lbl">Assign to Team <span style=${{fontSize:10,color:'var(--tx3)',fontWeight:400}}>(optional — adds all team members)</span></label>
                <select class="sel" value=${projTeam} onChange=${e=>setProjTeam(e.target.value)}>
                  <option value="">— No team —</option>
                  ${safe(teams).map(t=>{
                    const mids=JSON.parse(t.member_ids||'[]');
                    return html`<option key=${t.id} value=${t.id}>${t.name} (${mids.length} member${mids.length!==1?'s':''})</option>`;
                  })}
                </select>
              </div>`:null}
              ${err?html`<div style=${{color:'var(--rd)',fontSize:12,padding:'7px 11px',background:'rgba(248,113,113,.07)',borderRadius:7}}>${err}</div>`:null}
              <div style=${{display:'flex',gap:9,justifyContent:'flex-end',paddingTop:4}}>
                <button class="btn bg" onClick=${()=>setShowNew(false)}>Cancel</button>
                <button class="btn bp" onClick=${create}>Create Project</button>
              </div>
            </div>
          </div>
        </div>`:null}

      ${detail?html`<${ProjectDetail} project=${detail} allTasks=${tasks} allUsers=${users} cu=${cu}
        onClose=${()=>setDetail(null)} onReload=${reload} onSetReminder=${onSetReminder} teams=${teams} activeTeam=${activeTeam}/>`:null}
    </div>`;
}

/* ─── TasksView with inline stage dropdown ────────────────────────────────── */
const STAGE_DAYS={backlog:0,planning:7,development:21,code_review:28,testing:35,uat:42,release:49,production:56,completed:60,blocked:0};
const STAGE_PCT={backlog:0,planning:10,development:35,code_review:55,testing:70,uat:80,release:90,production:95,completed:100,blocked:null};
function addDays(n){const d=new Date();d.setDate(d.getDate()+n);return d.toISOString().split('T')[0];}

function TasksView({tasks,projects,users,cu,reload,onSetReminder,initialStage,initialPriority,initialAssignee,teams,activeTeam}){
  const [mode,setMode]=useState('kanban');
  const [pid,setPid]=useState('all');
  const [teamF,setTeamF]=useState('all');
  const [priF,setPriF]=useState(initialPriority||'all');
  const [stageF,setStageF]=useState(initialStage||'all');
  const [assF,setAssF]=useState(initialAssignee==='me'?(cu&&cu.id)||'all':'all');
  const [dueF,setDueF]=useState('all');
  const [typeF,setTypeF]=useState('all');
  const [search,setSearch]=useState('');
  const [showFilters,setShowFilters]=useState(!!(initialStage||initialPriority));
  const [showResolved,setShowResolved]=useState(true);
  const [sortCol,setSortCol]=useState(null);
  const [sortDir,setSortDir]=useState('asc');
  const [sprintFilter,setSprintFilter]=useState('');
  const [editT,setEditT]=useState(null);const [newT,setNewT]=useState(false);
  const [csvImporting,setCsvImporting]=useState(false);
  const [csvResult,setCsvResult]=useState(null);
  const csvRef=useRef(null);
  useEffect(()=>{
    const h=(e)=>{
      if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA'||e.target.isContentEditable)return;
      if(e.key==='n'||e.key==='N'){e.preventDefault();setNewT(true);}
      if(e.key==='Escape'){setEditT(null);setNewT(false);}
    };
    document.addEventListener('keydown',h);
    return()=>document.removeEventListener('keydown',h);
  },[]);

  useEffect(()=>{
    if(initialStage){setStageF(initialStage);setShowFilters(true);}
    if(initialStage==='completed'){setShowResolved(true);}
    if(initialPriority){setPriF(initialPriority);setShowFilters(true);}
    if(initialAssignee==='me'&&cu){setAssF(cu.id);setShowFilters(true);}
  },[initialStage,initialPriority,initialAssignee,cu]);

  const RESOLVED_STAGES=new Set(['completed']);

  const activeFilters=[pid,teamF,priF,stageF,assF,dueF].filter(v=>v!=='all').length;
  const clearAll=()=>{setPid('all');setTeamF('all');setPriF('all');setStageF('all');setAssF('all');setDueF('all');setSearch('');setShowResolved(false);};

  const teamFilterMemberIds=useMemo(()=>{
    if(teamF==='all')return null;
    const team=safe(teams).find(t=>t.id===teamF);
    return team?new Set(JSON.parse(team.member_ids||'[]')):null;
  },[teamF,teams]);

  const filtered=useMemo(()=>{
    const today=new Date();today.setHours(0,0,0,0);
    const endOfWeek=new Date(today);endOfWeek.setDate(today.getDate()+7);
    const endOfMonth=new Date(today);endOfMonth.setDate(today.getDate()+30);
    return safe(tasks).filter(t=>{
      if(!showResolved && RESOLVED_STAGES.has(t.stage) && stageF!=='completed') return false;
      if(pid!=='all'&&t.project!==pid)return false;
      if(priF!=='all'&&t.priority!==priF)return false;
      if(stageF!=='all'&&t.stage!==stageF)return false;
      if(assF!=='all'&&t.assignee!==assF)return false;
      if(teamF!=='all'){
        const byTeamId=t.team_id&&t.team_id===teamF;
        const byAssignee=teamFilterMemberIds&&t.assignee&&teamFilterMemberIds.has(t.assignee);
        if(!byTeamId&&!byAssignee)return false;
      }
      if(search){const sq=search.toLowerCase();if(!t.title.toLowerCase().includes(sq)&&!t.id.toLowerCase().includes(sq))return false;}
      if(dueF!=='all'&&t.due){
        const d=new Date(t.due);d.setHours(0,0,0,0);
        if(dueF==='overdue'&&d>=today)return false;
        if(dueF==='today'&&d.getTime()!==today.getTime())return false;
        if(dueF==='week'&&(d<today||d>endOfWeek))return false;
        if(dueF==='month'&&(d<today||d>endOfMonth))return false;
      } else if(dueF!=='all'&&!t.due) return false;
      if(typeF!=='all'&&t.task_type!==typeF)return false;
      if(sprintFilter&&t.sprint!==sprintFilter)return false;
      return true;
    });
  },[tasks,pid,teamF,teamFilterMemberIds,priF,stageF,assF,dueF,search,showResolved,sprintFilter,typeF]);

  const toggleSort=col=>{if(sortCol===col)setSortDir(d=>d==='asc'?'desc':'asc');else{setSortCol(col);setSortDir('asc');}};

  const PRI_ORD={critical:0,high:1,medium:2,low:3};
  const STAGE_ORD={backlog:0,planning:1,development:2,code_review:3,testing:4,uat:5,release:6,production:7,completed:8,blocked:9};

  const sorted=useMemo(()=>{
    if(!sortCol)return filtered;
    return [...filtered].sort((a,b)=>{
      let av,bv;
      if(sortCol==='assignee'){const au=safe(users).find(u=>u.id===a.assignee);const bu=safe(users).find(u=>u.id===b.assignee);av=(au&&au.name)||'';bv=(bu&&bu.name)||'';}
      else if(sortCol==='priority'){av=PRI_ORD[a.priority]??99;bv=PRI_ORD[b.priority]??99;return sortDir==='asc'?av-bv:bv-av;}
      else if(sortCol==='stage'){av=STAGE_ORD[a.stage]??99;bv=STAGE_ORD[b.stage]??99;return sortDir==='asc'?av-bv:bv-av;}
      else if(sortCol==='due'){av=a.due||'9999';bv=b.due||'9999';}
      else if(sortCol==='pct'){av=a.pct||0;bv=b.pct||0;return sortDir==='asc'?av-bv:bv-av;}
      return sortDir==='asc'?av.localeCompare(bv):bv.localeCompare(av);
    });
  },[filtered,sortCol,sortDir,users]);

  const [celebTask,setCelebTask]=useState(null);
  const _celebTimeout=useRef(null);
  const triggerTaskCelebration=(taskTitle,taskProjectId)=>{
    // Show task celebration
    setCelebTask({title:taskTitle,projectId:taskProjectId});
    if(_celebTimeout.current)clearTimeout(_celebTimeout.current);
    _celebTimeout.current=setTimeout(()=>setCelebTask(null),4000);
  };
  const saveT=async p=>{
    let r;
    if(p.id&&safe(tasks).find(t=>t.id===p.id))r=await api.put('/api/tasks/'+p.id,p);
    else r=await api.post('/api/tasks',p);
    // Trigger celebration if task just completed
    if(p.stage==='completed'||p.stage==='production'){
      const tTitle=p.title||(safe(tasks).find(t=>t.id===p.id)||{}).title||'Task';
      triggerTaskCelebration(tTitle,p.project);
    }
    reload();return r;
  };
  const delT=async id=>{await api.del('/api/tasks/'+id);reload();};
  const quickStage=async(tid,stage)=>{
    const autoPct=STAGE_PCT[stage];
    const payload={stage};
    if(autoPct!==null&&autoPct!==undefined)payload.pct=autoPct;
    // Celebration for quick stage change
    if(stage==='completed'||stage==='production'){
      const tk=safe(tasks).find(t=>t.id===tid);
      if(tk)triggerTaskCelebration(tk.title,tk.project);
    }
    await api.put('/api/tasks/'+tid,payload);reload();
  };

  // Export filtered tasks as CSV
  const exportTasksCsv=()=>{
    const projMap=safe(projects).reduce((m,p)=>{m[p.id]=p.name;return m;},{});
    const userMap=safe(users).reduce((m,u)=>{m[u.id]=u.name;return m;},{});
    const headers=['ID','Title','Project','Assignee','Priority','Stage','Due Date','Progress %','Type','Sprint','Story Points'];
    const rows=sorted.map(t=>[
      t.id,
      '"'+(t.title||'')+'"',
      '"'+(projMap[t.project]||'')+'"',
      '"'+(userMap[t.assignee]||'')+'"',
      t.priority||'',
      t.stage||'',
      t.due||'',
      t.pct||0,
      t.task_type||'task',
      t.sprint||'',
      t.story_points||0
    ]);
    const csv='data:text/csv;charset=utf-8,'+[headers,...rows].map(r=>r.join(',')).join('\n');
    const a=document.createElement('a');
    a.setAttribute('href',encodeURI(csv));
    a.setAttribute('download','vewit_tasks_'+new Date().toISOString().slice(0,10)+'.csv');
    document.body.appendChild(a);a.click();document.body.removeChild(a);
  };

  const importCsv=async(e)=>{
    const file=e.target.files&&e.target.files[0];
    if(!file)return;
    setCsvImporting(true);setCsvResult(null);
    const fd=new FormData();fd.append('file',file);
    const r=await api.upload('/api/import/csv',fd);
    setCsvImporting(false);
    setCsvResult(r);
    reload();
    e.target.value='';
  };

  return html`
    <div class="fi" style=${{display:'flex',flexDirection:'column',height:'100%',overflow:'hidden'}}>
    ${celebTask?html`
    <div style=${{position:'fixed',inset:0,background:'rgba(0,0,0,.5)',zIndex:9999,display:'flex',alignItems:'center',justifyContent:'center',backdropFilter:'blur(4px)'}}
      onClick=${()=>setCelebTask(null)}>
      <div style=${{background:'var(--sf)',borderRadius:24,padding:'36px 44px',textAlign:'center',boxShadow:'0 24px 80px rgba(0,0,0,.4)',border:'1px solid var(--bd)',maxWidth:360,animation:'vwBoot-up .4s ease both'}}>
        <div style=${{fontSize:52,marginBottom:10}}>🎉</div>
        <div style=${{fontSize:20,fontWeight:800,color:'var(--tx)',marginBottom:8}}>Task Completed!</div>
        <div style=${{fontSize:13,color:'var(--tx2)',fontWeight:600,maxWidth:280,margin:'0 auto 16px',wordBreak:'break-word'}}>${celebTask.title}</div>
        <div style=${{display:'flex',gap:8,justifyContent:'center',fontSize:22}}>
          ${['⭐','✨','🏅','🔥','💪','🎊'].map((e,i)=>html`<span key=${i}>${e}</span>`)}
        </div>
      </div>
    </div>`:null}
      <div style=${{padding:'8px 18px',borderBottom:'1px solid var(--bd)',background:'var(--sf)',flexShrink:0}}>
        <div style=${{display:'flex',gap:8,alignItems:'center',flexWrap:'wrap'}}>
          <div style=${{position:'relative',flex:'1 1 160px',minWidth:130}}>
            <span style=${{position:'absolute',left:10,top:'50%',transform:'translateY(-50%)',color:'var(--tx3)',fontSize:13}}>🔍</span>
            <input class="inp" style=${{paddingLeft:30}} placeholder="Search by task ID or name (e.g. T-015)" value=${search} onInput=${e=>setSearch(e.target.value)}/>
          </div>
          <button class=${'btn bg'+(showFilters?' act':'')} style=${{position:'relative',padding:'8px 13px',fontSize:12,borderColor:activeFilters>0?'var(--ac)':'',color:activeFilters>0?'var(--ac2)':''}}
            onClick=${()=>setShowFilters(!showFilters)}>
            ⚙ Filters${activeFilters>0?html` <span style=${{background:'var(--ac)',color:'#fff',borderRadius:8,fontSize:9,padding:'1px 5px',marginLeft:3,fontFamily:'monospace'}}>${activeFilters}</span>`:''}
          </button>
          ${assF!=='all'&&assF===cu.id?html`
            <div style=${{display:'flex',alignItems:'center',gap:6,padding:'5px 10px 5px 8px',background:'var(--ac3)',border:'1px solid var(--ac)',borderRadius:20,flexShrink:0}}>
              <div style=${{width:6,height:6,borderRadius:'50%',background:'var(--ac)',flexShrink:0}}></div>
              <span style=${{fontSize:11,fontWeight:700,color:'var(--tx2)'}}>My Tasks</span>
              <button onClick=${()=>setAssF('all')}
                style=${{background:'none',border:'none',cursor:'pointer',color:'var(--ac)',fontSize:14,lineHeight:1,padding:'0 2px'}}>×</button>
            </div>`:null}
          ${activeFilters>0?html`<button class="btn bam" style=${{padding:'7px 11px',fontSize:11}} onClick=${clearAll}>✕ Clear</button>`:null}
                    <div style=${{display:'flex',background:'var(--sf2)',borderRadius:9,padding:3,gap:2,flex:'0 0 auto'}}>
            <button class=${'tb'+(mode==='kanban'?' act':'')} onClick=${()=>setMode('kanban')}>⊞ Board</button>
            <button class=${'tb'+(mode==='list'?' act':'')} onClick=${()=>setMode('list')}>☰ List</button>
          </div>
          <input ref=${csvRef} type="file" accept=".csv" style=${{display:'none'}} onChange=${importCsv}/>
          ${cu&&(cu.role==='Admin'||cu.role==='Manager'||cu.role==='TeamLead')?html`
          <div style=${{display:'flex',gap:0,flex:'0 0 auto',borderRadius:100,overflow:'hidden',border:'1px solid var(--bd)'}}>
            <button style=${{fontSize:12,padding:'7px 12px',background:'transparent',border:'none',borderRight:'1px solid var(--bd)',cursor:'pointer',color:'var(--tx2)',fontWeight:600,display:'inline-flex',alignItems:'center',gap:5,transition:'background .12s'}}
              onClick=${()=>csvRef.current&&csvRef.current.click()} disabled=${csvImporting} title="Import tasks from CSV"
              onMouseEnter=${e=>e.currentTarget.style.background='var(--sf2)'} onMouseLeave=${e=>e.currentTarget.style.background='transparent'}>
              ${csvImporting?html`<span class="spin"></span>`:html`<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>`}
              Import
            </button>
            <button onClick=${exportTasksCsv} style=${{fontSize:12,padding:'7px 12px',background:'transparent',border:'none',cursor:'pointer',color:'var(--tx2)',fontWeight:600,display:'inline-flex',alignItems:'center',gap:5,transition:'background .12s'}}
              title="Export filtered tasks as CSV"
              onMouseEnter=${e=>e.currentTarget.style.background='var(--sf2)'} onMouseLeave=${e=>e.currentTarget.style.background='transparent'}>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
              Export
            </button>
          </div>`:null}
          <button class=${'btn '+(showResolved?'bg':'bp')} style=${{flex:'0 0 auto',fontSize:12,padding:'7px 13px'}}
            onClick=${()=>setShowResolved(v=>!v)}
            title=${showResolved?'Click to hide completed tasks':'Click to show completed tasks'}>
            ${showResolved?'Hide Completed':'Show Completed'}
          </button>
          <button class="btn bp" style=${{flex:'0 0 auto',fontSize:12,padding:'7px 13px'}} onClick=${()=>setNewT(true)}>+ New Task</button>
        </div>
        ${csvResult?html`<div style=${{marginTop:8,padding:'8px 12px',borderRadius:8,fontSize:12,background:csvResult.error?'rgba(185,28,28,0.10)':'rgba(21,128,61,0.12)',border:'1px solid '+(csvResult.error?'rgba(255,68,68,.2)':'rgba(62,207,110,.2)'),color:csvResult.error?'var(--rd)':'var(--gn)',display:'flex',alignItems:'center',justifyContent:'space-between'}}>
          <span>${csvResult.error?'✕ '+csvResult.error:'✓ Imported '+csvResult.created_tasks+' task(s)'+(csvResult.created_projects>0?' & '+csvResult.created_projects+' project(s)':'')+(csvResult.errors&&csvResult.errors.length?' · '+csvResult.errors.length+' skipped':'')}</span>
          <button class="btn bg" style=${{padding:'3px 8px',fontSize:10}} onClick=${()=>setCsvResult(null)}>✕</button>
        </div>`:null}
        ${showFilters?html`
          <div style=${{display:'flex',gap:8,flexWrap:'wrap',marginTop:9,paddingTop:9,borderTop:'1px solid var(--bd)'}}>
            ${cu&&(cu.role==='Admin'||cu.role==='Manager')?html`
            <div style=${{display:'flex',flexDirection:'column',gap:3}}>
              <label style=${{fontSize:9,color:'var(--tx3)',fontFamily:'monospace',textTransform:'uppercase',letterSpacing:.5}}>Team</label>
              <select class="sel" style=${{width:140,fontSize:12}} value=${teamF} onChange=${e=>setTeamF(e.target.value)}>
                <option value="all">All Teams</option>
                ${safe(teams).map(t=>html`<option key=${t.id} value=${t.id}>${t.name}</option>`)}
              </select>
            </div>`:null}
            <div style=${{display:'flex',flexDirection:'column',gap:3}}>
              <label style=${{fontSize:9,color:'var(--tx3)',fontFamily:'monospace',textTransform:'uppercase',letterSpacing:.5}}>Project</label>
              <select class="sel" style=${{width:155,fontSize:12}} value=${pid} onChange=${e=>setPid(e.target.value)}>
                <option value="all">All Projects</option>
                ${safe(projects).map(p=>html`<option key=${p.id} value=${p.id}>${p.name}</option>`)}
              </select>
            </div>
            <div style=${{display:'flex',flexDirection:'column',gap:3}}>
              <label style=${{fontSize:9,color:'var(--tx3)',fontFamily:'monospace',textTransform:'uppercase',letterSpacing:.5}}>Assignee</label>
              <button class=${'chip'+(assF===cu.id?' on':'')} style=${{fontSize:11,marginBottom:5,display:'inline-flex',alignItems:'center',gap:5}}
                onClick=${()=>setAssF(assF===cu.id?'all':cu.id)}>
                👤 My Tasks only
              </button>
              <select class="sel" style=${{width:140,fontSize:12}} value=${assF} onChange=${e=>setAssF(e.target.value)}>
                <option value="all">All Members</option>
                ${safe(users).map(u=>html`<option key=${u.id} value=${u.id}>${u.name}</option>`)}
              </select>
            </div>
            <div style=${{display:'flex',flexDirection:'column',gap:3}}>
              <label style=${{fontSize:9,color:'var(--tx3)',fontFamily:'monospace',textTransform:'uppercase',letterSpacing:.5}}>Priority</label>
              <select class="sel" style=${{width:125,fontSize:12}} value=${priF} onChange=${e=>setPriF(e.target.value)}>
                <option value="all">All Priority</option>
                ${Object.entries(PRIS).map(([k,v])=>html`<option key=${k} value=${k}>${v.sym} ${v.label}</option>`)}
              </select>
            </div>
            <div style=${{display:'flex',flexDirection:'column',gap:3}}>
              <label style=${{fontSize:9,color:'var(--tx3)',fontFamily:'monospace',textTransform:'uppercase',letterSpacing:.5}}>Stage</label>
              <select class="sel" style=${{width:130,fontSize:12}} value=${stageF} onChange=${e=>setStageF(e.target.value)}>
                <option value="all">All Stages</option>
                ${Object.entries(STAGES).map(([k,v])=>html`<option key=${k} value=${k}>${v.label}</option>`)}
              </select>
            </div>
            <div style=${{display:'flex',flexDirection:'column',gap:3}}>
              <label style=${{fontSize:9,color:'var(--tx3)',fontFamily:'monospace',textTransform:'uppercase',letterSpacing:.5}}>Due Date</label>
              <select class="sel" style=${{width:120,fontSize:12}} value=${typeF} onChange=${e=>setTypeF(e.target.value)}>
                <option value="all">All Types</option>
                ${['task','story','bug','epic','spike'].map(tp=>html`<option key=${tp} value=${tp}>${tp.charAt(0).toUpperCase()+tp.slice(1)}</option>`)}
              </select>
              <select class="sel" style=${{width:130,fontSize:12}} value=${dueF} onChange=${e=>setDueF(e.target.value)}>
                <option value="all">Any Due Date</option>
                <option value="overdue">⚠ Overdue</option>
                <option value="today">📅 Due Today</option>
                <option value="week">📆 Due This Week</option>
                <option value="month">🗓 Due This Month</option>
              </select>
            </div>
            <div style=${{display:'flex',alignItems:'flex-end',paddingBottom:1}}>
              <span style=${{fontSize:11,color:'var(--tx3)',fontFamily:'monospace',padding:'0 4px'}}>${filtered.length} task${filtered.length!==1?'s':''} shown</span>
            </div>
          </div>`:null}
      </div>

      ${mode==='kanban'?html`
        <div style=${{flex:1,overflowX:'auto',overflowY:'hidden',padding:'13px 18px'}}>
          <div style=${{display:'flex',gap:11,height:'100%',minWidth:'fit-content'}}>
            ${KCOLS.map(st=>{
              const col=filtered.filter(t=>t.stage===st);const si=STAGES[st];
              return html`<div key=${st} style=${{flex:'0 0 220px',background:'var(--sf2)',border:'1px solid var(--bd)',borderRadius:11,padding:10,display:'flex',flexDirection:'column',gap:7,borderTop:'3px solid '+si.color,maxHeight:'100%'}}>
                <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',paddingBottom:7,borderBottom:'1px solid var(--bd)'}}>
                  <div style=${{display:'flex',alignItems:'center',gap:6}}><div style=${{width:7,height:7,borderRadius:2,background:si.color}}></div><span style=${{fontSize:11,fontWeight:700,color:'var(--tx)'}}>${si.label}</span></div>
                  <span style=${{fontSize:9,color:'var(--tx3)',background:'var(--bd)',padding:'2px 6px',borderRadius:4,fontFamily:'monospace'}}>${col.length}</span>
                </div>
                <div style=${{overflowY:'auto',display:'flex',flexDirection:'column',gap:7,flex:1}}>
                  ${col.map(tk=>{
                    const au=safe(users).find(u=>u.id===tk.assignee);
                    const proj=safe(projects).find(p=>p.id===tk.project);
                    const isOverdue=tk.due&&new Date(tk.due)<new Date()&&tk.stage!=='completed';
                    const isDueToday=tk.due&&fmtD(tk.due)===fmtD(new Date().toISOString().split('T')[0]);
                    return html`<div key=${tk.id} class="tkc" onClick=${()=>setEditT(tk)}>
                      <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:4}}>
                        <div style=${{display:'flex',alignItems:'center',gap:4}}>
                          <span style=${{width:10,height:10,borderRadius:2,display:'inline-block',flexShrink:0,background:TYPE_COLORS[tk.task_type||'task']||'#1d4ed8'}}></span>
                          <span style=${{fontSize:9,fontWeight:700,fontFamily:'monospace',padding:'1px 6px',borderRadius:4,background:TYPE_BG[tk.task_type||'task']||'rgba(29,78,216,0.10)',color:TYPE_COLORS[tk.task_type||'task']||'#1d4ed8',border:'1px solid '+(TYPE_BORDER[tk.task_type||'task']||'rgba(29,78,216,0.2)')}}>${tk.id}</span>
                        </div>
                        <${PB} p=${tk.priority}/>
                      </div>
                      ${(tk.task_type&&tk.task_type!=='task')||tk.story_points>0?html`
                        <div style=${{display:'flex',gap:4,marginBottom:4,alignItems:'center'}}>
                          ${tk.task_type&&tk.task_type!=='task'?html`<span style=${{fontSize:8,fontWeight:800,padding:'1px 5px',borderRadius:3,textTransform:'uppercase',flexShrink:0,background:({'story':'rgba(21,128,61,0.12)','bug':'rgba(185,28,28,0.10)','epic':'rgba(109,40,217,0.12)','spike':'rgba(180,83,9,0.10)'})[tk.task_type]||'var(--ac3)',color:({'story':'var(--gn)','bug':'var(--rd)','epic':'var(--pu)','spike':'var(--am)'})[tk.task_type]||'var(--ac)'}}>${tk.task_type}</span>`:null}
                          ${tk.story_points>0?html`<span style=${{fontSize:8,fontWeight:700,padding:'1px 5px',borderRadius:3,background:'var(--sf2)',color:'var(--tx2)',border:'1px solid var(--bd)'}}>${tk.story_points}pt${tk.story_points>1?'s':''}</span>`:null}
                        </div>`:null}
                      <p style=${{fontSize:12,fontWeight:600,color:'var(--tx)',marginBottom:5,lineHeight:1.4}}>${tk.title}</p>
                      ${proj?html`<div style=${{fontSize:9,color:'var(--tx3)',marginBottom:5,display:'flex',alignItems:'center',gap:3}}>
                        <div style=${{width:5,height:5,borderRadius:1,background:proj.color,flexShrink:0}}></div>
                        <span style=${{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${proj.name}</span>
                      </div>`:null}
                      ${tk.pct>0?html`<div style=${{marginBottom:5}}><${Prog} pct=${tk.pct} color=${si.color}/></div>`:null}
                      <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginTop:2}}>
                        ${au?html`<${Av} u=${au} size=${20} title=${au.name}/>`:html`<div style=${{width:20,height:20,borderRadius:'50%',background:'var(--bd)'}}></div>`}
                        ${tk.due?html`<span style=${{fontSize:9,fontFamily:'monospace',color:isOverdue?'var(--rd)':isDueToday?'var(--am)':'var(--tx3)',fontWeight:isOverdue||isDueToday?700:400}}>${isOverdue?'⚠ ':isDueToday?'📅 ':''}${fmtD(tk.due)}</span>`:null}
                      </div>
                    </div>`;
                  })}
                  ${col.length===0?html`<div style=${{padding:'14px 0',textAlign:'center',color:'var(--tx3)',fontSize:12}}>Empty</div>`:null}
                </div>
              </div>`;
            })}
          </div>
        </div>`:null}

      <!-- Sprint + stats bar -->
      ${(()=>{
        const sprints=[...new Set(safe(tasks).filter(t=>t.sprint).map(t=>t.sprint))];
        const totalPts=filtered.reduce((a,t)=>a+(t.story_points||0),0);
        const donePts=filtered.filter(t=>t.stage==='completed').reduce((a,t)=>a+(t.story_points||0),0);
        if(!sprints.length&&!totalPts)return null;
        return html`
          <div style=${{padding:'4px 18px 8px',display:'flex',gap:8,flexWrap:'wrap',alignItems:'center'}}>
            ${sprints.length>0?html`
              <span style=${{fontSize:10,fontWeight:700,color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.5}}>Sprint:</span>
              <button class=${'chip'+(sprintFilter===''?' on':'')} onClick=${()=>setSprintFilter('')} style=${{fontSize:10}}>All</button>
              ${sprints.map(sp=>html`<button key=${sp} class=${'chip'+(sprintFilter===sp?' on':'')} onClick=${()=>setSprintFilter(sp)} style=${{fontSize:10}}>${sp}</button>`)}
              <div style=${{width:1,height:16,background:'var(--bd)',margin:'0 4px'}}></div>
            `:null}
            ${totalPts>0?html`
              <span style=${{fontSize:10,color:'var(--tx3)'}}>
                <b style=${{color:'var(--ac)'}}>${donePts}</b>/<b style=${{color:'var(--tx2)'}}>${totalPts}</b> pts done
              </span>
              <div style=${{height:6,width:80,background:'var(--sf2)',borderRadius:100,overflow:'hidden',border:'1px solid var(--bd)'}}>
                <div style=${{height:'100%',width:(totalPts?Math.round(donePts*100/totalPts):0)+'%',background:'var(--gn)',borderRadius:100}}></div>
              </div>
            `:null}
          </div>`;
      })()}
      ${mode==='list'?html`
        <div style=${{flex:1,overflowY:'auto',padding:'13px 18px'}}>
          <div class="card" style=${{padding:0,overflow:'hidden'}}>
            <table style=${{width:'100%',borderCollapse:'collapse'}}>
              <thead>
                <tr style=${{borderBottom:'2px solid var(--bd)',background:'var(--sf2)'}}>
                  ${[
                    {k:'id', lbl:'ID', s:null}, {k:'type', lbl:'Type', s:null}, {k:'title', lbl:'Title', s:null}, {k:'project', lbl:'Project', s:null}, {k:'assignee',lbl:'Assignee', s:'assignee'}, {k:'priority',lbl:'Priority', s:'priority'}, {k:'stage', lbl:'Stage', s:'stage'}, {k:'due', lbl:'Due', s:'due'}, {k:'pct', lbl:'%', s:'pct'}, {k:'pts', lbl:'Pts', s:null}, ].map(h=>{
                    const isA=sortCol===h.s;const can=!!h.s;
                    return html`<th key=${h.k}
                      onClick=${can?()=>toggleSort(h.s):null}
                      style=${{padding:'10px 13px',textAlign:'left',fontSize:10,fontFamily:'monospace',textTransform:'uppercase',letterSpacing:.5,userSelect:'none',cursor:can?'pointer':'default',whiteSpace:'nowrap',color:isA?'var(--ac2)':'var(--tx3)',borderBottom:isA?'2px solid var(--ac)':'2px solid transparent',transition:'all .15s',background:isA?'rgba(99,102,241,.07)':'',position:'relative'}}>
                      <div style=${{display:'flex',alignItems:'center',gap:5}}>
                        <span>${h.lbl}</span>
                        ${can?html`<span style=${{display:'flex',flexDirection:'column',lineHeight:.8,fontSize:8,gap:1}}>
                          <span style=${{color:isA&&sortDir==='asc'?'var(--ac2)':'var(--tx3)',opacity:isA&&sortDir==='asc'?1:.4}}>▲</span>
                          <span style=${{color:isA&&sortDir==='desc'?'var(--ac2)':'var(--tx3)',opacity:isA&&sortDir==='desc'?1:.4}}>▼</span>
                        </span>`:null}
                      </div>
                    </th>`;
                  })}
                </tr>
              </thead>
              <tbody>
                ${sorted.map((tk,i)=>{
                  const pr=safe(projects).find(p=>p.id===tk.project);
                  const au=safe(users).find(u=>u.id===tk.assignee);
                  const si=STAGES[tk.stage]||{color:'#94a3b8'};
                  return html`
                    <tr key=${tk.id} style=${{borderBottom:i<sorted.length-1?'1px solid var(--bd)':'none'}}
                      onMouseEnter=${e=>e.currentTarget.style.background='var(--sf2)'}
                      onMouseLeave=${e=>e.currentTarget.style.background=''}>
                      <td style=${{padding:'9px 13px'}}><span class="id-badge id-task">${tk.id}</span></td>
                      <td style=${{padding:'9px 13px'}}>${tk.task_type&&tk.task_type!=='task'?html`<span style=${{fontSize:9,fontWeight:800,padding:'2px 6px',borderRadius:3,textTransform:'uppercase',background:({'story':'rgba(21,128,61,0.12)','bug':'rgba(185,28,28,0.10)','epic':'rgba(109,40,217,0.12)','spike':'rgba(180,83,9,0.10)'})[tk.task_type]||'var(--ac3)',color:({'story':'var(--gn)','bug':'var(--rd)','epic':'var(--pu)','spike':'var(--am)'})[tk.task_type]||'var(--ac)'}}>${tk.task_type}</span>`:html`<span style=${{fontSize:9,color:'var(--tx3)'}}>task</span>`}</td>
                      <td style=${{padding:'9px 13px',cursor:'pointer'}} onClick=${()=>setEditT(tk)}><span style=${{fontSize:13,color:'var(--tx)',fontWeight:500}}>${tk.title}</span></td>
                      <td style=${{padding:'9px 13px'}}>${pr?html`<div style=${{display:'flex',alignItems:'center',gap:5}}><div style=${{width:6,height:6,borderRadius:2,background:pr.color}}></div><span style=${{fontSize:12,color:'var(--tx2)'}}>${pr.name}</span></div>`:null}</td>
                      <td style=${{padding:'9px 13px'}}>${au?html`<div style=${{display:'flex',alignItems:'center',gap:6}}><${Av} u=${au} size=${19}/><span style=${{fontSize:12,color:'var(--tx2)'}}>${au.name}</span></div>`:html`<span style=${{color:'var(--tx3)',fontSize:12}}>—</span>`}</td>
                      <td style=${{padding:'7px 11px'}}><${PB} p=${tk.priority}/></td>
                      <td style=${{padding:'5px 9px'}}>
                        <div style=${{position:'relative',display:'inline-flex',alignItems:'center'}}>
                          <select
                            value=${tk.stage}
                            onChange=${e=>{e.stopPropagation();quickStage(tk.id,e.target.value);}}
                            onClick=${e=>e.stopPropagation()}
                            style=${{background:si.color+'1a',border:'2px solid '+si.color,color:si.color,borderRadius:8,padding:'5px 26px 5px 9px',fontSize:11,fontFamily:'monospace',fontWeight:700,cursor:'pointer',outline:'none',appearance:'none',WebkitAppearance:'none',MozAppearance:'none',minWidth:90}}>
                            ${Object.entries(STAGES).map(([k,v])=>html`<option key=${k} value=${k} style=${{background:'#0d0f18',color:'#e2e8f0'}}>${v.label}</option>`)}
                          </select>
                          <span style=${{position:'absolute',right:7,top:'50%',transform:'translateY(-50%)',pointerEvents:'none',fontSize:9,color:si.color,fontWeight:900}}>▾</span>
                        </div>
                      </td>
                      <td style=${{padding:'9px 11px'}}>${(()=>{const isOD=tk.due&&new Date(tk.due)<new Date()&&tk.stage!=='completed';return html`<span style=${{fontSize:11,color:isOD?'var(--rd)':'var(--tx2)',fontFamily:'monospace',fontWeight:isOD?700:400}}>${isOD?'⚠ ':''}${fmtD(tk.due)}</span>`;})()}</td>
                      <td style=${{padding:'9px 11px',minWidth:100}}>
                        <div style=${{display:'flex',alignItems:'center',gap:7}}>
                          <div style=${{flex:1}}><${Prog} pct=${tk.pct} color=${si.color}/></div>
                          <span style=${{fontSize:10,color:'var(--tx3)',fontFamily:'monospace',width:28,textAlign:'right',fontWeight:700}}>${tk.pct}%</span>
                        </div>
                      </td>
                      <td style=${{padding:'9px 11px',textAlign:'center'}}>
                        ${tk.story_points>0
                          ?html`<span style=${{fontSize:11,fontWeight:700,color:'var(--tx2)',fontFamily:'monospace',background:'var(--sf2)',padding:'2px 7px',borderRadius:5,border:'1px solid var(--bd)'}}>${tk.story_points}</span>`
                          :html`<span style=${{color:'var(--tx3)',fontSize:11}}>—</span>`}
                      </td>
                    </tr>`;
                })}
              </tbody>
            </table>
            ${sorted.length===0?html`<div style=${{padding:40,textAlign:'center',color:'var(--tx3)',fontSize:13}}><div style=${{fontSize:28,marginBottom:8}}>🔍</div>No tasks match your filters.</div>`:null}
          </div>
        </div>`:null}

      ${editT?html`<${TaskModal} task=${editT} onClose=${()=>setEditT(null)} onSave=${saveT} onDel=${delT} projects=${projects} users=${users} cu=${cu} onSetReminder=${onSetReminder} teams=${teams||[]}/>`:null}
      ${newT?html`<${TaskModal} task=${null} onClose=${()=>setNewT(false)} onSave=${saveT} projects=${projects} users=${users} cu=${cu} onSetReminder=${onSetReminder} teams=${teams||[]} activeTeam=${activeTeam||null}/>`:null}
    </div>`;
}

/* ─── Dashboard ───────────────────────────────────────────────────────────── */
function Dashboard({cu,tasks,projects,users,onNav,activeTeam,teams,setTeamCtx}){
  const t=safe(tasks);const p=safe(projects);const u=safe(users);
  const isAdminManager=cu&&(cu.role==='Admin'||cu.role==='Manager');
  const [teamDropOpen,setTeamDropOpen]=useState(false);
  const [teamSearch,setTeamSearch]=useState('');
  const teamDropRef=useRef(null);
  useEffect(()=>{
    if(!teamDropOpen)return;
    const h=e=>{if(teamDropRef.current&&!teamDropRef.current.contains(e.target))setTeamDropOpen(false);};
    document.addEventListener('mousedown',h);
    return()=>document.removeEventListener('mousedown',h);
  },[teamDropOpen]);
  const filteredTeams=useMemo(()=>safe(teams).filter(t2=>t2.name.toLowerCase().includes(teamSearch.toLowerCase())),[teams,teamSearch]);
  const myT=t.filter(x=>x.assignee===cu.id);
  const myActiveTasks=myT.filter(x=>x.stage!=='completed').sort((a,b)=>new Date(b.created||0)-new Date(a.created||0));
  const done=t.filter(x=>x.stage==='completed').length;
  const active=t.filter(x=>x.stage!=='completed').length;
  const blocked=t.filter(x=>x.stage==='blocked').length;
  const [tickets,setTickets]=useState([]);
  useEffect(()=>{
    const url=activeTeam?'/api/tickets?team_id='+activeTeam.id:'/api/tickets';
    api.get(url).then(d=>setTickets(Array.isArray(d)?d:[]));
  },[activeTeam]);
  // Today's logged hours for current user
  const [todayHrs,setTodayHrs]=useState('—');
  useEffect(()=>{
    api.get('/api/timelogs').then(logs=>{
      if(!Array.isArray(logs))return;
      const today=(()=>{const d=new Date();return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');})();
      const mine=logs.filter(l=>l.user_id===cu.id&&l.date===today);
      const total=mine.reduce((s,l)=>s+(Number(l.hours||0))+(Number(l.minutes||0)/60),0);
      const wh=Math.floor(total);const wm=Math.round((total-wh)*60);
      setTodayHrs(total>0?(wh>0?wh+'h'+(wm>0?' '+wm+'m':''):wm+'m'):'0m');
    });
  },[cu.id]);
  const openTickets=tickets.filter(x=>x.status==='open').length;
  const inProgressTickets=tickets.filter(x=>x.status==='in-progress').length;
  const myTickets=tickets.filter(x=>x.assignee===cu.id&&x.status!=='closed'&&x.status!=='resolved').length;
  const activeProjectIds=new Set(p.map(proj=>proj.id));
  const activeTasks=t.filter(x=>activeProjectIds.has(x.project)&&x.stage!=='completed');
  const priChart=[
    {name:'Critical',value:activeTasks.filter(x=>x.priority==='critical').length,color:'var(--rd)',priKey:'critical'}, {name:'High',value:activeTasks.filter(x=>x.priority==='high').length,color:'var(--rd2)',priKey:'high'}, {name:'Medium',value:activeTasks.filter(x=>x.priority==='medium').length,color:'var(--pu)',priKey:'medium'}, {name:'Low',value:activeTasks.filter(x=>x.priority==='low').length,color:'var(--cy)',priKey:'low'}
  ];
  const stats=[
    {label:'Total Projects',val:p.length,color:'#1d4ed8',bg:'rgba(29,78,216,0.10)',icon:html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>`,nav:'projects'}, {label:'Active Tasks',val:active,color:'#0e7490',bg:'rgba(14,116,144,0.10)',icon:html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>`,nav:'tasks'}, {label:'Completed',val:done,color:'var(--gn)',bg:'rgba(21,128,61,0.12)',icon:html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>`,nav:'tasks:stage:completed'}, {label:'Blocked',val:blocked,color:'var(--rd)',bg:'rgba(185,28,28,0.10)',icon:html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg>`,nav:'tasks:stage:blocked'}, {label:'My Tasks',val:myT.filter(x=>x.stage!=='completed').length,color:'var(--am)',bg:'rgba(180,83,9,0.10)',icon:html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>`,nav:'tasks:assignee:me'}, {label:'Team Members',val:u.length,color:'var(--pu)',bg:'rgba(109,40,217,0.10)',icon:html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>`,nav:isAdminManager?'team':'tasks:assignee:me'}, {label:'Open Tickets',val:openTickets,color:'var(--cy)',bg:'rgba(14,116,144,0.10)',icon:html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M2 9a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v1.5a1.5 1.5 0 0 0 0 3V15a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-1.5a1.5 1.5 0 0 0 0-3V9z"/><line x1="9" y1="7" x2="9" y2="17" strokeDasharray="2 2"/></svg>`,nav:'tickets:status:open'}, {label:'In Progress',val:inProgressTickets,color:'var(--am)',bg:'rgba(180,83,9,0.10)',icon:html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,nav:isAdminManager?'tickets':'tasks:assignee:me'}, {label:'My Tickets',val:myTickets,color:'var(--or)',bg:'rgba(194,65,12,0.10)',icon:html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>`,nav:'tickets:assignee:me'}, {label:"Today's Hours",val:todayHrs,color:'#0891b2',bg:'rgba(8,145,178,0.10)',strVal:true,icon:html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,nav:'timesheet'}, ];
  // ── Last 7 days hours sparkline data ────────────────────────────────────────
  const [hoursChart,setHoursChart]=useState([]);
  useEffect(()=>{
    api.get('/api/timelogs').then(logs=>{
      if(!Array.isArray(logs))return;
      const days=[];
      for(let i=6;i>=0;i--){
        const d=new Date();d.setDate(d.getDate()-i);
        const key=d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
        const label=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'][d.getDay()];
        const myLogs=logs.filter(l=>l.user_id===cu.id&&l.date===key);
        const hrs=myLogs.reduce((s,l)=>s+(Number(l.hours||0))+(Number(l.minutes||0)/60),0);
        days.push({day:label,hrs:Math.round(hrs*10)/10,date:key});
      }
      setHoursChart(days);
    });
  },[cu.id]);

  return html`
    <div class="fi" style=${{height:'100%',overflowY:'auto',padding:'12px 20px',display:'flex',flexDirection:'column',gap:12}}>
      <div style=${{padding:'10px 14px',background:'var(--sf)',borderRadius:12,border:'1px solid var(--bd2)',display:'flex',alignItems:'center',gap:10}}>
        <${Av} u=${cu} size=${32}/>
        <div style=${{flex:1,minWidth:0}}>
          <div style=${{display:'flex',alignItems:'center',gap:8,flexWrap:'wrap'}}>
            <span style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em'}}>Good day, ${(cu&&cu.name||'there').split(' ')[0]}! 👋</span>
            ${activeTeam?html`
              <span style=${{display:'inline-flex',alignItems:'center',gap:5,padding:'2px 8px',background:'rgba(29,78,216,0.08)',border:'1px solid rgba(29,78,216,0.2)',borderRadius:20,fontSize:10,fontWeight:600,color:'#1d4ed8',flexShrink:0}}>
                <div style=${{width:5,height:5,borderRadius:1,background:'var(--ac)'}}></div>
                ${activeTeam.name}
              </span>`:null}
          </div>
          <p style=${{color:'var(--tx3)',fontSize:11,marginTop:1}}>
            ${activeTeam?html`${p.length} projects · ${t.length} tasks · ${u.length} members · `:null}
            <b style=${{color:'var(--tx2)'}}>${myT.filter(x=>x.stage!=='completed').length}</b> active task${myT.filter(x=>x.stage!=='completed').length!==1?'s':''} assigned to you
          </p>
        </div>
        ${isAdminManager&&safe(teams).length>0?html`
          <div ref=${teamDropRef} style=${{position:'relative',flexShrink:0}}>
            <button onClick=${()=>setTeamDropOpen(v=>!v)}
              style=${{display:'flex',alignItems:'center',gap:7,padding:'7px 12px 7px 10px',borderRadius:10, border:'1px solid '+(teamDropOpen?'var(--ac)':'var(--bd)'), background:activeTeam?'var(--ac3)':'var(--sf2)', color:activeTeam?'var(--ac)':'var(--tx2)', cursor:'pointer',fontSize:12,fontWeight:600,transition:'all .15s',whiteSpace:'nowrap'}}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><circle cx="17" cy="8" r="3"/><circle cx="7" cy="8" r="3"/><path d="M3 21v-2a5 5 0 0 1 8.66-3.43"/><path d="M13 21v-2a5 5 0 0 1 10 0v2"/></svg>
              ${activeTeam?html`<div style=${{width:7,height:7,borderRadius:2,background:activeTeam.color||'var(--ac)',flexShrink:0}}></div>`:null}
              <span style=${{maxWidth:120,overflow:'hidden',textOverflow:'ellipsis'}}>${activeTeam?activeTeam.name:'All Teams'}</span>
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
                style=${{transform:teamDropOpen?'rotate(180deg)':'none',transition:'transform .15s',flexShrink:0}}><polyline points="6 9 12 15 18 9"/></svg>
            </button>
            ${teamDropOpen?html`
              <div style=${{position:'absolute',top:'calc(100% + 6px)',right:0,width:240,background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:12,boxShadow:'0 8px 32px rgba(0,0,0,.25)',zIndex:500,overflow:'hidden'}}>
                <div style=${{padding:'8px 10px',borderBottom:'1px solid var(--bd)'}}>
                  <div style=${{position:'relative'}}>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
                      style=${{position:'absolute',left:8,top:'50%',transform:'translateY(-50%)',color:'var(--tx3)',pointerEvents:'none'}}>
                      <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
                    </svg>
                    <input class="inp" placeholder="Search teams…" value=${teamSearch} autoFocus
                      style=${{height:28,fontSize:11,paddingLeft:26}} onInput=${e=>setTeamSearch(e.target.value)}/>
                  </div>
                </div>
                <div style=${{maxHeight:200,overflowY:'auto',padding:'4px 6px'}}>
                  <button onClick=${()=>{setTeamCtx&&setTeamCtx('');setTeamDropOpen(false);setTeamSearch('');}}
                    style=${{width:'100%',padding:'7px 10px',borderRadius:7,border:'none', background:!activeTeam?'var(--ac3)':'transparent', color:!activeTeam?'var(--ac)':'var(--tx2)', fontSize:12,fontWeight:!activeTeam?700:400, cursor:'pointer',textAlign:'left',display:'flex',alignItems:'center',gap:8,transition:'all .1s'}}
                    onMouseEnter=${e=>{if(activeTeam)e.currentTarget.style.background='var(--sf2)';}}
                    onMouseLeave=${e=>{if(activeTeam)e.currentTarget.style.background='transparent';}}>
                    🌐 All Teams
                  </button>
                  ${filteredTeams.map(team=>html`
                    <button key=${team.id} onClick=${()=>{setTeamCtx&&setTeamCtx(team.id);setTeamDropOpen(false);setTeamSearch('');}}
                      style=${{width:'100%',padding:'7px 10px',borderRadius:7,border:'none', background:activeTeam&&activeTeam.id===team.id?'var(--ac3)':'transparent', color:activeTeam&&activeTeam.id===team.id?'var(--ac)':'var(--tx2)', fontSize:12,fontWeight:activeTeam&&activeTeam.id===team.id?700:400, cursor:'pointer',textAlign:'left',display:'flex',alignItems:'center',gap:8,transition:'all .1s'}}
                      onMouseEnter=${e=>{if(!(activeTeam&&activeTeam.id===team.id))e.currentTarget.style.background='var(--sf2)';}}
                      onMouseLeave=${e=>{if(!(activeTeam&&activeTeam.id===team.id))e.currentTarget.style.background='transparent';}}>
                      <div style=${{width:8,height:8,borderRadius:2,background:team.color||'var(--ac)',flexShrink:0}}></div>
                      <span style=${{flex:1,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${team.name}</span>
                      ${activeTeam&&activeTeam.id===team.id?html`<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><polyline points="20 6 9 17 4 12"/></svg>`:null}
                    </button>`)}
                  ${filteredTeams.length===0?html`<div style=${{padding:'10px',fontSize:11,color:'var(--tx3)',textAlign:'center'}}>No teams found</div>`:null}
                </div>
              </div>`:null}
          </div>`:null}
      </div>
            <div style=${{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(150px,1fr))',gap:8}}>
        ${stats.map((s,i)=>html`
          <div key=${i} onClick=${()=>onNav(s.nav)}
            style=${{background:'var(--sf)',borderRadius:14,padding:'12px 14px',position:'relative',overflow:'hidden',cursor:'pointer',transition:'all .16s',border:'1px solid var(--bd2)'}}
            onMouseEnter=${e=>{e.currentTarget.style.borderColor=s.color;e.currentTarget.style.transform='translateY(-2px)';}}
            onMouseLeave=${e=>{e.currentTarget.style.borderColor='';e.currentTarget.style.transform='';}}>
            <div style=${{position:'absolute',top:0,left:0,right:0,height:2,background:s.color,borderRadius:'16px 16px 0 0'}}></div>
            <div style=${{width:26,height:26,borderRadius:7,background:s.bg,display:'flex',alignItems:'center',justifyContent:'center',color:s.color,marginBottom:8}}>${s.icon}</div>
            <div style=${{fontSize:24,fontWeight:700,color:'var(--tx)',lineHeight:1,fontFamily:"'Space Grotesk',sans-serif",letterSpacing:-1}}>${s.val}</div>
            <div style=${{fontSize:11,color:'var(--tx2)',marginTop:5,fontWeight:500}}>${s.label}</div>
          </div>`)}
      </div>

      <!-- Hours Logged — Last 7 Days sparkline -->
      ${hoursChart.length>0?html`
      <div style=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:14,padding:'14px 18px',marginTop:4}}>
        <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:12}}>
          <div style=${{fontSize:13,fontWeight:700,color:'var(--tx)'}}>⏱ Hours Logged — Last 7 Days</div>
          <span style=${{fontSize:11,color:'var(--ac)',cursor:'pointer',fontWeight:600}} onClick=${()=>onNav('timesheet')}>View Timesheet →</span>
        </div>
        <div style=${{display:'flex',alignItems:'flex-end',gap:5,height:72,paddingBottom:2}}>
          ${hoursChart.map((d,i)=>{
            const maxH=Math.max(...hoursChart.map(x=>x.hrs),1);
            const barH=Math.max(4,Math.round((d.hrs/maxH)*54));
            const isToday=i===6;
            const barCol=isToday?'var(--ac)':d.hrs>0?'rgba(90,140,255,.35)':'var(--bd)';
            return html`<div key=${d.day} style=${{display:'flex',flexDirection:'column',alignItems:'center',gap:3,flex:1}}>
              <div style=${{fontSize:9,fontWeight:700,color:isToday?'var(--ac)':'var(--tx3)',minHeight:12}}>${d.hrs>0?d.hrs+'h':''}</div>
              <div style=${{width:'100%',height:barH+'px',background:barCol,borderRadius:'3px 3px 0 0',transition:'height .3s'}}></div>
              <div style=${{fontSize:9,color:isToday?'var(--ac)':'var(--tx3)',fontWeight:isToday?700:400}}>${isToday?'Today':d.day}</div>
            </div>`;
          })}
        </div>
      </div>`:null}
      <div style=${{display:'grid',gridTemplateColumns:'240px 1fr 1fr',gap:14}}>
        <div class="card">
          <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em',marginBottom:11}}>Priority Split</h3>
          <${RC.ResponsiveContainer} width="100%" height=${120}>
            <${RC.PieChart}>
              <${RC.Pie} data=${priChart} cx="50%" cy="50%" innerRadius=${34} outerRadius=${52} dataKey="value" paddingAngle=${4} cursor="pointer"
                onClick=${(data)=>{if(data&&data.priKey)onNav('tasks:priority:'+data.priKey);}}>
                ${priChart.map((e,i)=>html`<${RC.Cell} key=${i} fill=${e.color}/>`)}<//>
              <${RC.Tooltip} contentStyle=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:12,color:'var(--tx)',fontSize:12}}/>
            <//>
          <//>
          ${priChart.map((item,i)=>html`
            <div key=${i} style=${{display:'flex',alignItems:'center',justifyContent:'space-between',padding:'5px 0',borderBottom:i<3?'1px solid var(--bd)':'none',cursor:'pointer'}}
              onClick=${()=>onNav('tasks:priority:'+item.priKey)}>
              <div style=${{display:'flex',alignItems:'center',gap:7}}>
                <div style=${{width:7,height:7,borderRadius:2,background:item.color}}></div>
                <span style=${{fontSize:12,color:'var(--tx2)'}}>${item.name}</span>
              </div>
              <span style=${{fontSize:12,color:'var(--tx)',fontFamily:'monospace',fontWeight:700}}>${item.value}</span>
            </div>`)}
          <p style=${{fontSize:10,color:'var(--tx3)',marginTop:6,textAlign:'center'}}>Click to filter by priority</p>
        </div>
        <div class="card" style=${{display:'flex',flexDirection:'column'}}>
          <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:12}}>
            <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em',margin:0}}>Project Progress</h3>
            <button class="btn bg" style=${{fontSize:10,padding:'2px 9px',height:22}} onClick=${()=>onNav('projects')}>View All</button>
          </div>
          <div style=${{flex:1,overflowY:'auto',maxHeight:220}}>
          ${p.map(proj=>{
            const pt=t.filter(x=>x.project===proj.id);
            const pc=pt.length?Math.round(pt.reduce((a,x)=>a+(x.pct||0),0)/pt.length):(proj.progress||0);
            return html`<div key=${proj.id} style=${{marginBottom:11}}>
              <div style=${{display:'flex',justifyContent:'space-between',marginBottom:4}}>
                <div style=${{display:'flex',alignItems:'center',gap:6}}>
                  <div style=${{width:7,height:7,borderRadius:2,background:proj.color}}></div>
                  <span style=${{fontSize:13,color:'var(--tx)',fontWeight:500}}>${proj.name}</span>
                </div>
                <span style=${{fontSize:11,color:'var(--tx2)',fontFamily:'monospace'}}>${pc}%</span>
              </div>
              <${Prog} pct=${pc} color=${proj.color}/>
            </div>`;
          })}
          </div>
        </div>
        <div class="card">
          <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:12}}>
            <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em',margin:0}}>My Active Tasks</h3>
            ${myActiveTasks.filter(x=>x.due&&new Date(x.due)<new Date()).length>0?html`
              <span style=${{fontSize:10,color:'var(--rd)',fontWeight:700,background:'rgba(248,113,113,.1)',padding:'2px 8px',borderRadius:10}}>
                ⚠ ${myActiveTasks.filter(x=>x.due&&new Date(x.due)<new Date()).length} overdue
              </span>`:null}
          </div>
          ${myActiveTasks.slice(0,6).map((tk,i)=>html`
            <div key=${tk.id} onClick=${()=>onNav('tasks:assignee:me')}
              style=${{display:'flex',gap:9,padding:'7px 0',borderBottom:i<Math.min(myActiveTasks.length,6)-1?'1px solid var(--bd)':'none',alignItems:'center',cursor:'pointer',borderRadius:6,transition:'background .1s'}}
              onMouseEnter=${e=>e.currentTarget.style.background='var(--sf2)'}
              onMouseLeave=${e=>e.currentTarget.style.background='transparent'}>
              <div style=${{width:6,height:6,borderRadius:2,background:(STAGES[tk.stage]&&STAGES[tk.stage].color)||'var(--ac)',flexShrink:0,marginLeft:3}}></div>
              <div style=${{flex:1,minWidth:0}}>
                <div style=${{fontSize:12,color:'var(--tx)',fontWeight:500,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${tk.title}</div>
                <div style=${{display:'flex',gap:5,marginTop:2,alignItems:'center'}}><${SP} s=${tk.stage}/><${PB} p=${tk.priority}/>
                  ${tk.due&&new Date(tk.due)<new Date()?html`<span style=${{fontSize:9,color:'var(--rd)',fontWeight:700}}>⚠ Overdue</span>`:null}
                </div>
              </div>
              <span style=${{fontSize:10,color:'var(--tx3)',fontFamily:'monospace',flexShrink:0}}>${tk.pct}%</span>
            </div>`)}
          ${myActiveTasks.length===0?html`<div style=${{color:'var(--tx3)',fontSize:13,textAlign:'center',paddingTop:16}}>No active tasks assigned. 🎉</div>`:null}
          ${myActiveTasks.length>0?html`
            <button class="btn bg" style=${{width:'100%',marginTop:10,fontSize:11,padding:'6px 0'}}
              onClick=${()=>onNav('tasks:assignee:me')}>
              View all my tasks →
            </button>`:null}
        </div>
      </div>
    </div>`;
}

/* ─── TimelineView (Admin/Manager only) ───────────────────────────────────── */
function TimelineView({cu,tasks,projects,onNav}){
  const t=safe(tasks);const p=safe(projects);
  const now=new Date();now.setHours(0,0,0,0);
  const [filterHealth,setFilterHealth]=useState('all');
  const [search,setSearch]=useState('');
  const [sortBy,setSortBy]=useState('health');

  const HC={
    'on-track':{label:'On Track',color:'var(--gn)',bg:'rgba(74,222,128,.12)'}, 'warning':{label:'At Risk',color:'var(--am)',bg:'rgba(251,191,36,.12)'}, 'at-risk':{label:'Needs Attention',color:'var(--rd)',bg:'rgba(248,113,113,.12)'}, 'overdue':{label:'Overdue',color:'var(--rd)',bg:'rgba(248,113,113,.2)'}, 'no-dates':{label:'No Dates',color:'var(--tx3)',bg:'rgba(255,255,255,.04)'}, };
  const HO={'overdue':0,'at-risk':1,'warning':2,'on-track':3,'no-dates':4};

  const timelines=useMemo(()=>p.map(proj=>{
    const start=proj.start_date?new Date(proj.start_date):null;
    const end=proj.target_date?new Date(proj.target_date):null;
    if(start)start.setHours(0,0,0,0);
    if(end)end.setHours(0,0,0,0);
    const totalDays=(start&&end)?Math.max(1,Math.round((end-start)/86400000)):null;
    const daysSpent=start?Math.max(0,Math.round((now-start)/86400000)):null;
    const daysLeft=end?Math.round((end-now)/86400000):null;
    const timeProgress=(totalDays&&daysSpent!==null)?Math.min(100,Math.round((daysSpent/totalDays)*100)):null;
    const isOverdue=end&&now>end;
    const pt=t.filter(x=>x.project===proj.id);
    const taskProgress=pt.length?Math.round(pt.reduce((a,x)=>a+(x.pct||0),0)/pt.length):(proj.progress||0);
    const gap=timeProgress!==null?(timeProgress-taskProgress):null;
    const health=gap===null?'no-dates':isOverdue&&taskProgress<100?'overdue':gap>30?'at-risk':gap>15?'warning':'on-track';
    return {...proj,start,end,totalDays,daysSpent,daysLeft,timeProgress,taskProgress,isOverdue,health,gap, taskCount:pt.length,doneTasks:pt.filter(x=>x.stage==='completed').length};
  }),[p,t,now]);

  const filtered=useMemo(()=>{
    let rows=[...timelines];
    if(filterHealth!=='all')rows=rows.filter(r=>r.health===filterHealth);
    if(search.trim()){const q=search.toLowerCase();rows=rows.filter(r=>r.name.toLowerCase().includes(q));}
    rows.sort((a,b)=>{
      if(sortBy==='health')return(HO[a.health]??9)-(HO[b.health]??9);
      if(sortBy==='name')return a.name.localeCompare(b.name);
      if(sortBy==='progress')return b.taskProgress-a.taskProgress;
      if(sortBy==='days_left'){if(a.daysLeft===null)return 1;if(b.daysLeft===null)return-1;return a.daysLeft-b.daysLeft;}
      if(sortBy==='spent'){if(a.daysSpent===null)return 1;if(b.daysSpent===null)return-1;return b.daysSpent-a.daysSpent;}
      return 0;
    });
    return rows;
  },[timelines,filterHealth,search,sortBy]);

  const fmtD=d=>d?d.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'}):'—';
  const counts={total:timelines.length,...Object.fromEntries(Object.keys(HC).map(k=>[k,timelines.filter(r=>r.health===k).length]))};

  return html`
    <div style=${{flex:1,minHeight:0,overflow:'hidden',display:'flex',flexDirection:'column',background:'var(--bg)'}}>

            <div style=${{flexShrink:0,padding:'12px 20px 10px',borderBottom:'1px solid var(--bd)',background:'var(--bg)'}}>

                <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:10}}>
          <div>
            <h2 style=${{fontSize:15,fontWeight:800,color:'var(--tx)',display:'flex',alignItems:'center',gap:7,margin:0}}>📅 Project Timeline Tracker</h2>
            <p style=${{fontSize:11,color:'var(--tx2)',marginTop:2,fontWeight:500}}>Days spent vs. remaining — based on today</p>
          </div>
          <span style=${{fontSize:11,color:'var(--tx3)',background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:7,padding:'4px 10px',fontFamily:'monospace',flexShrink:0}}>
            ${now.toLocaleDateString('en-US',{weekday:'short',month:'short',day:'numeric',year:'numeric'})}
          </span>
        </div>

                <div style=${{display:'flex',gap:7,marginBottom:10,flexWrap:'wrap'}}>
          ${[['all','All','#1d4ed8','rgba(29,78,216,0.08)',counts.total], ['on-track','On Track','var(--gn)','rgba(74,222,128,.1)',counts['on-track']], ['warning','At Risk','var(--am)','rgba(251,191,36,.1)',counts['warning']], ['at-risk','Needs Attn','var(--rd)','rgba(248,113,113,.1)',counts['at-risk']], ['overdue','Overdue','var(--rd)','rgba(248,113,113,.15)',counts['overdue']], ['no-dates','No Dates','var(--tx3)','rgba(255,255,255,.04)',counts['no-dates']], ].map(([k,lbl,color,bg,cnt])=>html`
            <div key=${k} onClick=${()=>setFilterHealth(k)}
              style=${{background:filterHealth===k?bg:'var(--sf)',border:'2px solid '+(filterHealth===k?color:'var(--bd)'), borderRadius:9,padding:'7px 14px',cursor:'pointer',transition:'all .15s', display:'flex',alignItems:'center',gap:8}}
              onMouseEnter=${e=>{if(filterHealth!==k)e.currentTarget.style.borderColor=color+'66';}}
              onMouseLeave=${e=>{if(filterHealth!==k)e.currentTarget.style.borderColor='var(--bd)';}}>
              <span style=${{fontSize:17,fontWeight:800,color,fontFamily:'monospace',lineHeight:1}}>${cnt}</span>
              <span style=${{fontSize:9,color:filterHealth===k?color:'var(--tx3)',fontWeight:700,textTransform:'uppercase',letterSpacing:.5}}>${lbl}</span>
            </div>`)}
        </div>

                <div style=${{display:'flex',gap:8,alignItems:'center'}}>
                    <div style=${{position:'relative',flex:1,maxWidth:260}}>
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
              style=${{position:'absolute',left:8,top:'50%',transform:'translateY(-50%)',color:'var(--tx3)',pointerEvents:'none'}}>
              <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
            </svg>
            <input class="inp" placeholder="Search projects..." value=${search}
              style=${{height:26,fontSize:11,paddingLeft:26}}
              onInput=${e=>setSearch(e.target.value)}/>
          </div>
                    <span style=${{fontSize:10,color:'var(--tx3)',fontWeight:700,textTransform:'uppercase',letterSpacing:.5}}>Sort:</span>
          <div style=${{display:'flex',background:'var(--sf2)',borderRadius:6,padding:2,gap:1}}>
            ${[['health','🚦 Health'],['name','🔤 Name'],['progress','✅ Tasks'],['days_left','⏳ Days Left'],['spent','📆 Days Spent']].map(([k,lbl])=>html`
              <button key=${k} class=${'tb'+(sortBy===k?' act':'')} style=${{fontSize:10,padding:'2px 8px'}} onClick=${()=>setSortBy(k)}>${lbl}</button>`)}
          </div>
                    ${(filterHealth!=='all'||search)?html`
            <button class="btn bg" style=${{fontSize:10,padding:'3px 9px'}}
              onClick=${()=>{setFilterHealth('all');setSearch('');}}>✕ Clear</button>`:null}
          <span style=${{marginLeft:'auto',fontSize:11,color:'var(--tx3)',whiteSpace:'nowrap'}}>${filtered.length}/${timelines.length} projects</span>
        </div>
      </div>

            <div style=${{flex:1,minHeight:0,overflowY:'auto',padding:'12px 20px',display:'flex',flexDirection:'column',gap:10}}>
        ${filtered.length===0?html`
          <div style=${{textAlign:'center',padding:'48px 0',color:'var(--tx3)'}}>
            <div style=${{fontSize:36,marginBottom:10}}>🔍</div>
            <div>No projects match "${search||filterHealth}".</div>
            <button class="btn bg" style=${{marginTop:12,fontSize:11}} onClick=${()=>{setSearch('');setFilterHealth('all');}}>Clear filters</button>
          </div>`:null}
        ${filtered.map(proj=>{
          const hc=HC[proj.health];
          return html`
            <div key=${proj.id} style=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:12, padding:'13px 17px',borderLeft:'4px solid '+proj.color,transition:'all .15s',cursor:'pointer'}}
              onClick=${()=>onNav&&onNav('projects',proj.id)}
              onMouseEnter=${e=>{e.currentTarget.style.boxShadow='0 4px 20px rgba(0,0,0,.3)';e.currentTarget.style.borderColor=proj.color;}}
              onMouseLeave=${e=>{e.currentTarget.style.boxShadow='';e.currentTarget.style.borderColor='var(--bd)';}}>
              <div style=${{display:'flex',alignItems:'center',gap:10,marginBottom:proj.totalDays!==null?10:4}}>
                <span style=${{fontSize:13,fontWeight:700,color:'var(--tx)',flex:1,cursor:'pointer'}}>${proj.name}</span>
                <span style=${{fontSize:10,fontWeight:700,padding:'3px 10px',borderRadius:100,background:hc.bg,color:hc.color}}>${hc.label}</span>
                <span style=${{fontSize:10,color:'var(--tx3)'}}>📋 ${proj.doneTasks}/${proj.taskCount}</span>
              </div>
              ${proj.totalDays!==null?html`
                <div style=${{display:'flex',flexDirection:'column',gap:5,marginBottom:10}}>
                  <div style=${{display:'flex',alignItems:'center',gap:10}}>
                    <span style=${{fontSize:10,color:'var(--tx2)',fontWeight:600,width:90,flexShrink:0}}>⏱ Time elapsed</span>
                    <div style=${{flex:1,height:7,background:'var(--sf3)',borderRadius:100,overflow:'hidden',border:'1px solid var(--bd)'}}>
                      <div style=${{height:'100%',width:proj.timeProgress+'%',borderRadius:100, background:proj.isOverdue?'var(--rd)':proj.timeProgress>70?'var(--am)':'var(--cy)'}}></div>
                    </div>
                    <span style=${{fontSize:10,fontFamily:'monospace',color:'var(--tx2)',width:34,textAlign:'right',fontWeight:700}}>${proj.timeProgress}%</span>
                  </div>
                  <div style=${{display:'flex',alignItems:'center',gap:10}}>
                    <span style=${{fontSize:10,color:'var(--tx2)',fontWeight:600,width:90,flexShrink:0}}>✅ Tasks done</span>
                    <div style=${{flex:1,height:7,background:'var(--sf3)',borderRadius:100,overflow:'hidden',border:'1px solid var(--bd)'}}>
                      <div style=${{height:'100%',width:proj.taskProgress+'%',borderRadius:100,background:proj.color}}></div>
                    </div>
                    <span style=${{fontSize:10,fontFamily:'monospace',color:'var(--tx2)',width:34,textAlign:'right',fontWeight:700}}>${proj.taskProgress}%</span>
                  </div>
                </div>
                <div style=${{display:'flex',gap:7,flexWrap:'wrap'}}>
                  ${[
                    {lbl:'Start',val:fmtD(proj.start),c:'var(--tx2)'}, {lbl:'End',val:fmtD(proj.end),c:proj.isOverdue?'var(--rd)':'var(--tx2)'}, {lbl:'Total',val:proj.totalDays+' days',c:'var(--tx2)'}, {lbl:'Spent',val:proj.daysSpent+' days',c:'var(--tx2)'}, {lbl:proj.isOverdue?'Overdue by':'Remaining',val:Math.abs(proj.daysLeft)+' days',c:proj.isOverdue?'var(--rd)':'var(--gn)'}, proj.gap!==null?{lbl:'Gap',val:(proj.gap>0?'+':'')+proj.gap+'%',c:proj.gap>15?'var(--rd)':proj.gap>0?'var(--am)':'var(--gn)'}:null, ].filter(Boolean).map((ch,i)=>html`
                    <div key=${i} style=${{padding:'3px 8px',background:'var(--sf2)',borderRadius:6,border:'1px solid var(--bd)'}}>
                      <span style=${{fontSize:9,color:'var(--tx2)',fontWeight:600,textTransform:'uppercase',letterSpacing:.4}}>${ch.lbl} </span>
                      <span style=${{fontSize:10,fontWeight:700,color:ch.c,fontFamily:'monospace'}}>${ch.val}</span>
                    </div>`)}
                </div>`:html`
                <div style=${{fontSize:11,color:'var(--tx3)',fontStyle:'italic'}}>No dates set — edit project to enable timeline tracking.</div>`}
            </div>`;
        })}
      </div>
    </div>`;
}

/* ─── ProductivityView (Admin/Manager only) ───────────────────────────────── */
function ProductivityView({cu,tasks,projects,users}){
  const t=safe(tasks);const p=safe(projects);const u=safe(users);
  const now=new Date();now.setHours(0,0,0,0);
  const [tab,setTab]=useState('table'); // 'table' | 'chart' | 'detail'
  const [selectedDev,setSelectedDev]=useState(null);
  const [filterRole,setFilterRole]=useState('all');
  const [filterProject,setFilterProject]=useState('all');
  const [sortBy,setSortBy]=useState('score');
  const [search,setSearch]=useState('');
  const roles=[...new Set(u.map(x=>x.role).filter(Boolean))];

  const devStats=useMemo(()=>u.map(dev=>{
    let devTasks=t.filter(x=>x.assignee===dev.id);
    if(filterProject!=='all')devTasks=devTasks.filter(x=>x.project===filterProject);
    const completed=devTasks.filter(x=>x.stage==='completed');
    const inProg=devTasks.filter(x=>x.stage==='in-progress'||x.stage==='development');
    const blocked=devTasks.filter(x=>x.stage==='blocked');
    const overdue=devTasks.filter(x=>x.due&&new Date(x.due)<now&&x.stage!=='completed');
    const total=devTasks.length;
    const completionRate=total?Math.round((completed.length/total)*100):0;
    const avgPct=total?Math.round(devTasks.reduce((a,x)=>a+(x.pct||0),0)/total):0;
    const score=Math.min(100,Math.round(completionRate*0.5+avgPct*0.3+Math.max(0,20-overdue.length*5)));
    const scoreColor=score>=70?'var(--gn)':score>=40?'var(--am)':'var(--rd)';
    const last7=t.filter(x=>x.assignee===dev.id&&(now-new Date(x.created||0))<7*86400000).length;
    const projSet=new Set(devTasks.map(x=>x.project));
    return {...dev,total,completed:completed.length,inProg:inProg.length,blocked:blocked.length, overdue:overdue.length,completionRate,avgPct,score,scoreColor,last7,projCount:projSet.size};
  }),[u,t,filterProject,now]);

  const filtered=useMemo(()=>{
    let rows=[...devStats];
    if(filterRole!=='all')rows=rows.filter(r=>r.role===filterRole);
    if(search.trim())rows=rows.filter(r=>r.name.toLowerCase().includes(search.toLowerCase()));
    rows.sort((a,b)=>{
      if(sortBy==='score')return b.score-a.score;
      if(sortBy==='name')return a.name.localeCompare(b.name);
      if(sortBy==='completed')return b.completed-a.completed;
      if(sortBy==='overdue')return b.overdue-a.overdue;
      if(sortBy==='tasks')return b.total-a.total;
      return 0;
    });
    return rows;
  },[devStats,filterRole,search,sortBy]);

  const selDev=selectedDev?devStats.find(d=>d.id===selectedDev):null;
  const selTasks=selDev?t.filter(x=>x.assignee===selDev.id&&(filterProject==='all'||x.project===filterProject)):[];
  const chartData=filtered.map(d=>({name:d.name.split(' ')[0],Completed:d.completed,'In Progress':d.inProg,Blocked:d.blocked}));

  const openDetail=(devId)=>{setSelectedDev(devId);setTab('detail');};
  const closeDetail=()=>{setSelectedDev(null);setTab('table');};

  return html`
    <div style=${{flex:1,minHeight:0,overflow:'hidden',display:'flex',flexDirection:'column',background:'var(--bg)'}}>

            <div style=${{flexShrink:0,padding:'10px 18px',borderBottom:'1px solid var(--bd)',background:'var(--bg)',display:'flex',alignItems:'center',gap:10,flexWrap:'wrap'}}>

                <div style=${{marginRight:4}}>
          <span style=${{fontSize:14,fontWeight:800,color:'var(--tx)'}}>👩‍💻 Dev Productivity</span>
          <span style=${{fontSize:11,color:'var(--tx3)',marginLeft:8}}>${u.length} developers · ${t.length} tasks</span>
        </div>

                <div style=${{position:'relative',flex:'1',maxWidth:200}}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
            style=${{position:'absolute',left:8,top:'50%',transform:'translateY(-50%)',color:'var(--tx3)',pointerEvents:'none'}}>
            <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
          </svg>
          <input class="inp" placeholder="Search developer..." value=${search}
            style=${{height:26,fontSize:11,paddingLeft:26}}
            onInput=${e=>setSearch(e.target.value)}/>
        </div>

                <select class="inp" style=${{height:26,fontSize:11,padding:'0 8px',maxWidth:120}} value=${filterRole}
          onChange=${e=>{setFilterRole(e.target.value);setSelectedDev(null);}}>
          <option value="all">All Roles</option>
          ${roles.map(r=>html`<option key=${r} value=${r}>${r}</option>`)}
        </select>

                <select class="inp" style=${{height:26,fontSize:11,padding:'0 8px',maxWidth:140}} value=${filterProject}
          onChange=${e=>{setFilterProject(e.target.value);setSelectedDev(null);}}>
          <option value="all">All Projects</option>
          ${p.map(pr=>html`<option key=${pr.id} value=${pr.id}>${pr.name}</option>`)}
        </select>

                <select class="inp" style=${{height:26,fontSize:11,padding:'0 8px',maxWidth:130}} value=${sortBy}
          onChange=${e=>setSortBy(e.target.value)}>
          <option value="score">Sort: Score</option>
          <option value="name">Sort: Name</option>
          <option value="tasks">Sort: Tasks</option>
          <option value="completed">Sort: Done</option>
          <option value="overdue">Sort: Overdue</option>
        </select>

                <div style=${{display:'flex',background:'var(--sf2)',borderRadius:7,padding:2,gap:1,marginLeft:'auto'}}>
          ${[['table','📋 Table'],['chart','📊 Chart']].map(([k,lbl])=>html`
            <button key=${k} class=${'tb'+(tab===k&&!selDev?' act':'')} style=${{fontSize:10,padding:'3px 10px'}}
              onClick=${()=>{closeDetail();setTab(k);}}>${lbl}</button>`)}
        </div>

        <span style=${{fontSize:11,color:'var(--tx3)',whiteSpace:'nowrap'}}>${filtered.length}/${u.length}</span>
      </div>

            ${!selDev?html`
        <div style=${{flexShrink:0,display:'flex',gap:0,borderBottom:'1px solid var(--bd)',background:'var(--sf2)'}}>
          ${[
            {lbl:'Total Tasks',val:t.length,c:'var(--tx)'}, {lbl:'Completed',val:t.filter(x=>x.stage==='completed').length,c:'var(--gn)'}, {lbl:'In Progress',val:t.filter(x=>x.stage==='in-progress'||x.stage==='development').length,c:'var(--cy)'}, {lbl:'Blocked',val:t.filter(x=>x.stage==='blocked').length,c:'var(--rd)'}, {lbl:'Overdue',val:t.filter(x=>x.due&&new Date(x.due)<now&&x.stage!=='completed').length,c:'var(--am)'}, ].map((s,i)=>html`
            <div key=${i} style=${{flex:1,textAlign:'center',padding:'8px 4px',borderRight:i<4?'1px solid var(--bd)':'none'}}>
              <div style=${{fontSize:16,fontWeight:800,color:s.c,fontFamily:'monospace',lineHeight:1}}>${s.val}</div>
              <div style=${{fontSize:9,color:'var(--tx3)',fontWeight:600,marginTop:2,textTransform:'uppercase',letterSpacing:.4}}>${s.lbl}</div>
            </div>`)}
        </div>`:null}

            <div style=${{flex:1,minHeight:0,overflowY:'auto'}}>

                ${tab==='table'&&!selDev?html`
          <table style=${{width:'100%',borderCollapse:'collapse',fontSize:12}}>
            <thead style=${{position:'sticky',top:0,zIndex:10}}>
              <tr style=${{background:'var(--sf2)',borderBottom:'2px solid var(--bd)'}}>
                ${[['#','36px'],['Developer','180px'],['Role','90px'],['Score','60px'], ['Tasks','60px'],['Done','60px'],['Active','60px'],['Blocked','70px'], ['Overdue','70px'],['Avg %','100px'],['Last 7d','70px'],['Projects','70px'],['','48px']
                ].map(([h,w])=>html`
                  <th key=${h} style=${{padding:'8px 10px',textAlign:'left',fontSize:9,fontWeight:700, color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.5,whiteSpace:'nowrap', minWidth:w,width:w}}>${h}</th>`)}
              </tr>
            </thead>
            <tbody>
              ${filtered.map((dev,i)=>html`
                <tr key=${dev.id} style=${{borderBottom:'1px solid var(--bd)',cursor:'pointer',transition:'background .1s'}}
                  onMouseEnter=${e=>e.currentTarget.style.background='rgba(255,255,255,.04)'}
                  onMouseLeave=${e=>e.currentTarget.style.background=''}
                  onClick=${()=>openDetail(dev.id)}>
                                    <td style=${{padding:'9px 10px',textAlign:'center',fontSize:12}}>
                    ${i===0?'🥇':i===1?'🥈':i===2?'🥉':html`<span style=${{color:'var(--tx3)',fontFamily:'monospace',fontSize:10}}>${i+1}</span>`}
                  </td>
                                    <td style=${{padding:'9px 10px'}}>
                    <div style=${{display:'flex',alignItems:'center',gap:8}}>
                      <${Av} u=${dev} size=${28}/>
                      <div>
                        <div style=${{fontWeight:600,color:'var(--tx)',fontSize:12,lineHeight:1.2,whiteSpace:'nowrap'}}>${dev.name}</div>
                        ${dev.id===cu.id?html`<div style=${{fontSize:9,color:'#1d4ed8',fontWeight:700}}>YOU</div>`:null}
                      </div>
                    </div>
                  </td>
                  <td style=${{padding:'9px 10px',color:'var(--tx2)',fontSize:11,whiteSpace:'nowrap'}}>${dev.role||'—'}</td>
                                    <td style=${{padding:'9px 10px'}}>
                    <div style=${{width:32,height:32,borderRadius:'50%',border:'2.5px solid '+dev.scoreColor, display:'flex',alignItems:'center',justifyContent:'center', background:'rgba(255,255,255,.02)',fontSize:10,fontWeight:800, color:dev.scoreColor,fontFamily:'monospace'}}>${dev.score}</div>
                  </td>
                  <td style=${{padding:'9px 10px',fontFamily:'monospace',fontWeight:600,color:'var(--tx)',textAlign:'center'}}>${dev.total}</td>
                  <td style=${{padding:'9px 10px',fontFamily:'monospace',fontWeight:700,color:'var(--gn)',textAlign:'center'}}>${dev.completed}</td>
                  <td style=${{padding:'9px 10px',fontFamily:'monospace',color:'var(--cy)',textAlign:'center'}}>${dev.inProg}</td>
                  <td style=${{padding:'9px 10px',fontFamily:'monospace',color:dev.blocked>0?'var(--rd)':'var(--tx3)',textAlign:'center'}}>${dev.blocked}</td>
                  <td style=${{padding:'9px 10px',fontFamily:'monospace',fontWeight:dev.overdue>0?700:400, color:dev.overdue>0?'var(--rd)':'var(--tx3)',textAlign:'center'}}>${dev.overdue}</td>
                                    <td style=${{padding:'9px 10px'}}>
                    <div style=${{display:'flex',alignItems:'center',gap:5}}>
                      <div style=${{width:50,height:4,background:'var(--bd)',borderRadius:100,overflow:'hidden',flexShrink:0}}>
                        <div style=${{height:'100%',width:dev.avgPct+'%',borderRadius:100, background:dev.avgPct>70?'var(--gn)':dev.avgPct>40?'var(--am)':'var(--rd)'}}></div>
                      </div>
                      <span style=${{fontSize:10,fontFamily:'monospace',color:'var(--tx2)',flexShrink:0}}>${dev.avgPct}%</span>
                    </div>
                  </td>
                  <td style=${{padding:'9px 10px',fontFamily:'monospace',color:dev.last7>0?'var(--ac)':'var(--tx3)', fontWeight:dev.last7>0?700:400,textAlign:'center'}}>${dev.last7}</td>
                  <td style=${{padding:'9px 10px',color:'var(--tx2)',fontFamily:'monospace',textAlign:'center'}}>${dev.projCount}</td>
                  <td style=${{padding:'9px 10px',textAlign:'center'}}>
                    <button class="btn bg" style=${{fontSize:10,padding:'3px 8px',whiteSpace:'nowrap'}}
                      onClick=${e=>{e.stopPropagation();openDetail(dev.id);}}>View →</button>
                  </td>
                </tr>`)}
              ${filtered.length===0?html`
                <tr><td colspan="13" style=${{textAlign:'center',padding:'48px',color:'var(--tx3)',fontSize:13}}>
                  <div style=${{fontSize:32,marginBottom:8}}>🔍</div>No developers match the filter.
                </td></tr>`:null}
            </tbody>
          </table>`:null}

                ${tab==='chart'&&!selDev?html`
          <div style=${{padding:'16px 20px',display:'flex',flexDirection:'column',gap:14}}>
            <div style=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:12,padding:'16px 20px'}}>
              <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em',marginBottom:14}}>Task Distribution per Developer</h3>
              <${RC.ResponsiveContainer} width="100%" height=${Math.max(200,filtered.length*28)}>
                <${RC.BarChart} data=${chartData} layout="vertical" barSize=${14} margin=${{top:0,right:30,bottom:0,left:60}}>
                  <${RC.CartesianGrid} strokeDasharray="3 3" stroke="var(--bd)" horizontal=${false}/>
                  <${RC.XAxis} type="number" tick=${{fill:'var(--tx3)',fontSize:10}} axisLine=${false} tickLine=${false} allowDecimals=${false}/>
                  <${RC.YAxis} type="category" dataKey="name" tick=${{fill:'var(--tx2)',fontSize:11}} axisLine=${false} tickLine=${false} width=${55}/>
                  <${RC.Tooltip} contentStyle=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:10,color:'var(--tx)',fontSize:11}}/>
                  <${RC.Legend} iconSize=${8} wrapperStyle=${{fontSize:10,color:'var(--tx2)',paddingTop:8}}/>
                  <${RC.Bar} dataKey="Completed" stackId="a" fill="var(--gn)" radius=${[0,0,0,0]}/>
                  <${RC.Bar} dataKey="In Progress" stackId="a" fill="var(--cy)" radius=${[0,0,0,0]}/>
                  <${RC.Bar} dataKey="Blocked" stackId="a" fill="var(--rd)" radius=${[0,4,4,0]}/>
                <//>
              <//>
              <p style=${{fontSize:10,color:'var(--tx3)',marginTop:8,textAlign:'center'}}>All ${filtered.length} developers shown — horizontal bars scale with task count</p>
            </div>
          </div>`:null}

                ${selDev?html`
          <div style=${{padding:'14px 18px',display:'flex',flexDirection:'column',gap:12}}>
                        <div>
              <button onClick=${()=>closeDetail()}
                style=${{display:'inline-flex',alignItems:'center',gap:6,padding:'6px 12px',borderRadius:8,border:'1px solid var(--bd)',background:'var(--sf)',color:'var(--tx2)',fontSize:12,fontWeight:600,cursor:'pointer',transition:'all .12s'}}
                onMouseEnter=${e=>{e.currentTarget.style.borderColor='var(--ac)';e.currentTarget.style.color='var(--ac)';}}
                onMouseLeave=${e=>{e.currentTarget.style.borderColor='var(--bd)';e.currentTarget.style.color='var(--tx2)';}}>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><polyline points="15 18 9 12 15 6"/></svg>
                All Developers
              </button>
            </div>
                        <div style=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:12,padding:'16px 20px', display:'flex',alignItems:'center',gap:14,flexWrap:'wrap'}}>
              <${Av} u=${selDev} size=${52}/>
              <div style=${{flex:1,minWidth:100}}>
                <div style=${{fontSize:17,fontWeight:800,color:'var(--tx)'}}>${selDev.name}</div>
                <div style=${{fontSize:12,color:'var(--tx2)',marginTop:3}}>${selDev.role||'Team Member'}</div>
              </div>
              <div style=${{width:58,height:58,borderRadius:'50%',border:'3px solid '+selDev.scoreColor, display:'flex',alignItems:'center',justifyContent:'center',flexDirection:'column', background:'rgba(255,255,255,.03)',flexShrink:0}}>
                <span style=${{fontSize:20,fontWeight:900,color:selDev.scoreColor,fontFamily:'monospace',lineHeight:1}}>${selDev.score}</span>
                <span style=${{fontSize:8,color:'var(--tx3)',textTransform:'uppercase'}}>score</span>
              </div>
              ${[
                {l:'Total',v:selDev.total,c:'var(--tx)'}, {l:'Done',v:selDev.completed,c:'var(--gn)'}, {l:'Active',v:selDev.inProg,c:'var(--cy)'}, {l:'Blocked',v:selDev.blocked,c:selDev.blocked>0?'var(--rd)':'var(--tx3)'}, {l:'Overdue',v:selDev.overdue,c:selDev.overdue>0?'var(--rd)':'var(--tx3)'}, {l:'Avg%',v:selDev.avgPct+'%',c:selDev.avgPct>70?'var(--gn)':selDev.avgPct>40?'var(--am)':'var(--rd)'}, {l:'Last7d',v:selDev.last7,c:selDev.last7>0?'var(--ac)':'var(--tx3)'}, ].map(s=>html`
                <div key=${s.l} style=${{textAlign:'center',padding:'6px 10px',background:'var(--sf2)',borderRadius:8,border:'1px solid var(--bd)',minWidth:48}}>
                  <div style=${{fontSize:16,fontWeight:800,color:s.c,fontFamily:'monospace',lineHeight:1}}>${s.v}</div>
                  <div style=${{fontSize:8,color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.4,marginTop:2}}>${s.l}</div>
                </div>`)}
            </div>
                        <div style=${{fontSize:10,fontWeight:700,color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.5}}>
              Assigned Tasks <span style=${{color:'var(--ac)'}}>(${selTasks.length})</span>${filterProject!=='all'?' — filtered':''}
            </div>
            ${selTasks.length===0?html`
              <div style=${{textAlign:'center',padding:'32px',color:'var(--tx3)',fontSize:13,background:'var(--sf)',borderRadius:10,border:'1px solid var(--bd)'}}>
                <div style=${{fontSize:28,marginBottom:8}}>📭</div>No tasks assigned${filterProject!=='all'?' in this project':''}.
              </div>`:null}
            <div style=${{display:'flex',flexDirection:'column',gap:6}}>
              ${selTasks.map(tk=>{
                const proj=p.find(pr=>pr.id===tk.project);
                const isOvd=tk.due&&new Date(tk.due)<now&&tk.stage!=='completed';
                return html`
                  <div key=${tk.id} style=${{display:'flex',gap:10,padding:'9px 14px',background:'var(--sf)',borderRadius:9,border:'1px solid var(--bd)',alignItems:'center'}}>
                    <div style=${{width:6,height:6,borderRadius:2,flexShrink:0,background:(STAGES[tk.stage]&&STAGES[tk.stage].color)||'var(--ac)'}}></div>
                    <div style=${{flex:1,minWidth:0}}>
                      <div style=${{fontSize:12,fontWeight:600,color:'var(--tx)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${tk.title}</div>
                      <div style=${{display:'flex',gap:5,marginTop:3,flexWrap:'wrap',alignItems:'center'}}>
                        <${SP} s=${tk.stage}/><${PB} p=${tk.priority}/>
                        ${proj?html`<span style=${{fontSize:10,color:'var(--tx3)',display:'flex',alignItems:'center',gap:3}}>
                          <div style=${{width:5,height:5,borderRadius:1,background:proj.color,flexShrink:0}}></div>${proj.name}</span>`:null}
                        ${isOvd?html`<span style=${{fontSize:10,color:'var(--rd)',fontWeight:700}}>⚠ Overdue</span>`:null}
                      </div>
                    </div>
                    <div style=${{display:'flex',alignItems:'center',gap:5,flexShrink:0}}>
                      <div style=${{width:48,height:4,background:'var(--bd)',borderRadius:100,overflow:'hidden'}}>
                        <div style=${{height:'100%',width:(tk.pct||0)+'%',background:proj?proj.color:'var(--ac)',borderRadius:100}}></div>
                      </div>
                      <span style=${{fontSize:10,fontFamily:'monospace',color:'var(--tx2)',minWidth:26,textAlign:'right'}}>${tk.pct||0}%</span>
                    </div>
                  </div>`;
              })}
            </div>
          </div>`:null}

      </div>    </div>`;
}
function renderMd(text){
  return text.replace(/[*][*](.*?)[*][*]/g,'<b>$1</b>');
}
function MessagesView({projects,users,cu,tasks}){
  const [allProjects,setAllProjects]=useState(safe(projects));
  const [lastMsgTs,setLastMsgTs]=useState({});
  const [stableOrder,setStableOrder]=useState(null); // null = not yet fetched
  const orderSetRef=useRef(false);

  const allProjectsLoadedRef=useRef(false);
  useEffect(()=>{
    if(allProjectsLoadedRef.current)return;
    api.get('/api/projects/all').then(d=>{
      if(Array.isArray(d)&&d.length){
        allProjectsLoadedRef.current=true;
        setAllProjects(d);
        // If stableOrder not yet set, use creation order (alphabetical) as stable base
        if(!orderSetRef.current){
          orderSetRef.current=true;
          // Sort by name initially — overwritten when first message timestamps arrive
          const initial={};
          d.forEach(p=>{initial[p.id]=p.created||'';});
          setStableOrder(initial);
        }
      }
    });
  },[]);

  useEffect(()=>{
    const fetchTs=async()=>{
      const d=await api.get('/api/projects/last-messages');
      if(d&&typeof d==='object'){
        setLastMsgTs(d);
        if(!orderSetRef.current){
          orderSetRef.current=true;
          setStableOrder(d);
        }
        // Compute unread counts — messages newer than last seen ts
        const newUnread={};
        for(const [projId,latestTs] of Object.entries(d)){
          const lastSeen=lastSeenMsgRef.current[projId]||'';
          if(latestTs&&latestTs>lastSeen&&projId!==pidRef.current){
            // Fetch count of new messages
            newUnread[projId]=(newUnread[projId]||0)+1;
          }
        }
        // Only set unread=1 if the channel has a NEW message since last seen
        // Don't increment on every poll — just mark as unread (1) if newer
        setChannelUnread(prev=>{
          const merged={...prev};
          const newProjs=new Set();
          for(const [projId,latestTs] of Object.entries(d)){
            if(projId===pidRef.current)continue;
            const lastSeen=lastSeenMsgRef.current[projId]||'';
            const prevUnread=prev[projId]||0;
            if(latestTs&&latestTs>lastSeen){
              merged[projId]=prevUnread||1;
              newProjs.add(projId); // mark as having new messages
            }
          }
          if(newProjs.size>0)setNewMsgProjects(prev2=>new Set([...prev2,...newProjs]));
          return merged;
        });
      }
    };
    fetchTs();
    const id=setInterval(fetchTs,15000); // reduced 8s→15s
    return()=>clearInterval(id);
  },[]);

  const [pid,setPid]=useState('');
  const pidRef=useRef('');
  useEffect(()=>{pidRef.current=pid;},[pid]);
  const [msgs,setMsgs]=useState([]);const [txt,setTxt]=useState('');const ref=useRef(null);
  const [channelUnread,setChannelUnread]=useState({}); // {projectId: count}
  // Persist lastSeen to localStorage so refresh doesn't reset unread counts
  const _pfLastSeenInit=(()=>{try{return JSON.parse(localStorage.getItem('pfLastSeen')||'{}');}catch{return {};}})();
  const lastSeenMsgRef=useRef(_pfLastSeenInit);
  const saveLastSeen=(obj)=>{try{localStorage.setItem('pfLastSeen',JSON.stringify(obj));}catch{}};
  const [showInfo,setShowInfo]=useState(false);
  const [chanSearch,setChanSearch]=useState('');
  const [newestFirst,setNewestFirst]=useState(false);

  const loadMsgs=useCallback(async(id)=>{
    if(!id)return;
    const d=await api.get('/api/messages?project='+id);
    if(Array.isArray(d)){
      setMsgs(d);
      // Mark channel as read — store the latest message ts
      if(d.length>0){
        const latestTs=d.reduce((mx,m)=>m.ts>mx?m.ts:mx,'');
        lastSeenMsgRef.current[id]=latestTs;
        saveLastSeen(lastSeenMsgRef.current);
      }
      setChannelUnread(prev=>({...prev,[id]:0}));
    }
  },[]);

  useEffect(()=>{loadMsgs(pid);},[pid]);

  useEffect(()=>{
    if(!pid)return;
    const id=setInterval(()=>{
      api.get('/api/messages?project='+pid).then(d=>{
        if(Array.isArray(d)){
          setMsgs(prev=>{
            if(d.length>prev.length){
              playSound('notif');
              if(d.length>0){
                const latest=d.reduce((mx,m)=>m.ts>mx?m.ts:mx,'');
                setLastMsgTs(prev2=>({...prev2,[pid]:latest}));
                lastSeenMsgRef.current[pid]=latest;
                saveLastSeen(lastSeenMsgRef.current);
                setChannelUnread(prev3=>({...prev3,[pid]:0}));
              }
            }
            return d;
          });
        }
      });
    },2000);
    return()=>clearInterval(id);
  },[pid]);

  useEffect(()=>{
    if(ref.current&&!newestFirst) ref.current.scrollTop=ref.current.scrollHeight;
  },[msgs,newestFirst]);

  const sp=allProjects.find(p=>p.id===pid);
  const projTasks=safe(tasks).filter(t=>t.project===pid);
  const projMembers=safe(sp&&sp.members?JSON.parse(sp.members||'[]'):[]).map(id=>safe(users).find(u=>u.id===id)).filter(Boolean);
  const doneTasks=projTasks.filter(t=>t.stage==='completed').length;
  const blockedTasks=projTasks.filter(t=>t.stage==='blocked').length;
  const pc=projTasks.length?Math.round(projTasks.reduce((a,t)=>a+(t.pct||0),0)/projTasks.length):0;

  const send=async()=>{
    if(!txt.trim())return;const c=txt.trim();setTxt('');
    const m=await api.post('/api/messages',{project:pid,content:c});
    setMsgs(prev=>[...prev,m]);
    setLastMsgTs(prev=>({...prev,[pid]:m.ts||new Date().toISOString()}));
  };

  // Fixed order ref — set once, never changes (no re-sorting unless new msg arrives)
  const fixedOrderRef=useRef(null);
  const [newMsgProjects,setNewMsgProjects]=useState(new Set()); // projects with new msgs since load

  // Build fixed order ONCE — on first render that has both projects + timestamps
  // After that, never rebuild (prevents glitching)
  const orderBuiltRef=useRef(false);
  useEffect(()=>{
    if(orderBuiltRef.current)return; // never rebuild
    if(!allProjects.length)return;   // wait for projects
    orderBuiltRef.current=true;
    const ts=lastMsgTs||{};
    const sorted=[...allProjects].sort((a,b)=>{
      const at=ts[a.id]||a.created||'';
      const bt=ts[b.id]||b.created||'';
      return bt.localeCompare(at);
    });
    fixedOrderRef.current=sorted.map(p=>p.id);
  },[allProjects.length,Object.keys(lastMsgTs).length]); // only rebuild if item count changes

  const sortedProjects=useMemo(()=>{
    let rows=[...allProjects];
    if(chanSearch.trim()){
      const q=chanSearch.toLowerCase();
      rows=rows.filter(p=>p.name.toLowerCase().includes(q));
      return rows.sort((a,b)=>a.name.localeCompare(b.name));
    }
    const order=fixedOrderRef.current;
    if(order&&order.length){
      // Channels with new messages since load bubble to top
      const newSet=newMsgProjects;
      rows.sort((a,b)=>{
        const an=newSet.has(a.id)?0:1, bn=newSet.has(b.id)?0:1;
        if(an!==bn)return an-bn;
        const ai=order.indexOf(a.id),bi=order.indexOf(b.id);
        return (ai===-1?999:ai)-(bi===-1?999:bi);
      });
    }
    return rows;
  },[allProjects,chanSearch,newMsgProjects]);

  return html`<div class="fi" style=${{display:'flex',height:'100%',overflow:'hidden'}}>

        <div style=${{width:220,borderRight:'1px solid var(--bd)',display:'flex',flexDirection:'column',flexShrink:0}}>
            <div style=${{padding:'10px 10px 8px',borderBottom:'1px solid var(--bd)',flexShrink:0}}>
        <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:7}}>
          <span style=${{fontSize:10,fontWeight:700,color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.7}}>Channels</span>
          <span style=${{fontSize:10,color:'var(--tx3)'}}>${sortedProjects.length} of ${allProjects.length}</span>
        </div>
                <div style=${{position:'relative'}}>
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
            style=${{position:'absolute',left:7,top:'50%',transform:'translateY(-50%)',color:'var(--tx3)',pointerEvents:'none'}}>
            <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
          </svg>
          <input class="inp" placeholder="Search channels..." value=${chanSearch}
            style=${{height:26,fontSize:11,paddingLeft:24,width:'100%'}}
            onInput=${e=>setChanSearch(e.target.value)}/>
        </div>
      </div>
            <div style=${{flex:1,overflowY:'auto',padding:'4px 6px'}}>
        ${sortedProjects.length===0?html`
          <div style=${{textAlign:'center',padding:'24px 8px',color:'var(--tx3)',fontSize:11}}>No channels match "${chanSearch}"</div>`:null}
        ${sortedProjects.map(p=>{
          const pt=safe(tasks).filter(t=>t.project===p.id);
          const activeCnt=pt.filter(t=>t.stage!=='completed'&&t.stage!=='backlog').length;
          const lastMsg=lastMsgTs[p.id];
          const hasRecentMsg=lastMsg&&(Date.now()-new Date(lastMsg).getTime())<3600000; // msg in last 1h
          const fmtLastMsg=ts=>{
            if(!ts)return '';
            const d=new Date(ts);const now=new Date();
            const diff=now-d;
            if(diff<60000)return 'just now';
            if(diff<3600000)return Math.floor(diff/60000)+'m ago';
            if(diff<86400000)return d.toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit'});
            return d.toLocaleDateString('en-US',{month:'short',day:'numeric'});
          };
          return html`
            <button key=${p.id} class=${'nb'+(pid===p.id?' act':'')}
              style=${{marginBottom:2,fontSize:12,alignItems:'center',height:'auto',padding:'7px 10px',width:'100%',display:'flex'}}
              onClick=${()=>setPid(p.id)}>
              <div style=${{display:'flex',alignItems:'center',gap:7,width:'100%'}}>
                <div style=${{width:7,height:7,borderRadius:2,background:p.color,flexShrink:0}}></div>
                <span style=${{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',flex:1,textAlign:'left'}}># ${p.name}</span>
                ${channelUnread[p.id]>0?html`<span style=${{fontSize:9,fontWeight:800,background:'var(--ac)',color:'#fff',borderRadius:8,padding:'1px 6px',flexShrink:0,minWidth:16,textAlign:'center'}}>${channelUnread[p.id]>9?'9+':channelUnread[p.id]}</span>`:null}
              </div>
            </button>`;
        })}
      </div>
    </div>

        <div style=${{flex:1,display:'flex',flexDirection:'column',overflow:'hidden'}}>
            <div style=${{padding:'9px 14px',borderBottom:'1px solid var(--bd)',display:'flex',alignItems:'center',gap:9,flexShrink:0}}>
        ${sp?html`
          <div style=${{width:9,height:9,borderRadius:2,background:sp.color}}></div>
          <span style=${{fontSize:14,fontWeight:700,color:'var(--tx)'}}># ${sp.name}</span>
          <span style=${{fontSize:11,color:'var(--tx3)',marginLeft:4}}>${projTasks.length} tasks · ${pc}% done</span>
                    <button class=${'btn bg'+(newestFirst?' act':'')} style=${{fontSize:10,padding:'3px 9px',marginLeft:6}}
            onClick=${()=>setNewestFirst(v=>!v)}
            title=${newestFirst?'Showing newest first — click to show oldest first':'Showing oldest first — click to show newest first'}>
            ${newestFirst?'↓ Newest first':'↑ Oldest first'}
          </button>
          <button class=${'btn bg'+(showInfo?' act':'')} style=${{marginLeft:'auto',fontSize:11,padding:'4px 10px'}} onClick=${()=>setShowInfo(p=>!p)}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
            Info
          </button>
        `:html`<span style=${{color:'var(--tx3)'}}>Select a channel</span>`}
      </div>

            ${showInfo&&sp?html`
        <div style=${{padding:'12px 16px',background:'var(--sf2)',borderBottom:'1px solid var(--bd)',flexShrink:0}}>
          <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr 1fr 1fr',gap:10,marginBottom:12}}>
            ${[
              {label:'Total Tasks',val:projTasks.length,color:'var(--tx)'}, {label:'Completed',val:doneTasks,color:'var(--gn)'}, {label:'In Progress',val:projTasks.filter(t=>t.stage==='development'||t.stage==='testing'||t.stage==='uat').length,color:'var(--cy)'}, {label:'Blocked',val:blockedTasks,color:'var(--rd)'}, ].map(s=>html`
              <div key=${s.label} style=${{background:'var(--sf)',borderRadius:9,padding:'10px 12px',border:'1px solid var(--bd)'}}>
                <div style=${{fontSize:20,fontWeight:800,color:s.color,lineHeight:1}}>${s.val}</div>
                <div style=${{fontSize:10,color:'var(--tx3)',marginTop:3}}>${s.label}</div>
              </div>`)}
          </div>
          <div style=${{marginBottom:10}}>
            <div style=${{display:'flex',justifyContent:'space-between',marginBottom:4}}>
              <span class="tx3-11">Overall Progress</span>
              <span style=${{fontSize:11,color:'var(--tx2)',fontFamily:'monospace',fontWeight:700}}>${pc}%</span>
            </div>
            <div style=${{height:6,background:'var(--bd)',borderRadius:100,overflow:'hidden'}}>
              <div style=${{height:'100%',width:pc+'%',background:sp.color,borderRadius:100,transition:'width .5s'}}></div>
            </div>
          </div>
          <div style=${{display:'flex',gap:6,flexWrap:'wrap',marginBottom:10}}>
            ${Object.entries(STAGES).map(([k,v])=>{
              const cnt=projTasks.filter(t=>t.stage===k).length;
              if(!cnt)return null;
              return html`<span key=${k} style=${{fontSize:10,padding:'2px 8px',borderRadius:5,background:v.color+'22',color:v.color,fontWeight:600}}>${v.label}: ${cnt}</span>`;
            })}
          </div>
          <div style=${{display:'flex',alignItems:'center',gap:6}}>
            <span class="tx3-11">Members:</span>
            <div style=${{display:'flex',gap:-4}}>
              ${projMembers.slice(0,8).map((m,i)=>html`<div key=${m.id} title=${m.name} style=${{marginLeft:i>0?-6:0,border:'2px solid var(--sf2)',borderRadius:'50%'}}><${Av} u=${m} size=${22}/></div>`)}
              ${projMembers.length>8?html`<span style=${{fontSize:10,color:'var(--tx3)',marginLeft:6}}>+${projMembers.length-8} more</span>`:null}
            </div>
          </div>
        </div>`:null}

            <div ref=${ref} style=${{flex:1,overflowY:'auto',padding:'13px 15px',display:'flex',flexDirection:'column',gap:0}}>
        ${(()=>{
          const fmtDate=iso=>{const d=new Date(iso);return String(d.getDate()).padStart(2,'0')+'/'+String(d.getMonth()+1).padStart(2,'0')+'/'+d.getFullYear();};
          const fmtTime=iso=>{const d=new Date(iso);return String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');};
          const dateLabel=iso=>{
            const today=new Date();today.setHours(0,0,0,0);
            const yesterday=new Date(today);yesterday.setDate(today.getDate()-1);
            const d=new Date(iso);d.setHours(0,0,0,0);
            if(d.getTime()===today.getTime()) return 'Today · '+fmtDate(iso);
            if(d.getTime()===yesterday.getTime()) return 'Yesterday · '+fmtDate(iso);
            return fmtDate(iso);
          };
          const sorted=[...msgs].sort((a,b)=>newestFirst
            ? new Date(b.ts)-new Date(a.ts)
            : new Date(a.ts)-new Date(b.ts));
          const groups=[];let lastDate='';
          sorted.forEach(m=>{
            const d=new Date(m.ts);
            const key=d.getFullYear()+'-'+(d.getMonth()+1)+'-'+d.getDate();
            if(key!==lastDate){groups.push({type:'separator',label:dateLabel(m.ts),key:'sep-'+key});lastDate=key;}
            groups.push({type:'msg',msg:m});
          });
          return groups.map((item,idx)=>{
            if(item.type==='separator') return html`
              <div key=${item.key} style=${{display:'flex',alignItems:'center',gap:10,margin:'14px 0 10px'}}>
                <div style=${{flex:1,height:1,background:'var(--bd)'}}></div>
                <span style=${{fontSize:10,fontWeight:700,color:'var(--tx2)',background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:100,padding:'3px 12px',letterSpacing:.4,whiteSpace:'nowrap'}}>📅 ${item.label}</span>
                <div style=${{flex:1,height:1,background:'var(--bd)'}}></div>
              </div>`;
            const m=item.msg;
            const isSystem=m.is_system===1||m.sender==='system';
            const timeStr=fmtTime(m.ts);
            if(isSystem) return html`
              <div key=${m.id} style=${{display:'flex',justifyContent:'center',padding:'3px 0',marginBottom:6}}>
                <div style=${{display:'flex',flexDirection:'column',alignItems:'center',gap:3,maxWidth:'90%'}}>
                  <div style=${{fontSize:12,color:'var(--tx)',fontWeight:500,background:'var(--sf2)',border:'1px solid var(--bd)',borderRadius:20,padding:'5px 16px',textAlign:'center',lineHeight:1.5}}
                    dangerouslySetInnerHTML=${{__html:renderMd(m.content)}}></div>
                  <span style=${{fontSize:10,color:'var(--tx3)',fontFamily:'monospace',letterSpacing:.2}}>${timeStr}</span>
                </div>
              </div>`;
            const s=safe(users).find(u=>u.id===m.sender);
            const isMe=m.sender===cu.id;
            return html`
              <div key=${m.id} style=${{display:'flex',gap:8,alignItems:'flex-end',flexDirection:isMe?'row-reverse':'row',marginBottom:6}}>
                ${!isMe?html`<${Av} u=${s} size=${25}/>`:null}
                <div style=${{display:'flex',flexDirection:'column',gap:3,alignItems:isMe?'flex-end':'flex-start',maxWidth:'65%'}}>
                  ${!isMe?html`<span style=${{fontSize:11,color:'var(--tx3)',fontWeight:600,marginLeft:2}}>${(s&&s.name)||'?'}</span>`:null}
                  <div style=${{padding:'9px 13px',borderRadius:12,fontSize:13,lineHeight:1.5, background:isMe?'var(--ac)':'var(--sf2)',color:isMe?'var(--ac-tx)':'var(--tx)', border:isMe?'none':'1px solid var(--bd)', borderBottomRightRadius:isMe?3:12,borderBottomLeftRadius:isMe?12:3}}>${m.content}</div>
                  <span class="mono-10">${timeStr}</span>
                </div>
              </div>`;
          });
        })()}
        ${msgs.length===0?html`<div style=${{textAlign:'center',paddingTop:48,color:'var(--tx3)',fontSize:13}}>
          <div style=${{fontSize:28,marginBottom:8}}>💬</div>
          <p>No messages yet. Task activity will appear here automatically.</p>
        </div>`:null}
      </div>

            <div style=${{padding:'10px 14px',borderTop:'1px solid var(--bd)',display:'flex',gap:8,flexShrink:0}}>
        <input class="inp" style=${{flex:1}} placeholder=${'Message in #'+((sp&&sp.name)||'...')} value=${txt}
          onInput=${e=>setTxt(e.target.value)} onKeyDown=${e=>e.key==='Enter'&&!e.shiftKey&&send()}/>
        <button class="btn bp" style=${{padding:'8px 14px',fontSize:12}} onClick=${send}>➤</button>
      </div>
    </div>
  </div>`;
}

/* ─── DirectMessages ──────────────────────────────────────────────────────── */
const playSound=(type='notif')=>{
  try{
    const ctx=new(window.AudioContext||window.webkitAudioContext)();
    if(type==='reminder'){
      [[660,0],[880,0.15],[1100,0.3]].forEach(([freq,delay])=>{
        const o=ctx.createOscillator();const g=ctx.createGain();
        o.connect(g);g.connect(ctx.destination);o.type='sine';
        o.frequency.setValueAtTime(freq,ctx.currentTime+delay);
        g.gain.setValueAtTime(0.08,ctx.currentTime+delay);
        g.gain.exponentialRampToValueAtTime(0.001,ctx.currentTime+delay+0.4);
        o.start(ctx.currentTime+delay);o.stop(ctx.currentTime+delay+0.5);
      });
    } else {
      [[523,0],[659,0.15]].forEach(([freq,delay])=>{
        const o=ctx.createOscillator();const g=ctx.createGain();
        o.connect(g);g.connect(ctx.destination);o.type='sine';
        o.frequency.setValueAtTime(freq,ctx.currentTime+delay);
        g.gain.setValueAtTime(0.06,ctx.currentTime+delay);
        g.gain.exponentialRampToValueAtTime(0.001,ctx.currentTime+delay+0.35);
        o.start(ctx.currentTime+delay);o.stop(ctx.currentTime+delay+0.5);
      });
    }
  }catch(e){}
};
function DirectMessages({cu,users,dmUnread,onDmRead,dmEnabled=true,initialUserId=null,onClearInitial,onlineUsers=new Set()}){
  const isAdminOrManager=cu&&(cu.role==='Admin'||cu.role==='Manager');
  if(!dmEnabled&&!isAdminOrManager) return html`
    <div style=${{flex:1,display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',height:'100%',gap:12,color:'var(--tx3)'}}>
      <div style=${{fontSize:40}}>💬</div>
      <div style=${{fontSize:14,fontWeight:700,color:'var(--tx2)'}}>Direct Messages Disabled</div>
      <div style=${{fontSize:13,color:'var(--tx3)',textAlign:'center',maxWidth:280,lineHeight:1.6}}>Your workspace admin has disabled direct messages. Contact your admin to enable them.</div>
    </div>`;
  const others=safe(users).filter(u=>u.id!==cu.id);
  const [toId,setToId]=useState(others[0]&&others[0].id||'');const [msgs,setMsgs]=useState([]);const [txt,setTxt]=useState('');const [search,setSearch]=useState('');const ref=useRef(null);
  useEffect(()=>{
    if(initialUserId){
      const u=safe(users).find(u=>u.id===initialUserId);
      if(u){setToId(initialUserId);if(onClearInitial)onClearInitial();}
    }
  },[initialUserId]);
  const prevMsgCount=useRef(0);
  const loadMsgs=useCallback(async(id)=>{if(!id)return;const d=await api.get('/api/dm/'+id);if(Array.isArray(d)){setMsgs(d);onDmRead(id);};},[onDmRead]);
  useEffect(()=>{
    if(!toId)return;
    loadMsgs(toId);
    const id=setInterval(async()=>{
      const d=await api.get('/api/dm/'+toId);
      if(Array.isArray(d)){
        setMsgs(prev=>{
          if(d.length>prev.length){playSound('notif');}
          return d;
        });
        onDmRead(toId);
      }
    },3000);
    return()=>clearInterval(id);
  },[toId]);
  useEffect(()=>{if(ref.current)ref.current.scrollTop=ref.current.scrollHeight;},[msgs]);
  const send=async()=>{if(!txt.trim()||!toId)return;const c=txt.trim();setTxt('');const m=await api.post('/api/dm',{recipient:toId,content:c});setMsgs(prev=>[...prev,m]);};
  const filtered=others.filter(u=>u.name.toLowerCase().includes(search.toLowerCase()));
  const toUser=safe(users).find(u=>u.id===toId);
  const unreadFor=id=>(dmUnread.find(x=>x.sender===id)||{cnt:0}).cnt;
  return html`<div class="fi" style=${{display:'flex',height:'100%',overflow:'hidden'}}>
    <div style=${{width:220,borderRight:'1px solid var(--bd)',display:'flex',flexDirection:'column',flexShrink:0}}>
      <div style=${{padding:'11px 12px',borderBottom:'1px solid var(--bd)'}}><div style=${{fontSize:11,fontWeight:700,color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.7,marginBottom:8}}>Direct Messages</div><input class="inp" style=${{fontSize:12,padding:'6px 10px'}} placeholder="Search..." value=${search} onInput=${e=>setSearch(e.target.value)}/></div>
      <div style=${{flex:1,overflowY:'auto',padding:6}}>
        ${filtered.map(u=>{const unr=unreadFor(u.id);const isA=toId===u.id;return html`
          <button key=${u.id} onClick=${()=>setToId(u.id)} style=${{display:'flex',alignItems:'center',gap:9,width:'100%',padding:'8px 10px',border:'none',borderRadius:9,cursor:'pointer',marginBottom:2,background:isA?'rgba(99,102,241,.14)':'transparent',transition:'all .14s'}}>
            <div style=${{position:'relative',flexShrink:0}}>
              <${Av} u=${u} size=${32}/>
              <div style=${{position:'absolute',bottom:0,right:0,width:10,height:10,borderRadius:'50%',background:onlineUsers.has(u.id)?'#22c55e':'#475569',border:'2px solid var(--bg)',boxShadow:onlineUsers.has(u.id)?'0 0 0 1px #22c55e,0 0 6px rgba(34,197,94,.5)':'none',transition:'background .3s,box-shadow .3s'}}></div>
            </div>
            <div style=${{flex:1,minWidth:0,textAlign:'left'}}>
              <div style=${{fontSize:13,fontWeight:600,color:'var(--tx)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${u.name}</div>
            </div>
            ${unr>0?html`<span style=${{background:'var(--ac)',color:'#fff',borderRadius:10,fontSize:10,padding:'2px 6px',fontFamily:'monospace',fontWeight:700}}>${unr}</span>`:null}
          </button>`;})}
      </div>
    </div>
    <div style=${{flex:1,display:'flex',flexDirection:'column',overflow:'hidden'}}>
      <div style=${{padding:'11px 16px',borderBottom:'1px solid var(--bd)',display:'flex',alignItems:'center',gap:11,flexShrink:0}}>
        ${toUser?html`
          <div style=${{position:'relative'}}>
            <${Av} u=${toUser} size=${36}/>
            <div style=${{position:'absolute',bottom:0,right:0,width:11,height:11,borderRadius:'50%',background:onlineUsers.has(toUser.id)?'#22c55e':'#475569',border:'2px solid var(--bg)',boxShadow:onlineUsers.has(toUser.id)?'0 0 0 1px #22c55e,0 0 7px rgba(34,197,94,.6)':'none',transition:'background .3s,box-shadow .3s'}}></div>
          </div>
          <div>
            <div style=${{fontSize:14,fontWeight:700,color:'var(--tx)'}}>${toUser.name}</div>
            <div style=${{fontSize:11,color:onlineUsers.has(toUser.id)?'#22c55e':'var(--tx3)',fontWeight:500}}>${onlineUsers.has(toUser.id)?'Active now':'Offline'}</div>
          </div>`:html`<span style=${{color:'var(--tx3)'}}>Select someone to chat</span>`}
      </div>
      <div ref=${ref} style=${{flex:1,overflowY:'auto',padding:'16px',display:'flex',flexDirection:'column',gap:12}}>
        ${msgs.length===0?html`<div style=${{textAlign:'center',paddingTop:60,color:'var(--tx3)',fontSize:13}}><div style=${{fontSize:36,marginBottom:10}}>👋</div><div style=${{fontWeight:600,marginBottom:4,color:'var(--tx2)'}}>${toUser?'Start a conversation with '+toUser.name:'Select someone'}</div></div>`:null}
        ${msgs.map((m,i)=>{const isMe=m.sender===cu.id;const showT=i===msgs.length-1||msgs[i+1].sender!==m.sender;return html`
          <div key=${m.id} style=${{display:'flex',gap:8,alignItems:'flex-end',flexDirection:isMe?'row-reverse':'row'}}>
            <div style=${{width:28,flexShrink:0}}>${!isMe&&(i===0||msgs[i-1].sender!==m.sender)?html`<${Av} u=${toUser} size=${28}/>`:null}</div>
            <div style=${{display:'flex',flexDirection:'column',gap:2,alignItems:isMe?'flex-end':'flex-start',maxWidth:'68%'}}>
              <div style=${{padding:'9px 13px',borderRadius:14,fontSize:13,lineHeight:1.55,wordBreak:'break-word',background:isMe?'var(--ac)':'var(--sf2)',color:isMe?'var(--ac-tx)':'var(--tx)',border:isMe?'none':'1px solid var(--bd)',borderBottomRightRadius:isMe?3:14,borderBottomLeftRadius:isMe?14:3}}>${m.content}</div>
              ${showT?html`<span style=${{fontSize:10,color:'var(--tx3)',fontFamily:'monospace',margin:'0 2px'}}>${ago(m.ts)}</span>`:null}
            </div>
          </div>`;})}
      </div>
      <div style=${{padding:'11px 16px',borderTop:'1px solid var(--bd)',display:'flex',gap:8,flexShrink:0}}>
        <textarea class="inp" style=${{flex:1,minHeight:40,maxHeight:100,resize:'none',padding:'9px 13px',lineHeight:1.5}} placeholder=${'Message '+((toUser&&toUser.name)||'...')} value=${txt} onInput=${e=>setTxt(e.target.value)} onKeyDown=${e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send();}}}></textarea>
        <button class="btn bp" style=${{padding:'9px 15px',flexShrink:0}} onClick=${send} disabled=${!txt.trim()||!toId}>➤</button>
      </div>
    </div>
  </div>`;
}

/* ─── NotifsView ──────────────────────────────────────────────────────────── */
function NotifsView({notifs,reload,onNavigate}){
  const NT={
    task_assigned:{icon:html`<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>`,c:'var(--ac)',nav:'tasks',label:'View Tasks'}, status_change:{icon:html`<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><polyline points="13 17 18 12 13 7"/><polyline points="6 17 11 12 6 7"/></svg>`,c:'var(--cy)',nav:'tasks',label:'View Tasks'}, comment:{icon:html`<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`,c:'var(--pu)',nav:'tasks',label:'View Tasks'}, deadline:{icon:html`<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,c:'var(--am)',nav:'tasks',label:'View Tasks'}, dm:{icon:html`<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><circle cx="9" cy="10" r="1" fill="currentColor"/><circle cx="12" cy="10" r="1" fill="currentColor"/><circle cx="15" cy="10" r="1" fill="currentColor"/></svg>`,c:'#06b6d4',nav:'dm',label:'Open Messages'}, project_added:{icon:html`<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><line x1="12" y1="10" x2="12" y2="16"/><line x1="9" y1="13" x2="15" y2="13"/></svg>`,c:'#10b981',nav:'projects',label:'View Projects'}, reminder:{icon:html`<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,c:'#f59e0b',nav:'tasks',label:'View Tasks'}, call:{icon:html`<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07A19.5 19.5 0 0 1 4.69 12 19.79 19.79 0 0 1 1.61 3.28a2 2 0 0 1 1.99-2.18h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L7.91 8.96a16 16 0 0 0 6.29 6.29l1.24-.82a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></svg>`,c:'#22c55e',nav:'dashboard',label:'Join Instant Meet'}, };
  const unread=safe(notifs).filter(n=>!n.read).length;
  const handleClick=async(n)=>{
    if(!n.read) await api.put('/api/notifications/'+n.id+'/read',{});
    const T=NT[n.type]||NT.comment;
    if(T.nav&&onNavigate){onNavigate(T.nav);}
    reload();
  };
  const clearAll=async()=>{
    await api.put('/api/notifications/read-all',{});
    reload();
  };
  return html`<div class="fi" style=${{height:'100%',overflowY:'auto',padding:'18px 22px',boxSizing:'border-box'}}>
    <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:16}}>
      <span style=${{fontSize:13,color:'var(--tx2)'}}>${unread>0?html`<b style=${{color:'var(--ac)'}}>${unread}</b> unread`:'All caught up!'}</span>
      <div style=${{display:'flex',gap:8}}>
        ${unread>0?html`<button class="btn bg" style=${{fontSize:12}} onClick=${clearAll}>✓ Mark all read</button>`:null}
        ${notifs.length>0?html`<button class="btn brd" style=${{fontSize:12,color:'var(--rd)'}}
          onClick=${()=>{if(window.confirm('Clear all notifications?'))api.del('/api/notifications/all').then(reload);}}>🗑 Clear all</button>`:null}
      </div>
    </div>
    ${notifs.length===0?html`<div style=${{textAlign:'center',padding:'48px 0',color:'var(--tx3)',fontSize:13}}>
      <div style=${{fontSize:32,marginBottom:10}}>🔔</div><p>No notifications yet.</p></div>`:null}
    <div style=${{display:'flex',flexDirection:'column',gap:8,maxWidth:780}}>
      ${safe(notifs).map(n=>{const T=NT[n.type]||NT.comment;return html`
        <div key=${n.id} onClick=${()=>handleClick(n)}
          style=${{display:'flex',gap:12,padding:'12px 15px',background:n.read?'var(--sf)':'rgba(99,102,241,.07)',border:'1px solid '+(n.read?'var(--bd)':'rgba(99,102,241,.22)'),borderRadius:12,cursor:'pointer',alignItems:'center',transition:'all .15s'}}>
          <div style=${{width:36,height:36,borderRadius:10,background:T.c+'22',display:'flex',alignItems:'center',justifyContent:'center',flexShrink:0}}>${T.icon}</div>
          <div style=${{flex:1}}>
            <p style=${{fontSize:13,color:'var(--tx)',fontWeight:n.read?400:600,marginBottom:3}}>${n.content}</p>
            <div style=${{display:'flex',gap:10,alignItems:'center'}}>
              <span class="mono-10">${ago(n.ts)}</span>
              ${T.nav?html`<span style=${{fontSize:10,color:T.c,fontWeight:600}}>→ ${T.label}</span>`:null}
            </div>
          </div>
          ${!n.read?html`<div style=${{width:8,height:8,borderRadius:'50%',background:'var(--ac)',flexShrink:0}}></div>`:null}
        </div>
        `;})}

    </div>
  </div>`;
}

/* ─── TeamView ────────────────────────────────────────────────────────────── */
/* ─── MemberRow (used inside TeamView members table) ───────────────────── */
function MemberRow({u,cu,i,total,reload,ROLE_COLORS}){
  const [showPw,setShowPw]=useState(false);
  const [editPw,setEditPw]=useState(false);
  const [newPw,setNewPw]=useState('');
  const [saving,setSaving]=useState(false);
  const [showTotpSetup,setShowTotpSetup]=useState(false);
  const [totpData,setTotpData]=useState(null);
  const [totpVerifyToken,setTotpVerifyToken]=useState('');
  const [totpVerifying,setTotpVerifying]=useState(false);
  const [totpMsg,setTotpMsg]=useState('');
  const [twoFaLoading,setTwoFaLoading]=useState(false);

  const resetPw=async()=>{
    if(!newPw.trim())return;
    setSaving(true);
    await api.put('/api/users/'+u.id,{password:newPw.trim()});
    setSaving(false);setEditPw(false);setNewPw('');
    reload&&reload();
  };

  const startTotpSetup=async()=>{
    setTotpMsg('');
    // Only self can setup own TOTP; admin can reset others but not setup for them
    if(u.id!==cu.id){alert('Users must set up their own Authenticator. Use Reset to clear it.');return;}
    const r=await api.post('/api/auth/totp/setup',{});
    if(r.error){setTotpMsg(r.error);return;}
    setTotpData(r);setShowTotpSetup(true);setTotpVerifyToken('');
  };

  const confirmTotpSetup=async()=>{
    if(totpVerifyToken.replace(/\s/g,'').length!==6){setTotpMsg('Enter the 6-digit code from your app.');return;}
    setTotpVerifying(true);setTotpMsg('');
    const r=await api.post('/api/auth/totp/verify-setup',{token:totpVerifyToken.replace(/\s/g,'')});
    setTotpVerifying(false);
    if(r.error){setTotpMsg(r.error);return;}
    setTotpMsg('✓ Google Authenticator configured!');
    setShowTotpSetup(false);setTotpData(null);
    setTimeout(()=>{setTotpMsg('');reload&&reload();},1500);
  };

  const resetTotp=async()=>{
    if(!window.confirm('Reset 2FA for '+u.name+'? They will need to set up Authenticator again.'))return;
    setTwoFaLoading(true);
    const r=await api.post('/api/auth/totp/reset',{user_id:u.id});
    setTwoFaLoading(false);
    if(r.error){alert(r.error);return;}
    setTotpMsg('✓ 2FA reset');
    setTimeout(()=>{setTotpMsg('');reload&&reload();},1200);
  };

  const toggleEmailOtp=async()=>{
    setTwoFaLoading(true);
    const r=await api.post('/api/auth/toggle-2fa',{user_id:u.id,enabled:!u.two_fa_enabled});
    setTwoFaLoading(false);
    if(r.error){alert(r.error);}
    else reload&&reload();
  };

  const totpConfigured=u.totp_configured||(!!(u.totp_verified));
  const isSelf=u.id===cu.id;
  const isAdminOrSelf=cu.role==='Admin'||cu.role==='Manager'||isSelf;

  return html`
    <tr style=${{borderBottom:i<total-1?'1px solid var(--bd)':'none',verticalAlign:'top'}}>
      <td style=${{padding:'12px 14px'}}>
        <div style=${{display:'flex',alignItems:'center',gap:10}}>
          <${Av} u=${u} size=${34}/>
          <div>
            <div style=${{fontSize:13,fontWeight:600,color:'var(--tx)',display:'flex',alignItems:'center',gap:6}}>
              ${u.name}
              ${isSelf?html`<span style=${{fontSize:9,color:'var(--ac)',background:'var(--ac3)',padding:'2px 6px',borderRadius:4,fontFamily:'monospace'}}>YOU</span>`:null}
            </div>
            <div style=${{fontSize:10,color:ROLE_COLORS[u.role]||'var(--tx3)',marginTop:2}}>${u.role}</div>
          </div>
        </div>
      </td>

      <td style=${{padding:'12px 14px'}}>
        <span style=${{fontSize:12,color:'var(--tx2)',fontFamily:'monospace'}}>${u.email}</span>
      </td>

      <!-- Password reset column -->
      <td style=${{padding:'12px 14px',minWidth:160}}>
        ${editPw?html`
          <div style=${{display:'flex',gap:5,alignItems:'center'}}>
            <input class="inp" type="text" placeholder="New password" value=${newPw}
              style=${{height:28,fontSize:12,flex:1,minWidth:0}}
              onInput=${e=>setNewPw(e.target.value)}
              onKeyDown=${e=>{if(e.key==='Enter')resetPw();if(e.key==='Escape'){setEditPw(false);setNewPw('');}}}/>
            <button class="btn bp" style=${{padding:'4px 9px',fontSize:11,flexShrink:0}} onClick=${resetPw} disabled=${saving||!newPw.trim()}>
              ${saving?'…':'Save'}
            </button>
            <button class="btn bg" style=${{padding:'4px 8px',fontSize:11,flexShrink:0}} onClick=${()=>{setEditPw(false);setNewPw('');}}>✕</button>
          </div>`:html`
          <div style=${{display:'flex',alignItems:'center',gap:6,flexWrap:'wrap'}}>
            
          </div>

          <!-- Action buttons -->
          <div style=${{display:'flex',gap:5,flexWrap:'wrap'}}>
            ${isSelf&&!totpConfigured?html`
              <button class="btn bg" style=${{padding:'3px 9px',fontSize:10,color:'#4ade80',borderColor:'rgba(74,222,128,0.3)'}}
                onClick=${startTotpSetup}>
                📱 Setup Authenticator
              </button>`:null}
            ${totpConfigured&&isAdminOrSelf?html`
              <button class="btn brd" style=${{padding:'3px 9px',fontSize:10}}
                onClick=${resetTotp} disabled=${twoFaLoading}>
                ${twoFaLoading?'…':'↺ Reset 2FA'}
              </button>`:null}
            ${!totpConfigured&&(cu.role==='Admin'||cu.role==='Manager')?html`
              <button class=${'btn bg'} style=${{padding:'3px 9px',fontSize:10,color:u.two_fa_enabled?'var(--rd)':'var(--cy)',borderColor:u.two_fa_enabled?'rgba(255,68,68,0.3)':'rgba(34,211,238,0.3)'}}
                onClick=${toggleEmailOtp} disabled=${twoFaLoading}>
                ${twoFaLoading?'…':u.two_fa_enabled?'Disable Email 2FA':'Enable Email 2FA'}
              </button>`:null}
          </div>

          ${totpMsg?html`<div style=${{fontSize:10,color:totpMsg.startsWith('✓')?'var(--gn)':'var(--rd)',fontWeight:600}}>${totpMsg}</div>`:null}
        </div>

        <!-- TOTP Setup modal inline -->
        ${showTotpSetup&&totpData?html`
          <div class="ov" onClick=${e=>e.target===e.currentTarget&&setShowTotpSetup(false)}>
            <div class="mo fi" style=${{maxWidth:480}}>
              <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:20}}>
                <div>
                  <h2 style=${{fontSize:17,fontWeight:800,color:'var(--tx)',margin:0,display:'flex',alignItems:'center',gap:8}}>
                    <span style=${{width:36,height:36,borderRadius:10,background:'linear-gradient(135deg,#1d4ed8,#7c3aed)',display:'inline-flex',alignItems:'center',justifyContent:'center',fontSize:18}}>📱</span>
                    Setup Google Authenticator
                  </h2>
                  <p style=${{fontSize:11,color:'var(--tx3)',marginTop:4}}>Scan the QR code with your authenticator app</p>
                </div>
                <button class="btn bg" style=${{padding:'6px 10px'}} onClick=${()=>setShowTotpSetup(false)}>✕</button>
              </div>

              <div style=${{display:'grid',gridTemplateColumns:'auto 1fr',gap:20,marginBottom:20,alignItems:'start'}}>
                <div style=${{textAlign:'center'}}>
                  <div style=${{background:'white',padding:10,borderRadius:10,border:'1px solid var(--bd)',display:'inline-block',boxShadow:'0 4px 16px rgba(0,0,0,.1)'}}>
                    <${QRCodeDisplay} otpauth=${totpData.otpauth} size=${160}/>
                  </div>
                  <div style=${{fontSize:9,color:'var(--tx3)',marginTop:6}}>Scan with Google Authenticator</div>
                </div>
                <div>
                  <div style=${{marginBottom:12}}>
                    <div style=${{fontSize:11,fontWeight:700,color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.7,marginBottom:6}}>Manual Entry</div>
                    <div style=${{fontFamily:'monospace',fontSize:12,background:'var(--sf2)',padding:'10px 12px',borderRadius:9,border:'1px solid var(--bd)',letterSpacing:3,wordBreak:'break-all',color:'var(--tx)',userSelect:'all'}}>
                      ${totpData.secret}
                    </div>
                    <div style=${{fontSize:10,color:'var(--tx3)',marginTop:4}}>Copy this key if you can't scan the QR code</div>
                  </div>
                  <div style=${{padding:'10px 12px',background:'rgba(29,78,216,0.06)',borderRadius:9,border:'1px solid rgba(29,78,216,0.15)',fontSize:11,color:'var(--tx2)',lineHeight:1.6}}>
                    <b>Steps:</b><br/>
                    1. Open <b>Google Authenticator</b> or <b>Authy</b><br/>
                    2. Tap <b>+</b> → Scan QR code (or enter key manually)<br/>
                    3. Enter the 6-digit code below to confirm
                  </div>
                </div>
              </div>

              <div style=${{marginBottom:14}}>
                <label class="lbl">Enter Code from App to Confirm</label>
                <input class="inp" type="text" inputMode="numeric" pattern="[0-9]*"
                  value=${totpVerifyToken}
                  onInput=${e=>setTotpVerifyToken(e.target.value.replace(/\D/g,'').slice(0,6))}
                  onKeyDown=${e=>e.key==='Enter'&&confirmTotpSetup()}
                  placeholder="000000"
                  style=${{textAlign:'center',fontSize:22,fontWeight:700,fontFamily:'monospace',letterSpacing:6}}/>
              </div>
              ${totpMsg?html`<div style=${{padding:'8px 12px',background:totpMsg.startsWith('✓')?'rgba(74,222,128,0.1)':'rgba(239,68,68,0.08)',border:'1px solid '+(totpMsg.startsWith('✓')?'rgba(74,222,128,0.3)':'rgba(239,68,68,0.2)'),borderRadius:8,fontSize:12,color:totpMsg.startsWith('✓')?'#4ade80':'var(--rd)',marginBottom:12}}>${totpMsg}</div>`:null}
              <div style=${{display:'flex',gap:9,justifyContent:'flex-end'}}>
                <button class="btn bg" onClick=${()=>setShowTotpSetup(false)}>Cancel</button>
                <button class="btn bp" onClick=${confirmTotpSetup} disabled=${totpVerifying||totpVerifyToken.replace(/\s/g,'').length!==6}>
                  ${totpVerifying?html`<span class="spin"></span>`:null} Confirm & Enable
                </button>
              </div>
            </div>
          </div>`:null}
          `}
      </td>

      <td style=${{padding:'12px 14px'}}>
        <select class="sel" style=${{width:130,padding:'6px 28px 6px 10px'}} value=${u.role}
          onChange=${e=>api.put('/api/users/'+u.id,{role:e.target.value}).then(()=>reload&&reload())}
          disabled=${u.id===cu.id&&cu.role==='Admin'}>
          ${ROLES.map(r=>html`<option key=${r}>${r}</option>`)}
        </select>
      </td>
      <td style=${{padding:'12px 14px'}}>
        ${u.id!==cu.id?html`<button class="btn brd" style=${{padding:'5px 11px',fontSize:12}}
          onClick=${()=>window.confirm('Remove '+u.name+'?')&&api.del('/api/users/'+u.id).then(()=>reload&&reload())}>🗑</button>`:null}
      </td>
    </tr>`;
}

function TeamView({users,cu,reload}){
  const [tab,setTab]=useState('teams');
  const [showNew,setShowNew]=useState(false);const [name,setName]=useState('');const [email,setEmail]=useState('');const [pw,setPw]=useState('');const [role,setRole]=useState('Developer');const [err,setErr]=useState('');
  const [teams,setTeams]=useState([]);const [showNewTeam,setShowNewTeam]=useState(false);
  const [editTeam,setEditTeam]=useState(null);
  const [tName,setTName]=useState('');const [tLead,setTLead]=useState('');const [tMembers,setTMembers]=useState([]);
  const [savingTeam,setSavingTeam]=useState(false);
  const [memberSearch,setMemberSearch]=useState('');
  const [teamSearch,setTeamSearch]=useState('');

  const loadTeams=useCallback(async()=>{const d=await api.get('/api/teams');setTeams(Array.isArray(d)?d:[]);},[]);
  useEffect(()=>{loadTeams();},[loadTeams]);

  const add=async()=>{if(!name||!email||!pw){setErr('All fields required.');return;}setErr('');const r=await api.post('/api/users',{name,email,password:pw,role});if(r.error)setErr(r.error);else{await reload();setShowNew(false);setName('');setEmail('');setPw('');}};

  const openNewTeam=()=>{setEditTeam(null);setTName('');setTLead('');setTMembers([]);setShowNewTeam(true);};
  const openEditTeam=t=>{setEditTeam(t);setTName(t.name);setTLead(t.lead_id||'');setTMembers(JSON.parse(t.member_ids||'[]'));setShowNewTeam(true);};
  const saveTeam=async()=>{
    if(!tName.trim())return;
    setSavingTeam(true);
    const payload={name:tName,lead_id:tLead,member_ids:tMembers};
    if(editTeam)await api.put('/api/teams/'+editTeam.id,payload);
    else await api.post('/api/teams',payload);
    setSavingTeam(false);setShowNewTeam(false);setEditTeam(null);
    loadTeams();
  };
  const delTeam=async id=>{if(!window.confirm('Delete this team?'))return;await api.del('/api/teams/'+id);loadTeams();};
  const toggleMember=id=>{setTMembers(prev=>prev.includes(id)?prev.filter(x=>x!==id):[...prev,id]);};

  const umap=safe(users).reduce((a,u)=>{a[u.id]=u;return a;},{});
  const filteredMembers=useMemo(()=>safe(users).filter(u=>!memberSearch||u.name.toLowerCase().includes(memberSearch.toLowerCase())||u.email.toLowerCase().includes(memberSearch.toLowerCase())),[users,memberSearch]);
  const filteredTeams=useMemo(()=>teams.filter(t=>!teamSearch||t.name.toLowerCase().includes(teamSearch.toLowerCase())),[teams,teamSearch]);
  const ROLE_COLORS={Admin:'var(--ac)',Manager:'var(--gn)',TeamLead:'var(--cy)',Developer:'var(--pu)',Tester:'var(--am)',Viewer:'var(--tx3)'};

  return html`<div class="fi" style=${{height:'100%',overflowY:'auto',padding:'18px 22px',boxSizing:'border-box'}}>
        <div style=${{display:'flex',gap:4,marginBottom:18,background:'var(--sf2)',borderRadius:12,padding:4,width:'fit-content',border:'1px solid var(--bd)'}}>
      ${['members','teams'].map(t=>html`
        <button key=${t} class="btn" onClick=${()=>setTab(t)}
          style=${{padding:'6px 18px',borderRadius:9,fontSize:12,fontWeight:600,border:'none',cursor:'pointer', background:tab===t?'var(--ac)':'transparent',color:tab===t?'var(--ac-tx)':'var(--tx2)',transition:'all .14s'}}>
          ${t==='members'?'👥 Members':'🏷 Teams'}
        </button>`)}
    </div>

    ${tab==='members'?html`
      <div style=${{display:'flex',alignItems:'center',gap:10,marginBottom:16}}>
        <div style=${{position:'relative',flex:1,maxWidth:300}}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
            style=${{position:'absolute',left:10,top:'50%',transform:'translateY(-50%)',color:'var(--tx3)',pointerEvents:'none'}}>
            <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
          </svg>
          <input class="inp" placeholder="Search members by name or email…" value=${memberSearch}
            style=${{paddingLeft:30,height:34,fontSize:12}} onInput=${e=>setMemberSearch(e.target.value)}/>
        </div>
        <span style=${{fontSize:12,color:'var(--tx3)',flexShrink:0}}>${filteredMembers.length} of ${safe(users).length}</span>
        <button class="btn bp" style=${{flexShrink:0}} onClick=${()=>setShowNew(true)}>+ Add Member</button>
      </div>
      <div class="card" style=${{padding:0,overflow:'auto'}}>
        <table style=${{width:'100%',borderCollapse:'collapse'}}>
          <thead><tr style=${{borderBottom:'1px solid var(--bd)',background:'var(--sf2)'}}>
            ${['Member','Email','Password','2FA / Authenticator','Role',''].map((h,i)=>html`<th key=${i} style=${{padding:'9px 15px',textAlign:'left',fontSize:10,fontFamily:'monospace',color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.5}}>${h}</th>`)}
          </tr></thead>
          <tbody>
            ${filteredMembers.length===0?html`<tr><td colspan="5" style=${{padding:'20px',textAlign:'center',color:'var(--tx3)',fontSize:12}}>No members match your search.</td></tr>`:null}
            ${filteredMembers.map((u,i)=>html`<${MemberRow} key=${u.id} u=${u} cu=${cu} i=${i} total=${filteredMembers.length} reload=${reload} ROLE_COLORS=${ROLE_COLORS}/>`)}
          </tbody>
        </table>
      </div>`:null}

    ${tab==='teams'?html`
      <div style=${{display:'flex',alignItems:'center',gap:10,marginBottom:16}}>
        <div style=${{position:'relative',flex:1,maxWidth:300}}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
            style=${{position:'absolute',left:10,top:'50%',transform:'translateY(-50%)',color:'var(--tx3)',pointerEvents:'none'}}>
            <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
          </svg>
          <input class="inp" placeholder="Search teams…" value=${teamSearch}
            style=${{paddingLeft:30,height:34,fontSize:12}} onInput=${e=>setTeamSearch(e.target.value)}/>
        </div>
        <span style=${{fontSize:12,color:'var(--tx3)',flexShrink:0}}>${filteredTeams.length} of ${teams.length}</span>
        <button class="btn bp" style=${{flexShrink:0}} onClick=${openNewTeam}>+ New Team</button>
      </div>
      ${teams.length===0&&teamSearch===''?html`
        <div style=${{textAlign:'center',padding:'40px 16px',color:'var(--tx3)',fontSize:13,background:'var(--sf)',borderRadius:12,border:'1px dashed var(--bd)'}}>
          <div style=${{fontSize:32,marginBottom:10}}>🏷</div>
          <div style=${{fontWeight:600,marginBottom:4}}>No teams yet</div>
          <div>Create sub-teams to group members and manage multi-team workflows</div>
        </div>`:null}
      <div style=${{display:'flex',flexDirection:'column',gap:10}}>
        ${filteredTeams.length===0&&teams.length>0?html`
          <div style=${{textAlign:'center',padding:'20px',color:'var(--tx3)',fontSize:13,background:'var(--sf)',borderRadius:10,border:'1px solid var(--bd)'}}>No teams match your search.</div>`:null}
        ${filteredTeams.map(t=>{
          const members=JSON.parse(t.member_ids||'[]').map(id=>umap[id]).filter(Boolean);
          const lead=t.lead_id?umap[t.lead_id]:null;
          return html`
          <div key=${t.id} class="card" style=${{display:'flex',gap:14,alignItems:'flex-start'}}>
            <div style=${{width:44,height:44,borderRadius:12,background:'var(--ac3)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:20,flexShrink:0}}>🏷</div>
            <div style=${{flex:1,minWidth:0}}>
              <div style=${{display:'flex',alignItems:'center',gap:8,marginBottom:6}}>
                <span style=${{fontSize:14,fontWeight:700,color:'var(--tx)'}}>${t.name}</span>
                <span class="tx3-11">${members.length} member${members.length!==1?'s':''}</span>
              </div>
              ${lead?html`<div style=${{display:'flex',alignItems:'center',gap:6,marginBottom:8}}>
                <span class="tx3-11">Lead:</span>
                <${Av} u=${lead} size=${20}/>
                <span style=${{fontSize:12,fontWeight:600,color:'var(--cy)'}}>${lead.name}</span>
              </div>`:null}
              <div style=${{display:'flex',gap:6,flexWrap:'wrap'}}>
                ${members.map(m=>html`
                  <div key=${m.id} style=${{display:'flex',alignItems:'center',gap:5,padding:'4px 10px',background:'var(--sf2)',borderRadius:20,border:'1px solid var(--bd)'}}>
                    <${Av} u=${m} size=${18}/>
                    <div>
                      <div style=${{fontSize:11,color:'var(--tx2)',fontWeight:500}}>${m.name}</div>
                      
                    </div>
                  </div>`)}
              </div>
            </div>
            <div style=${{display:'flex',gap:6,flexShrink:0}}>
              <button class="btn bg" style=${{padding:'6px 10px',fontSize:12}} onClick=${()=>openEditTeam(t)}>✏️ Edit</button>
              <button class="btn brd" style=${{padding:'6px 10px',fontSize:12,color:'var(--rd)'}} onClick=${()=>delTeam(t.id)}>🗑</button>
            </div>
          </div>`;
        })}
      </div>`:null}

        ${showNew?html`<div class="ov" onClick=${e=>e.target===e.currentTarget&&setShowNew(false)}>
      <div class="mo fi" style=${{maxWidth:400}}>
        <div style=${{display:'flex',justifyContent:'space-between',marginBottom:18}}><h2 style=${{fontSize:17,fontWeight:700,color:'var(--tx)'}}>👤 Add Member</h2><button class="btn bg" style=${{padding:'7px 10px'}} onClick=${()=>setShowNew(false)}>✕</button></div>
        <div style=${{display:'flex',flexDirection:'column',gap:11}}>
          <input class="inp" placeholder="Full Name" value=${name} onInput=${e=>setName(e.target.value)}/>
          <input class="inp" type="email" placeholder="Email" value=${email} onInput=${e=>setEmail(e.target.value)}/>
          <input class="inp" type="password" placeholder="Password" value=${pw} onInput=${e=>setPw(e.target.value)}/>
          <select class="sel" value=${role} onChange=${e=>setRole(e.target.value)}>${ROLES.map(r=>html`<option key=${r}>${r}</option>`)}</select>
          ${err?html`<div style=${{color:'var(--rd)',fontSize:12,padding:'7px 11px',background:'rgba(248,113,113,.07)',borderRadius:7}}>${err}</div>`:null}
          <div style=${{display:'flex',gap:9,justifyContent:'flex-end'}}>
            <button class="btn bg" onClick=${()=>setShowNew(false)}>Cancel</button>
            <button class="btn bp" onClick=${add}>Add Member</button>
          </div>
        </div>
      </div>
    </div>`:null}

        ${showNewTeam?html`<div class="ov" onClick=${e=>e.target===e.currentTarget&&setShowNewTeam(false)}>
      <div class="mo fi" style=${{maxWidth:480}}>
        <div style=${{display:'flex',justifyContent:'space-between',marginBottom:18}}>
          <h2 style=${{fontSize:16,fontWeight:700,color:'var(--tx)'}}>${editTeam?'✏️ Edit Team':'🏷 New Sub-Team'}</h2>
          <button class="btn bg" style=${{padding:'7px 10px'}} onClick=${()=>setShowNewTeam(false)}>✕</button>
        </div>
        <div style=${{display:'flex',flexDirection:'column',gap:13}}>
          <div>
            <label class="lbl">Team Name *</label>
            <input class="inp" value=${tName} onInput=${e=>setTName(e.target.value)} placeholder="e.g. Frontend, Backend, QA, Design…"/>
          </div>
          <div>
            <label class="lbl">Team Lead</label>
            <select class="inp" value=${tLead} onChange=${e=>setTLead(e.target.value)}>
              <option value="">— No lead —</option>
              ${safe(users).map(u=>html`<option key=${u.id} value=${u.id}>${u.name} (${u.role})</option>`)}
            </select>
          </div>
          <div>
            <label class="lbl">Members</label>
            <div style=${{display:'flex',flexDirection:'column',gap:6,maxHeight:200,overflowY:'auto',border:'1px solid var(--bd)',borderRadius:9,padding:'8px 12px',background:'var(--sf2)'}}>
              ${safe(users).map(u=>html`
                <label key=${u.id} style=${{display:'flex',alignItems:'center',gap:10,cursor:'pointer',padding:'5px 0'}}>
                  <input type="checkbox" checked=${tMembers.includes(u.id)} onChange=${()=>toggleMember(u.id)}
                    style=${{width:16,height:16,accentColor:'var(--ac)',cursor:'pointer'}}/>
                  <${Av} u=${u} size=${24}/>
                  <div>
                    <div style=${{fontSize:12,fontWeight:600,color:'var(--tx)'}}>${u.name}</div>
                    <div style=${{fontSize:10,color:ROLE_COLORS[u.role]||'var(--tx3)'}}>${u.role}</div>
                  </div>
                </label>`)}
            </div>
            <div style=${{fontSize:11,color:'var(--tx3)',marginTop:4}}>${tMembers.length} member${tMembers.length!==1?'s':''} selected</div>
          </div>
          <div style=${{display:'flex',gap:9,justifyContent:'flex-end',paddingTop:4}}>
            <button class="btn bg" onClick=${()=>setShowNewTeam(false)}>Cancel</button>
            <button class="btn bp" onClick=${saveTeam} disabled=${savingTeam||!tName.trim()}>
              ${savingTeam?'Saving...':editTeam?'Save Changes':'Create Team'}
            </button>
          </div>
        </div>
      </div>
    </div>`:null}
  </div>`;
}

/* ─── TicketsView ────────────────────────────────────────────────────────── */
function TicketsView({cu,users,projects,onReload,activeTeam,initialAssignee,initialStatus}){
  const [tickets,setTickets]=useState([]);
  const [busy,setBusy]=useState(true);
  const [filterStatus,setFilterStatus]=useState(initialStatus||'');
  const [filterPriority,setFilterPriority]=useState('');
  const [filterType,setFilterType]=useState('');
  const [filterAssignee,setFilterAssignee]=useState(()=>initialAssignee==='me'&&cu?cu.id:'');
  const [showNew,setShowNew]=useState(false);
  const [editTicket,setEditTicket]=useState(null);
  const [detailTicket,setDetailTicket]=useState(null);
  const [comments,setComments]=useState([]);
  const [newComment,setNewComment]=useState('');
  const [savingComment,setSavingComment]=useState(false);
  const [showResolved,setShowResolved]=useState(false);

  const canEdit=cu&&cu.role!=='Developer'&&cu.role!=='Viewer';
  const canDelete=cu&&['Admin','Manager','TeamLead'].includes(cu.role);

  const [nTitle,setNTitle]=useState('');
  const [nDesc,setNDesc]=useState('');
  const [nType,setNType]=useState('bug');
  const [nPriority,setNPriority]=useState('medium');
  const [nAssignee,setNAssignee]=useState(()=>cu&&(cu.role==='Developer'||cu.role==='Tester')?cu.id:'');
  const [nProject,setNProject]=useState('');
  const [nStatus,setNStatus]=useState('open');
  const [saving,setSaving]=useState(false);

  const load=useCallback(async()=>{
    setBusy(true);
    const url=activeTeam?'/api/tickets?team_id='+activeTeam.id:'/api/tickets';
    const d=await api.get(url);
    setTickets(Array.isArray(d)?d:[]);
    setBusy(false);
  },[activeTeam]);
  useEffect(()=>{load();},[load]);

  const visibleTickets=useMemo(()=>{
    return tickets.filter(t=>{
      const isResolved=t.status==='resolved'||t.status==='closed';
      if(isResolved&&!showResolved&&filterStatus!=='resolved'&&filterStatus!=='closed')return false;
      if(filterStatus&&t.status!==filterStatus)return false;
      if(filterPriority&&t.priority!==filterPriority)return false;
      if(filterType&&t.type!==filterType)return false;
      if(filterAssignee&&t.assignee!==filterAssignee)return false;
      return true;
    });
  },[tickets,showResolved,filterStatus,filterPriority,filterType,filterAssignee]);

  const saveTicket=async()=>{
    if(!nTitle.trim())return;
    setSaving(true);
    const payload={title:nTitle,description:nDesc,type:nType,priority:nPriority,assignee:nAssignee,project:nProject,status:nStatus,team_id:activeTeam?activeTeam.id:''};
    if(editTicket){await api.put('/api/tickets/'+editTicket.id,payload);}
    else{await api.post('/api/tickets',payload);}
    setSaving(false);setShowNew(false);setEditTicket(null);
    setNTitle('');setNDesc('');setNType('bug');setNPriority('medium');setNAssignee('');setNProject('');setNStatus('open');
    load();
  };

  const openEdit=(t)=>{
    setEditTicket(t);setNTitle(t.title);setNDesc(t.description||'');setNType(t.type||'bug');
    setNPriority(t.priority||'medium');setNAssignee(t.assignee||'');setNProject(t.project||'');setNStatus(t.status||'open');
    setShowNew(true);
  };

  const openDetail=async(t)=>{
    setDetailTicket(t);
    const c=await api.get('/api/tickets/'+t.id+'/comments');
    setComments(Array.isArray(c)?c:[]);
  };

  const postComment=async()=>{
    if(!newComment.trim()||!detailTicket)return;
    setSavingComment(true);
    await api.post('/api/tickets/'+detailTicket.id+'/comments',{content:newComment});
    setNewComment('');
    const c=await api.get('/api/tickets/'+detailTicket.id+'/comments');
    setComments(Array.isArray(c)?c:[]);
    setSavingComment(false);
  };

  const quickStatus=async(t,status)=>{
    await api.put('/api/tickets/'+t.id,{status});
    load();
    if(detailTicket&&detailTicket.id===t.id)setDetailTicket(prev=>({...prev,status}));
  };

  const del=async(id)=>{
    if(!window.confirm('Delete this ticket?'))return;
    await api.del('/api/tickets/'+id);
    setDetailTicket(null);load();
  };

  const TYPE_CFG={
    bug:{icon:'🐛',color:'var(--rd)',bg:'rgba(248,113,113,.12)',label:'Bug'}, feature:{icon:'✨',color:'var(--ac)',bg:'rgba(90,140,255,.10)',label:'Feature'}, improvement:{icon:'🔧',color:'var(--cy)',bg:'rgba(34,211,238,.12)',label:'Improvement'}, task:{icon:'✅',color:'var(--gn)',bg:'rgba(74,222,128,.12)',label:'Task'}, question:{icon:'❓',color:'var(--pu)',bg:'rgba(167,139,250,.12)',label:'Question'}, };
  const PRIORITY_CFG={
    critical:{icon:'🔴',color:'#ef4444',label:'Critical'}, high:{icon:'🟠',color:'#f97316',label:'High'}, medium:{icon:'🟡',color:'#eab308',label:'Medium'}, low:{icon:'🟢',color:'#22c55e',label:'Low'}, };
  const STATUS_CFG={
    open:{icon:'🔵',color:'var(--cy)',label:'Open'}, 'in-progress':{icon:'🟡',color:'var(--am)',label:'In Progress'}, review:{icon:'🟣',color:'var(--pu)',label:'In Review'}, resolved:{icon:'🟢',color:'var(--gn)',label:'Resolved'}, closed:{icon:'⚫',color:'var(--tx3)',label:'Closed'}, };

  const statCounts=Object.keys(STATUS_CFG).reduce((a,s)=>{a[s]=tickets.filter(t=>t.status===s).length;return a;},{});
  const myTicketsCount=tickets.filter(t=>t.assignee===cu.id&&t.status!=='closed'&&t.status!=='resolved').length;

  const umap=safe(users).reduce((a,u)=>{a[u.id]=u;return a;},{});

  const FORM=html`
    <div class="ov" onClick=${e=>e.target===e.currentTarget&&(setShowNew(false),setEditTicket(null))}>
      <div class="mo fi" style=${{maxWidth:560}}>
        <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:18}}>
          <h2 style=${{fontSize:16,fontWeight:700,color:'var(--tx)'}}>${editTicket?'✏️ Edit Ticket':'🎫 New Ticket'}</h2>
          <button class="btn bg" style=${{padding:'7px 10px'}} onClick=${()=>{setShowNew(false);setEditTicket(null);}}>✕</button>
        </div>
        <div style=${{display:'flex',flexDirection:'column',gap:13}}>
          <div>
            <label class="lbl">Title *</label>
            <input class="inp" value=${nTitle} onInput=${e=>setNTitle(e.target.value)} placeholder="Brief description of the issue"/>
          </div>
          <div>
            <label class="lbl">Description</label>
            <textarea class="inp" rows="3" style=${{resize:'vertical'}} value=${nDesc} onInput=${e=>setNDesc(e.target.value)} placeholder="Steps to reproduce, expected vs actual behaviour..."></textarea>
          </div>
          <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:10}}>
            <div>
              <label class="lbl">Type</label>
              <select class="inp" value=${nType} onChange=${e=>setNType(e.target.value)}>
                ${Object.entries(TYPE_CFG).map(([v,c])=>html`<option key=${v} value=${v}>${c.icon} ${c.label}</option>`)}
              </select>
            </div>
            <div>
              <label class="lbl">Priority</label>
              <select class="inp" value=${nPriority} onChange=${e=>setNPriority(e.target.value)}>
                ${Object.entries(PRIORITY_CFG).map(([v,c])=>html`<option key=${v} value=${v}>${c.icon} ${c.label}</option>`)}
              </select>
            </div>
            <div>
              <label class="lbl">Status</label>
              <select class="inp" value=${nStatus} onChange=${e=>setNStatus(e.target.value)}>
                ${Object.entries(STATUS_CFG).map(([v,c])=>html`<option key=${v} value=${v}>${c.icon} ${c.label}</option>`)}
              </select>
            </div>
          </div>
          <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr',gap:10}}>
            <div>
              <label class="lbl">Assignee</label>
              <select class="inp" value=${nAssignee} onChange=${e=>setNAssignee(e.target.value)}>
                <option value="">— Unassigned —</option>
                ${safe(users).map(u=>html`<option key=${u.id} value=${u.id}>${u.name}</option>`)}
              </select>
            </div>
            <div>
              <label class="lbl">Project</label>
              <select class="inp" value=${nProject} onChange=${e=>setNProject(e.target.value)}>
                <option value="">— No project —</option>
                ${safe(projects).map(p=>html`<option key=${p.id} value=${p.id}>${p.name}</option>`)}
              </select>
            </div>
          </div>
          <div style=${{display:'flex',gap:9,justifyContent:'flex-end',paddingTop:4}}>
            <button class="btn bg" onClick=${()=>{setShowNew(false);setEditTicket(null);}}>Cancel</button>
            <button class="btn bp" onClick=${saveTicket} disabled=${saving||!nTitle.trim()}>
              ${saving?'Saving...':editTicket?'Save Changes':'Create Ticket'}
            </button>
          </div>
        </div>
      </div>
    </div>`;

  const DETAIL=detailTicket?html`
    <div class="ov" onClick=${e=>e.target===e.currentTarget&&setDetailTicket(null)}>
      <div class="mo fi" style=${{maxWidth:620,maxHeight:'85vh',display:'flex',flexDirection:'column'}}>
        <div style=${{display:'flex',justifyContent:'space-between',alignItems:'flex-start',marginBottom:16,flexShrink:0}}>
          <div style=${{flex:1,minWidth:0,marginRight:12}}>
            <div style=${{display:'flex',alignItems:'center',gap:8,marginBottom:6}}>
              <span style=${{fontSize:18}}>${(TYPE_CFG[detailTicket.type]||TYPE_CFG.bug).icon}</span>
              <span style=${{fontSize:11,padding:'2px 8px',borderRadius:6,background:(PRIORITY_CFG[detailTicket.priority]||PRIORITY_CFG.medium).color+'22',color:(PRIORITY_CFG[detailTicket.priority]||PRIORITY_CFG.medium).color,fontWeight:700}}>${(PRIORITY_CFG[detailTicket.priority]||PRIORITY_CFG.medium).label}</span>
              <select value=${detailTicket.status} onChange=${e=>quickStatus(detailTicket,e.target.value)}
                style=${{fontSize:11,padding:'2px 8px',borderRadius:6,background:'var(--sf2)',border:'1px solid var(--bd)',color:'var(--tx)',cursor:'pointer'}}>
                ${Object.entries(STATUS_CFG).map(([v,c])=>html`<option key=${v} value=${v}>${c.icon} ${c.label}</option>`)}
              </select>
            </div>
            <div style=${{display:'flex',alignItems:'center',gap:8,marginBottom:6}}>
              <span class="id-badge id-ticket">${detailTicket.id}</span>
              ${detailTicket.type?html`<span class="id-badge" style=${{background:({'bug':'rgba(185,28,28,0.10)','feature':'rgba(29,78,216,0.10)','improvement':'rgba(14,116,144,0.10)','task':'rgba(21,128,61,0.10)','question':'rgba(109,40,217,0.10)'})[detailTicket.type]||'var(--ac3)',color:({'bug':'var(--rd)','feature':'var(--ac)','improvement':'var(--cy)','task':'var(--gn)','question':'var(--pu)'})[detailTicket.type]||'var(--ac)'}}>${detailTicket.type}</span>`:null}
            </div>
            <h2 style=${{fontSize:16,fontWeight:700,color:'var(--tx)',marginBottom:4}}>${detailTicket.title}</h2>
            <div class="tx3-11">
              Reported by ${(umap[detailTicket.reporter]||{name:'Unknown'}).name} · ${new Date(detailTicket.created).toLocaleDateString()}
              ${detailTicket.assignee?html` · Assigned to <b style=${{color:'var(--tx2)'}}>${(umap[detailTicket.assignee]||{name:'?'}).name}</b>`:null}
            </div>
          </div>
          <div style=${{display:'flex',gap:6,flexShrink:0}}>
            ${canEdit?html`<button class="btn bg" style=${{fontSize:11,padding:'5px 9px'}} onClick=${()=>openEdit(detailTicket)}>✏️ Edit</button>`:null}
            ${canDelete?html`<button class="btn brd" style=${{fontSize:11,padding:'5px 9px',color:'var(--rd)'}} onClick=${()=>del(detailTicket.id)}>🗑</button>`:null}
            <button class="btn bg" style=${{padding:'7px 10px'}} onClick=${()=>setDetailTicket(null)}>✕</button>
          </div>
        </div>
        ${detailTicket.description?html`
          <div style=${{background:'var(--sf2)',borderRadius:9,padding:'12px 14px',marginBottom:14,fontSize:13,color:'var(--tx2)',lineHeight:1.6,flexShrink:0,border:'1px solid var(--bd)'}}>
            ${detailTicket.description}
          </div>`:null}
        <div style=${{flex:1,overflowY:'auto',paddingBottom:8}}>
          <div style=${{fontWeight:700,fontSize:12,color:'var(--tx2)',marginBottom:10}}>💬 Comments (${comments.length})</div>
          ${comments.length===0?html`<p style=${{color:'var(--tx3)',fontSize:12,textAlign:'center',padding:'16px 0'}}>No comments yet. Be the first!</p>`:null}
          <div style=${{display:'flex',flexDirection:'column',gap:8}}>
            ${comments.map(c=>html`
              <div key=${c.id} style=${{display:'flex',gap:10,padding:'10px 12px',background:'var(--sf2)',borderRadius:10,border:'1px solid var(--bd)'}}>
                <${Av} u=${umap[c.user_id]||{name:'?',color:'#888'}} size=${30}/>
                <div style=${{flex:1}}>
                  <div style=${{display:'flex',gap:8,alignItems:'center',marginBottom:4}}>
                    <span style=${{fontSize:12,fontWeight:700,color:'var(--tx)'}}>${(umap[c.user_id]||{name:'?'}).name}</span>
                    <span style=${{fontSize:10,color:'var(--tx3)'}}>${new Date(c.created).toLocaleString('en-US',{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}</span>
                  </div>
                  <div style=${{fontSize:12,color:'var(--tx2)',lineHeight:1.5}}>${c.content}</div>
                </div>
              </div>`)}
          </div>
        </div>
        <div style=${{display:'flex',gap:9,paddingTop:12,borderTop:'1px solid var(--bd)',flexShrink:0}}>
          <input class="inp" style=${{flex:1}} value=${newComment} onInput=${e=>setNewComment(e.target.value)}
            onKeyDown=${e=>e.key==='Enter'&&!e.shiftKey&&postComment()}
            placeholder="Add a comment… (Enter to submit)"/>
          <button class="btn bp" onClick=${postComment} disabled=${savingComment||!newComment.trim()}>
            ${savingComment?html`<span class="spin"></span>`:'Send'}
          </button>
        </div>
      </div>
    </div>`:null;

  return html`
    <div class="fi" style=${{height:'100%',overflowY:'auto',padding:'18px 22px',background:'var(--bg)'}}>
            <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:16}}>
        <div style=${{display:'flex',gap:8,flexWrap:'wrap'}}>
          ${Object.entries(STATUS_CFG).map(([s,c])=>html`
            <button key=${s} class=${'chip'+(filterStatus===s?' on':'')} onClick=${()=>setFilterStatus(filterStatus===s?'':s)}
              style=${{fontSize:11,display:'flex',alignItems:'center',gap:4}}>
              ${c.icon} ${c.label} <span style=${{fontWeight:700,color:c.color}}>${statCounts[s]||0}</span>
            </button>`)}
        </div>
        <button class="btn bp" style=${{fontSize:12}} onClick=${()=>{setEditTicket(null);setNTitle('');setNDesc('');setNType('bug');setNPriority('medium');setNAssignee('');setNProject('');setNStatus('open');setShowNew(true);}}>
          + New Ticket
        </button>
      </div>

            <div style=${{display:'flex',gap:8,marginBottom:14,flexWrap:'wrap',alignItems:'center'}}>
        ${filterStatus?html`
          <div style=${{display:'flex',alignItems:'center',gap:6,padding:'4px 10px 4px 8px',background:'var(--sf2)',border:'1px solid var(--bd)',borderRadius:20,flexShrink:0}}>
            <span style=${{fontSize:11,color:'var(--tx2)',fontWeight:600}}>${(STATUS_CFG[filterStatus]||{label:filterStatus}).icon} ${(STATUS_CFG[filterStatus]||{label:filterStatus}).label}</span>
            <button onClick=${()=>setFilterStatus('')}
              style=${{background:'none',border:'none',cursor:'pointer',color:'var(--tx3)',fontSize:13,lineHeight:1,padding:'0 2px'}}>×</button>
          </div>`:null}
        ${filterAssignee?html`
          <div style=${{display:'flex',alignItems:'center',gap:6,padding:'4px 10px 4px 8px',background:'var(--ac3)',border:'1px solid var(--ac)',borderRadius:20,flexShrink:0}}>
            <div style=${{width:6,height:6,borderRadius:'50%',background:'var(--ac)',flexShrink:0}}></div>
            <span style=${{fontSize:11,fontWeight:700,color:'var(--ac)'}}>Assigned to me</span>
            <button onClick=${()=>setFilterAssignee('')}
              style=${{background:'none',border:'none',cursor:'pointer',color:'var(--ac)',fontSize:13,lineHeight:1,padding:'0 2px',marginLeft:2}}
              title="Clear filter">×</button>
          </div>`:null}
        <button class=${'chip'+(filterAssignee===cu.id?' on':'')} style=${{fontSize:11,flexShrink:0}}
          onClick=${()=>setFilterAssignee(filterAssignee===cu.id?'':cu.id)}>
          👤 My Tickets ${myTicketsCount>0?html`<span style=${{fontWeight:700,marginLeft:3}}>(${myTicketsCount})</span>`:null}
        </button>
        <select class="sel" style=${{fontSize:11,padding:'5px 10px',height:30}} value=${filterPriority} onChange=${e=>setFilterPriority(e.target.value)}>
          <option value="">All Priorities</option>
          ${Object.entries(PRIORITY_CFG).map(([v,c])=>html`<option key=${v} value=${v}>${c.icon} ${c.label}</option>`)}
        </select>
        <select class="sel" style=${{fontSize:11,padding:'5px 10px',height:30}} value=${filterType} onChange=${e=>setFilterType(e.target.value)}>
          <option value="">All Types</option>
          ${Object.entries(TYPE_CFG).map(([v,c])=>html`<option key=${v} value=${v}>${c.icon} ${c.label}</option>`)}
        </select>
        <span style=${{fontSize:11,color:'var(--tx3)',alignSelf:'center',marginLeft:4}}>${visibleTickets.length} ticket${visibleTickets.length!==1?'s':''}</span>
      </div>

            ${busy?html`<div style=${{textAlign:'center',padding:40}}><div class="spin" style=${{margin:'0 auto'}}></div></div>`:null}
      ${!busy&&visibleTickets.length===0?html`
        <div style=${{textAlign:'center',padding:'48px 16px',color:'var(--tx3)',fontSize:13,background:'var(--sf)',borderRadius:12,border:'1px solid var(--bd)'}}>
          <div style=${{fontSize:36,marginBottom:12}}>🎫</div>
          <div style=${{fontWeight:600,marginBottom:6}}>No tickets yet</div>
          <div>Create a ticket to track bugs, features, and tasks</div>
        </div>`:null}
      <div style=${{display:'flex',flexDirection:'column',gap:8}}>
        ${visibleTickets.map(t=>{
          const tc=TYPE_CFG[t.type]||TYPE_CFG.bug;
          const pc=PRIORITY_CFG[t.priority]||PRIORITY_CFG.medium;
          const sc=STATUS_CFG[t.status]||STATUS_CFG.open;
          const assignee=t.assignee?umap[t.assignee]:null;
          return html`
          <div key=${t.id} onClick=${()=>openDetail(t)}
            style=${{display:'flex',gap:12,padding:'12px 15px',background:'var(--sf)',borderRadius:11,border:'1px solid var(--bd)',alignItems:'center',cursor:'pointer',transition:'all .14s'}}
            onMouseEnter=${e=>{e.currentTarget.style.borderColor='var(--ac)';e.currentTarget.style.background='var(--sf2)';}}
            onMouseLeave=${e=>{e.currentTarget.style.borderColor='var(--bd)';e.currentTarget.style.background='var(--sf)';}}>
                        <div style=${{width:36,height:36,borderRadius:9,background:tc.bg,display:'flex',alignItems:'center',justifyContent:'center',fontSize:17,flexShrink:0}}>${tc.icon}</div>
                        <div style=${{flex:1,minWidth:0}}>
              <div style=${{display:'flex',alignItems:'center',gap:7,marginBottom:3}}>
                <span class="id-badge id-ticket" style=${{fontSize:9,flexShrink:0}}>${t.id}</span>
                <span style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',flex:1}}>${t.title}</span>
                <span style=${{fontSize:10,padding:'1px 7px',borderRadius:5,background:sc.color+'22',color:sc.color,fontWeight:700,flexShrink:0}}>${sc.icon} ${sc.label}</span>
              </div>
              <div style=${{display:'flex',gap:8,alignItems:'center',flexWrap:'wrap'}}>
                <span style=${{fontSize:10,padding:'1px 6px',borderRadius:4,background:pc.color+'22',color:pc.color,fontWeight:600}}>${pc.icon} ${pc.label}</span>
                <span style=${{fontSize:10,color:'var(--tx3)'}}>${tc.label}</span>
                ${t.project?html`<span style=${{fontSize:10,color:'var(--tx3)'}}>📁 ${(safe(projects).find(p=>p.id===t.project)||{name:t.project}).name}</span>`:null}
                <span style=${{fontSize:10,color:'var(--tx3)',marginLeft:'auto'}}>${new Date(t.created).toLocaleDateString()}</span>
              </div>
            </div>
                        ${assignee?html`<div style=${{flexShrink:0}}><${Av} u=${assignee} size=${28}/></div>`:null}
          </div>`;})}
      </div>
      ${showNew?FORM:null}
      ${DETAIL}
    </div>`;
}

/* ─── Reusable ToggleSwitch ───────────────────────────────────────────────── */
function ToggleSwitch({checked,onChange,acColor}){
  const ac=acColor||'var(--ac)';
  return html`
    <div onClick=${onChange} style=${{
      width:44,height:24,borderRadius:100,
      background:checked?ac:'rgba(255,255,255,0.1)',
      border:checked?'1px solid '+ac:'1px solid var(--bd)',
      position:'relative',cursor:'pointer',transition:'all .2s',flexShrink:0
    }}>
      <div style=${{
        position:'absolute',top:2,left:checked?'22px':'2px',
        width:18,height:18,borderRadius:'50%',
        background:checked?'#fff':'var(--tx3)',
        transition:'left .2s',boxShadow:'0 1px 4px rgba(0,0,0,0.4)'
      }}></div>
    </div>`;
}

/* ─── TwoFASettingsCard ───────────────────────────────────────────────────── */
function TwoFASettingsCard({cu}){
  const [userTfaList,setUserTfaList]=useState([]);
  const [loadingTfa,setLoadingTfa]=useState(false);
  const [togglingId,setTogglingId]=useState(null);
  const isAdmin=cu&&(cu.role==='Admin'||cu.role==='Manager');

  useEffect(()=>{
    if(!isAdmin)return;
    setLoadingTfa(true);
    api.get('/api/auth/2fa-status').then(d=>{
      if(Array.isArray(d))setUserTfaList(d);
      setLoadingTfa(false);
    }).catch(()=>setLoadingTfa(false));
  },[isAdmin]);

  const resetUserTotp=async(userId,userName)=>{
    if(!window.confirm('Reset Google Authenticator for '+userName+'? They will need to set it up again.'))return;
    setTogglingId(userId);
    const r=await api.post('/api/auth/totp/reset',{user_id:userId});
    if(r.error){alert(r.error);}
    else{setUserTfaList(prev=>prev.map(u=>u.id===userId?{...u,totp_configured:false,totp_verified:0}:u));}
    setTogglingId(null);
  };

  return html`
    <div class="card" style=${{marginBottom:16}}>
      <div style=${{marginBottom:14}}>
        <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em',marginBottom:4,display:'flex',alignItems:'center',gap:8}}>
          <span style=${{width:30,height:30,borderRadius:9,background:'linear-gradient(135deg,#1d4ed8,#7c3aed)',display:'inline-flex',alignItems:'center',justifyContent:'center',fontSize:15}}>🔐</span>
          Two-Factor Authentication (Google Authenticator)
        </h3>
        <p style=${{fontSize:12,color:'var(--tx2)',lineHeight:1.6,marginBottom:0}}>Users set up Google Authenticator from their profile menu (top-right avatar). Once configured, every login requires a 6-digit code from the app.</p>
      </div>
      <div style=${{padding:'10px 14px',background:'rgba(29,78,216,0.06)',borderRadius:10,border:'1px solid rgba(29,78,216,0.15)',marginBottom:14,display:'flex',gap:12,alignItems:'flex-start'}}>
        <span style=${{fontSize:20,flexShrink:0}}>📱</span>
        <div style=${{fontSize:11,color:'var(--tx2)',lineHeight:1.6}}>          <b>How to enable:</b> Profile avatar → "Setup Authenticator" → scan QR code → confirm code. Works with Google Authenticator, Authy, 1Password, Bitwarden. No email required.
        </div>
      </div>
      ${isAdmin?html`
        <div style=${{fontSize:11,fontWeight:700,color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.7,marginBottom:10}}>User Status</div>
        ${loadingTfa?html`<div style=${{padding:'12px',textAlign:'center',color:'var(--tx3)'}}><span class="spin"></span></div>`:null}
        ${!loadingTfa&&userTfaList.length>0?html`
          <div style=${{borderRadius:10,border:'1px solid var(--bd)',overflow:'hidden'}}>
            ${userTfaList.map((u,i)=>{
              const configured=u.totp_configured||u.totp_verified;
              return html`
                <div key=${u.id} style=${{display:'flex',alignItems:'center',gap:12,padding:'10px 14px',
                  background:i%2===0?'transparent':'rgba(255,255,255,0.02)',
                  borderBottom:i<userTfaList.length-1?'1px solid var(--bd)':'none',
                  opacity:togglingId===u.id?.6:1,transition:'opacity .2s'}}>
                  <div style=${{width:32,height:32,borderRadius:'50%',background:u.color||'#2563eb',
                    display:'flex',alignItems:'center',justifyContent:'center',
                    fontSize:12,fontWeight:700,color:'#fff',flexShrink:0}}>
                    ${(u.name||'?').slice(0,2).toUpperCase()}
                  </div>
                  <div style=${{flex:1,minWidth:0}}>
                    <div style=${{fontSize:12,fontWeight:600,color:'var(--tx)'}}>
                      ${u.name} ${u.id===cu.id?html`<span style=${{fontSize:9,color:'var(--ac)',background:'var(--ac3)',padding:'1px 5px',borderRadius:3}}>YOU</span>`:null}
                    </div>
                    <div style=${{fontSize:10,color:'var(--tx3)'}}>${u.email}</div>
                  </div>
                  <div style=${{fontSize:10,padding:'3px 10px',borderRadius:100,fontWeight:700,
                    background:configured?'rgba(74,222,128,0.1)':'rgba(255,255,255,0.04)',
                    color:configured?'#4ade80':'var(--tx3)',
                    border:'1px solid '+(configured?'rgba(74,222,128,0.3)':'var(--bd)')}}>
                    ${configured?'🔒 Active':'⭕ Not set up'}
                  </div>
                  ${configured?html`
                    <button class="btn brd" style=${{padding:'3px 10px',fontSize:10,flexShrink:0}}
                      onClick=${()=>resetUserTotp(u.id,u.name)} disabled=${togglingId===u.id}>
                      ${togglingId===u.id?'…':'↺ Reset'}
                    </button>`:null}
                </div>`;
            })}
          </div>`:null}`:html`
        <div style=${{padding:'10px 14px',background:'var(--sf2)',borderRadius:8,border:'1px solid var(--bd)',fontSize:12,color:'var(--tx3)',textAlign:'center'}}>
          Set up your own Google Authenticator from the <b style=${{color:'var(--tx2)'}}>profile avatar menu</b> → top-right corner.
        </div>`}
    </div>`;
}

/* ─── WorkspaceSettings ───────────────────────────────────────────────────── */
function WorkspaceSettings({cu,onReload}){
  const [ws,setWs]=useState(null);const [wsName,setWsName]=useState('');const [aiKey,setAiKey]=useState('');const [showKey,setShowKey]=useState(false);const [saving,setSaving]=useState(false);const [saved,setSaved]=useState(false);
  const [emailEnabled,setEmailEnabled]=useState(true);const [smtpServer,setSmtpServer]=useState('smtp.gmail.com');const [smtpPort,setSmtpPort]=useState(587);const [smtpUsername,setSmtpUsername]=useState('');const [smtpPassword,setSmtpPassword]=useState('');const [fromEmail,setFromEmail]=useState('');const [showSmtpPass,setShowSmtpPass]=useState(false);const [testEmail,setTestEmail]=useState('');const [testingEmail,setTestingEmail]=useState(false);const [testResult,setTestResult]=useState(null);const [otpEnabled,setOtpEnabled]=useState(false);
  const [dmEnabled,setDmEnabled]=useState(true);
  const PERM_DEFAULTS={
    'Create & Edit Projects':   {Admin:true, Manager:true, TeamLead:true, Developer:false,Tester:false,Viewer:false}, 'Create & Assign Tasks':    {Admin:true, Manager:true, TeamLead:true, Developer:true, Tester:false,Viewer:false}, 'Edit Tasks':               {Admin:true, Manager:true, TeamLead:true, Developer:false,Tester:false,Viewer:false}, 'Delete Tasks':             {Admin:true, Manager:true, TeamLead:true, Developer:false,Tester:false,Viewer:false}, 'Create Tickets':           {Admin:true, Manager:true, TeamLead:true, Developer:true, Tester:true, Viewer:false}, 'Edit Tickets':             {Admin:true, Manager:true, TeamLead:true, Developer:false,Tester:false,Viewer:false}, 'Delete Tickets':           {Admin:true, Manager:true, TeamLead:true, Developer:false,Tester:false,Viewer:false}, 'Close / Resolve Tickets':  {Admin:true, Manager:true, TeamLead:true, Developer:true, Tester:false,Viewer:false}, 'Delete Projects':          {Admin:true, Manager:true, TeamLead:false,Developer:false,Tester:false,Viewer:false}, 'Send Channel Messages':    {Admin:true, Manager:true, TeamLead:true, Developer:true, Tester:true, Viewer:true}, 'Manage Team Members':      {Admin:true, Manager:true, TeamLead:true, Developer:false,Tester:false,Viewer:false}, 'Manage Workspace Settings':{Admin:true, Manager:false,TeamLead:false,Developer:false,Tester:false,Viewer:false}, 'View All Projects':        {Admin:true, Manager:true, TeamLead:true, Developer:true, Tester:true, Viewer:true}, 'Start Instant Meet Calls':       {Admin:true, Manager:true, TeamLead:true, Developer:true, Tester:true, Viewer:true}, 'Delete Team Members':      {Admin:true, Manager:false,TeamLead:false,Developer:false,Tester:false,Viewer:false}, };
  const storedPerms=()=>{try{return JSON.parse(localStorage.getItem('pf_perms')||'null');}catch{return null;}};
  const [perms,setPerms]=useState(()=>storedPerms()||PERM_DEFAULTS);
  const togglePerm=(label,role)=>{
    if(role==='Admin')return;// Admin always has all perms
    setPerms(prev=>{const n={...prev,[label]:{...prev[label],[role]:!prev[label][role]}};localStorage.setItem('pf_perms',JSON.stringify(n));return n;});
  };
  const resetPerms=()=>{setPerms(PERM_DEFAULTS);localStorage.removeItem('pf_perms');};

  useEffect(()=>{api.get('/api/workspace').then(d=>{if(!d.error){setWs(d);setWsName(d.name||'');setAiKey(d.ai_api_key?'•'.repeat(20):'');setEmailEnabled(d.email_enabled!==0);setSmtpServer(d.smtp_server||'smtp.gmail.com');setSmtpPort(d.smtp_port||587);setSmtpUsername(d.smtp_username||'');setSmtpPassword(d.smtp_password?'•'.repeat(16):'');setFromEmail(d.from_email||'');setOtpEnabled(!!d.otp_enabled);setDmEnabled(d.dm_enabled!==0);}});},[]);

  const save=async()=>{
    setSaving(true);
    const payload={name:wsName,email_enabled:emailEnabled,smtp_server:smtpServer,smtp_port:smtpPort,smtp_username:smtpUsername,from_email:fromEmail,otp_enabled:otpEnabled,dm_enabled:dmEnabled};
    if(aiKey&&!aiKey.startsWith('•'))payload.ai_api_key=aiKey;
    if(smtpPassword&&!smtpPassword.startsWith('•'))payload.smtp_password=smtpPassword;
    await api.put('/api/workspace',payload);
    setSaving(false);setSaved(true);setTimeout(()=>setSaved(false),2000);
    await onReload();
  };

  const sendTestEmail=async()=>{
    if(!testEmail){alert('Please enter an email address');return;}
    setTestingEmail(true);setTestResult(null);
    const r=await api.post('/api/workspace/test-email',{test_email:testEmail});
    setTestingEmail(false);
    setTestResult(r.success?{success:true,message:r.message}:{success:false,message:r.message||'Failed to send test email'});
    setTimeout(()=>setTestResult(null),5000);
  };

  const newInvite=async()=>{
    if(!window.confirm('Generate a new invite code? The old one will stop working.'))return;
    const r=await api.post('/api/workspace/new-invite',{});
    setWs(prev=>({...prev,invite_code:r.invite_code}));
  };

  const copy=text=>{navigator.clipboard&&navigator.clipboard.writeText(text);};

  if(!ws)return html`<div style=${{padding:40,textAlign:'center'}}><span class="spin"></span></div>`;

  return html`<div class="fi" style=${{height:'100%',overflowY:'auto',padding:'24px'}}>
    <div style=${{maxWidth:640}}>
      <h2 style=${{fontSize:17,fontWeight:700,color:'var(--tx)',marginBottom:20}}>⚙ Workspace Settings</h2>

      <div class="card" style=${{marginBottom:16}}>
        <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em',marginBottom:4}}>🎨 Theme & Accent Color</h3>
        <p style=${{fontSize:12,color:'var(--tx2)',marginBottom:14}}>Choose a preset or set a custom accent color for the UI.</p>
        <div style=${{display:'flex',gap:10,flexWrap:'wrap',alignItems:'center',marginBottom:12}}>
          ${[
            {name:'Ocean', ac:'#1d4ed8',ac2:'#1e40af',tx:'#ffffff'}, {name:'Cyan', ac:'#22d3ee',ac2:'#06b6d4',tx:'#001a1f'}, {name:'Purple', ac:'#a78bfa',ac2:'#8b5cf6',tx:'#1a0a2e'}, {name:'Pink', ac:'#f472b6',ac2:'#ec4899',tx:'#2d001a'}, {name:'Orange', ac:'#fb923c',ac2:'#f97316',tx:'#2d0f00'}, {name:'Green', ac:'#4ade80',ac2:'#22c55e',tx:'#002d10'}, ].map(({name,ac,ac2,tx})=>html`
            <button key=${name} title=${name}
              onClick=${()=>{
                const r=document.body.style;
                r.setProperty('--ac',ac);r.setProperty('--ac2',ac2);
                const hex=ac.replace('#','');const bigint=parseInt(hex,16);
                const ri=Math.round((bigint>>16)&255),gi=Math.round((bigint>>8)&255),bi=Math.round(bigint&255);
                r.setProperty('--ac3','rgba('+ri+','+gi+','+bi+',.10)');
                r.setProperty('--ac4','rgba('+ri+','+gi+','+bi+',.06)');
                r.setProperty('--ac-tx',tx);
                localStorage.setItem('pf_accent',JSON.stringify({ac,ac2,tx}));
              }}
              style=${{width:34,height:34,borderRadius:10,background:ac,border:'3px solid '+(localStorage.getItem('pf_accent')&&JSON.parse(localStorage.getItem('pf_accent')).ac===ac?'var(--tx)':'transparent'),cursor:'pointer',transition:'all .14s',boxShadow:'0 2px 8px rgba(0,0,0,.25)'}}
              onMouseEnter=${e=>e.currentTarget.style.transform='scale(1.15)'}
              onMouseLeave=${e=>e.currentTarget.style.transform='scale(1)'}
            ></button>`)}
          <div style=${{display:'flex',alignItems:'center',gap:8,marginLeft:4}}>
            <label style=${{fontSize:12,color:'var(--tx2)'}}>Custom:</label>
            <input type="color" defaultValue="#1d4ed8"
              style=${{width:34,height:34,borderRadius:10,border:'2px solid var(--bd)',cursor:'pointer',background:'none',padding:2}}
              onChange=${e=>{
                const hex=e.target.value;
                const r=document.body.style;
                r.setProperty('--ac',hex);
                const darker='#'+hex.slice(1).replace(/../g,c=>Math.max(0,parseInt(c,16)-16).toString(16).padStart(2,'0'));
                r.setProperty('--ac2',darker);
                const bigint=parseInt(hex.replace('#',''),16);
                const ri=Math.round((bigint>>16)&255),gi=Math.round((bigint>>8)&255),bi=Math.round(bigint&255);
                r.setProperty('--ac3','rgba('+ri+','+gi+','+bi+',.10)');
                r.setProperty('--ac4','rgba('+ri+','+gi+','+bi+',.06)');
                const lum=(0.299*ri+0.587*gi+0.114*bi)/255;
                const tx=lum>0.6?'#111111':'#f5f5f5';
                r.setProperty('--ac-tx',tx);
                localStorage.setItem('pf_accent',JSON.stringify({ac:hex,ac2:darker,tx}));
              }}/>
          </div>
          <button class="btn brd" style=${{fontSize:11,padding:'5px 10px',marginLeft:4}} onClick=${()=>{
            const r=document.body.style;
            r.setProperty('--ac','#1d4ed8');r.setProperty('--ac2','#1e40af');
            r.setProperty('--ac3','rgba(29,78,216,.10)');r.setProperty('--ac4','rgba(29,78,216,.06)');
            r.setProperty('--ac-tx','#ffffff');
            localStorage.removeItem('pf_accent');
          }}>↺ Reset</button>
        </div>
        <div style=${{fontSize:11,color:'var(--tx3)',padding:'8px 12px',background:'var(--sf2)',borderRadius:8,border:'1px solid var(--bd)'}}>
          Preview: <span style=${{color:'var(--ac)',fontWeight:700}}>Active color</span> · <button class="btn bp" style=${{fontSize:10,padding:'2px 8px',marginLeft:4}}>Sample button</button>
        </div>
      </div>

      <div class="card" style=${{marginBottom:16}}>
        <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em',marginBottom:16}}>🏢 Workspace</h3>
        <div style=${{display:'flex',flexDirection:'column',gap:12}}>
          <div><label class="lbl">Workspace Name</label><input class="inp" value=${wsName} onInput=${e=>setWsName(e.target.value)}/></div>
          <div><label class="lbl">Workspace ID</label><div style=${{fontSize:12,color:'var(--tx3)',fontFamily:'monospace',padding:'8px 12px',background:'var(--sf2)',borderRadius:8}}>${ws.id}</div></div>
        </div>
      </div>

      <div class="card" style=${{marginBottom:16}}>
        <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em',marginBottom:4}}>💳 Plan & Billing</h3>
        <p style=${{fontSize:12,color:'var(--tx2)',marginBottom:14}}>Your current subscription plan and member usage.</p>
        ${(()=>{
          const plan=(ws.plan||'starter');
          const limits={starter:5,team:30,enterprise:null};
          const limit=limits[plan];
          const planColors={starter:'var(--tx3)',team:'var(--ac)',enterprise:'var(--pu)'};
          const planLabels={starter:'Starter — Free',team:'Team — ₹999/mo',enterprise:'Enterprise — Custom'};
          return html\`
            <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',padding:'12px 16px',background:'var(--sf2)',borderRadius:10,border:'1px solid var(--bd)',marginBottom:10}}>
              <div>
                <div style=${{fontSize:13,fontWeight:700,color:planColors[plan]||'var(--tx)'}}>${planLabels[plan]||plan}</div>
                <div style=${{fontSize:11,color:'var(--tx3)',marginTop:2}}>
                  ${limit?html\`Member limit: \${limit} · Contact ceo@vewit.in to upgrade\`:html\`Unlimited members\`}
                </div>
              </div>
              <div style=${{fontSize:22,padding:'6px 14px',background:plan==='team'?'rgba(90,140,255,.12)':plan==='enterprise'?'rgba(168,85,247,.12)':'var(--sf2)',borderRadius:8,fontWeight:700,color:planColors[plan]||'var(--tx3)',border:'1px solid var(--bd)'}}>
                ${plan==='starter'?'FREE':plan==='team'?'TEAM':plan==='enterprise'?'ENT':'—'}
              </div>
            </div>
            ${plan==='starter'?html\`
              <div style=${{fontSize:12,padding:'9px 12px',background:'rgba(90,140,255,.06)',borderRadius:8,border:'1px solid rgba(90,140,255,.15)',color:'var(--tx2)'}}>
                🚀 <b>Upgrade to Team</b> — Get up to 30 members, analytics, custom SMTP & more for ₹999/mo. Email <a href="mailto:ceo@vewit.in" style=${{color:'var(--ac)'}}>ceo@vewit.in</a>
              </div>
            \`:null}
          \`;
        })()}
      </div>

      <div class="card" style=${{marginBottom:16}}>
        <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em',marginBottom:4}}>🔗 Invite Code</h3>
        <p style=${{fontSize:12,color:'var(--tx2)',marginBottom:14}}>Share this code with teammates to join your workspace.</p>
        <div style=${{display:'flex',alignItems:'center',gap:10}}>
          <div style=${{flex:1,textAlign:'center',padding:'14px',background:'linear-gradient(135deg,rgba(90,140,255,.10),rgba(109,40,217,0.10))',borderRadius:12,border:'1px solid rgba(90,140,255,.18)'}}>
            <div style=${{fontSize:28,fontWeight:700,color:'var(--ac2)',fontFamily:'monospace',letterSpacing:4}}>${ws.invite_code}</div>
          </div>
          <div style=${{display:'flex',flexDirection:'column',gap:8}}>
            <button class="btn bp" style=${{fontSize:12,padding:'8px 14px'}} onClick=${()=>copy(ws.invite_code)}>📋 Copy</button>
            <button class="btn bam" style=${{fontSize:12,padding:'8px 14px'}} onClick=${newInvite}>↻ New Code</button>
          </div>
        </div>
      </div>

      <div class="card" style=${{marginBottom:16}}>
        <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em',marginBottom:4}}>🤖 AI Assistant</h3>
        <p style=${{fontSize:12,color:'var(--tx2)',marginBottom:14}}>Paste your Anthropic API key to enable the AI assistant. The key is stored securely in your workspace only.</p>
        <div><label class="lbl">Anthropic API Key</label>
          <div style=${{position:'relative'}}>
            <input class="inp" style=${{paddingRight:40,fontFamily:showKey?'monospace':'monospace',letterSpacing:aiKey.startsWith('•')?0:0}} type=${showKey?'text':'password'} placeholder="sk-ant-api..." value=${aiKey}
              onInput=${e=>setAiKey(e.target.value)} onFocus=${()=>{if(aiKey.startsWith('•'))setAiKey('');}}/>
            <button onClick=${()=>setShowKey(!showKey)} style=${{position:'absolute',right:11,top:'50%',transform:'translateY(-50%)',background:'none',border:'none',cursor:'pointer',color:'var(--tx3)'}}>${showKey?'🙈':'👁'}</button>
          </div>
        </div>
        <div style=${{marginTop:10,padding:'9px 12px',background:'rgba(99,102,241,.07)',borderRadius:8,border:'1px solid rgba(90,140,255,.15)',fontSize:12,color:'var(--tx2)'}}>
          💡 Get your API key at <b style=${{color:'var(--ac2)'}}>console.anthropic.com</b>. The AI can answer questions, create tasks, update statuses, and generate EOD reports.
        </div>
      </div>

      <div class="card" style=${{marginBottom:16}}>
        <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:4}}>
          <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em'}}>🔐 Role Permissions</h3>
          <button class="btn brd" style=${{fontSize:11,padding:'4px 10px'}} onClick=${resetPerms}>↺ Reset defaults</button>
        </div>
        <p style=${{fontSize:12,color:'var(--tx2)',marginBottom:14}}>Click checkboxes to toggle permissions per role. Admin always has full access.</p>
        <div style=${{overflowX:'auto'}}>
          <table style=${{width:'100%',borderCollapse:'collapse',fontSize:12}}>
            <thead>
              <tr>
                <th style=${{padding:'8px 12px',textAlign:'left',color:'var(--tx3)',fontWeight:600,borderBottom:'1px solid var(--bd)'}}>Permission</th>
                ${['Admin','Manager','TeamLead','Developer','Tester','Viewer'].map(r=>html`
                  <th key=${r} style=${{padding:'8px 12px',textAlign:'center',color:r==='Admin'?'var(--ac)':'var(--tx3)',fontWeight:700,borderBottom:'1px solid var(--bd)',minWidth:80,fontSize:11}}>
                    ${r}${r==='Admin'?html`<div style=${{fontSize:9,fontWeight:400,color:'var(--tx3)'}}>locked</div>`:null}
                  </th>`)}
              </tr>
            </thead>
            <tbody>
              ${Object.entries(perms).map(([label,roleMap],i)=>html`
                <tr key=${label} style=${{background:i%2===0?'transparent':'var(--sf2)'}}>
                  <td style=${{padding:'9px 12px',color:'var(--tx2)',fontWeight:500,fontSize:12}}>${label}</td>
                  ${['Admin','Manager','TeamLead','Developer','Tester','Viewer'].map(r=>html`
                    <td key=${r} style=${{padding:'9px 12px',textAlign:'center'}}>
                      <label style=${{cursor:r==='Admin'?'not-allowed':'pointer',display:'inline-flex',alignItems:'center',justifyContent:'center'}}>
                        <input type="checkbox" checked=${!!roleMap[r]} disabled=${r==='Admin'}
                          onChange=${()=>togglePerm(label,r)}
                          style=${{width:16,height:16,accentColor:'var(--ac)',cursor:r==='Admin'?'not-allowed':'pointer'}}/>
                      </label>
                    </td>`)}
                </tr>`)}
            </tbody>
          </table>
        </div>
        <div style=${{marginTop:12,padding:'9px 13px',background:'rgba(90,140,255,.05)',borderRadius:9,border:'1px solid rgba(90,140,255,.15)',fontSize:12,color:'var(--tx3)'}}>
          💡 Changes save automatically. Assign roles in the <b style=${{color:'var(--tx2)'}}>Team</b> tab.
        </div>
      </div>

      <${TwoFASettingsCard} cu=${cu}/>

            <div class="card" style=${{marginBottom:0}}>
        <div style=${{display:'flex',alignItems:'flex-start',justifyContent:'space-between',gap:16}}>
          <div style=${{flex:1}}>
            <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em',marginBottom:4}}>💬 Direct Messages (DMs)</h3>
            <p style=${{fontSize:12,color:'var(--tx2)',marginBottom:8}}>Control whether workspace members can send private direct messages to each other. When disabled, DMs are hidden for all non-admin users.</p>
            <div style=${{padding:'9px 13px',background:dmEnabled?'rgba(29,78,216,0.06)':'rgba(255,255,255,0.02)',borderRadius:9,border:dmEnabled?'1px solid rgba(29,78,216,0.2)':'1px solid var(--bd)',fontSize:12,color:'var(--tx2)',display:'flex',flexDirection:'column',gap:5}}>
              <div style=${{display:'flex',alignItems:'center',gap:6}}>
                <span>${dmEnabled?'✅':'⬜'}</span>
                <span style=${{fontWeight:600,color:dmEnabled?'var(--ac)':'var(--tx2)'}}>Direct Messages are ${dmEnabled?'ENABLED':'DISABLED'}</span>
              </div>
              <div class="tx3-11">
                ${dmEnabled?'Members can send private messages to each other.':'Members cannot send or view DMs. Admins & Managers can still access DMs.'}
              </div>
            </div>
          </div>
          <div style=${{flexShrink:0,paddingTop:4,display:'flex',flexDirection:'column',alignItems:'center',gap:5}}>
            <${ToggleSwitch} checked=${dmEnabled} onChange=${()=>setDmEnabled(!dmEnabled)}/>
            <span style=${{fontSize:10,fontWeight:600,color:dmEnabled?'var(--ac)':'var(--tx3)'}}>${dmEnabled?'On':'Off'}</span>
          </div>
        </div>
      </div>

      <div style=${{display:'flex',gap:10,justifyContent:'flex-end'}}>
        <button class="btn bp" onClick=${save} disabled=${saving}>
          ${saving?html`<span class="spin"></span>`:saved?'✓ Saved!':'Save Settings'}
        </button>
      </div>
    </div>
  </div>`;
}

/* ─── AiDocsView ──────────────────────────────────────────────────────────── */

/* ─── AiDocsView — Chat-first AI Documentation Studio ─────────────────────── */

/* ─── AiDocsView — Chat-first AI Documentation Studio ─────────────────────── */
function AiDocsView({cu,projects,tasks,users}){
  const [messages,setMessages]=useState([]);
  const [input,setInput]=useState('');
  const [sending,setSending]=useState(false);
  const [sideTab,setSideTab]=useState('recents');
  const [copied,setCopied]=useState(null);
  const bottomRef=useRef(null);
  const inputRef=useRef(null);
  const chatRef=useRef(null);

  // ── Recents: stored in localStorage, keyed by user ──────────────────────
  const STORAGE_KEY='vw_ai_recents_'+(cu&&cu.id||'x');
  const loadRecents=()=>{try{return JSON.parse(localStorage.getItem(STORAGE_KEY)||'[]');}catch{return [];}};
  const saveRecents=(list)=>{try{localStorage.setItem(STORAGE_KEY,JSON.stringify(list.slice(0,30)));}catch{}};

  const [recents,setRecents]=useState(()=>loadRecents());
  const [activeRecent,setActiveRecent]=useState(null); // id of loaded conversation

  const saveCurrentAsRecent=(msgs)=>{
    if(!msgs||msgs.length<=1)return;
    const userMsgs=msgs.filter(m=>m.role==='user');
    if(!userMsgs.length)return;
    const title=userMsgs[0].content.slice(0,60)+(userMsgs[0].content.length>60?'…':'');
    const id=activeRecent||('r'+Date.now());
    setActiveRecent(id);
    const entry={id,title,ts:new Date().toISOString(),msgs};
    setRecents(prev=>{
      const filtered=prev.filter(r=>r.id!==id);
      const next=[entry,...filtered];
      saveRecents(next);
      return next;
    });
  };

  const loadRecent=(r)=>{
    setMessages(r.msgs||[]);
    setActiveRecent(r.id);
    setInput('');
    scrollToBottom();
  };

  const deleteRecent=(id,e)=>{
    e.stopPropagation();
    setRecents(prev=>{
      const next=prev.filter(r=>r.id!==id);
      saveRecents(next);
      return next;
    });
    if(activeRecent===id){setActiveRecent(null);}
  };

  const newChat=()=>{
    saveCurrentAsRecent(messages);
    setMessages([{role:'assistant',id:'w'+Date.now(),ts:new Date(),
      content:'New chat started. What would you like to create today?',type:'assistant'}]);
    setActiveRecent(null);
    setInput('');
  };

  const scrollToBottom=()=>{ setTimeout(()=>{ if(bottomRef.current) bottomRef.current.scrollIntoView({behavior:'smooth'}); },80); };

  const fmtRecent=(iso)=>{
    try{
      const d=new Date(iso),now=new Date();
      const diff=now-d;
      if(diff<60000)return'Just now';
      if(diff<3600000)return Math.floor(diff/60000)+'m ago';
      if(diff<86400000)return Math.floor(diff/3600000)+'h ago';
      if(diff<604800000)return Math.floor(diff/86400000)+'d ago';
      return d.toLocaleDateString('en-US',{month:'short',day:'numeric'});
    }catch{return'';}
  };

  useEffect(()=>{
    // Welcome message
    setMessages([{
      role:'assistant',id:'welcome',ts:new Date(),
      content:`# Welcome to the AI Documentation Studio 🤖

I'm your AI assistant powered by Claude. I can help you create:

- **Architecture diagrams** (Mermaid.js, ready to render)
- **Project documentation** (using your real workspace data)  
- **Technical specifications** and API references
- **Sprint reports**, test plans, and security checklists

Your workspace has **${safe(projects).length} projects**, **${safe(tasks).length} tasks**, and **${safe(users).length} team members** — I'll use this context automatically.

**Try a template below**, or just describe what you need in plain English. 💬`,
      type:'welcome'
    }]);
  },[]);

  const send=async(textOverride)=>{
    const text=(textOverride||input).trim();
    if(!text||sending)return;
    setInput('');setSending(true);

    const userMsg={role:'user',id:'u'+Date.now(),ts:new Date(),content:text};
    const thinkingId='t'+Date.now();
    setMessages(m=>[...m,userMsg,{role:'assistant',id:thinkingId,ts:new Date(),content:'',type:'thinking'}]);
    scrollToBottom();

    // Build workspace context
    const projCtx=safe(projects).slice(0,8).map(p=>`- ${p.name} (progress:${p.progress||0}%, due:${p.target_date||'TBD'})`).join('\n');
    const taskCtx=safe(tasks).filter(t=>t.stage!=='completed').slice(0,20).map(t=>`- [${t.id}] ${t.title} | ${t.stage} | ${t.priority}`).join('\n');
    const teamCtx=safe(users).map(u=>`- ${u.name} (${u.role})`).join('\n');

    const systemPrompt=`You are an expert technical documentation assistant for VEWIT, an AI-powered project management platform.

WORKSPACE CONTEXT:
Projects (${safe(projects).length} total):
${projCtx||'No projects yet.'}

Active Tasks (${safe(tasks).filter(t=>t.stage!=='completed').length} active):
${taskCtx||'No active tasks.'}

Team (${safe(users).length} members):
${teamCtx||'No team members.'}

Current user: ${cu.name} (${cu.role})

INSTRUCTIONS:
- Always respond in clean Markdown
- For architecture diagrams, wrap Mermaid code in triple backtick mermaid blocks
- Be specific and use real workspace data when available
- Format docs professionally — use headers, tables, bullet points
- For diagrams, explain what the diagram shows after the code block
- Keep responses rich and actionable`;

    const history=messages.filter(m=>m.role!=='assistant'||m.type!=='thinking').slice(-12).map(m=>({role:m.role,content:m.content}));
    history.push({role:'user',content:text});

    // Check for API key
    const ws=await api.get('/api/workspace');
    if(!ws.ai_api_key){
      setMessages(m=>m.map(msg=>msg.id===thinkingId?{...msg,type:'error',content:'**No AI API Key configured.**\n\nPlease add your Anthropic API key in **Workspace Settings → AI Key** to enable the AI assistant.\n\nYou can get a key at [anthropic.com](https://anthropic.com).'}:msg));
      setSending(false);scrollToBottom();return;
    }

    try{
      const r=await fetch('https://api.anthropic.com/v1/messages',{
        method:'POST',
        headers:{'Content-Type':'application/json','x-api-key':ws.ai_api_key,'anthropic-version':'2023-06-01'},
        body:JSON.stringify({model:'claude-sonnet-4-5',max_tokens:3000,system:systemPrompt,messages:history})
      });
      if(!r.ok){
        const e=await r.json().catch(()=>({}));
        throw new Error(e.error?.message||'API error '+r.status);
      }
      const data=await r.json();
      const reply=data.content?.[0]?.text||'Sorry, I could not generate a response.';
      setMessages(m=>m.map(msg=>msg.id===thinkingId?{...msg,type:'assistant',content:reply}:msg));
    }catch(e){
      const errMsg=e.message||'Network error';
      setMessages(m=>m.map(msg=>msg.id===thinkingId?{...msg,type:'error',content:`**Error:** ${errMsg}\n\nPlease check your API key in Workspace Settings.`}:msg));
    }
    setSending(false);scrollToBottom();
    // Auto-save to recents after AI responds
    setMessages(current=>{saveCurrentAsRecent(current);return current;});
  };

  const copyMsg=(content,id)=>{
    navigator.clipboard&&navigator.clipboard.writeText(content);
    setCopied(id);setTimeout(()=>setCopied(null),2200);
  };

  const downloadMsg=(content,label)=>{
    const blob=new Blob([content],{type:'text/markdown'});
    const a=document.createElement('a');
    a.href=URL.createObjectURL(blob);
    a.download=(label||'document')+'.md';a.click();
  };

  const extractMermaid=(content)=>{
    const m=content.match(/```mermaid\s*([\s\S]*?)```/);
    return m?m[1].trim():null;
  };

  const renderMd=(md)=>{
    if(!md)return '';
    return md
      .replace(/^# (.+)$/gm,'<h1 style="font-size:20px;font-weight:800;color:var(--tx);margin:0 0 14px;letter-spacing:-.4px;padding-bottom:8px;border-bottom:2px solid rgba(255,100,60,.2)">$1</h1>')
      .replace(/^## (.+)$/gm,'<h2 style="font-size:16px;font-weight:700;color:var(--tx);margin:20px 0 8px;letter-spacing:-.3px">$1</h2>')
      .replace(/^### (.+)$/gm,'<h3 style="font-size:14px;font-weight:700;color:var(--tx);margin:14px 0 6px">$1</h3>')
      .replace(/\*\*(.+?)\*\*/g,'<b style="color:var(--tx);font-weight:700">$1</b>')
      .replace(/\*(.+?)\*/g,'<i style="color:var(--tx2)">$1</i>')
      .replace(/`([^`\n]+)`/g,'<code style="font-family:monospace;font-size:11.5px;background:rgba(139,92,246,.1);padding:2px 7px;border-radius:5px;color:#a78bfa;border:1px solid rgba(139,92,246,.2)">$1</code>')
      .replace(/```mermaid\s*([\s\S]*?)```/g,(m,code)=>`<div class="vw-mermaid-block" style="margin:14px 0;border-radius:12px;overflow:hidden;border:1px solid rgba(139,92,246,.25)"><div style="background:rgba(139,92,246,.08);padding:8px 14px;font-size:11px;font-weight:700;color:#a78bfa;display:flex;align-items:center;justify-content:space-between"><span>🏗️ Mermaid Diagram</span><a href='https://mermaid.live' target='_blank' style='color:#a78bfa;font-size:10px;text-decoration:none;border:1px solid rgba(139,92,246,.3);padding:2px 8px;border-radius:6px'>Open in mermaid.live ↗</a></div><pre style="background:rgba(15,10,30,.6);padding:14px;margin:0;font-family:monospace;font-size:12px;color:#c4b5fd;overflow-x:auto;line-height:1.7;white-space:pre-wrap">${code}</pre></div>`)
      .replace(/```[\w]*\n?([\s\S]*?)```/g,'<pre style="background:rgba(15,10,30,.6);border:1px solid rgba(255,255,255,.08);border-radius:10px;padding:14px 16px;overflow-x:auto;font-family:monospace;font-size:12px;color:#c4b5fd;margin:10px 0;line-height:1.7;white-space:pre-wrap">$1</pre>')
      .replace(/^\|(.+)\|$/gm,row=>{
        const cells=row.split('|').filter(c=>c.trim()!==''&&!c.trim().match(/^[-:]+$/));
        if(!cells.length)return '';
        return '<tr>'+cells.map(c=>`<td style="padding:8px 14px;border:1px solid rgba(255,255,255,.08);font-size:12.5px;color:var(--tx2)">${c.trim()}</td>`).join('')+'</tr>';
      })
      .replace(/(<tr>[\s\S]*?<\/tr>\n?)+/g,t=>`<div style="overflow-x:auto;margin:10px 0"><table style="width:100%;border-collapse:collapse;border-radius:8px;overflow:hidden">${t}</table></div>`)
      .replace(/^- (.+)$/gm,'<li style="font-size:13.5px;color:var(--tx2);margin:5px 0;line-height:1.6;padding-left:4px">$1</li>')
      .replace(/^(\d+)\. (.+)$/gm,'<li style="font-size:13.5px;color:var(--tx2);margin:5px 0;line-height:1.6;padding-left:4px"><b style="color:#ff6433;margin-right:6px">$1.</b>$2</li>')
      .replace(/(<li[\s\S]*?<\/li>\n?)+/g,l=>`<ul style="margin:8px 0 12px 18px;padding:0;list-style:disc">${l}</ul>`)
      .replace(/^> (.+)$/gm,'<blockquote style="border-left:3px solid #ff6433;margin:12px 0;padding:10px 16px;background:rgba(255,100,60,.06);border-radius:0 8px 8px 0;font-style:italic;color:var(--tx2);font-size:13px">$1</blockquote>')
      .replace(/\n\n/g,'<br/>')
      .replace(/\n(?!<)/g,'<br/>');
  };

  const clearChat=()=>{
    setMessages([{role:'assistant',id:'welcome2',ts:new Date(),content:'Chat cleared. What would you like to create?',type:'assistant'}]);
  };

  return html`
    <div style=${{display:'flex',height:'100%',overflow:'hidden',background:'var(--bg)'}}>

      <!-- LEFT SIDEBAR — Claude/ChatGPT-style recents -->
      <div style=${{width:260,flexShrink:0,borderRight:'1px solid var(--bd)',display:'flex',flexDirection:'column',background:'var(--sf)',overflow:'hidden'}}>

        <!-- Header: New Chat -->
        <div style=${{padding:'12px 12px 10px',borderBottom:'1px solid var(--bd)',flexShrink:0}}>
          <button onClick=${newChat}
            style=${{width:'100%',height:36,borderRadius:10,border:'1px dashed var(--bd)',background:'var(--sf2)',cursor:'pointer',
              display:'flex',alignItems:'center',justifyContent:'center',gap:8,
              color:'var(--tx3)',fontSize:12.5,fontWeight:600,fontFamily:'inherit',transition:'all .15s'}}
            onMouseEnter=${e=>{e.currentTarget.style.borderColor='var(--ac)';e.currentTarget.style.color='var(--ac)';e.currentTarget.style.background='var(--ac4)';}}
            onMouseLeave=${e=>{e.currentTarget.style.borderColor='var(--bd)';e.currentTarget.style.color='var(--tx3)';e.currentTarget.style.background='var(--sf2)';}}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            New chat
          </button>
        </div>

        <!-- Recents list — Claude/ChatGPT style -->
        <div style=${{flex:1,overflowY:'auto',padding:'8px 8px'}}>
          ${recents.length===0?html`
            <div style=${{textAlign:'center',padding:'32px 16px',color:'var(--tx3)'}}>
              <div style=${{fontSize:32,marginBottom:12,opacity:.4}}>💬</div>
              <div style=${{fontSize:12,fontWeight:600,marginBottom:6}}>No recent chats</div>
              <div style=${{fontSize:11,lineHeight:1.6}}>Your conversations will appear here. Start chatting to create docs, diagrams, and specs.</div>
            </div>
          `:html`
            <!-- Group by Today / Yesterday / Older -->
            ${(()=>{
              const now=new Date();
              const today=now.toDateString();
              const yesterday=new Date(now-86400000).toDateString();
              const groups={Today:[],Yesterday:[],'Previous 7 days':[],'Older':[]};
              recents.forEach(r=>{
                const d=new Date(r.ts).toDateString();
                const diff=(now-new Date(r.ts))/86400000;
                if(d===today)groups['Today'].push(r);
                else if(d===yesterday)groups['Yesterday'].push(r);
                else if(diff<7)groups['Previous 7 days'].push(r);
                else groups['Older'].push(r);
              });
              return Object.entries(groups).filter(([,v])=>v.length>0).map(([gname,items])=>html`
                <div key=${gname} style=${{marginBottom:4}}>
                  <div style=${{fontSize:10,fontWeight:700,color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.7,padding:'8px 8px 4px'}}>${gname}</div>
                  ${items.map(r=>html`
                    <div key=${r.id}
                      onClick=${()=>loadRecent(r)}
                      style=${{
                        padding:'8px 10px',borderRadius:9,cursor:'pointer',marginBottom:1,
                        display:'flex',alignItems:'center',gap:8,
                        background:activeRecent===r.id?'var(--ac3)':'transparent',
                        border:'1px solid '+(activeRecent===r.id?'var(--ac3)':'transparent'),
                        transition:'all .12s',position:'relative',
                        color:activeRecent===r.id?'var(--ac)':'var(--tx2)',
                        group:'r'+r.id
                      }}
                      onMouseEnter=${e=>{if(activeRecent!==r.id){e.currentTarget.style.background='var(--sf2)';e.currentTarget.querySelectorAll('.del-btn').forEach(b=>b.style.opacity='1');}}}
                      onMouseLeave=${e=>{if(activeRecent!==r.id){e.currentTarget.style.background='transparent';e.currentTarget.querySelectorAll('.del-btn').forEach(b=>b.style.opacity='0');}}}>
                      <!-- Chat icon -->
                      <svg style=${{flexShrink:0,opacity:.55}} width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                      <!-- Title -->
                      <span style=${{flex:1,fontSize:12.5,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',lineHeight:1.4}}>
                        ${r.title}
                      </span>
                      <!-- Delete button — hidden until hover -->
                      <button class="del-btn" onClick=${(e)=>deleteRecent(r.id,e)}
                        style=${{opacity:0,background:'none',border:'none',cursor:'pointer',
                          color:'var(--tx3)',padding:'2px 4px',borderRadius:5,fontSize:13,
                          transition:'opacity .15s,color .15s',flexShrink:0,lineHeight:1}}
                        onMouseEnter=${e=>{e.target.style.color='var(--rd)';}}
                        onMouseLeave=${e=>{e.target.style.color='var(--tx3)';}}>
                        ✕
                      </button>
                    </div>`)}
                </div>`);
            })()}
          `}
        </div>

        <!-- Footer: workspace stats pill -->
        <div style=${{padding:'8px 12px',borderTop:'1px solid var(--bd)',flexShrink:0,display:'flex',gap:10,alignItems:'center'}}>
          <div style=${{display:'flex',gap:8,flex:1,flexWrap:'wrap'}}>
            ${[
              {val:safe(projects).length,label:'proj',color:'var(--ac)'},
              {val:safe(tasks).filter(t=>!['completed','backlog'].includes(t.stage)).length,label:'active',color:'var(--gn)'},
              {val:safe(users).length,label:'members',color:'var(--pu)'},
            ].map((s,i)=>html`
              <span key=${i} style=${{fontSize:10.5,color:'var(--tx3)',display:'flex',alignItems:'center',gap:3}}>
                <b style=${{color:s.color,fontWeight:700}}>${s.val}</b> ${s.label}
              </span>`)}
          </div>
        </div>
      </div>

      <!-- MAIN CHAT AREA -->
      <div style=${{flex:1,display:'flex',flexDirection:'column',overflow:'hidden'}}>

        <!-- Chat header -->
        <div style=${{padding:'14px 20px',borderBottom:'1px solid var(--bd)',display:'flex',alignItems:'center',justifyContent:'space-between',background:'var(--sf)',flexShrink:0}}>
          <div style=${{display:'flex',alignItems:'center',gap:12}}>
            <div style=${{width:40,height:40,borderRadius:12,background:'linear-gradient(135deg,#5a5ef7,#a855f7)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:20,boxShadow:'0 4px 14px rgba(90,94,247,.35)'}}>🤖</div>
            <div>
              <div style=${{fontSize:15,fontWeight:800,color:'var(--tx)',letterSpacing:'-.3px'}}>AI Documentation Studio</div>
              <div style=${{fontSize:11,color:'var(--tx3)'}}>Powered by Claude · Chat to create docs, diagrams &amp; specs</div>
            </div>
          </div>
          <div style=${{display:'flex',gap:7}}>
            <button class="btn bg" style=${{fontSize:11,padding:'5px 12px'}} onClick=${clearChat}>🗑 Clear</button>
          </div>
        </div>

        <!-- Messages -->
        <div ref=${chatRef} style=${{flex:1,overflowY:'auto',padding:'20px',display:'flex',flexDirection:'column',gap:16}}>
          ${messages.map(msg=>html`
            <div key=${msg.id} style=${{display:'flex',gap:12,flexDirection:msg.role==='user'?'row-reverse':'row',animation:'fadeIn .3s ease both'}}>

              <!-- Avatar -->
              <div style=${{width:36,height:36,borderRadius:11,flexShrink:0,display:'flex',alignItems:'center',justifyContent:'center',fontSize:16,
                background:msg.role==='user'?`linear-gradient(135deg,${cu.color||'#5a5ef7'},${cu.color||'#5a5ef7'}bb)`:'linear-gradient(135deg,#5a5ef7,#a855f7)',
                boxShadow:msg.role==='user'?'none':'0 3px 12px rgba(255,100,60,.25)',
                fontWeight:800,color:'#fff',fontFamily:'inherit',fontSize:13}}>
                ${msg.role==='user'?(cu.avatar||cu.name?.charAt(0)||'U'):'🤖'}
              </div>

              <!-- Bubble -->
              <div style=${{maxWidth:'78%',flex:1}}>
                ${msg.type==='thinking'?html`
                  <div style=${{display:'inline-flex',alignItems:'center',gap:8,padding:'12px 18px',borderRadius:14,background:'var(--sf)',border:'1px solid var(--bd)',color:'var(--tx3)',fontSize:13}}>
                    <span style=${{width:16,height:16,border:'2px solid var(--bd)',borderTopColor:'var(--ac)',borderRadius:'50%',animation:'sp .7s linear infinite',display:'inline-block'}}></span>
                    Claude is thinking…
                  </div>`:

                msg.type==='error'?html`
                  <div style=${{padding:'14px 18px',borderRadius:14,background:'rgba(239,68,68,.08)',border:'1px solid rgba(239,68,68,.2)',fontSize:13,color:'#f87171',lineHeight:1.6}}>
                    <div dangerouslySetInnerHTML=${{__html:renderMd(msg.content)}}></div>
                  </div>`:

                msg.role==='user'?html`
                  <div style=${{padding:'12px 16px',borderRadius:14,borderBottomRightRadius:4,background:'var(--ac)',color:'#fff',fontSize:13.5,lineHeight:1.6,wordBreak:'break-word'}}>
                    ${msg.content}
                  </div>`:html`

                  <!-- Assistant message -->
                  <div style=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:14,borderBottomLeftRadius:4,overflow:'hidden'}}>
                    <div style=${{padding:'16px 18px',fontSize:13.5,color:'var(--tx2)',lineHeight:1.75,wordBreak:'break-word'}}>
                      <div dangerouslySetInnerHTML=${{__html:renderMd(msg.content)}}></div>
                    </div>
                    ${msg.content&&msg.content.length>50?html`
                      <div style=${{borderTop:'1px solid var(--bd)',padding:'8px 12px',display:'flex',gap:6,flexWrap:'wrap'}}>
                        <button class="btn bg" style=${{fontSize:10,padding:'3px 10px'}} onClick=${()=>copyMsg(msg.content,msg.id)}>
                          ${copied===msg.id?'✓ Copied':'📋 Copy'}
                        </button>
                        <button class="btn bg" style=${{fontSize:10,padding:'3px 10px'}} onClick=${()=>downloadMsg(msg.content,'document')}>⬇ Download .md</button>
                        ${extractMermaid(msg.content)?html`
                          <a href="https://mermaid.live" target="_blank" rel="noopener"
                            style=${{fontSize:10,padding:'3px 10px',borderRadius:100,border:'1px solid rgba(139,92,246,.3)',color:'#a78bfa',background:'rgba(139,92,246,.08)',textDecoration:'none',display:'inline-flex',alignItems:'center'}}>
                            🏗️ Open Diagram ↗
                          </a>`:null}
                      </div>`:null}
                  </div>`}
              </div>
            </div>`)}
          <div ref=${bottomRef}></div>
        </div>

        <!-- Input bar -->
        <div style=${{padding:'14px 20px',borderTop:'1px solid var(--bd)',background:'var(--sf)',flexShrink:0}}>
          <!-- Quick action chips -->
          <div style=${{display:'flex',gap:6,marginBottom:10,flexWrap:'wrap'}}>
            ${[
              {label:'Architecture',icon:'🏗️',q:'Create a Mermaid architecture diagram for my workspace'},
              {label:'Sprint Report',icon:'📊',q:'Generate a sprint status report with task progress'},
              {label:'API Docs',icon:'🔌',q:'Write API documentation for the main endpoints'},
              {label:'Tech Spec',icon:'⚙️',q:'Create a technical specification document'},
            ].map((c,i)=>html`
              <button key=${i} onClick=${()=>send(c.q)} disabled=${sending}
                style=${{display:'flex',alignItems:'center',gap:5,padding:'5px 12px',borderRadius:100,border:'1px solid var(--bd)',background:'var(--sf2)',color:'var(--tx3)',fontSize:11,fontWeight:600,cursor:'pointer',transition:'all .15s',fontFamily:'inherit'}}
                onMouseEnter=${e=>{e.currentTarget.style.borderColor='var(--ac)';e.currentTarget.style.color='var(--ac)';e.currentTarget.style.background='var(--ac4)';}}
                onMouseLeave=${e=>{e.currentTarget.style.borderColor='var(--bd)';e.currentTarget.style.color='var(--tx3)';e.currentTarget.style.background='var(--sf2)';}}>
                ${c.icon} ${c.label}
              </button>`)}
          </div>

          <!-- Text input row -->
          <div style=${{display:'flex',gap:10,alignItems:'flex-end'}}>
            <div style=${{flex:1,position:'relative'}}>
              <textarea ref=${inputRef} value=${input}
                onInput=${e=>{setInput(e.target.value);e.target.style.height='auto';e.target.style.height=Math.min(e.target.scrollHeight,160)+'px';}}
                onKeyDown=${e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send();}}}
                placeholder="Describe what you need… (Enter to send, Shift+Enter for new line)"
                rows=1
                style=${{width:'100%',padding:'12px 16px',borderRadius:14,fontSize:13.5,outline:'none',background:'var(--sf2)',border:'1px solid var(--bd)',color:'var(--tx)',fontFamily:'inherit',resize:'none',lineHeight:1.55,boxSizing:'border-box',maxHeight:'160px',overflow:'hidden auto',transition:'border-color .15s'}}
                onFocus=${e=>e.target.style.borderColor='var(--ac)'}
                onBlur=${e=>e.target.style.borderColor='var(--bd)'}
              ></textarea>
            </div>
            <button onClick=${()=>send()} disabled=${sending||!input.trim()}
              style=${{height:46,width:46,borderRadius:13,border:'none',cursor:sending||!input.trim()?'not-allowed':'pointer',
                background:sending||!input.trim()?'var(--sf3)':'linear-gradient(135deg,#5a5ef7,#a855f7)',
                color:'#fff',fontSize:18,display:'flex',alignItems:'center',justifyContent:'center',flexShrink:0,
                transition:'all .2s',boxShadow:sending||!input.trim()?'none':'0 4px 16px rgba(90,94,247,.45)'}}>
              ${sending
                ?html`<span style=${{width:16,height:16,border:'2px solid rgba(255,255,255,.3)',borderTopColor:'#fff',borderRadius:'50%',animation:'sp .7s linear infinite',display:'block'}}></span>`
                :'↑'}
            </button>
          </div>
          <div style=${{marginTop:7,fontSize:10.5,color:'var(--tx3)',textAlign:'center'}}>
            AI uses your workspace data automatically · Diagrams open in mermaid.live · Press Enter to send
          </div>
        </div>
      </div>
    </div>`;
}


/* ─── AIAssistant floating panel ──────────────────────────────────────────── */
function AIAssistant({cu,projects,tasks,users}){
  const [open,setOpen]=useState(false);const [msgs,setMsgs]=useState([]);const [input,setInput]=useState('');const [busy,setBusy]=useState(false);const ref=useRef(null);const iref=useRef(null);

  useEffect(()=>{if(ref.current)ref.current.scrollTop=ref.current.scrollHeight;},[msgs]);

  const QUICK=[
    {label:'📊 EOD Report',msg:'Generate an end-of-day status report for all projects'}, {label:'🔴 Blocked tasks',msg:'What tasks are blocked and need attention?'}, {label:'📈 Progress summary',msg:'Give me a quick summary of overall project progress'}, {label:'⚠️ Overdue',msg:'Are there any overdue tasks?'}, ];

  const send=async(text)=>{
    const m=text||input.trim();
    if(!m||busy)return;
    setInput('');
    const userMsg={role:'user',content:m};
    setMsgs(prev=>[...prev,userMsg]);
    setBusy(true);
    const history=[...msgs,userMsg];
    const r=await api.post('/api/ai/chat',{message:m,history:history.slice(-10)});
    setBusy(false);
    if(r.error&&r.error==='NO_KEY'){
      setMsgs(prev=>[...prev,{role:'ai',content:'⚙️ No API key configured.\n\nGo to **Settings → AI Assistant** and paste your Anthropic API key to get started.',actions:[]}]);
    } else if(r.error){
      setMsgs(prev=>[...prev,{role:'ai',content:'Error: '+(r.message||r.error),actions:[]}]);
    } else {
      setMsgs(prev=>[...prev,{role:'ai',content:r.message||'',actions:r.actions||[]}]);
    }
  };

  const actionLabel=a=>{
    if(a.type==='create_task')return'✅ Created task: '+a.title+' ('+a.id+')';
    if(a.type==='update_task')return'✏️ Updated task: '+a.id;
    if(a.type==='create_project')return'📁 Created project: '+a.name;
    if(a.type==='eod_report')return'📊 EOD Report generated';
    if(a.type==='error')return'⚠️ Error: '+a.message;
    return'✓ '+a.type;
  };

  return html`
    <button class="ai-btn" onClick=${()=>setOpen(!open)} title="AI Assistant">
      ${open?'✕':'🤖'}
    </button>
    ${open?html`
      <div class="ai-panel">
        <div style=${{padding:'14px 16px',borderBottom:'1px solid var(--bd)',display:'flex',alignItems:'center',gap:10,flexShrink:0}}>
          <div style=${{width:32,height:32,background:'linear-gradient(135deg,var(--ac),var(--pu))',borderRadius:9,display:'flex',alignItems:'center',justifyContent:'center',fontSize:16,boxShadow:'0 2px 8px rgba(0,113,227,0.3)'}}>🤖</div>
          <div style=${{flex:1}}>
            <div style=${{fontSize:14,fontWeight:700,color:'var(--tx)'}}>AI Assistant</div>
            <div style=${{fontSize:10,color:'var(--tx3)'}}>Powered by Claude</div>
          </div>
          ${msgs.length>0?html`<button class="btn bg" style=${{fontSize:10,padding:'4px 9px'}} onClick=${()=>setMsgs([])}>Clear</button>`:null}
        </div>

        <div ref=${ref} style=${{flex:1,overflowY:'auto',padding:'12px',display:'flex',flexDirection:'column',gap:10}}>
          ${msgs.length===0?html`
            <div style=${{paddingTop:8}}>
              <p style=${{fontSize:12,color:'var(--tx2)',marginBottom:12,textAlign:'center'}}>Ask me anything about your projects, or try a quick action:</p>
              <div style=${{display:'flex',flexDirection:'column',gap:6}}>
                ${QUICK.map(q=>html`<button key=${q.label} class="btn bg" style=${{justifyContent:'flex-start',fontSize:12,padding:'8px 12px',textAlign:'left'}} onClick=${()=>send(q.msg)}>${q.label}</button>`)}
              </div>
            </div>`:null}
          ${msgs.map((m,i)=>html`
            <div key=${i}>
              ${m.role==='user'?html`<div class="ai-msg-user">${m.content}</div>`:null}
              ${m.role==='ai'?html`
                <div class="ai-msg-ai">${m.content}</div>
                ${(m.actions||[]).length>0?html`<div style=${{display:'flex',flexDirection:'column',gap:5,marginTop:6}}>
                  ${(m.actions||[]).map((a,j)=>html`<div key=${j} class="ai-action">${actionLabel(a)}${a.type==='eod_report'&&a.summary?html`<pre style=${{marginTop:6,fontSize:10,whiteSpace:'pre-wrap',color:'var(--gn)',lineHeight:1.6}}>${a.summary}</pre>`:null}</div>`)}
                </div>`:null}`:null}
            </div>`)}
          ${busy?html`<div class="ai-msg-ai pulse" style=${{display:'flex',gap:4,alignItems:'center'}}><span style=${{fontSize:16}}>🤖</span><span style=${{fontSize:12}}>Thinking...</span><span class="spin" style=${{width:12,height:12,borderWidth:2}}></span></div>`:null}
        </div>

        <div style=${{padding:'10px 12px',borderTop:'1px solid var(--bd)',flexShrink:0}}>
          <div style=${{display:'flex',gap:7}}>
            <input ref=${iref} class="inp" style=${{flex:1,fontSize:13}} placeholder="Ask about your projects..." value=${input}
              onInput=${e=>setInput(e.target.value)} onKeyDown=${e=>e.key==='Enter'&&!e.shiftKey&&send()}
              disabled=${busy}/>
            <button class="btn bp" style=${{padding:'8px 12px',flexShrink:0}} onClick=${()=>send()} disabled=${!input.trim()||busy}>➤</button>
          </div>
        </div>
      </div>`:null}`;
}

/* ─── Toast System ────────────────────────────────────────────────────────── */
const TOAST_CFG={
  dm:      {icon:'💬', color:'var(--ac)', bg:'var(--ac3)', nav:'dm'}, call:    {icon:'📞', color:'var(--gn)', bg:'rgba(62,207,110,.12)', nav:'dashboard'}, task_assigned:{icon:'✅',color:'var(--cy)', bg:'rgba(34,211,238,.1)', nav:'tasks'}, status_change:{icon:'🔄',color:'var(--pu)', bg:'rgba(167,139,250,.1)',nav:'tasks'}, comment: {icon:'💬', color:'var(--pu)', bg:'rgba(167,139,250,.1)', nav:'tasks'}, deadline:{icon:'⏰', color:'var(--am)', bg:'rgba(245,158,11,.1)', nav:'tasks'}, project_added:{icon:'📁',color:'var(--or)',bg:'rgba(251,146,60,.1)',nav:'projects'}, reminder:{icon:'⏰', color:'var(--rd)', bg:'rgba(255,68,68,.1)', nav:'reminders'}, message: {icon:'#️⃣', color:'#a78bfa', bg:'rgba(167,139,250,.1)', nav:'messages'}, default: {icon:'🔔', color:'var(--ac)', bg:'var(--ac3)', nav:'notifs'},
};

function ToastStack({toasts,onDismiss,onNav}){
  return html`
    <div class="toast-stack">
      ${toasts.map(t=>{
        const cfg=TOAST_CFG[t.type]||TOAST_CFG.default;
        return html`
          <div key=${t.id} class=${'toast'+(t.leaving?' leaving':'')}
            onClick=${()=>{onDismiss(t.id);onNav&&onNav(cfg.nav);}}>
            <div class="toast-bar" style=${{width:t.progress+'%',background:cfg.color}}></div>
            <div class="toast-icon" style=${{background:cfg.bg,color:cfg.color}}>${cfg.icon}</div>
            <div class="toast-body">
              <div class="toast-title">${t.title}</div>
              <div class="toast-msg">${t.body}</div>
              <div class="toast-time">${t.timeStr}</div>
            </div>
            <button class="toast-close" onClick=${e=>{e.stopPropagation();onDismiss(t.id);}}>✕</button>
          </div>`;
      })}
    </div>`;
}

/* ─── ReminderModal ───────────────────────────────────────────────────────── */
function ReminderModal({task,onClose,onSaved}){
  const [remindAt,setRemindAt]=useState('');
  const [minBefore,setMinBefore]=useState('10');
  const [saving,setSaving]=useState(false);
  const [err,setErr]=useState('');

  useEffect(()=>{
    if(task&&task.due){
      try{
        const d=new Date(task.due);
        if(!isNaN(d)){
          d.setHours(9,0,0,0);
          setRemindAt(d.toISOString().slice(0,16));
        }
      }catch(e){}
    } else {
      const d=new Date();d.setHours(d.getHours()+1,0,0,0);
      setRemindAt(d.toISOString().slice(0,16));
    }
  },[task]);

  const save=async()=>{
    if(!remindAt){setErr('Please set a reminder date and time.');return;}
    const remindUtc=new Date(remindAt);
    const alertAt=new Date(remindUtc.getTime()-parseInt(minBefore)*60000);
    setSaving(true);
    const r=await api.post('/api/reminders',{
      task_id:task?task.id:'', task_title:task?task.title:'Reminder', remind_at:alertAt.toISOString(), minutes_before:parseInt(minBefore), });
    setSaving(false);
    if(r.error){setErr(r.error);return;}
    playSound('reminder');onSaved&&onSaved(r);
    onClose();
  };

  return html`
    <div class="ov" onClick=${e=>e.target===e.currentTarget&&onClose()}>
      <div class="mo" style=${{maxWidth:420}}>
        <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:18}}>
          <h2 style=${{fontSize:17,fontWeight:700,color:'var(--tx)'}}>⏰ Set Reminder</h2>
          <button class="btn bg" style=${{padding:'7px 10px'}} onClick=${onClose}>✕</button>
        </div>
        ${task?html`<div style=${{padding:'10px 13px',background:'var(--sf2)',borderRadius:9,border:'1px solid var(--bd)',marginBottom:16,fontSize:13,color:'var(--tx2)'}}>
          Task: <b style=${{color:'var(--tx)'}}>${task.title}</b>
        </div>`:null}
        <div style=${{display:'grid',gap:14}}>
          <div>
            <label class="lbl">Remind me at (date & time)</label>
            <input class="inp" type="datetime-local" value=${remindAt}
              onChange=${e=>setRemindAt(e.target.value)}/>
          </div>
          <div>
            <label class="lbl">Notify me how early?</label>
            <select class="inp" value=${minBefore} onChange=${e=>setMinBefore(e.target.value)}>
              <option value="5">5 minutes before</option>
              <option value="10">10 minutes before</option>
              <option value="15">15 minutes before</option>
              <option value="30">30 minutes before</option>
              <option value="60">1 hour before</option>
              <option value="0">At exact time</option>
            </select>
          </div>
        </div>
        ${err?html`<p style=${{color:'var(--rd)',fontSize:12,marginTop:10}}>${err}</p>`:null}
        <div style=${{display:'flex',gap:9,justifyContent:'flex-end',marginTop:18}}>
          <button class="btn bg" onClick=${onClose}>Cancel</button>
          <button class="btn bp" onClick=${save} disabled=${saving}>
            ${saving?html`<span class="spin"></span>`:'⏰ Set Reminder'}
          </button>
        </div>
      </div>
    </div>`;
}

/* ─── Notification Utilities ──────────────────────────────────────────────── */
const NOTIF_ICON="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%232563eb'/%3E%3Ccircle cx='32' cy='32' r='9' fill='white'/%3E%3Ccircle cx='32' cy='11' r='6' fill='white' opacity='.95'/%3E%3Ccircle cx='51' cy='43' r='6' fill='white' opacity='.95'/%3E%3Ccircle cx='13' cy='43' r='6' fill='white' opacity='.95'/%3E%3Cline x1='32' y1='17' x2='32' y2='23' stroke='white' stroke-width='3.5' stroke-linecap='round'/%3E%3Cline x1='46' y1='40' x2='40' y2='36' stroke='white' stroke-width='3.5' stroke-linecap='round'/%3E%3Cline x1='18' y1='40' x2='24' y2='36' stroke='white' stroke-width='3.5' stroke-linecap='round'/%3E%3C/svg%3E";

function updateBadge(count){
  try{
    if(navigator.setAppBadge){
      if(count>0)navigator.setAppBadge(count);
      else navigator.clearAppBadge();
    }
  }catch(e){}
  try{
    const canvas=document.createElement('canvas');
    canvas.width=32;canvas.height=32;
    const ctx=canvas.getContext('2d');
    const img=new Image();
    img.onload=()=>{
      ctx.drawImage(img,0,0,32,32);
      if(count>0){
        ctx.fillStyle='#ef4444';
        ctx.beginPath();ctx.arc(24,8,9,0,2*Math.PI);ctx.fill();
        ctx.fillStyle='#fff';ctx.font='bold 10px Inter,sans-serif';
        ctx.textAlign='center';ctx.textBaseline='middle';
        ctx.fillText(count>9?'9+':String(count),24,8);
      }
      const links=document.querySelectorAll("link[rel*='icon']");
      links.forEach(l=>{l.href=canvas.toDataURL();});
      document.title=count>0?'('+count+') VEWIT':'VEWIT';
    };
    img.src=NOTIF_ICON;
  }catch(e){}
}

async function requestNotifPermission(){
  if(window.__TAURI__){
    try{
      const {isPermissionGranted,requestPermission,sendNotification}=window.__TAURI__.notification;
      let ok=await isPermissionGranted();
      if(!ok){const p=await requestPermission();ok=(p==='granted');}
      if(ok)await sendNotification({title:'VEWIT',body:'Notifications enabled.'});
      return;
    }catch(e){}
  }
  if('Notification' in window&&Notification.permission==='default'){
    const p=await Notification.requestPermission();
    if(p==='granted'){
      if(window._pfSWReg){
        try{
          const r=await fetch('/api/push/vapid-key',{credentials:'include'});
          const d=await r.json();
          if(d.publicKey){
            const padding='='.repeat((4-d.publicKey.length%4)%4);
            const base64=(d.publicKey+padding).replace(/-/g,'+').replace(/_/g,'/');
            const raw=window.atob(base64);
            const key=new Uint8Array(raw.length);
            for(let i=0;i<raw.length;i++) key[i]=raw.charCodeAt(i);
            const sub=await window._pfSWReg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:key});
            window._pfPushSub=sub;
            const sj=sub.toJSON();
            fetch('/api/push/subscribe',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({endpoint:sj.endpoint,keys:sj.keys})}).catch(()=>{});
          }
        }catch(e){}
      }
      new Notification('VEWIT',{body:'Desktop notifications enabled! You\'ll be notified for tasks, projects & reminders.',icon:NOTIF_ICON,silent:true});
    }
  }
}

async function showBrowserNotif(title,body,onClick,opts={}){
  const tag=opts.tag||'pf-'+Date.now();
  if(onClick){window._pfNotifHandlers=window._pfNotifHandlers||{};window._pfNotifHandlers[tag]=onClick;}
  if(window.__TAURI__){
    try{
      const {isPermissionGranted,requestPermission,sendNotification}=window.__TAURI__.notification;
      let ok=await isPermissionGranted();
      if(!ok){const p=await requestPermission();ok=(p==='granted');}
      if(ok){await sendNotification({title,body});return;}
    }catch(e){}
  }
  if(!('Notification' in window)||Notification.permission!=='granted')return;
  if(window._pfSWReg){
    try{
      await window._pfSWReg.showNotification(title,{body,icon:NOTIF_ICON,badge:NOTIF_ICON,tag,vibrate:[200,100,200],requireInteraction:opts.requireInteraction||false,data:{tag}});
      return;
    }catch(e){}
  }
  try{
    const n=new Notification(title,{body,icon:NOTIF_ICON,badge:NOTIF_ICON,tag,requireInteraction:opts.requireInteraction||false,silent:false});
    if(onClick)n.onclick=()=>{window.focus();onClick();n.close();};
    if(!opts.requireInteraction)setTimeout(()=>n.close(),6000);
  }catch(e){}
}

/* ─── RemindersView ──────────────────────────────────────────────────────── */
function RemindersView({cu,tasks,projects,onSetReminder,onReload,initialView}){
  const [reminders,setReminders]=useState([]);
  const [busy,setBusy]=useState(true);
  const [showAdd,setShowAdd]=useState(false);
  const [addTaskId,setAddTaskId]=useState('');
  const [addCustomTitle,setAddCustomTitle]=useState('');
  const [addDate,setAddDate]=useState('');
  const [addTime,setAddTime]=useState('');
  const [addMins,setAddMins]=useState(10);
  const [saving,setSaving]=useState(false);
  const [addProjId,setAddProjId]=useState('');
  const [showCompleted,setShowCompleted]=useState(false);
  const [editReminder,setEditReminder]=useState(null);
  const [editDate,setEditDate]=useState('');
  const [editTime,setEditTime]=useState('');
  const [editMins,setEditMins]=useState(10);
  const now=new Date();
  const filteredTasks=addProjId?safe(tasks).filter(t=>t.project===addProjId):safe(tasks);

  const load=useCallback(async()=>{
    setBusy(true);
    const d=await api.get('/api/reminders?include_fired=1');
    setReminders(Array.isArray(d)?d:[]);
    setBusy(false);
  },[]);

  useEffect(()=>{load();},[load]);

  const del=async id=>{await api.del('/api/reminders/'+id);load();onReload&&onReload();};

  const openEdit=(r)=>{
    setEditReminder(r);
    const d=new Date(r.remind_at);
    const pad=n=>String(n).padStart(2,'0');
    setEditDate(d.getFullYear()+'-'+pad(d.getMonth()+1)+'-'+pad(d.getDate()));
    setEditTime(pad(d.getHours())+':'+pad(d.getMinutes()));
    setEditMins(r.minutes_before||10);
  };

  const saveEdit=async()=>{
    if(!editDate||!editTime)return;
    setSaving(true);
    const dt=new Date(editDate+'T'+editTime);
    await api.put('/api/reminders/'+editReminder.id,{remind_at:dt.toISOString(),minutes_before:editMins,task_title:editReminder.task_title});
    setSaving(false);setEditReminder(null);load();onReload&&onReload();
  };

  const saveReminder=async()=>{
    const realTaskId=(addTaskId&&addTaskId!=='__custom__')?addTaskId:'';
    const titleToUse=realTaskId
      ?(safe(tasks).find(t=>t.id===realTaskId)||{title:addCustomTitle.trim()||'Reminder'}).title
      :(addCustomTitle.trim()||'Reminder');
    if(!titleToUse||!addDate||!addTime)return;
    setSaving(true);
    const dt=new Date(addDate+'T'+addTime);
    await api.post('/api/reminders',{task_id:realTaskId,task_title:titleToUse,remind_at:dt.toISOString(),minutes_before:addMins});
    setSaving(false);
    setShowAdd(false);
    setAddTaskId('');setAddCustomTitle('');setAddDate('');setAddTime('');setAddMins(10);
    load();
  };

  const active=reminders.filter(r=>!r.fired);
  const completed=reminders.filter(r=>r.fired);
  const upcoming=active.filter(r=>new Date(r.remind_at)>=now).sort((a,b)=>new Date(a.remind_at)-new Date(b.remind_at));
  const overdue=active.filter(r=>new Date(r.remind_at)<now).sort((a,b)=>new Date(b.remind_at)-new Date(a.remind_at));

  const fmtRem=dt=>{
    const d=new Date(dt);
    const diff=d-now;
    if(diff<0)return{label:'Overdue',cls:'var(--rd)',bg:'rgba(248,113,113,.12)'};
    if(diff<3600000)return{label:'< 1 hr',cls:'var(--am)',bg:'rgba(251,191,36,.12)'};
    if(diff<86400000)return{label:'Today',cls:'var(--cy)',bg:'rgba(34,211,238,.12)'};
    if(diff<172800000)return{label:'Tomorrow',cls:'var(--gn)',bg:'rgba(74,222,128,.12)'};
    return{label:d.toLocaleDateString('en-US',{month:'short',day:'numeric'}),cls:'var(--tx2)',bg:'var(--sf2)'};
  };

  const statCards=[
    {label:'Upcoming',val:upcoming.length,color:'var(--cy)',bg:'rgba(34,211,238,.1)',icon:'⚡'}, {label:'Overdue',val:overdue.length,color:'var(--rd)',bg:'rgba(248,113,113,.1)',icon:'🚨'}, {label:'Completed',val:completed.length,color:'var(--gn)',bg:'rgba(74,222,128,.1)',icon:'✅'}, {label:'Today',val:active.filter(r=>{const d=new Date(r.remind_at);return d.toDateString()===now.toDateString();}).length,color:'#1d4ed8',bg:'rgba(29,78,216,0.10)',icon:'📅'}, ];

  return html`
    <div class="fi" style=${{height:'100%',overflowY:'auto',padding:'18px 22px',background:'var(--bg)'}}>

      <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:16}}>
        <div style=${{fontSize:13,color:'var(--tx2)'}}>Set reminders for your tasks — get notified with sound before they're due.</div>
        <div style=${{display:'flex',gap:8}}>
          <button class=${'btn '+(showCompleted?'bp':'bg')} style=${{fontSize:12}} onClick=${()=>setShowCompleted(p=>!p)}>
            ${showCompleted?'Hide Completed':'Show Completed ('+completed.length+')'}
          </button>
          <button class="btn bp" style=${{fontSize:12}} onClick=${()=>setShowAdd(true)}>+ Add Reminder</button>
        </div>
      </div>

      <div style=${{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:12,marginBottom:18}}>
        ${statCards.map(s=>{
          return html`
            <div key=${s.label} style=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:12,padding:'14px 16px',display:'flex',alignItems:'center',gap:12}}>
              <div style=${{width:40,height:40,borderRadius:10,background:s.bg,display:'flex',alignItems:'center',justifyContent:'center',fontSize:18}}>${s.icon}</div>
              <div>
                <div style=${{fontSize:24,fontWeight:900,color:s.color,lineHeight:1}}>${s.val}</div>
                <div style=${{fontSize:11,color:'var(--tx2)',marginTop:2,fontWeight:500,fontWeight:600}}>${s.label}</div>
              </div>
            </div>`;
        })}
      </div>

      ${showAdd?html`
        <div class="ov" onClick=${e=>e.target===e.currentTarget&&setShowAdd(false)}>
          <div class="mo fi" style=${{maxWidth:600}}>
                        <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:20}}>
              <div>
                <h2 style=${{fontSize:17,fontWeight:700,color:'var(--tx)',display:'flex',alignItems:'center',gap:8}}>
                  <span style=${{width:32,height:32,borderRadius:9,background:'rgba(251,191,36,.15)',border:'1px solid rgba(251,191,36,.3)',display:'inline-flex',alignItems:'center',justifyContent:'center',fontSize:16}}>⏰</span>
                  Add Reminder
                </h2>
                <p style=${{fontSize:11,color:'var(--tx3)',marginTop:3,marginLeft:40}}>Fill in one or both sections, then pick a date &amp; time.</p>
              </div>
              <button class="btn bg" style=${{padding:'7px 10px'}} onClick=${()=>{setShowAdd(false);setAddCustomTitle('');setAddTaskId('');}}>✕</button>
            </div>

                        <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr',gap:14,marginBottom:16}}>

                            <div style=${{background:'var(--sf2)',borderRadius:14,padding:'16px',border:'2px solid '+(addTaskId&&addTaskId!=='__custom__'?'var(--ac)':'var(--bd)'),transition:'border-color .15s',position:'relative',overflow:'hidden'}}>
                <div style=${{position:'absolute',top:0,left:0,right:0,height:3,background:'linear-gradient(90deg,var(--ac),var(--cy))',borderRadius:'14px 14px 0 0',opacity:addTaskId&&addTaskId!=='__custom__'?1:.3,transition:'opacity .15s'}}></div>
                <div style=${{display:'flex',alignItems:'center',gap:7,marginBottom:14}}>
                  <div style=${{width:28,height:28,borderRadius:7,background:'rgba(90,140,255,.10)',border:'1px solid rgba(90,140,255,.25)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:14}}>📋</div>
                  <div>
                    <div style=${{fontSize:12,fontWeight:700,color:'var(--tx)'}}>Project Reminder</div>
                    <div style=${{fontSize:10,color:'var(--tx3)'}}>Linked to a task</div>
                  </div>
                </div>
                <div style=${{display:'flex',flexDirection:'column',gap:10}}>
                  <div>
                    <label class="lbl">Project <span style=${{color:'var(--tx3)',fontWeight:400,textTransform:'none',fontSize:9}}>(filter tasks)</span></label>
                    <select class="inp" style=${{fontSize:12}} value=${addProjId} onChange=${e=>{setAddProjId(e.target.value);setAddTaskId('');}}>
                      <option value="">— All projects —</option>
                      ${safe(projects).map(p=>html`<option key=${p.id} value=${p.id}>${p.name}</option>`)}
                    </select>
                  </div>
                  <div>
                    <label class="lbl">Task <span style=${{color:'var(--tx3)',fontWeight:400,textTransform:'none',fontSize:9}}>(optional)</span></label>
                    <select class="inp" style=${{fontSize:12}} value=${addTaskId==='__custom__'?'':addTaskId} onChange=${e=>{setAddTaskId(e.target.value);if(e.target.value)setAddCustomTitle('');}}>
                      <option value="">— Select a task (or set custom below) —</option>
                      ${filteredTasks.map(t=>html`<option key=${t.id} value=${t.id}>${t.title}</option>`)}
                    </select>
                  </div>
                  ${addTaskId&&addTaskId!=='__custom__'?html`
                    <div style=${{padding:'7px 10px',background:'rgba(90,140,255,.07)',borderRadius:8,border:'1px solid rgba(90,140,255,.18)',fontSize:11,color:'var(--tx2)',display:'flex',alignItems:'center',gap:6}}>
                      <span style=${{color:'var(--ac)'}}>✓</span>
                      <span>Linked: <b style=${{color:'var(--tx)'}}>${(safe(tasks).find(t=>t.id===addTaskId)||{title:''}).title}</b></span>
                    </div>`:null}
                </div>
              </div>

                            <div style=${{background:'var(--sf2)',borderRadius:14,padding:'16px',border:'2px solid '+(addTaskId==='__custom__'||(!addTaskId&&addCustomTitle.trim())?'var(--pu)':'var(--bd)'),transition:'border-color .15s',position:'relative',overflow:'hidden'}}>
                <div style=${{position:'absolute',top:0,left:0,right:0,height:3,background:'linear-gradient(90deg,var(--pu),var(--pk))',borderRadius:'14px 14px 0 0',opacity:addTaskId==='__custom__'||(!addTaskId&&addCustomTitle.trim())?1:.3,transition:'opacity .15s'}}></div>
                <div style=${{display:'flex',alignItems:'center',gap:7,marginBottom:14}}>
                  <div style=${{width:28,height:28,borderRadius:7,background:'rgba(167,139,250,.12)',border:'1px solid rgba(167,139,250,.25)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:14}}>✏️</div>
                  <div>
                    <div style=${{fontSize:12,fontWeight:700,color:'var(--tx)'}}>Custom Reminder</div>
                    <div style=${{fontSize:10,color:'var(--tx3)'}}>Standalone note or meeting</div>
                  </div>
                </div>
                <div>
                  <label class="lbl">Reminder Title <span style=${{color:'var(--rd)',fontWeight:600}}>*</span></label>
                  <input class="inp" style=${{fontSize:12}} value=${addCustomTitle}
                    onInput=${e=>{setAddCustomTitle(e.target.value);if(e.target.value)setAddTaskId('__custom__');else if(addTaskId==='__custom__')setAddTaskId('');}}
                    placeholder="e.g. Team standup, Review designs, Call client…"/>
                </div>
                <div style=${{marginTop:10,padding:'8px 10px',background:'rgba(167,139,250,.07)',borderRadius:8,border:'1px solid rgba(167,139,250,.18)',fontSize:11,color:'var(--tx3)',lineHeight:1.5}}>
                  💡 Use this for meetings, calls, or any non-task reminder.
                </div>
              </div>
            </div>

                        <div style=${{background:'var(--sf2)',borderRadius:14,padding:'16px',border:'1px solid var(--bd)',marginBottom:16}}>
              <div style=${{display:'flex',alignItems:'center',gap:7,marginBottom:14}}>
                <div style=${{width:28,height:28,borderRadius:7,background:'rgba(34,211,238,.12)',border:'1px solid rgba(34,211,238,.25)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:14}}>📅</div>
                <div style=${{fontSize:12,fontWeight:700,color:'var(--tx)'}}>Date, Time &amp; Notification</div>
              </div>
              <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12,marginBottom:12}}>
                <div>
                  <label class="lbl">Date <span style=${{color:'var(--rd)',fontWeight:600}}>*</span></label>
                  <input class="inp" type="date" value=${addDate} onChange=${e=>setAddDate(e.target.value)} min=${new Date().toISOString().split('T')[0]} onFocus=${e=>{if(!e.target.value)e.target.value=new Date().toISOString().split('T')[0];}}/>
                </div>
                <div>
                  <label class="lbl">Time <span style=${{color:'var(--rd)',fontWeight:600}}>*</span></label>
                  <input class="inp" type="time" value=${addTime} onChange=${e=>setAddTime(e.target.value)}/>
                </div>
              </div>
              <div>
                <label class="lbl">Notify me before</label>
                <div style=${{display:'flex',gap:6,flexWrap:'wrap',marginTop:6}}>
                  ${[5,10,15,30,60].map(m=>html`
                    <button key=${m} onClick=${()=>setAddMins(m)}
                      style=${{padding:'6px 14px',borderRadius:100,fontSize:12,fontWeight:700,border:'2px solid '+(addMins===m?'var(--ac)':'var(--bd)'),background:addMins===m?'var(--ac)':'transparent',color:addMins===m?'var(--ac-tx)':'var(--tx2)',cursor:'pointer',transition:'all .12s'}}>
                      ${m<60?m+' min':'1 hr'}
                    </button>`)}
                </div>
              </div>
              <div style=${{marginTop:12,background:'rgba(90,140,255,.06)',borderRadius:9,padding:'10px 13px',fontSize:12,color:'var(--tx2)',border:'1px solid rgba(90,140,255,.15)',display:'flex',alignItems:'center',gap:8}}>
                <span style=${{fontSize:16}}>🔔</span>
                <span>You'll get a browser notification + sound <b style=${{color:'#1d4ed8'}}>${addMins} min</b> before the reminder time.</span>
              </div>
            </div>

            <div style=${{display:'flex',gap:9,justifyContent:'flex-end'}}>
              <button class="btn bg" onClick=${()=>{setShowAdd(false);setAddCustomTitle('');setAddTaskId('');}}>Cancel</button>
              <button class="btn bp" style=${{minWidth:120}} onClick=${saveReminder}
                disabled=${saving||(!addTaskId&&!addCustomTitle.trim())||!addDate||!addTime}>
                ${saving?html`<span class="spin"></span>`:'⏰ Set Reminder'}
              </button>
            </div>
          </div>
        </div>`:null}

      <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr',gap:16}}>
        <div>
          <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:10}}>
            <span style=${{fontWeight:700,fontSize:13,color:'var(--tx)'}}>⚡ Upcoming</span>
            <span class="tx3-11">${upcoming.length} reminder${upcoming.length!==1?'s':''}</span>
          </div>
          ${busy?html`<div class="spin" style=${{margin:'20px auto',display:'block'}}></div>`:null}
          ${!busy&&upcoming.length===0?html`
            <div style=${{textAlign:'center',padding:'28px 16px',color:'var(--tx3)',fontSize:13,background:'var(--sf)',borderRadius:10,border:'1px solid var(--bd)'}}>
              <div style=${{fontSize:28,marginBottom:8}}>✅</div>
              <div>No upcoming reminders</div>
            </div>`:null}
          <div style=${{display:'flex',flexDirection:'column',gap:8}}>
            ${upcoming.map(r=>{
              const ft=fmtRem(r.remind_at);
              return html`
                <div key=${r.id} style=${{display:'flex',gap:10,padding:'11px 13px',background:'var(--sf)',borderRadius:10,border:'1px solid var(--bd)',alignItems:'center'}}>
                  <div style=${{width:36,height:36,borderRadius:9,background:'rgba(251,191,36,.1)',border:'1px solid rgba(251,191,36,.2)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:16,flexShrink:0}}>⏰</div>
                  <div style=${{flex:1,minWidth:0}}>
                    <div style=${{fontSize:12,fontWeight:700,color:'var(--tx)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',marginBottom:3}}>${r.task_title}</div>
                    <div style=${{display:'flex',gap:6,alignItems:'center'}}>
                      <span style=${{fontSize:10,padding:'1px 6px',borderRadius:4,background:ft.bg,color:ft.cls,fontWeight:700}}>${ft.label}</span>
                      <span class="mono-10">${new Date(r.remind_at).toLocaleString('en-US',{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}</span>
                      ${r.minutes_before>0?html`<span style=${{fontSize:10,color:'var(--am)'}}>🔔 ${r.minutes_before}min before</span>`:null}
                    </div>
                  </div>
                  <button class="btn bg" title="Edit" style=${{fontSize:11,padding:'4px 8px',flexShrink:0,marginRight:4}} onClick=${()=>openEdit(r)}>✏️</button>
                  <button class="btn brd" style=${{fontSize:10,padding:'4px 8px',flexShrink:0}} onClick=${()=>del(r.id)}>✕</button>
                </div>`;
            })}
          </div>
        </div>
        <div>
          <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:10}}>
            <span style=${{fontWeight:700,fontSize:13,color:'var(--rd)'}}>🚨 Overdue</span>
            <span class="tx3-11">${overdue.length} past due</span>
          </div>
          ${!busy&&overdue.length===0?html`
            <div style=${{textAlign:'center',padding:'28px 16px',color:'var(--tx3)',fontSize:13,background:'var(--sf)',borderRadius:10,border:'1px solid var(--bd)'}}>
              <div style=${{fontSize:28,marginBottom:8}}>🎉</div>
              <div>Nothing overdue!</div>
            </div>`:null}
          <div style=${{display:'flex',flexDirection:'column',gap:8}}>
            ${overdue.map(r=>html`
              <div key=${r.id} style=${{display:'flex',gap:10,padding:'11px 13px',background:'rgba(248,113,113,.03)',borderRadius:10,border:'1px solid rgba(248,113,113,.15)',alignItems:'center'}}>
                <div style=${{width:36,height:36,borderRadius:9,background:'rgba(248,113,113,.1)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:16,flexShrink:0}}>⚠️</div>
                <div style=${{flex:1,minWidth:0}}>
                  <div style=${{fontSize:12,fontWeight:700,color:'var(--tx)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',marginBottom:3}}>${r.task_title}</div>
                  <span style=${{fontSize:10,color:'var(--rd)',fontFamily:'monospace'}}>${new Date(r.remind_at).toLocaleString('en-US',{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}</span>
                </div>
                <button class="btn brd" style=${{fontSize:10,padding:'4px 8px',flexShrink:0}} onClick=${()=>del(r.id)}>✕</button>
              </div>`)}
          </div>
        </div>
      </div>

      ${showCompleted&&completed.length>0?html`
        <div style=${{marginTop:20}}>
          <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:10}}>
            <span style=${{fontWeight:700,fontSize:13,color:'var(--gn)'}}>✅ Completed Reminders</span>
            <span class="tx3-11">${completed.length} done</span>
          </div>
          <div style=${{display:'flex',flexDirection:'column',gap:8}}>
            ${completed.map(r=>html`
              <div key=${r.id} style=${{display:'flex',gap:10,padding:'10px 13px',background:'rgba(74,222,128,.04)',borderRadius:10,border:'1px solid rgba(74,222,128,.15)',alignItems:'center',opacity:.75}}>
                <div style=${{width:32,height:32,borderRadius:8,background:'rgba(74,222,128,.1)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:14,flexShrink:0}}>✅</div>
                <div style=${{flex:1,minWidth:0}}>
                  <div style=${{fontSize:12,fontWeight:600,color:'var(--tx)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',textDecoration:'line-through',opacity:.7}}>${r.task_title}</div>
                  <span class="mono-10">${new Date(r.remind_at).toLocaleString('en-US',{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}</span>
                </div>
                <button class="btn brd" style=${{fontSize:10,padding:'4px 8px',flexShrink:0}} onClick=${()=>del(r.id)}>✕</button>
              </div>`)}
          </div>
        </div>`:null}

      ${editReminder?html`
        <div class="ov" onClick=${e=>e.target===e.currentTarget&&setEditReminder(null)}>
          <div class="mo fi" style=${{maxWidth:420}}>
            <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:18}}>
              <h2 style=${{fontSize:16,fontWeight:700,color:'var(--tx)'}}>✏️ Edit Reminder</h2>
              <button class="btn bg" style=${{padding:'7px 10px'}} onClick=${()=>setEditReminder(null)}>✕</button>
            </div>
            <div style=${{marginBottom:12,padding:'10px 13px',background:'var(--sf2)',borderRadius:9,border:'1px solid var(--bd)'}}>
              <div style=${{fontSize:13,fontWeight:600,color:'var(--tx)'}}>${editReminder.task_title}</div>
            </div>
            <div style=${{display:'flex',flexDirection:'column',gap:13}}>
              <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr',gap:11}}>
                <div>
                  <label class="lbl">Date *</label>
                  <input class="inp" type="date" value=${editDate} onChange=${e=>setEditDate(e.target.value)} onFocus=${e=>{if(!e.target.value)e.target.value=new Date().toISOString().split('T')[0];}}/>
                </div>
                <div>
                  <label class="lbl">Time *</label>
                  <input class="inp" type="time" value=${editTime} onChange=${e=>setEditTime(e.target.value)}/>
                </div>
              </div>
              <div>
                <label class="lbl">Notify me before</label>
                <div style=${{display:'flex',gap:8,flexWrap:'wrap',marginTop:4}}>
                  ${[5,10,15,30,60].map(m=>html`
                    <button key=${m} class=${'chip'+(editMins===m?' on':'')} onClick=${()=>setEditMins(m)} style=${{fontSize:12,padding:'5px 12px'}}>
                      ${m<60?m+' min':'1 hr'}
                    </button>`)}
                </div>
              </div>
              <div style=${{display:'flex',gap:9,justifyContent:'flex-end',paddingTop:4}}>
                <button class="btn bg" onClick=${()=>setEditReminder(null)}>Cancel</button>
                <button class="btn bp" onClick=${saveEdit} disabled=${saving||!editDate||!editTime}>
                  ${saving?'Saving...':'Save Changes'}
                </button>
              </div>
            </div>
          </div>
        </div>`:null}
    </div>`;
}
/* ─── RemindersPanel ──────────────────────────────────────────────────────── */
function RemindersPanel({onClose,onReload}){
  const [reminders,setReminders]=useState([]);
  useEffect(()=>{
    api.get('/api/reminders').then(d=>{if(Array.isArray(d))setReminders(d);});
  },[]);
  const del=async(id)=>{
    await api.del('/api/reminders/'+id);
    setReminders(prev=>prev.filter(r=>r.id!==id));
    onReload&&onReload();
  };
  return html`
    <div class="ov" onClick=${e=>e.target===e.currentTarget&&onClose()}>
      <div class="mo" style=${{maxWidth:500}}>
        <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:18}}>
          <h2 style=${{fontSize:17,fontWeight:700,color:'var(--tx)'}}>⏰ My Reminders</h2>
          <button class="btn bg" style=${{padding:'7px 10px'}} onClick=${onClose}>✕</button>
        </div>
        ${reminders.length===0?html`<p style=${{color:'var(--tx3)',fontSize:13,textAlign:'center',padding:'24px 0'}}>No active reminders.</p>`:null}
        <div style=${{display:'flex',flexDirection:'column',gap:9}}>
          ${reminders.map(r=>html`
            <div key=${r.id} style=${{display:'flex',alignItems:'center',gap:12,padding:'11px 14px',background:'var(--sf2)',borderRadius:11,border:'1px solid var(--bd)'}}>
              <div style=${{fontSize:24}}>⏰</div>
              <div style=${{flex:1}}>
                <p style=${{fontSize:13,fontWeight:600,color:'var(--tx)',marginBottom:3}}>${r.task_title}</p>
                <p class="tx3-11">
                  ${r.minutes_before>0?r.minutes_before+' min before · ':''}
                  ${new Date(r.remind_at).toLocaleString()}
                </p>
              </div>
              <button class="btn brd" style=${{fontSize:11,padding:'5px 9px',color:'var(--rd)'}}
                onClick=${()=>del(r.id)}>✕</button>
            </div>`)}
        </div>
      </div>
    </div>`;
}


/* ─── TimesheetView ─────────────────────────────────────────────────────────── */
function TimesheetView({cu,teams,users,projects,tasks}){
  const isAdmin=cu&&(cu.role==='Admin'||cu.role==='Manager');
  const [logs,setLogs]=useState([]);
  const [busy,setBusy]=useState(false);
  const [loading,setLoading]=useState(true);

  // ── Local date helper
  const localToday=()=>{const d=new Date();return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');};
  const localMonth=()=>{const d=new Date();return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0');};

  // ── Form state
  const blankForm=()=>({date:localToday(),tab:'project',project_id:'',task_id:'',task_name:'',hours:'',minutes:'',comments:''});
  const [form,setForm]=useState(blankForm());

  // ── Inline edit
  const [editId,setEditId]=useState(null);
  const [editForm,setEditForm]=useState({hours:'',minutes:'',comments:''});

  // ── Filter state — Today/Yesterday/Week/Month/Custom
  const [filterMode,setFilterMode]=useState('today');
  const [filterMonth,setFilterMonth]=useState(localMonth());
  const [filterFrom,setFilterFrom]=useState('');
  const [filterTo,setFilterTo]=useState('');
  const [filterUser,setFilterUser]=useState('');
  const [searchQ,setSearchQ]=useState('');

  // ── Policy
  const [requiredHrs,setRequiredHrs]=useState(8);
  const [adminHrsInput,setAdminHrsInput]=useState('8');
  const [showForm,setShowForm]=useState(false);
  const [saveMsg,setSaveMsg]=useState('');

  // ── Celebration state
  const [celebration,setCelebration]=useState(null); // {type:'task'|'project', name:'...'}

  // ── Festival detection
  const FESTIVALS={
    '01-14':'🪁 Makar Sankranti','01-26':'🇮🇳 Republic Day',
    '03-22':'🌸 Ugadi','04-14':'🌺 Tamil New Year',
    '08-15':'🇮🇳 Independence Day','10-02':'🙏 Gandhi Jayanti',
    '10-20':'🪔 Diwali','11-01':'🎊 Kannada Rajyotsava',
  };
  const todayKey=(()=>{const d=new Date();return String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');})();
  const todayFestival=FESTIVALS[todayKey]||null;

  // ── Projects/tasks — exclude completed
  const ACTIVE_STAGES=new Set(['planning','development','code_review','testing','uat','release','production','blocked']);
  const activeTasks=useMemo(()=>safe(tasks).filter(t=>ACTIVE_STAGES.has(t.stage)||!t.stage),[tasks]);
  const myProjects=useMemo(()=>{
    const pids=new Set(activeTasks.map(t=>t.project));
    return safe(projects).filter(p=>pids.has(p.id));
  },[projects,activeTasks]);
  const tasksForProject=useMemo(()=>{
    if(!form.project_id)return[];
    return activeTasks.filter(t=>t.project===form.project_id);
  },[activeTasks,form.project_id]);

  // ── Load — called on mount, on focus, after saves
  const load=useCallback(async()=>{
    const [d,h]=await Promise.all([api.get('/api/timelogs'),api.get('/api/timelogs/required-hours')]);
    if(Array.isArray(d)){setLogs(d);setLoading(false);}
    const hrs=Number((h&&h.hours!=null)?h.hours:8);
    setRequiredHrs(hrs);setAdminHrsInput(String(hrs));
  },[]);

  // Mount: setup schema → then load immediately, also check for prefill from TaskModal
  useEffect(()=>{
    api.post('/api/timelogs/setup',{}).finally(()=>{
      load();
      // Check if TaskModal sent a prefill via sessionStorage
      try{
        const raw=sessionStorage.getItem('ts_prefill');
        if(raw){
          const pf=JSON.parse(raw);
          sessionStorage.removeItem('ts_prefill');
          if(pf.project_id||pf.task_id){
            setForm(f=>({...f,tab:'project',project_id:pf.project_id||'',task_id:pf.task_id||''}));
            setShowForm(true);
          }
        }
      }catch{}
    });
  },[]);

  // Refresh when user returns to tab (focus event)
  useEffect(()=>{
    const onFocus=()=>load();
    window.addEventListener('focus',onFocus);
    return()=>window.removeEventListener('focus',onFocus);
  },[load]);

  // ── Filter logic — Today / Yesterday / This Week / Month / Custom
  const filtered=useMemo(()=>{
    const now=new Date();
    const toLocal=d=>new Date(d.getFullYear(),d.getMonth(),d.getDate());
    const todayD=toLocal(now);
    let fromD=null,toD=null;

    if(filterMode==='today'){
      fromD=todayD; toD=todayD;
    } else if(filterMode==='yesterday'){
      const y=new Date(now); y.setDate(now.getDate()-1);
      fromD=toLocal(y); toD=toLocal(y);
    } else if(filterMode==='week'){
      const sun=new Date(now); sun.setDate(now.getDate()-now.getDay());
      fromD=toLocal(sun);
      const sat=new Date(sun); sat.setDate(sun.getDate()+6);
      toD=toLocal(sat);
    } else if(filterMode==='month'){
      const [yr,mo]=filterMonth.split('-').map(Number);
      fromD=new Date(yr,mo-1,1); toD=new Date(yr,mo,0);
    } else {
      fromD=filterFrom?toLocal(new Date(filterFrom)):null;
      toD=filterTo?toLocal(new Date(filterTo)):null;
    }
    return logs.filter(l=>{
      const [yr,mo,dy]=l.date.split('-').map(Number);
      const ld=new Date(yr,mo-1,dy);
      if(fromD&&ld<fromD)return false;
      if(toD&&ld>toD)return false;
      if(filterUser&&l.user_id!==filterUser)return false;
      if(searchQ){
        const q=searchQ.toLowerCase();
        const match=(l.task_name||'').toLowerCase().includes(q)||
                     (l.comments||'').toLowerCase().includes(q)||
                     (l.user_name||'').toLowerCase().includes(q);
        if(!match)return false;
      }
      return true;
    });
  },[logs,filterMode,filterMonth,filterFrom,filterTo,filterUser,searchQ]);

  // ── Aggregations
  const toHrs=l=>Number(l.hours||0)+(Number(l.minutes||0)/60);
  const totalHrs=filtered.reduce((s,l)=>s+toHrs(l),0);
  const byUser=useMemo(()=>{
    const m={};
    filtered.forEach(l=>{
      if(!m[l.user_id])m[l.user_id]={name:l.user_name||l.user_id,hrs:0};
      m[l.user_id].hrs+=toHrs(l);
    });
    return Object.values(m).sort((a,b)=>b.hrs-a.hrs);
  },[filtered]);

  // ── Helpers
  const fmtHrs=h=>{h=Math.max(0,h);const wh=Math.floor(h);const wm=Math.round((h-wh)*60);if(wh>0&&wm>0)return wh+'h '+wm+'m';if(wh>0)return wh+'h';if(wm>0)return wm+'m';return '0m';};
  const projName=id=>{const p=safe(projects).find(p=>p.id===id);return p?p.name:'';};

  // ── Celebration trigger
  const triggerCelebration=(type,name)=>{
    setCelebration({type,name});
    setTimeout(()=>setCelebration(null),4000);
  };

  // ── Check if project is fully complete after a task save
  const checkProjectCompletion=(projectId,allTasks)=>{
    const pt=safe(allTasks).filter(t=>t.project===projectId);
    if(pt.length>0&&pt.every(t=>t.stage==='completed')){
      const p=safe(projects).find(p=>p.id===projectId);
      triggerCelebration('project',p?p.name:'Project');
    }
  };

  // ── Save new log (optimistic update)
  const handleSave=async()=>{
    if(form.tab==='project'&&!form.task_id)return setSaveMsg('⚠ Select a task');
    if(form.tab==='manual'&&!form.task_name.trim())return setSaveMsg('⚠ Enter task name');
    const h=Number(form.hours||0),m=Number(form.minutes||0);
    if(!h&&!m)return setSaveMsg('⚠ Enter at least 1 minute');
    setBusy(true);
    const {tab:_tab,...formData}=form;
    let payload={...formData,hours:h,minutes:Math.min(59,m)};
    if(form.tab==='project'){
      const t=safe(tasks).find(t=>t.id===form.task_id);
      payload.task_name=t?t.title:form.task_id;
    }
    // Optimistic — add to local list immediately
    const tempId='tmp_'+Date.now();
    const optimisticEntry={...payload,id:tempId,user_id:cu.id,user_name:cu.name};
    setLogs(prev=>[optimisticEntry,...prev]);
    const res=await api.post('/api/timelogs',payload);
    if(res&&res.id){
      setForm(blankForm());
      setShowForm(false);
      setSaveMsg('✓ Hours logged!');
      setTimeout(()=>setSaveMsg(''),3000);
      load(); // refresh to get real ID and server data
    } else {
      setLogs(prev=>prev.filter(l=>l.id!==tempId)); // rollback optimistic
      setSaveMsg('⚠ Save failed — please retry');
    }
    setBusy(false);
  };

  // ── Delete optimistic
  const handleDelete=async(id)=>{
    if(!confirm('Delete this log entry?'))return;
    setLogs(prev=>prev.filter(l=>l.id!==id));
    await api.del('/api/timelogs/'+id);
    load();
  };

  // ── Inline edit
  const startEdit=l=>{setEditId(l.id);setEditForm({hours:String(l.hours||0),minutes:String(l.minutes||0),comments:l.comments||''}); };
  const cancelEdit=()=>setEditId(null);
  const saveEdit=async(l)=>{
    const h=Number(editForm.hours||0),m=Number(editForm.minutes||0);
    if(!h&&!m)return;
    const r=await api.put('/api/timelogs/'+l.id,{hours:h,minutes:Math.min(59,m),comments:editForm.comments});
    if(r&&(r.ok||r.id)){
      setLogs(prev=>prev.map(x=>x.id===l.id?{...x,hours:h,minutes:Math.min(59,m),comments:editForm.comments}:x));
    }
    setEditId(null);
  };

  // ── CSV export
  const downloadCSV=()=>{
    const projMap=safe(projects).reduce((m,p)=>{m[p.id]=p.name;return m;},{});
    const headers=['Date','User','Project','Task','Hours','Minutes','Total Decimal Hrs','Comments'];
    const rows=filtered.map(l=>[l.date,'"'+(l.user_name||l.user_id)+'"','"'+(l.project_id?projMap[l.project_id]||l.project_id:'')+'"','"'+(l.task_name||'')+'"',Number(l.hours||0),Number(l.minutes||0),toHrs(l).toFixed(2),'"'+(l.comments||'')+'"']);
    const csv='data:text/csv;charset=utf-8,'+[headers,...rows].map(r=>r.join(',')).join('\n');
    const a=document.createElement('a');
    a.setAttribute('href',encodeURI(csv));
    const lbl=filterMode==='today'?localToday():filterMode==='yesterday'?'yesterday':filterMode==='week'?'thisweek':filterMode==='month'?filterMonth:'custom';
    a.setAttribute('download','vewit_timelogs_'+lbl+'.csv');
    document.body.appendChild(a);a.click();document.body.removeChild(a);
  };

  // ── Filter label for display
  const filterLabels={today:'Today',yesterday:'Yesterday',week:'This Week',month:'Month',custom:'Custom'};

  return html`<div style=${{padding:'0 0 60px'}}>

    <!-- ══ Festival Banner ══ -->
    ${todayFestival?html`
    <div style=${{background:'linear-gradient(135deg,#7c3aed,#ec4899,#f59e0b)',borderRadius:12,padding:'10px 18px',marginBottom:16,display:'flex',alignItems:'center',gap:10,boxShadow:'0 4px 20px rgba(124,58,237,.3)'}}>
      <span style=${{fontSize:20}}>🎊</span>
      <span style=${{fontSize:13,fontWeight:700,color:'#fff',flex:1}}>
        ${todayFestival} — Wishing you and your team a joyful celebration!
      </span>
      <span style=${{fontSize:18}}>✨</span>
    </div>`:null}

    <!-- ══ Celebration Overlay ══ -->
    ${celebration?html`
    <div style=${{position:'fixed',inset:0,background:'rgba(0,0,0,.55)',zIndex:9999,display:'flex',alignItems:'center',justifyContent:'center',backdropFilter:'blur(4px)'}}
      onClick=${()=>setCelebration(null)}>
      <div style=${{background:'var(--sf)',borderRadius:24,padding:'40px 48px',textAlign:'center',boxShadow:'0 24px 80px rgba(0,0,0,.4)',border:'1px solid var(--bd)',maxWidth:380,animation:'vwBoot-up .5s ease both'}}>
        <div style=${{fontSize:56,marginBottom:12,animation:'pulse 1s infinite'}}>
          ${celebration.type==='project'?'🏆':'🎉'}
        </div>
        <div style=${{fontSize:22,fontWeight:800,color:'var(--tx)',marginBottom:8,letterSpacing:'-.5px'}}>
          ${celebration.type==='project'?'Project Complete!':'Task Done!'}
        </div>
        <div style=${{fontSize:14,color:'var(--tx2)',marginBottom:20,fontWeight:500}}>
          ${celebration.name}
        </div>
        <div style=${{fontSize:13,color:'var(--tx3)'}}>
          ${celebration.type==='project'?'🎊 Outstanding teamwork! Every task is complete.':'⭐ Great work! Keep it up.'}
        </div>
        <div style=${{marginTop:20,display:'flex',gap:6,justifyContent:'center'}}>
          ${['🎉','⭐','🏅','✨','🎊','💪','🔥','👏'].map((e,i)=>html`
            <span key=${i} style=${{fontSize:18,animation:'vwBoot-orb'+(1+i%3)+' '+(1.2+i*0.15)+'s ease-in-out infinite'}}>${e}</span>`)}
        </div>
      </div>
    </div>`:null}

    <!-- ══ Action Bar ══ -->
    <div style=${{display:'flex',alignItems:'center',justifyContent:'flex-end',flexWrap:'wrap',gap:8,marginBottom:16}}>
      ${saveMsg?html`<span style=${{fontSize:12,color:saveMsg.startsWith('⚠')?'#ef4444':'#22c55e',fontWeight:600,marginRight:4}}>${saveMsg}</span>`:null}
      <button class="btn bg" style=${{fontSize:12,padding:'7px 14px',display:'flex',alignItems:'center',gap:5}} onClick=${downloadCSV}>
        ⬇ CSV ${filtered.length?html`<span style=${{fontSize:10,background:'var(--ac)',color:'var(--ac-tx)',borderRadius:100,padding:'1px 6px',fontWeight:700}}>${filtered.length}</span>`:null}
      </button>
      <button class="btn bp" style=${{fontSize:12,padding:'7px 16px'}} onClick=${()=>{setShowForm(v=>!v);setSaveMsg('');}}>
        ${showForm?'✕ Close':'+ Log Hours'}
      </button>
    </div>

    <!-- ══ Log Hours Form ══ -->
    ${showForm?html`
    <div style=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:16,padding:20,marginBottom:20,boxShadow:'0 4px 24px rgba(0,0,0,.18)'}}>
      <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:16,flexWrap:'wrap',gap:8}}>
        <h3 style=${{fontSize:14,fontWeight:700,color:'var(--tx)',margin:0}}>Log Hours</h3>
        <div style=${{display:'flex',background:'var(--bg)',borderRadius:8,padding:2,border:'1px solid var(--bd)'}}>
          ${['project','manual'].map(tab=>html`
            <button key=${tab} onClick=${()=>setForm(f=>({...f,tab,project_id:'',task_id:'',task_name:''}))}
              style=${{padding:'5px 16px',borderRadius:6,border:'none',cursor:'pointer',fontSize:11,fontWeight:700,
                background:form.tab===tab?'var(--ac)':'transparent',
                color:form.tab===tab?'var(--ac-tx)':'var(--tx2)',transition:'all .15s'}}>
              ${tab==='project'?'📁 Project':'✏ Manual'}
            </button>`)}
        </div>
      </div>
      <div style=${{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(190px,1fr))',gap:12}}>
        <div>
          <label style=${{fontSize:11,fontWeight:600,color:'var(--tx2)',display:'block',marginBottom:4}}>📅 Date</label>
          <input type="date" value=${form.date} onChange=${e=>setForm(f=>({...f,date:e.target.value}))}
            style=${{width:'100%',background:'var(--bg)',border:'1px solid var(--bd)',borderRadius:8,padding:'8px 10px',color:'var(--tx)',fontSize:12,boxSizing:'border-box'}}/>
        </div>
        ${form.tab==='project'?html`
          <div>
            <label style=${{fontSize:11,fontWeight:600,color:'var(--tx2)',display:'block',marginBottom:4}}>📁 Project *</label>
            <select value=${form.project_id} onChange=${e=>setForm(f=>({...f,project_id:e.target.value,task_id:''}))}
              style=${{width:'100%',background:'var(--bg)',border:'1px solid '+(form.project_id?'var(--ac)':'var(--bd)'),borderRadius:8,padding:'8px 10px',color:form.project_id?'var(--tx)':'var(--tx3)',fontSize:12,boxSizing:'border-box'}}>
              <option value="">— Select Project —</option>
              ${myProjects.map(p=>html`<option key=${p.id} value=${p.id}>${p.name}</option>`)}
            </select>
          </div>
          <div>
            <label style=${{fontSize:11,fontWeight:600,color:'var(--tx2)',display:'block',marginBottom:4}}>✅ Task *</label>
            <select value=${form.task_id} onChange=${e=>setForm(f=>({...f,task_id:e.target.value}))}
              disabled=${!form.project_id}
              style=${{width:'100%',background:'var(--bg)',border:'1px solid '+(form.task_id?'var(--ac)':'var(--bd)'),borderRadius:8,padding:'8px 10px',color:form.task_id?'var(--tx)':'var(--tx3)',fontSize:12,boxSizing:'border-box',opacity:form.project_id?1:0.5}}>
              <option value="">${form.project_id?'— Select Task —':'Select project first'}</option>
              ${tasksForProject.map(t=>html`<option key=${t.id} value=${t.id}>${t.title}</option>`)}
            </select>
          </div>
        `:html`
          <div style=${{gridColumn:'span 2'}}>
            <label style=${{fontSize:11,fontWeight:600,color:'var(--tx2)',display:'block',marginBottom:4}}>✏ Task Name *</label>
            <input type="text" placeholder="e.g. Fix login bug, Code review, Standup…" value=${form.task_name}
              onChange=${e=>setForm(f=>({...f,task_name:e.target.value}))}
              style=${{width:'100%',background:'var(--bg)',border:'1px solid var(--bd)',borderRadius:8,padding:'8px 10px',color:'var(--tx)',fontSize:12,boxSizing:'border-box'}}/>
          </div>
        `}
        <div style=${{display:'flex',gap:8,alignItems:'flex-end'}}>
          <div style=${{flex:1}}>
            <label style=${{fontSize:11,fontWeight:600,color:'var(--tx2)',display:'block',marginBottom:4}}>⏱ Hours</label>
            <input type="number" min="0" max="23" placeholder="0" value=${form.hours}
              onChange=${e=>setForm(f=>({...f,hours:e.target.value}))}
              style=${{width:'100%',background:'var(--bg)',border:'1px solid var(--bd)',borderRadius:8,padding:'8px 10px',color:'var(--tx)',fontSize:12,boxSizing:'border-box'}}/>
          </div>
          <div style=${{flex:1}}>
            <label style=${{fontSize:11,fontWeight:600,color:'var(--tx2)',display:'block',marginBottom:4}}>Mins</label>
            <input type="number" min="0" max="59" placeholder="0" value=${form.minutes}
              onChange=${e=>setForm(f=>({...f,minutes:e.target.value}))}
              style=${{width:'100%',background:'var(--bg)',border:'1px solid var(--bd)',borderRadius:8,padding:'8px 10px',color:'var(--tx)',fontSize:12,boxSizing:'border-box'}}/>
          </div>
          <div style=${{flexShrink:0,fontSize:14,fontWeight:800,color:'var(--ac)',paddingBottom:9,minWidth:36,textAlign:'right'}}>
            ${(()=>{const h=Number(form.hours||0),m=Number(form.minutes||0);return(h||m)?fmtHrs(h+m/60):'';})()}
          </div>
        </div>
        <div style=${{gridColumn:'1/-1'}}>
          <label style=${{fontSize:11,fontWeight:600,color:'var(--tx2)',display:'block',marginBottom:4}}>💬 Comments</label>
          <textarea placeholder="What did you work on? (optional)" value=${form.comments}
            onChange=${e=>setForm(f=>({...f,comments:e.target.value}))}
            style=${{width:'100%',background:'var(--bg)',border:'1px solid var(--bd)',borderRadius:8,padding:'8px 10px',color:'var(--tx)',fontSize:12,resize:'vertical',minHeight:52,fontFamily:'inherit',boxSizing:'border-box'}}></textarea>
        </div>
      </div>
      <div style=${{marginTop:14,display:'flex',gap:8,alignItems:'center',flexWrap:'wrap'}}>
        <button class="btn bp" style=${{fontSize:12,padding:'9px 22px',display:'flex',alignItems:'center',gap:6}} onClick=${handleSave} disabled=${busy}>
          ${busy?html`<span class="spin"></span>`:null} Save Log
        </button>
        <button class="btn bg" style=${{fontSize:12,padding:'9px 14px'}} onClick=${()=>{setShowForm(false);setSaveMsg('');}}>Cancel</button>
        ${saveMsg?html`<span style=${{fontSize:12,color:saveMsg.startsWith('⚠')?'#ef4444':'#22c55e',fontWeight:600}}>${saveMsg}</span>`:null}
      </div>
    </div>`:null}

    <!-- ══ Admin: Workspace Policy ══ -->
    ${isAdmin?html`
    <div style=${{background:'linear-gradient(135deg,rgba(90,140,255,.07),rgba(168,85,247,.07))',border:'1px solid rgba(90,140,255,.22)',borderRadius:12,padding:'11px 18px',marginBottom:16,display:'flex',alignItems:'center',gap:12,flexWrap:'wrap'}}>
      <span style=${{fontSize:11,fontWeight:700,color:'var(--ac)',letterSpacing:'.04em',flexShrink:0}}>⚙ WORKSPACE POLICY</span>
      <span style=${{fontSize:12,color:'var(--tx2)',flexShrink:0}}>Required hours/day:</span>
      <input type="number" min="1" max="24" step="0.5" value=${adminHrsInput}
        onChange=${e=>setAdminHrsInput(e.target.value)}
        style=${{width:64,background:'var(--bg)',border:'1px solid var(--bd)',borderRadius:7,padding:'5px 8px',color:'var(--tx)',fontSize:13,textAlign:'center',fontWeight:700}}/>
      <button class="btn bp" style=${{fontSize:11,padding:'5px 14px'}} onClick=${async()=>{
        const hrs=parseFloat(adminHrsInput)||8;
        const r=await api.post('/api/timelogs/required-hours',{hours:hrs});
        if(r&&r.ok){setRequiredHrs(hrs);setSaveMsg('✓ Policy saved for all members');setTimeout(()=>setSaveMsg(''),3000);}
      }}>Save for All</button>
      <span style=${{fontSize:12,color:'var(--tx3)'}}>Active: <b style=${{color:'var(--tx)',fontSize:13}}>${requiredHrs}h/day</b></span>
    </div>`:null}

    <!-- ══ Filter Bar ══ -->
    <div style=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:12,padding:'10px 16px',marginBottom:16,display:'flex',alignItems:'center',gap:8,flexWrap:'wrap'}}>
      <span style=${{fontSize:11,fontWeight:700,color:'var(--tx3)',flexShrink:0}}>Filter:</span>
      ${['today','yesterday','week','month','custom'].map(m=>html`
        <button key=${m} onClick=${()=>setFilterMode(m)}
          style=${{fontSize:11,padding:'5px 13px',borderRadius:100,cursor:'pointer',fontWeight:600,transition:'all .15s',
            border:'1px solid '+(filterMode===m?'var(--ac)':'var(--bd)'),
            background:filterMode===m?'var(--ac3)':'transparent',
            color:filterMode===m?'var(--ac)':'var(--tx2)'}}>
          ${filterLabels[m]}
        </button>`)}
      ${filterMode==='month'?html`
        <input type="month" value=${filterMonth} onChange=${e=>setFilterMonth(e.target.value)}
          style=${{background:'var(--bg)',border:'1px solid var(--bd)',borderRadius:7,padding:'5px 9px',color:'var(--tx)',fontSize:11}}/>`:null}
      ${filterMode==='custom'?html`
        <input type="date" value=${filterFrom} onChange=${e=>setFilterFrom(e.target.value)}
          style=${{background:'var(--bg)',border:'1px solid var(--bd)',borderRadius:7,padding:'5px 9px',color:'var(--tx)',fontSize:11}}/>
        <span style=${{color:'var(--tx3)',fontSize:12}}>→</span>
        <input type="date" value=${filterTo} onChange=${e=>setFilterTo(e.target.value)}
          style=${{background:'var(--bg)',border:'1px solid var(--bd)',borderRadius:7,padding:'5px 9px',color:'var(--tx)',fontSize:11}}/>`:null}
      <div style=${{marginLeft:'auto',display:'flex',gap:8,alignItems:'center'}}>
        <input type="search" placeholder="🔍 Search tasks, comments…" value=${searchQ}
          onChange=${e=>setSearchQ(e.target.value)}
          style=${{background:'var(--bg)',border:'1px solid var(--bd)',borderRadius:7,padding:'5px 10px',color:'var(--tx)',fontSize:11,width:180}}/>
        ${isAdmin&&users&&users.length>0?html`
          <select value=${filterUser} onChange=${e=>setFilterUser(e.target.value)}
            style=${{background:'var(--bg)',border:'1px solid var(--bd)',borderRadius:7,padding:'5px 9px',color:'var(--tx)',fontSize:11}}>
            <option value="">All Users</option>
            ${(users||[]).map(u=>html`<option key=${u.id} value=${u.id}>${u.name}</option>`)}
          </select>`:null}
      </div>
    </div>

    <!-- ══ Summary Cards ══ -->
    <div style=${{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(145px,1fr))',gap:12,marginBottom:20}}>
      <div style=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:12,padding:'14px 16px'}}>
        <div style=${{fontSize:10,color:'var(--tx3)',fontWeight:700,textTransform:'uppercase',letterSpacing:'.05em',marginBottom:6}}>Total Hours</div>
        <div style=${{fontSize:24,fontWeight:800,color:'var(--ac)',lineHeight:1}}>
          ${loading?html`<span class="spin" style=${{display:'inline-block'}}></span>`:fmtHrs(totalHrs)}
        </div>
      </div>
      <div style=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:12,padding:'14px 16px'}}>
        <div style=${{fontSize:10,color:'var(--tx3)',fontWeight:700,textTransform:'uppercase',letterSpacing:'.05em',marginBottom:6}}>Log Entries</div>
        <div style=${{fontSize:24,fontWeight:800,color:'var(--tx)',lineHeight:1}}>${filtered.length}</div>
      </div>
      <div style=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:12,padding:'14px 16px'}}>
        <div style=${{fontSize:10,color:'var(--tx3)',fontWeight:700,textTransform:'uppercase',letterSpacing:'.05em',marginBottom:6}}>Avg / Entry</div>
        <div style=${{fontSize:24,fontWeight:800,color:'var(--tx)',lineHeight:1}}>${filtered.length?fmtHrs(totalHrs/filtered.length):'—'}</div>
      </div>
      <div style=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:12,padding:'14px 16px'}}>
        <div style=${{fontSize:10,color:'var(--tx3)',fontWeight:700,textTransform:'uppercase',letterSpacing:'.05em',marginBottom:6}}>vs Policy</div>
        ${(()=>{
          const days=filterMode==='today'||filterMode==='yesterday'?1:filterMode==='week'?5:filterMode==='month'?22:1;
          const expected=days*requiredHrs;
          const pct=expected>0?Math.round(totalHrs/expected*100):0;
          const col=pct>=100?'#22c55e':pct>=70?'var(--ac)':'#ef4444';
          return html`<div style=${{fontSize:24,fontWeight:800,color:col,lineHeight:1}}>${pct}%</div><div style=${{fontSize:10,color:'var(--tx3)',marginTop:4}}>${fmtHrs(expected)} expected</div>`;
        })()}
      </div>
    </div>

    <!-- ══ Team Summary (admin) ══ -->
    ${isAdmin&&byUser.length>0?html`
    <div style=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:12,padding:'14px 18px',marginBottom:16}}>
      <div style=${{fontSize:12,fontWeight:700,color:'var(--tx)',marginBottom:12,display:'flex',alignItems:'center',gap:8}}>
        👥 Team Summary <span style=${{fontSize:10,color:'var(--tx3)',fontWeight:500}}>policy: ${requiredHrs}h/day</span>
      </div>
      <div style=${{display:'flex',flexDirection:'column',gap:8}}>
        ${byUser.map(u=>{
          const days=filterMode==='today'||filterMode==='yesterday'?1:filterMode==='week'?5:filterMode==='month'?22:1;
          const expected=days*requiredHrs;
          const pct=expected>0?Math.min(100,Math.round(u.hrs/expected*100)):0;
          const col=pct>=100?'#22c55e':pct>=70?'var(--ac)':'#ef4444';
          return html`<div key=${u.name} style=${{display:'flex',alignItems:'center',gap:10}}>
            <div style=${{width:110,fontSize:11,color:'var(--tx2)',fontWeight:600,flexShrink:0,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${u.name}</div>
            <div style=${{flex:1,height:7,background:'var(--bd)',borderRadius:100,overflow:'hidden'}}>
              <div style=${{height:'100%',width:pct+'%',background:col,borderRadius:100,transition:'width .4s'}}></div>
            </div>
            <div style=${{fontSize:11,fontWeight:700,color:col,width:46,textAlign:'right',flexShrink:0}}>${fmtHrs(u.hrs)}</div>
            <div style=${{fontSize:10,color:'var(--tx3)',width:30,textAlign:'right',flexShrink:0}}>${pct}%</div>
          </div>`;
        })}
      </div>
    </div>`:null}

    <!-- ══ Entries Table ══ -->
    <div style=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:12,overflow:'hidden'}}>
      <div style=${{padding:'11px 18px',borderBottom:'1px solid var(--bd)',display:'flex',alignItems:'center',justifyContent:'space-between'}}>
        <span style=${{fontSize:13,fontWeight:700,color:'var(--tx)'}}>Entries <span style=${{fontSize:11,color:'var(--tx3)',fontWeight:400}}>· ${filterLabels[filterMode]}</span></span>
        <span style=${{fontSize:11,color:'var(--tx3)'}}>${filtered.length} record${filtered.length!==1?'s':''}</span>
      </div>
      ${loading?html`<div style=${{textAlign:'center',padding:'40px',color:'var(--tx3)'}}><span class="spin"></span></div>`
      :filtered.length===0?html`
        <div style=${{textAlign:'center',padding:'52px 20px',color:'var(--tx3)'}}>
          <div style=${{fontSize:36,marginBottom:10}}>⏱</div>
          <div style=${{fontSize:13,fontWeight:600,color:'var(--tx2)',marginBottom:4}}>No entries for ${filterLabels[filterMode].toLowerCase()}</div>
          <div style=${{fontSize:12}}>Click <b style=${{color:'var(--ac)'}}>+ Log Hours</b> to add your first entry.</div>
        </div>
      `:html`
        <div style=${{overflowX:'auto'}}>
          <table style=${{width:'100%',borderCollapse:'collapse',fontSize:12}}>
            <thead>
              <tr style=${{background:'var(--sf2)'}}>
                ${isAdmin?html`<th style=${{padding:'9px 14px',textAlign:'left',fontWeight:700,color:'var(--tx3)',fontSize:10,letterSpacing:'.04em'}}>USER</th>`:null}
                <th style=${{padding:'9px 14px',textAlign:'left',fontWeight:700,color:'var(--tx3)',fontSize:10,letterSpacing:'.04em'}}>DATE</th>
                <th style=${{padding:'9px 14px',textAlign:'left',fontWeight:700,color:'var(--tx3)',fontSize:10,letterSpacing:'.04em'}}>PROJECT</th>
                <th style=${{padding:'9px 14px',textAlign:'left',fontWeight:700,color:'var(--tx3)',fontSize:10,letterSpacing:'.04em'}}>TASK</th>
                <th style=${{padding:'9px 14px',textAlign:'right',fontWeight:700,color:'var(--tx3)',fontSize:10,letterSpacing:'.04em'}}>TIME</th>
                <th style=${{padding:'9px 14px',textAlign:'left',fontWeight:700,color:'var(--tx3)',fontSize:10,letterSpacing:'.04em'}}>COMMENTS</th>
                <th style=${{padding:'9px 10px',textAlign:'center',fontWeight:700,color:'var(--tx3)',fontSize:10,letterSpacing:'.04em'}}>ACTIONS</th>
              </tr>
            </thead>
            <tbody>
              ${filtered.map((l,i)=>{
                const hrs=toHrs(l);
                const pName=l.project_id?projName(l.project_id):'';
                const isEditing=editId===l.id;
                const canEdit=l.user_id===cu.id||isAdmin;
                return html`<tr key=${l.id}
                  style=${{borderTop:'1px solid var(--bd)',background:i%2?'rgba(0,0,0,.015)':'transparent',transition:'background .1s'}}
                  onMouseEnter=${e=>e.currentTarget.style.background='var(--sf2)'}
                  onMouseLeave=${e=>e.currentTarget.style.background=i%2?'rgba(0,0,0,.015)':'transparent'}>
                  ${isAdmin?html`<td style=${{padding:'9px 14px',fontWeight:600,whiteSpace:'nowrap',color:'var(--tx)',fontSize:11}}>${l.user_name||l.user_id}</td>`:null}
                  <td style=${{padding:'9px 14px',color:'var(--tx2)',whiteSpace:'nowrap',fontSize:11}}>${l.date}</td>
                  <td style=${{padding:'9px 14px',maxWidth:110,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>
                    ${pName?html`<span style=${{fontSize:10,padding:'2px 7px',borderRadius:5,background:'var(--ac3)',color:'var(--ac)',fontWeight:700}}>${pName}</span>`
                           :html`<span style=${{color:'var(--tx3)',fontSize:11}}>—</span>`}
                  </td>
                  <td style=${{padding:'9px 14px',color:'var(--tx)',fontWeight:500,maxWidth:200,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',fontSize:12}}>${l.task_name||'—'}</td>
                  <td style=${{padding:'9px 14px',textAlign:'right',whiteSpace:'nowrap'}}>
                    ${isEditing?html`
                      <div style=${{display:'flex',gap:4,justifyContent:'flex-end',alignItems:'center'}}>
                        <input type="number" min="0" max="23" value=${editForm.hours}
                          onChange=${e=>setEditForm(f=>({...f,hours:e.target.value}))}
                          style=${{width:44,background:'var(--bg)',border:'1px solid var(--ac)',borderRadius:5,padding:'3px 5px',color:'var(--tx)',fontSize:11,textAlign:'center'}}/>
                        <span style=${{color:'var(--tx3)',fontSize:10}}>h</span>
                        <input type="number" min="0" max="59" value=${editForm.minutes}
                          onChange=${e=>setEditForm(f=>({...f,minutes:e.target.value}))}
                          style=${{width:40,background:'var(--bg)',border:'1px solid var(--ac)',borderRadius:5,padding:'3px 5px',color:'var(--tx)',fontSize:11,textAlign:'center'}}/>
                        <span style=${{color:'var(--tx3)',fontSize:10}}>m</span>
                      </div>
                    `:html`<span style=${{color:'var(--ac)',fontWeight:800,fontSize:13}}>${fmtHrs(hrs)}</span>`}
                  </td>
                  <td style=${{padding:'9px 14px',maxWidth:180,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',color:'var(--tx3)',fontSize:11}}>
                    ${isEditing?html`
                      <input type="text" value=${editForm.comments} onChange=${e=>setEditForm(f=>({...f,comments:e.target.value}))}
                        style=${{width:'100%',background:'var(--bg)',border:'1px solid var(--ac)',borderRadius:5,padding:'3px 6px',color:'var(--tx)',fontSize:11}}/>
                    `:l.comments||'—'}
                  </td>
                  <td style=${{padding:'7px 10px',textAlign:'center',whiteSpace:'nowrap'}}>
                    ${canEdit?html`
                      ${isEditing?html`
                        <button onClick=${()=>saveEdit(l)}
                          style=${{background:'var(--ac)',color:'var(--ac-tx)',border:'none',cursor:'pointer',fontSize:10,padding:'3px 9px',borderRadius:5,fontWeight:700,marginRight:3}}>✓ Save</button>
                        <button onClick=${cancelEdit}
                          style=${{background:'var(--sf2)',color:'var(--tx2)',border:'1px solid var(--bd)',cursor:'pointer',fontSize:10,padding:'3px 7px',borderRadius:5}}>✕</button>
                      `:html`
                        <button onClick=${()=>startEdit(l)}
                          style=${{background:'none',border:'1px solid transparent',cursor:'pointer',color:'var(--tx3)',fontSize:12,padding:'3px 7px',borderRadius:5,marginRight:2,transition:'all .12s'}}
                          onMouseEnter=${e=>{e.currentTarget.style.color='var(--ac)';e.currentTarget.style.borderColor='var(--ac)';e.currentTarget.style.background='var(--ac3)';}}
                          onMouseLeave=${e=>{e.currentTarget.style.color='var(--tx3)';e.currentTarget.style.borderColor='transparent';e.currentTarget.style.background='none';}}
                          title="Edit">✎</button>
                        <button onClick=${()=>handleDelete(l.id)}
                          style=${{background:'none',border:'1px solid transparent',cursor:'pointer',color:'var(--tx3)',fontSize:12,padding:'3px 7px',borderRadius:5,transition:'all .12s'}}
                          onMouseEnter=${e=>{e.currentTarget.style.color='#ef4444';e.currentTarget.style.borderColor='#ef4444';e.currentTarget.style.background='rgba(239,68,68,.08)';}}
                          onMouseLeave=${e=>{e.currentTarget.style.color='var(--tx3)';e.currentTarget.style.borderColor='transparent';e.currentTarget.style.background='none';}}
                          title="Delete">✕</button>
                      `}
                    `:null}
                  </td>
                </tr>`;
              })}
            </tbody>
          </table>
        </div>
      `}
    </div>
  </div>`;
}
function App(){
  const [dark,setDark]=useState(()=>{try{return localStorage.getItem('pf_dark')==='1';}catch{return false;}});const [cu,setCu]=useState(null);const [loading,setLoading]=useState(true);
  // Read initial view from URL path or ?page= param
  const VALID_VIEWS=['dashboard','projects','tasks','messages','dm','tickets','timeline','reminders','settings','team','productivity','ai-docs','timesheet'];
  // Also treat /projects/<id> as valid
  useEffect(()=>{
    try{
      const p=window.location.pathname;
      if(p.startsWith('/projects/')&&p.length>10){
        const pid=p.split('/')[2];
        if(pid)setInitialProjectId(pid);
        setView('projects');
      }
    }catch(e){}
  },[]);
  // Set initial page title based on current URL path
  useEffect(()=>{
    try{
      const p=window.location.pathname.replace(/^\//, '').split('/')[0].trim();
      const VIEW_T={dashboard:'Dashboard',projects:'Projects',tasks:'Kanban Board',messages:'Channels',dm:'Direct Messages',tickets:'Tickets',timeline:'Timeline Tracker',reminders:'Reminders',settings:'Settings',team:'Team Management',productivity:'Dev Productivity'};
      if(p&&VIEW_T[p]) document.title='VEWIT — '+VIEW_T[p]+' | AI-Powered Team Collaboration';
      else document.title='VEWIT — AI-Powered Team Collaboration Platform';
    }catch(e){}
  },[]);
  const [view,setView]=useState(()=>{
    try{
      const p=window.location.pathname.replace(/^\//, '').split('/')[0].trim();
      if(p&&VALID_VIEWS.includes(p)) return p;
      const sp=new URLSearchParams(window.location.search).get('page');
      if(sp&&VALID_VIEWS.includes(sp)) return sp;
    }catch(e){}
    return 'dashboard';
  });
  // Keep browser URL in sync with current view
  const VIEW_TITLES={
    dashboard:'Dashboard',projects:'Projects',tasks:'Kanban Board',
    messages:'Channels',dm:'Direct Messages',tickets:'Tickets',
    timeline:'Timeline Tracker',reminders:'Reminders',
    settings:'Settings',team:'Team Management',productivity:'Dev Productivity',
    'ai-docs':'AI Documentation'
  };
  const _setView=useCallback((v)=>{
    setView(v);
    try{
      const base=v.split(':')[0];
      if(VALID_VIEWS.includes(base)){
        history.pushState(null,'','/'+base);
        document.title='VEWIT — '+(VIEW_TITLES[base]||base)+' | AI-Powered Team Collaboration';
      }
    }catch(e){}
  },[]);
  // Handle browser back/forward
  useEffect(()=>{
    const onPop=()=>{
      try{
        const p=window.location.pathname.replace(/^\//, '').split('/')[0].trim();
        if(p&&VALID_VIEWS.includes(p)) setView(p);
        else setView('dashboard');
      }catch(e){}
    };
    window.addEventListener('popstate',onPop);
    return()=>window.removeEventListener('popstate',onPop);
  },[]);
  const [col,setCol]=useState(()=>{try{return localStorage.getItem('pf_col')==='1';}catch{return false;}});
  const [initialProjectId,setInitialProjectId]=useState(null);
  useEffect(()=>{
    try{
      const saved=JSON.parse(localStorage.getItem('pf_accent')||'null');
      const oldGreen=['#5a8cff','#4d7fff','#5a8cff','#5a8cff'.toLowerCase(),'#7c3aed','#8b5cf6','#6d28d9','#9333ea','#a855f7'];
      if(saved&&saved.ac&&oldGreen.includes(saved.ac.toLowerCase())){
        localStorage.removeItem('pf_accent');
        return;
      }
      if(saved&&saved.ac){
        const r=document.body.style;
        r.setProperty('--ac',saved.ac);r.setProperty('--ac2',saved.ac2||saved.ac);
        const hex=saved.ac.replace('#','');const bigint=parseInt(hex,16);
        const ri=Math.round((bigint>>16)&255),gi=Math.round((bigint>>8)&255),bi=Math.round(bigint&255);
        r.setProperty('--ac3','rgba('+ri+','+gi+','+bi+',.10)');
        r.setProperty('--ac4','rgba('+ri+','+gi+','+bi+',.06)');
        r.setProperty('--ac-tx',saved.tx||'#ffffff');
      }
    }catch(e){}
  },[]);
  const [data,setData]=useState({users:[],projects:[],tasks:[],notifs:[],teams:[],tickets:[]});
  const [teamCtx,setTeamCtxRaw]=useState(()=>{try{return localStorage.getItem('pf_team_ctx')||'';}catch{return '';}});
  const setTeamCtx=useCallback((id,forceDev=false)=>{
    if(cu&&cu.role!=='Admin'&&cu.role!=='Manager'&&!forceDev)return;
    setTeamCtxRaw(id);
    try{localStorage.setItem('pf_team_ctx',id||'');}catch{}
  },[cu]);
  const [dmUnread,setDmUnread]=useState([]);
  const [globalSearch,setGlobalSearch]=useState('');
  const [showGlobalSearch,setShowGlobalSearch]=useState(false);
  const [searchSubtasks,setSearchSubtasks]=useState([]);const [wsName,setWsName]=useState('');const [wsDmEnabled,setWsDmEnabled]=useState(true);const [dmTargetUser,setDmTargetUser]=useState(null);
  const [onlineUsers,setOnlineUsers]=useState(new Set());

  // Presence heartbeat — ping every 30s, fetch online users every 15s
  useEffect(()=>{
    if(!cu)return;
    const fetchPresence=()=>api.get('/api/presence').then(ids=>{
      if(Array.isArray(ids)&&ids.length>=0)setOnlineUsers(new Set(ids));
    }).catch(()=>{});
    const beat=()=>api.post('/api/presence',{}).then(()=>fetchPresence()).catch(()=>{});
    // Fire immediately on mount
    fetchPresence(); // fetch current online users right away (don't wait for beat)
    beat();          // then beat + fetch again
    const beatId=setInterval(beat,15000);
    window.addEventListener('focus',()=>{beat();});
    const presId=setInterval(fetchPresence,20000); // reduced 8s→20s
    return()=>{clearInterval(beatId);clearInterval(presId);};
  },[cu]);
  const [showReminders,setShowReminders]=useState(false);const [reminderTask,setReminderTask]=useState(null);const [upcomingReminders,setUpcomingReminders]=useState([]);
  const [showNotifBanner,setShowNotifBanner]=useState(false);
  const [toasts,setToasts]=useState([]);
  const toastTimers=useRef({});
  const TOAST_DUR=6000; // ms before auto-dismiss

  const addToast=useCallback((type,title,body)=>{
    const id='t'+Date.now()+Math.random();
    const timeStr=new Date().toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit'});
    setToasts(prev=>[{id,type,title,body,timeStr,progress:100,leaving:false},...prev].slice(0,5));
    const start=Date.now();
    const tick=setInterval(()=>{
      const elapsed=Date.now()-start;
      const pct=Math.max(0,100-(elapsed/TOAST_DUR*100));
      setToasts(prev=>prev.map(t=>t.id===id?{...t,progress:pct}:t));
      if(elapsed>=TOAST_DUR){clearInterval(tick);dismissToast(id);}
    },100);
    toastTimers.current[id]=tick;
  },[]);

  const dismissToast=useCallback((id)=>{
    if(toastTimers.current[id]){clearInterval(toastTimers.current[id]);delete toastTimers.current[id];}
    setToasts(prev=>prev.map(t=>t.id===id?{...t,leaving:true}:t));
    setTimeout(()=>setToasts(prev=>prev.filter(t=>t.id!==id)),220);
  },[]);

  useEffect(()=>{window._pfToast=addToast;},[addToast]);

  const notify=useCallback((type,title,body,navTo,opts={})=>{
    addToast(type,title,body);
    showBrowserNotif(title,body,()=>setView(navTo),{...opts,tag:opts.tag||type+'-'+Date.now()});
    playSound(type==='call'?'call':'notif');
  },[addToast]);

  useEffect(()=>{
    if(cu&&'Notification' in window&&Notification.permission==='default'){
      setTimeout(()=>setShowNotifBanner(true),2500);
    }
  },[cu]);


  const [teamLoading,setTeamLoading]=useState(false);

  const load=useCallback(async(overrideTeamCtx)=>{
    if(!cu)return;
    const tCtx=overrideTeamCtx!==undefined?overrideTeamCtx:teamCtx;
    try{
      const projUrl=tCtx?'/api/projects?team_id='+tCtx:'/api/projects';
      const taskUrl=tCtx?'/api/tasks?team_id='+tCtx:'/api/tasks';
      const [users,projects,tasks,notifs,dmu,ws,teamsRaw,ticketsRaw]=await Promise.all([
        api.get('/api/users'),api.get(projUrl),api.get(taskUrl), api.get('/api/notifications'),api.get('/api/dm/unread'),api.get('/api/workspace'), api.get('/api/teams'),api.get('/api/tickets'), ]);
      const teams=Array.isArray(teamsRaw)?teamsRaw:[];
      const tickets=Array.isArray(ticketsRaw)?ticketsRaw:[];
      setData({users:Array.isArray(users)?users:[],projects:Array.isArray(projects)?projects:[],tasks:Array.isArray(tasks)?tasks:[],notifs:Array.isArray(notifs)?notifs:[],teams,tickets});
      setDmUnread(Array.isArray(dmu)?dmu:[]);
      if(ws&&ws.name)setWsName(ws.name);
      if(ws)setWsDmEnabled(ws.dm_enabled!==0);
      const rems=await api.get('/api/reminders');
      if(Array.isArray(rems)){const now=new Date();setUpcomingReminders(rems.filter(r=>new Date(r.remind_at)>=now).sort((a,b)=>new Date(a.remind_at)-new Date(b.remind_at)));}
    }catch(e){console.error(e);}
  },[cu]);

  useEffect(()=>{api.get('/api/auth/me').then(u=>{if(u&&!u.error)setCu(u);setLoading(false);}).catch(()=>setLoading(false));},[]);
  // Expose search opener for topbar button
  useEffect(()=>{window._pfOpenSearch=()=>{setShowGlobalSearch(v=>!v);setGlobalSearch('');setSearchSubtasks([]);};},[]);
  // Expose DM target setter for notification click handlers
  useEffect(()=>{window._pfSetDmTarget=(uid)=>{setDmTargetUser(uid);};},[]);
  // Fetch subtask search results
  useEffect(()=>{
    const q=(globalSearch||'').trim();
    if(!q||q.length<2){setSearchSubtasks([]);return;}
    const t=setTimeout(()=>{
      api.get('/api/subtasks/search?q='+encodeURIComponent(q))
        .then(d=>{if(Array.isArray(d))setSearchSubtasks(d);})
        .catch(()=>{});
    },300); // debounce
    return()=>clearTimeout(t);
  },[globalSearch]);
  // Log Time shortcut from TaskModal (vw:logtime event)
  useEffect(()=>{
    const handler=()=>{
      _setView('timesheet');
      // TimesheetView will pick up sessionStorage.ts_prefill on next render
    };
    window.addEventListener('vw:logtime',handler);
    return()=>window.removeEventListener('vw:logtime',handler);
  },[_setView]);

  // Global search shortcut: Cmd+K / Ctrl+K
  useEffect(()=>{
    const h=(e)=>{
      if((e.metaKey||e.ctrlKey)&&e.key==='k'){e.preventDefault();setShowGlobalSearch(v=>!v);setGlobalSearch('');}
      // Shift+L → jump straight to Timesheet
      if(e.shiftKey&&e.key==='L'&&!e.metaKey&&!e.ctrlKey){const tag=document.activeElement&&document.activeElement.tagName;if(tag!=='INPUT'&&tag!=='TEXTAREA'){e.preventDefault();_setView('timesheet');}}
      if(e.key==='Escape')setShowGlobalSearch(false);
    };
    document.addEventListener('keydown',h);
    return()=>document.removeEventListener('keydown',h);
  },[]);
  useEffect(()=>{load();},[load]);

  const prevTeamCtxRef=useRef(teamCtx);
  useEffect(()=>{
    if(!cu)return;
    if(prevTeamCtxRef.current===teamCtx)return; // skip initial mount
    prevTeamCtxRef.current=teamCtx;
    setTeamLoading(true);
    setView('dashboard'); // always go to dashboard on team switch
    setData(prev=>({...prev,projects:[],tasks:[]}));
    load(teamCtx).finally(()=>setTeamLoading(false));
  },[teamCtx,cu]);
  useEffect(()=>{
    if(!cu)return;
    const id=setInterval(async()=>{
      try{
        const projUrl=teamCtx?'/api/projects?team_id='+teamCtx:'/api/projects';
        const taskUrl=teamCtx?'/api/tasks?team_id='+teamCtx:'/api/tasks';
        const [projects,tasks]=await Promise.all([api.get(projUrl),api.get(taskUrl)]);
        if(Array.isArray(projects)&&Array.isArray(tasks)){
          setData(prev=>({...prev,projects,tasks}));
        }
      }catch(e){}
    },30000);
    return()=>clearInterval(id);
  },[cu,teamCtx]);
  useEffect(()=>{
    document.body.className=dark?'dm':'';
    try{
      const saved=JSON.parse(localStorage.getItem('pf_accent')||'null');
      if(saved&&saved.ac){
        const r=document.body.style;
        const hex=saved.ac.replace('#','');const bigint=parseInt(hex,16);
        const ri=Math.round((bigint>>16)&255),gi=Math.round((bigint>>8)&255),bi=Math.round(bigint&255);
        r.setProperty('--ac',saved.ac);r.setProperty('--ac2',saved.ac2||saved.ac);
        r.setProperty('--ac3','rgba('+ri+','+gi+','+bi+','+(dark?'.10':'.15')+')');
        r.setProperty('--ac4','rgba('+ri+','+gi+','+bi+','+(dark?'.06':'.08')+')');
        r.setProperty('--ac-tx',saved.tx||'#0d1f00');
      }
    }catch(e){}
  },[dark]);

  const prevDmsRef=useRef([]);
  useEffect(()=>{
    if(!cu)return;
    api.get('/api/dm/unread').then(d=>{if(Array.isArray(d)){prevDmsRef.current=d;setDmUnread(d);}});
    const id=setInterval(()=>{
      api.get('/api/dm/unread').then(d=>{
        if(!Array.isArray(d))return;
        const prev=prevDmsRef.current;
        d.forEach(x=>{
          const old=prev.find(p=>p.sender===x.sender);
          if(!old||(x.cnt||0)>(old.cnt||0)){
            const sender=data.users.find(u=>u.id===x.sender);
            const sname=sender?sender.name:'Someone';
            window._pfToast&&window._pfToast('dm','💬 New message from '+sname,'Tap to open Direct Messages');
            showBrowserNotif('💬 '+sname,'New message',()=>{setDmTargetUser(x.sender);_setView('dm');window.focus();},{tag:'dm-'+x.sender});
            playSound('notif');
          }
        });
        prevDmsRef.current=d;
        setDmUnread(d);
      });
    },5000);
    return()=>clearInterval(id);
  },[cu]); // intentionally omit data.users to avoid reset — sender name is best-effort

  const prevNotifIdsRef=useRef(null); // null = not yet seeded
  const NTITLES={
    task_assigned:'✅ Task assigned to you', status_change:'🔄 Task status changed', comment:'💬 New comment on task', deadline:'⏰ Deadline approaching', dm:'📨 New direct message', project_added:'📁 Added to a project', reminder:'⏰ Reminder', call:'📞 Huddle call', message:'#️⃣ New channel message', };
  const NNAV={task_assigned:'tasks',status_change:'tasks',comment:'tasks',deadline:'tasks',dm:'dm',project_added:'projects',reminder:'reminders',call:'dm',message:'messages'};
  useEffect(()=>{
    if(!cu)return;

    const pollOnce=()=>{
      api.get('/api/notifications').then(d=>{
        if(!Array.isArray(d))return;
        if(prevNotifIdsRef.current===null){
          prevNotifIdsRef.current=new Set(d.map(n=>n.id));
          setData(prev=>({...prev,notifs:d}));
          return;
        }
        const brandNew=d.filter(n=>!prevNotifIdsRef.current.has(n.id));
        brandNew.forEach(n=>{
          if(n.type==='dm')return; // DMs handled by separate poll
          if(n.type==='call') return;
          const title=NTITLES[n.type]||'VEWIT';
          const nav=NNAV[n.type]||'notifs';
          addToast(n.type,title,n.content||'');
          showBrowserNotif(title,n.content||'',()=>{
            window.focus();
            if(n.type==='dm'){const sid=n.sender_id||n.sender;if(sid)setDmTargetUser(sid);_setView('dm');}
            else{_setView(nav);}
          },{tag:'notif-'+n.id});
          playSound('notif');
        });
        prevNotifIdsRef.current=new Set(d.map(n=>n.id));
        setData(prev=>({...prev,notifs:d}));
        const unread=d.filter(n=>!n.read).length;
        const dmTotal=dmUnread.reduce((a,x)=>a+(x.cnt||0),0);
        updateBadge(unread+dmTotal);
      });
    };

    api.get('/api/notifications').then(d=>{
      if(Array.isArray(d)){
        prevNotifIdsRef.current=new Set(d.map(n=>n.id));
        setData(prev=>({...prev,notifs:d}));
        const unread=d.filter(n=>!n.read).length;
        updateBadge(unread+dmUnread.reduce((a,x)=>a+(x.cnt||0),0));
      }
    });

    triggerPollRef.current=pollOnce;

    const id=setInterval(pollOnce, 30000); // reduced from 6s → 30s (5× less DB load)
    return()=>{ clearInterval(id); if(triggerPollRef.current===pollOnce) triggerPollRef.current=null; };
  },[cu,addToast]);

  const onDmRead=useCallback(sid=>{
    setDmUnread(prev=>prev.filter(x=>x.sender!==sid));
    // Also clear DM notifications from this sender in the panel
    setData(prev=>{
      const toDelete=prev.notifs.filter(n=>n.type==='dm'&&(n.sender_id===sid||n.sender===sid));
      toDelete.forEach(n=>{
        api.del('/api/notifications/'+n.id).catch(()=>{});
      });
      return {...prev,notifs:prev.notifs.filter(n=>!(n.type==='dm'&&(n.sender_id===sid||n.sender===sid)))};
    });
  },[]);
  const logout=async()=>{
    if(window._pfPushUnsubscribe) await window._pfPushUnsubscribe().catch(()=>{});
    try{ await api.post('/api/auth/logout',{}); }catch(e){}
    setCu(null);setData({users:[],projects:[],tasks:[],notifs:[]});setDmUnread([]);
    // Redirect to login immediately — clears all state and shows auth page
    window.location.href='/?action=login';
  };

  useEffect(()=>{if(cu)requestNotifPermission();},[cu]);

  const triggerPollRef = useRef(null);
  useEffect(()=>{
    window._pfOnVisible = ()=>{
      if(triggerPollRef.current) triggerPollRef.current();
    };
    return ()=>{ window._pfOnVisible = null; };
  },[]);

  useEffect(()=>{
    const unread=safe(data.notifs).filter(n=>!n.read).length;
    const dmTotal=dmUnread.reduce((a,x)=>a+(x.cnt||0),0);
    updateBadge(unread+dmTotal);
  },[data.notifs,dmUnread]);

  const firedEarlyRef=useRef(new Set());
  useEffect(()=>{
    if(!cu)return;
    const checkDue=async()=>{
      const due=await api.get('/api/reminders/due');
      if(Array.isArray(due)&&due.length>0){
        due.forEach(r=>{
          addToast('reminder','⏰ Reminder: '+r.task_title,'Click to view');
          showBrowserNotif('⏰ '+r.task_title,'Reminder is due now!',()=>{
            setView('reminders');
            if(window.electronAPI){window.electronAPI.focusWindow();}else{window.focus();}
          },{tag:'rem-'+r.id,requireInteraction:true});
          playSound('reminder');
        });
      }
      const rems=await api.get('/api/reminders');
      if(Array.isArray(rems)){
        const now=new Date();
        rems.forEach(r=>{
          const remAt=new Date(r.remind_at);
          const minsBefore=r.minutes_before||0;
          if(minsBefore>0){
            const warnAt=new Date(remAt.getTime()-minsBefore*60000);
            const diff=warnAt-now;
            const earlyKey='early-'+r.id+'-'+minsBefore;
            if(diff>=-60000&&diff<=60000&&!firedEarlyRef.current.has(earlyKey)){
              firedEarlyRef.current.add(earlyKey);
              addToast('reminder','⏰ Coming up in '+minsBefore+'min',r.task_title);
              showBrowserNotif('⏰ Reminder in '+minsBefore+' min',r.task_title,()=>{
                setView('reminders');
                if(window.electronAPI){window.electronAPI.focusWindow();}else{window.focus();}
              },{tag:earlyKey,requireInteraction:false});
              playSound('reminder');
            }
          }
        });
        setUpcomingReminders(rems.filter(r=>!r.fired&&new Date(r.remind_at)>=now).sort((a,b)=>new Date(a.remind_at)-new Date(b.remind_at)));
      }
    };
    checkDue();
    const id=setInterval(checkDue,30000);
    return()=>clearInterval(id);
  },[cu,addToast]);

  const isDevRole=cu&&cu.role!=='Admin'&&cu.role!=='Manager';
  const [devNoTeam,setDevNoTeam]=useState(false);
  useEffect(()=>{
    if(!isDevRole||!cu||safe(data.teams).length===0)return;
    const myTeams=safe(data.teams).filter(t=>{
      try{return JSON.parse(t.member_ids||'[]').includes(cu.id);}catch{return false;}
    });
    if(myTeams.length===0){setDevNoTeam(true);return;}
    setDevNoTeam(false);
    if(!teamCtx){
      setTeamCtx(myTeams[0].id,true); // forceDev=true bypasses lock
    } else {
      const valid=myTeams.find(t=>t.id===teamCtx);
      if(!valid)setTeamCtx(myTeams[0].id,true);setView('dashboard');
    }
  },[cu,isDevRole,data.teams,teamCtx,setTeamCtx]);

  const activeTeam=useMemo(()=>teamCtx?safe(data.teams).find(t=>t.id===teamCtx)||null:null,[teamCtx,data.teams]);
  const teamMemberIds=useMemo(()=>activeTeam?new Set(JSON.parse(activeTeam.member_ids||'[]')):new Set(),[activeTeam]);
  const scopedProjects=data.projects;
  const scopedTasks=data.tasks;
  const scopedUsers=useMemo(()=>{
    if(!activeTeam)return data.users;
    return safe(data.users).filter(u=>teamMemberIds.has(u.id));
  },[data.users,activeTeam,teamMemberIds]);

  if(loading)return html`<${AppLoader}/>`;
  if(!cu)return html`<${AuthScreen} onLogin=${u=>{setCu(u);}}/>`;

  if(isDevRole && devNoTeam && safe(data.teams).length>0) return html`
    <div style=${{display:'flex',alignItems:'center',justifyContent:'center',height:'100vh',background:'var(--bg)',flexDirection:'column',gap:16,padding:24}}>
      <div style=${{width:72,height:72,borderRadius:20,background:'var(--sf)',border:'1px solid var(--bd)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:34}}>🏷</div>
      <div style=${{textAlign:'center',maxWidth:380}}>
        <h2 style=${{fontSize:18,fontWeight:700,color:'var(--tx)',marginBottom:8}}>Not assigned to a team yet</h2>
        <p style=${{fontSize:13,color:'var(--tx2)',lineHeight:1.6}}>You haven't been added to any team. Ask your Admin to assign you to a team before you can access the workspace.</p>
        <div style=${{marginTop:16,padding:'10px 16px',background:'var(--sf)',borderRadius:12,border:'1px solid var(--bd)',fontSize:12,color:'var(--tx3)'}}>
          Logged in as <b style=${{color:'var(--tx)'}}>${cu.name}</b> · ${cu.email}
        </div>
      </div>
      <button class="btn bg" style=${{fontSize:12,marginTop:4}} onClick=${logout}>Sign out</button>
    </div>`;

  const unread=safe(data.notifs).filter(n=>!n.read).length;
  const totalDm=dmUnread.reduce((a,x)=>a+(x.cnt||0),0);

  const activeTeamName=activeTeam?activeTeam.name:'';
  const TITLES={
    dashboard:{title:'Dashboard',sub:activeTeamName?activeTeamName+' Team Dashboard':'Overview of your work'}, projects:{title:'Projects',sub:scopedProjects.length+' projects'+(activeTeamName?' · '+activeTeamName:'')}, tasks:{title:'Kanban Board',sub:scopedTasks.filter(t=>t.stage!=='completed'&&t.stage!=='backlog').length+' active · '+scopedTasks.length+' total'+(activeTeamName?' · '+activeTeamName:'')}, messages:{title:'Channels',sub:(activeTeamName?activeTeamName+' · ':'')+'Project channels'}, dm:{title:'Direct Messages',sub:totalDm>0?totalDm+' unread':'Private conversations'}, reminders:{title:'Reminders',sub:'Upcoming task reminders'}, notifs:{title:'Notifications',sub:unread+' unread'}, team:{title:'Team Management',sub:'Members & sub-teams'}, settings:{title:'Settings',sub:wsName||'Workspace configuration'}, timeline:{title:'Timeline Tracker',sub:activeTeamName?activeTeamName+' project timeline':'Project schedule'}, productivity:{title:'Dev Productivity',sub:activeTeamName?activeTeamName+' performance':'Team performance analytics'}, tickets:{title:'Tickets',sub:activeTeamName?activeTeamName+' tickets':'Support tickets'}, 'ai-docs':{title:'AI Documentation',sub:'Generate docs & architecture diagrams'}, timesheet:{title:'Timesheet',sub:'Log hours · export reports · track productivity'}, };

  const baseView=(view||'dashboard').split(':')[0];
  const viewParts=view.split(':');
  const taskFilterType=viewParts[1]||null;
  const taskFilterValue=viewParts[2]||null;
  const ticketFilterType=baseView==='tickets'?(viewParts[1]||null):null;
  const ticketFilterValue=baseView==='tickets'?(viewParts[2]||null):null;
  const info=TITLES[baseView]||{title:baseView,sub:''};
  const extra=null;

  return html`
    <div style=${{display:'flex',width:'100vw',height:'100vh',background:'var(--bg)',overflow:'hidden'}}>
      <${Sidebar} cu=${cu} view=${baseView} setView=${v=>{
          if(typeof v==='string'&&v.startsWith('dm:')){const uid=v.slice(3);setDmTargetUser(uid);_setView('dm');}
          else _setView(v);
        }} onLogout=${logout} unread=${unread} dmUnread=${dmUnread} col=${col} setCol=${v=>{setCol(v);try{localStorage.setItem('pf_col',v?'1':'0');}catch{}}} wsName=${wsName}
        dark=${dark} setDark=${setDark} wsDmEnabled=${wsDmEnabled} onlineUsers=${onlineUsers}
        teams=${data.teams} users=${data.users} projects=${scopedProjects} tasks=${scopedTasks}
        teamCtx=${teamCtx} setTeamCtx=${setTeamCtx} activeTeam=${activeTeam}
        />
      <div style=${{flex:1,display:'flex',flexDirection:'column',overflow:'hidden',minWidth:0}}>
        <${Header} title=${info.title} sub=${info.sub} dark=${dark} setDark=${setDark} extra=${extra}
          cu=${cu} setCu=${setCu} upcomingReminders=${upcomingReminders} onViewReminders=${()=>setView('reminders')}
          notifs=${data.notifs}
          activeTeam=${activeTeam} teams=${data.teams} setTeamCtx=${setTeamCtx}
          onNotifClick=${async n=>{
            // Mark read + DELETE from panel immediately (natural notification behaviour)
            api.put('/api/notifications/'+n.id+'/read',{}).catch(()=>{});
            api.del('/api/notifications/'+n.id).catch(()=>{});
            // Remove from local state instantly — panel clears without waiting for reload
            setData(prev=>({...prev,notifs:prev.notifs.filter(x=>x.id!==n.id)}));
            const nav={task_assigned:'tasks',status_change:'tasks',comment:'tasks',deadline:'tasks',dm:'dm',project_added:'projects',reminder:'reminders',call:'dm',message:'messages'};
            const dest=nav[n.type]||'notifs';
            // DM: open sender's chat thread
            if(n.type==='dm'||n.type==='message'){
              const senderId=n.sender_id||n.sender||null;
              if(senderId)setDmTargetUser(senderId);
            }
            // Call: open Jitsi with the caller directly
            if(n.type==='call'){
              const senderId=n.sender_id||n.sender||null;
              if(senderId){
                const callerUser=data.users.find(u=>u.id===senderId);
                if(senderId)setDmTargetUser(senderId);
              }
            }
            setView(dest);
          }}
          onMarkAllRead=${async()=>{await api.put('/api/notifications/read-all',{});load();}}
          onClearAll=${async()=>{await api.del('/api/notifications/all');load();}}
        />
        <div style=${{flex:1,overflow:'hidden',display:'flex',flexDirection:'column'}}>
          <${ErrorBoundary}>
            <div key=${baseView+'-'+(teamCtx||'all')} class="page-enter" style=${{flex:1,overflow:'hidden',display:'flex',flexDirection:'column',height:'100%'}}>
            ${baseView==='dashboard'?html`<${Dashboard} cu=${cu} tasks=${scopedTasks} projects=${scopedProjects} users=${scopedUsers} onNav=${setView} activeTeam=${activeTeam} teams=${data.teams} setTeamCtx=${setTeamCtx}/>`:null}
            ${baseView==='projects'?html`<${ProjectsView} projects=${scopedProjects} tasks=${scopedTasks} users=${data.users} cu=${cu} reload=${load} onSetReminder=${t=>{setReminderTask(t);}} teams=${data.teams} activeTeam=${activeTeam} initialProjectId=${initialProjectId} onClearInitial=${()=>setInitialProjectId(null)}/>`:null}
            ${baseView==='tasks'?html`<${TasksView} tasks=${scopedTasks} projects=${scopedProjects} users=${scopedUsers} cu=${cu} reload=${load} onSetReminder=${t=>{setReminderTask(t);}} teams=${data.teams} activeTeam=${activeTeam}
              initialStage=${taskFilterType==='stage'?taskFilterValue:null}
              initialPriority=${taskFilterType==='priority'?taskFilterValue:null}
              initialAssignee=${taskFilterType==='assignee'?taskFilterValue:null}
            />`:null}
            ${baseView==='messages'?html`<${MessagesView} projects=${scopedProjects} users=${data.users} cu=${cu} tasks=${scopedTasks} key=${'msgs-'+(teamCtx||'all')}/>`:null}
            ${baseView==='dm'?html`<${DirectMessages} cu=${cu} users=${data.users} dmUnread=${dmUnread} onDmRead=${onDmRead} dmEnabled=${wsDmEnabled} initialUserId=${dmTargetUser} onClearInitial=${()=>setDmTargetUser(null)} onlineUsers=${onlineUsers}/>`:null}
            ${baseView==='reminders'?html`<${RemindersView} cu=${cu} tasks=${scopedTasks} projects=${scopedProjects} onSetReminder=${t=>{setReminderTask(t);}} onReload=${load}/>`:null}
            ${baseView==='notifs'?html`<${NotifsView} notifs=${data.notifs} reload=${load} onNavigate=${setView}/>`:null}
            ${baseView==='tickets'?html`<${TicketsView} cu=${cu} users=${scopedUsers} projects=${scopedProjects} onReload=${load} activeTeam=${activeTeam} initialAssignee=${ticketFilterType==='assignee'?ticketFilterValue:null} initialStatus=${ticketFilterType==='status'?ticketFilterValue:null}/>`:null}
            ${baseView==='team'&&(cu.role==='Admin'||cu.role==='Manager'||cu.role==='TeamLead')?html`<${TeamView} users=${data.users} cu=${cu} reload=${load}/>`:null}
            ${baseView==='settings'&&(cu.role==='Admin'||cu.role==='Manager'||cu.role==='TeamLead')?html`<${WorkspaceSettings} cu=${cu} onReload=${load}/>`:null}
            ${baseView==='timeline'?html`<${TimelineView} cu=${cu} tasks=${scopedTasks} projects=${scopedProjects} onNav=${(v,pid)=>{setView(v);if(pid)setInitialProjectId(pid);else setInitialProjectId(null);}}/>`:null}
            ${baseView==='productivity'&&(cu.role==='Admin'||cu.role==='Manager')?html`<${ProductivityView} cu=${cu} tasks=${scopedTasks} projects=${scopedProjects} users=${scopedUsers}/>`:null}
            ${baseView==='ai-docs'?html`<${AiDocsView} cu=${cu} projects=${scopedProjects} tasks=${scopedTasks} users=${data.users}/>`:null}
            ${baseView==='timesheet'?html`<${TimesheetView} cu=${cu} teams=${data.teams} users=${data.users} projects=${scopedProjects} tasks=${scopedTasks}/>`:null}
            </div>
          <//>
        </div>
      </div>
    </div>
    <${AIAssistant} cu=${cu} projects=${scopedProjects} tasks=${scopedTasks} users=${data.users}/>
    <!-- Global Search Spotlight — Cmd+K -->
    ${showGlobalSearch?html`
      <div style=${{position:'fixed',inset:0,background:'rgba(0,0,0,.55)',zIndex:9800,display:'flex',alignItems:'flex-start',justifyContent:'center',paddingTop:'10vh',backdropFilter:'blur(4px)'}}
        onClick=${e=>{if(e.target===e.currentTarget)setShowGlobalSearch(false);}}>
        <div style=${{width:'min(640px,92vw)',background:'var(--sf)',borderRadius:16,boxShadow:'0 24px 80px rgba(0,0,0,.35)',border:'1px solid var(--bd)',overflow:'hidden'}}>
          <!-- Search input -->
          <div style=${{display:'flex',alignItems:'center',gap:10,padding:'14px 18px',borderBottom:'1px solid var(--bd)'}}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--tx3)" strokeWidth="2.5" strokeLinecap="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
            <input autoFocus class="inp" style=${{border:'none',background:'transparent',fontSize:16,flex:1,height:28,outline:'none',color:'var(--tx)'}}
              placeholder="Search by ID (T-xxx, tkt-xxx) or name... (Ctrl+K)"
              value=${globalSearch}
              onInput=${e=>setGlobalSearch(e.target.value)}
              onKeyDown=${e=>{if(e.key==='Escape')setShowGlobalSearch(false);}}/>
            <span style=${{fontSize:10,color:'var(--tx3)',background:'var(--sf2)',padding:'2px 6px',borderRadius:5,border:'1px solid var(--bd)'}}>ESC</span>
          </div>
          <!-- Results -->
          <div style=${{maxHeight:400,overflowY:'auto'}}>
            ${(()=>{
              const q=(globalSearch||'').trim().toLowerCase();
              if(!q||q.length<1)return html`
  <div style=${{padding:'16px 18px'}}> 
    <div style=${{fontSize:12,color:'var(--tx2)',fontWeight:600,marginBottom:8}}>Search by:</div>
    <div style=${{display:'flex',gap:8,flexWrap:'wrap'}}>
      ${[['Task ID','T-015-305','#1d4ed8'],['Bug','T-xxx bug','#b91c1c'],['Ticket ID','TK-xxx','#c2410c'],['Subtask','ST-xxx','#475569'],['Project name','SecOps','#15803d']].map(([label,ex,color])=>html`
        <div style=${{padding:'4px 10px',borderRadius:7,background:color+'11',border:'1px solid '+color+'33',fontSize:11,color,cursor:'pointer',fontWeight:600}}
          onClick=${()=>setGlobalSearch(ex)}>
          ${label}
        </div>`)}
    </div>
  </div>
`;
              const results=[];
              // Search tasks by ID or title
              safe(data.tasks).forEach(t=>{
                if(!t||!t.id||!t.title)return;
                const tid=(t.id||'').toLowerCase();
                const ttl=(t.title||'').toLowerCase();
                if(tid.includes(q)||ttl.includes(q)){
                  const proj=(data.projects||[]).find(p=>p.id===t.project);
                  results.push({type:t.task_type||'task',id:t.id,title:t.title,sub:proj?proj.name:'',color:TYPE_COLORS[t.task_type||'task']||'#1d4ed8',bg:TYPE_BG[t.task_type||'task']||'rgba(29,78,216,0.1)',item:t,nav:'tasks'});
                }
              });
              // Search tickets by ID, title, or description
              safe(data.tickets||[]).forEach(t=>{
                const tStr=(t.id+' '+(t.title||'')+' '+(t.description||'')).toLowerCase();
                if(tStr.includes(q)){
                  const tColors={bug:'#b91c1c',feature:'#1d4ed8',improvement:'#0e7490',task:'#15803d',question:'#6d28d9'};
                  const tBg={bug:'rgba(185,28,28,0.10)',feature:'rgba(29,78,216,0.10)',improvement:'rgba(14,116,144,0.10)',task:'rgba(21,128,61,0.10)',question:'rgba(109,40,217,0.10)'};
                  results.push({type:'ticket',id:t.id,title:t.title,sub:(t.type||'bug')+' · '+(t.status||'open'),color:tColors[t.type]||'#c2410c',bg:tBg[t.type]||'rgba(194,65,12,0.10)',item:t});
                }
              });
              // Search subtasks by title
              // (subtasks fetched lazily — skip for global search)
              // Search projects
              safe(data.projects).forEach(p=>{
                if(!p||!p.id||!p.name)return;
                if(p.id.toLowerCase().includes(q)||(p.name||'').toLowerCase().includes(q)){
                  results.push({type:'project',id:p.id,title:p.name,sub:'Project',color:p.color||'#1d4ed8',bg:'rgba(29,78,216,0.06)',item:p,nav:'projects'});
                }
              });
              // Subtask search results (async fetched)
              safe(searchSubtasks).forEach(s=>{
                if(!s||!s.id)return;
                results.push({type:'subtask',id:s.id.slice(0,12),title:s.title||'',sub:'↳ '+(s.task_title||'Task'),color:'#475569',bg:'rgba(71,85,105,0.10)',item:s,nav:'tasks'});
              });
              if(!results.length)return html`<div style=${{padding:'20px',textAlign:'center',color:'var(--tx3)',fontSize:13}}>No results for "${q}"</div>`;
              return results.slice(0,15).map((r,i)=>html`
                <div key=${i}
                  onClick=${()=>{
                    setShowGlobalSearch(false);
                    if(r.nav==='projects'){setView('projects');setInitialProjectId(r.item.id);}
                    else if(r.type==='ticket'){setView('tickets');}
                    else{setView('tasks');}
                  }}
                  style=${{display:'flex',alignItems:'center',gap:12,padding:'10px 18px',cursor:'pointer',borderBottom:'1px solid var(--bd)',transition:'background .1s'}}
                  onMouseEnter=${e=>e.currentTarget.style.background='var(--sf2)'}
                  onMouseLeave=${e=>e.currentTarget.style.background='transparent'}>
                  <span style=${{fontSize:9,fontWeight:800,padding:'2px 7px',borderRadius:4,background:r.bg,color:r.color,border:'1px solid '+r.color+'44',flexShrink:0,textTransform:'uppercase'}}>${r.type}</span>
                  <span style=${{fontSize:9,fontWeight:700,fontFamily:'monospace',padding:'2px 7px',borderRadius:4,background:r.bg,color:r.color,border:'1px solid '+r.color+'33',flexShrink:0}}>${r.id}</span>
                  <span style=${{fontSize:13,color:'var(--tx)',fontWeight:500,flex:1,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${r.title}</span>
                  ${r.sub?html`<span style=${{fontSize:11,color:'var(--tx3)',flexShrink:0}}>${r.sub}</span>`:null}
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--tx3)" strokeWidth="2" strokeLinecap="round"><path d="M9 18l6-6-6-6"/></svg>
                </div>`);
            })()}
          </div>
          <!-- Footer -->
          <div style=${{padding:'8px 18px',borderTop:'1px solid var(--bd)',display:'flex',gap:12,fontSize:11,color:'var(--tx3)'}}>
            <span>↵ Open</span><span>↑↓ Navigate</span><span style=${{marginLeft:'auto'}}>Ctrl+K to close</span>
          </div>
        </div>
      </div>`:null}

        ${teamLoading?html`
      <div style=${{position:'fixed',top:0,left:0,right:0,bottom:0,zIndex:9999, background:'rgba(0,0,0,.55)',display:'flex',alignItems:'center',justifyContent:'center', backdropFilter:'blur(2px)'}}>
        <div style=${{background:'var(--sf)',borderRadius:16,padding:'24px 32px',display:'flex',flexDirection:'column',alignItems:'center',gap:12,border:'1px solid var(--bd)',boxShadow:'0 8px 40px rgba(0,0,0,.5)'}}>
          <div style=${{width:40,height:40,border:'3px solid var(--bd)',borderTop:'3px solid var(--ac)',borderRadius:'50%',animation:'sp .7s linear infinite'}}></div>
          <div style=${{fontSize:13,fontWeight:600,color:'var(--tx)'}}>Switching to ${activeTeam?activeTeam.name:'workspace'}...</div>
          <div class="tx3-11">Loading team data</div>
        </div>
      </div>`:null}

    <${ToastStack} toasts=${toasts} onDismiss=${dismissToast} onNav=${setView}/>

    ${showNotifBanner?html`
      <div style=${{position:'fixed',bottom:20,left:'50%',transform:'translateX(-50%)',zIndex:9100, background:'var(--sf)',border:'1px solid rgba(90,140,255,.30)',borderRadius:18, padding:'16px 20px',boxShadow:'0 8px 40px rgba(0,0,0,.7)', display:'flex',alignItems:'flex-start',gap:14,maxWidth:440, animation:'slideUp .3s cubic-bezier(.34,1.56,.64,1)'}}>
        <div style=${{width:44,height:44,borderRadius:13,background:'linear-gradient(135deg,rgba(90,140,255,.18),rgba(90,140,255,.05))',border:'1px solid rgba(90,140,255,.30)', display:'flex',alignItems:'center',justifyContent:'center',flexShrink:0,fontSize:22}}>🔔</div>
        <div style=${{flex:1,minWidth:0}}>
          <div style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em',marginBottom:4}}>Enable desktop notifications</div>
          <div style=${{fontSize:11,color:'var(--tx2)',lineHeight:1.55,marginBottom:10}}>
            Stay informed even when the app is minimised or you're in another tab:
          </div>
          <div style=${{display:'flex',flexWrap:'wrap',gap:5,marginBottom:12}}>
            ${['✅ Task assigned','🔄 Status changes','💬 Comments','📁 Project updates','⏰ Reminders'].map(tag=>html`
              <span key=${tag} style=${{fontSize:10,padding:'2px 8px',borderRadius:100,background:'rgba(90,140,255,.08)',border:'1px solid rgba(90,140,255,.18)',color:'var(--ac)',fontWeight:600}}>${tag}</span>`)}
          </div>
          <div style=${{display:'flex',gap:7}}>
            <button class="btn bp" style=${{padding:'7px 16px',fontSize:12}}
              onClick=${()=>{requestNotifPermission();setShowNotifBanner(false);}}>🔔 Allow Notifications</button>
            <button class="btn bg" style=${{padding:'7px 12px',fontSize:11}}
              onClick=${()=>setShowNotifBanner(false)}>Later</button>
          </div>
        </div>
        <button class="btn bg" style=${{padding:'4px 8px',fontSize:11,flexShrink:0,alignSelf:'flex-start'}}
          onClick=${()=>setShowNotifBanner(false)}>✕</button>
      </div>`:null}

    ${reminderTask!==null?html`<${ReminderModal} task=${reminderTask} onClose=${()=>setReminderTask(null)} onSaved=${()=>{setReminderTask(null);load();}}/>`:null}
    ${showReminders?html`<${RemindersPanel} onClose=${()=>{setShowReminders(false);load();}} onReload=${load}/>`:null}`;
}

ReactDOM.createRoot(document.getElementById('root')).render(html`<${ErrorBoundary}><${App}<//>`);
if(window._vwHideBoot)window._vwHideBoot();
};
waitForLibs(window._pfStartApp);
})();