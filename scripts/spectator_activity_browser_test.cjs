// Run only against the dedicated loopback test portal described in the acceptance document.
const {chromium}=require('playwright');
const fs=require('fs');
(async()=>{
 fs.mkdirSync('runtime/astra', {recursive:true});
 fs.writeFileSync('runtime/astra/activity-test-token','activity-test-token');
 let downloadServer;
 const browser=await chromium.launch({headless:true,executablePath:'/usr/bin/google-chrome',args:['--no-sandbox']});
 try {
  const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});
  const origin='http://127.0.0.1:8182';
  const csrf=(await (await context.request.get(origin+'/api/auth/csrf')).json()).data.token;
  const tokenFile='runtime/astra/activity-ui-data/secrets/bootstrap-token';
  const bootstrap=fs.existsSync(tokenFile);
  const response=await context.request.post(origin+'/api/auth/'+(bootstrap?'bootstrap':'login'),{
   headers:{'X-CSRF-TOKEN':csrf},data:bootstrap?{token:fs.readFileSync(tokenFile,'utf8').trim(),password:'ActivityTest9!',confirmPassword:'ActivityTest9!'}:{username:'admin',password:'ActivityTest9!',rememberMe:false}});
  if(!response.ok())throw Error('Local test authentication failed '+response.status());
  require('child_process').execFileSync('python3',['-c',`
import sqlite3
c=sqlite3.connect('runtime/astra/activity-ui-data/portal.sqlite3')
uid=c.execute('select Id from AspNetUsers limit 1').fetchone()[0]
def insert(table,overrides):
 data={name:(0 if typ in ('INTEGER','REAL') else '') for _,name,typ,required,default,pk in c.execute('pragma table_info('+table+')') if required and default is None}
 data.update(overrides)
 c.execute('insert or replace into '+table+'('+','.join(data)+') values('+','.join('?' for _ in data)+')',list(data.values()))
insert('PortalMatches',{'MatchId':'match-activity-preview','OwnerUserId':uid,'DisplayName':'Activity test','Status':'completed','CreatedAt':'2026-09-08 00:00:00+00:00','UpdatedAt':'2026-09-08 00:00:00+00:00'})
insert('PortalLobbySeats',{'MatchId':'match-activity-preview','SeatIndex':0,'ControllerKind':'agent','AgentId':'agent-preview'})
c.commit()
`]);
  let phase='running',tick=0;
  const match='match-activity-preview';
  const seats=[{seatIndex:0,controllerKind:'agent',agentId:'agent-preview',playerHandle:'Commissioner Pravin Lal',factionName:'Peacekeeping Forces',status:'running',managed:true,instanceId:'instance-preview',canSpectate:true},{seatIndex:1,controllerKind:'native',playerHandle:'Human Hive',status:'running',managed:true,instanceId:'instance-bot',canSpectate:true}];
  await context.route('**/api/lobbies/'+match,route=>route.fulfill({json:{ok:true,data:{matchId:match,displayName:'Peacekeepers · Fresh campaign',mode:'standard',status:phase,currentTurn:24,currentYear:2124,seats,settings:{},createdAt:new Date().toISOString(),updatedAt:new Date().toISOString()}}}));
  const event=(id,request,payload)=>({event_id:id,recorded_unix:1788880000+Number(request.slice(1)),correlation:{run_id:'run-preview',request_id:request,call_id:'call-'+request},payload});
  const initial=[];
  for(let i=0;i<12;i++) { const r='r'+i;
   initial.push(event('s'+i,r,{type:'response_started',model:'Qwen3.8-27B',reasoning_effort:'low'}));
   initial.push(event('d'+i,r,{type:'response_delta',chunks:[{choices:[{delta:{reasoning_content:'The current mineral surplus is positive. I will check the production receipt before committing the next order.',content:i===11?'The Colony Pod is progressing. I’ll finish the nearby farm and verify the next founding site.':'I’m reviewing the current base and unit state before selecting the next action. The order receipt will confirm whether it took effect.'}}]}]}));
   initial.push(event('t'+i,r,{type:'tool_requested',managed_name:'smac_world',arguments:{view:'expansion'}}));
   initial.push(event('o'+i,r,{type:'tool_returned',managed_name:'smac_world',content:JSON.stringify({ok:true,mineral_surplus:3,epistemic_status:'current'})}));
   initial.push(event('f'+i,r,{type:'response_finished',complete:true}));
  }
  await context.route(new RegExp('/api/lobbies/'+match+'/ai-activity/[0-9]+'),route=>{
   const cursor=new URL(route.request().url()).searchParams.get('cursor');
   const events=cursor?[initial[0],event('live'+(++tick),'r11',{type:'response_delta',chunks:[{choices:[{delta:{content:'\nNew activity received.'}}]}]})]:initial;
   return route.fulfill({json:{ok:true,data:{events,cursor:String(tick+1),gaps:[],has_more:false,status:phase}}});
  });
  await context.route('**/stream/**',route=>route.fulfill({contentType:'text/html',body:'<body style="margin:0;height:100vh;background:radial-gradient(ellipse at center,#294938,#142b2e);color:#cfdbbf;display:grid;place-content:center;font:18px monospace;text-align:center"><div style="font-size:38px">◈ PLANET ◈</div><p>Controlled spectator frame</p><small>Peacekeeping Forces · MY 2124</small></body>'}));
  downloadServer=require('http').createServer((req,res)=>{
   if(!req.url.includes('/activity/agent-preview')){res.writeHead(503);res.end();return;}
   const next=new URL(req.url,'http://local').searchParams.get('cursor');
   res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({ok:true,report:{events:next?initial.slice(30):initial.slice(0,30),cursor:'next',has_more:!next,gaps:[]}}));
  });
  await new Promise(resolve=>downloadServer.listen(8183,'127.0.0.1',resolve));
  const page=await context.newPage(); const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(origin+'/spectate/'+match);
  await page.getByRole('button',{name:'AI activity',exact:true}).click();
  await page.locator('.activity-message').nth(11).waitFor();
  await page.screenshot({path:'runtime/astra/activity-desktop.png',fullPage:true});
  await page.locator('.activity-message').last().locator('summary').first().click();
  await page.locator('.activity-message').last().locator('.activity-tools summary').click();
  await page.screenshot({path:'runtime/astra/activity-expanded.png',fullPage:true});
  await page.locator('.activity-feed').evaluate(el=>el.scrollTop=0);
  await page.waitForTimeout(3500);
  if(await page.locator('.activity-feed').evaluate(el=>el.scrollTop)>20)throw Error('Scroll stole position');
  await page.getByRole('button',{name:'New activity ↓'}).click();
  if(!await page.locator('.activity-feed').evaluate(el=>el.scrollHeight-el.scrollTop-el.clientHeight<50))throw Error('Follow failed');
  if(await page.locator('.activity-message').count()!==12)throw Error('Duplicate cards');
  await page.getByRole('button',{name:'Minimize AI activity'}).click();
  await page.getByRole('button',{name:'AI activity',exact:true}).click();
  if(await page.locator('.activity-message').count()!==12)throw Error('Minimize lost state');
  const download=page.waitForEvent('download');await page.getByRole('link',{name:'Download match JSON'}).click();
  const file=await download;await file.saveAs('runtime/astra/activity-export.json');
  const exported=JSON.parse(fs.readFileSync('runtime/astra/activity-export.json'));if(exported.seats[0].events.length!==60)throw Error('Bad export');
  await page.getByRole('button',{name:'Toggle spectator fullscreen'}).click();
  await page.waitForFunction(()=>!!document.fullscreenElement);
  await page.screenshot({path:'runtime/astra/activity-fullscreen.png'});
  phase='completed'; await page.waitForTimeout(5500);
  if(!await page.getByRole('button',{name:'Lobby',exact:true}).isVisible())throw Error('No exit after end');
  await page.getByRole('button',{name:'Toggle spectator fullscreen'}).click();
  for(const width of [768,390]){await page.setViewportSize({width,height:900});await page.screenshot({path:`runtime/astra/activity-${width}.png`,fullPage:true});if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1))throw Error('Horizontal overflow '+width);}
  if(errors.length)throw Error(errors.join('\n'));
  console.log(JSON.stringify({passed:true,desktop_tablet_phone:true,fullscreen:true,end_navigation:true,scroll_follow:true,duplicate_replay:true,minimize_preserves:true,json_export:true,fixture:'controlled API and stream; actual Blazor page and authenticated local portal'}));
 }finally{await browser.close();downloadServer?.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
