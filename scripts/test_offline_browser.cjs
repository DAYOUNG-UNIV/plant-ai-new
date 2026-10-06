/* Offline viewer checks. Launches a fresh headless browser, never a user session.
   Usage: node scripts/test_offline_browser.cjs [tablet.html] [screenshot-directory]
   Requires Playwright and a local Chromium / Edge; no browser download is made. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const {pathToFileURL} = require('node:url');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const htmlPath = path.resolve(process.argv[2] || path.join(root,'artifacts/exhibition/tablet.html'));
const screenshotDir = path.resolve(process.argv[3] || path.join(root,'artifacts/offline-ui-qa'));
const html = fs.readFileSync(htmlPath,'utf8');
const code = html.match(/<script id="offline-app">([\s\S]*?)<\/script>/)[1];
const payload = JSON.parse(html.match(/<script id="plant-payload" type="application\/json">([\s\S]*?)<\/script>/)[1]);
const scope = {module:{exports:{}},Intl};
vm.runInNewContext(code, scope);
const api = scope.module.exports;
let checks = 0;
function check(label, fn) { fn(); checks += 1; console.log('PASS '+label); }
function copy(v) { return JSON.parse(JSON.stringify(v)); }
check('rendered builder payload accepted', ()=>assert.equal(api.validatePayload(payload),payload));
check('no network, storage, dynamic HTML or remote resource in standalone viewer', ()=> {
  assert.doesNotMatch(code,/\b(?:fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon|localStorage|sessionStorage|indexedDB|eval)\b/);
  assert.doesNotMatch(code,/\.innerHTML\b|insertAdjacentHTML|document\.write|\bimport\s*\(/);
  assert.doesNotMatch(html,/<(?:script|img|link|iframe)[^>]+(?:src|href)\s*=\s*["'](?:https?:)?\/\//i);
  assert.doesNotMatch(html,/@import\b|url\(\s*["']?(?:https?:)?\/\//i);
});
check('reject unsupported schema and prototype provenance', ()=> {
  let p=copy(payload);p.schema_version='future';assert.throws(()=>api.validatePayload(p));
  p=copy(payload);p.provenance.kind='__proto__';assert.throws(()=>api.validatePayload(p));
});
check('empty provenance cannot hide sensor numbers', ()=> {
  const p=copy(payload);p.provenance.kind='none';p.overview.record_count=0;p.metrics=[{id:'x',value:22}];assert.throws(()=>api.validatePayload(p));
});
check('gap boundaries and missing values never become a continuous mean line', ()=> {
  const p=[{mean:1},{mean:2},{mean:3,break_before:true},{mean:null},{mean:4},{mean:5,contains_gap:true},{mean:6},{mean:7}];
  assert.equal(JSON.stringify(api.splitSeries(p).map(s=>s.map(x=>x.mean))),JSON.stringify([[1,2],[3],[4],[5],[6,7]]));
});
check('no missing-value coercion to zero', ()=> {
  assert.equal(api.numberLabel(null),'기록 없음');assert.equal(api.numberLabel(0),'0');
  assert.equal(api.dateLabel('2026-10-04T20:30:45.123456+09:00'),'2026-10-04 20:30:45 (한국 시간)');
});
check('local quiz distinguishes correct, incorrect and invalid choices', ()=> {
  const m=payload.missions[0];assert.equal(api.quizResult(m,m.correct_key).correct,true);
  assert.equal(api.quizResult(m,m.choices.find(c=>c.key!==m.correct_key).key).correct,false);
  assert.equal(api.quizResult(m,'injected'),null);
});
const fixture=copy(payload);
fixture.provenance={kind:'synthetic',label:'UI 검사 전용 합성 예시',source_name:'automated-ui-test fixture',last_record_at:'2020-01-01T00:14:00+09:00',notice:'실제 측정 자료가 아닌 자동 UI 테스트 fixture입니다.'};
fixture.overview={record_count:8,period_start:'2020-01-01T00:00:00+09:00',period_end:'2020-01-01T00:14:00+09:00'};
fixture.metrics=[{id:'temperature',label:'온도',value:23,unit:'°C',detail:'자동 검사 fixture의 마지막 측정 행'}, {id:'humidity',label:'공기 습도',value:55,unit:'%RH',detail:'자동 검사 fixture'}, {id:'soil_moisture',label:'토양 센서',value:780,unit:'원시값',detail:'보정하지 않은 원시값 · 자동 검사 fixture'}];
fixture.spotlights=[0,1,2].map(i=>({id:'s'+i,title:['기록의 시작','온도 범위를 살펴본 구간','마지막 기록'][i],period:'2020-01-01 · UI 검사 fixture',summary:'합성 UI 검사 자료입니다. 실제 화분을 측정한 값이 아닙니다.',facts:[{label:'검사 값',value:20+i,unit:'°C'}]}));
fixture.charts=fixture.metrics.map((m,i)=>({id:m.id,label:m.label,unit:m.unit,points:[0,1,2,3,4,5,6,7].map(n=>({t:'2020-01-01T00:'+String(n*2).padStart(2,'0')+':00+09:00',mean:n===3?null:m.value+n,min:n===3?null:m.value+n-1,max:n===3?null:m.value+n+1,count:n===3?0:2,break_before:n===5,contains_gap:n===6}))}));
fixture.guided_qa=[{id:'check',question:'UI 검사 질문',answer:'저장된 안내입니다. 외부 AI를 호출하지 않았습니다.',sources:['로컬 검사 fixture']},{id:'escape',question:'문자 안전성 확인',answer:'<img src="https://invalid.example/x" onerror="globalThis.__offlineXss=1">',sources:['</script><script>globalThis.__offlineXss=2</script>']}];
check('synthetic chart fixture accepted with nulls and gaps', ()=>api.validatePayload(fixture));
check('reject unbounded points, invalid chronology and nonfinite values', ()=> {
  const p=copy(fixture);p.charts[0].points=Array(2001).fill(p.charts[0].points[0]);assert.throws(()=>api.validatePayload(p));
  const q=copy(fixture);q.charts[0].points.reverse();assert.throws(()=>api.validatePayload(q));
  const r=copy(fixture);r.charts[0].points[0].mean=Infinity;assert.throws(()=>api.validatePayload(r));
});
async function waitText(page,selector,fragment) {
  await page.waitForFunction(({selector,fragment})=>document.querySelector(selector).textContent.includes(fragment),{selector,fragment});
}
(async()=> {
  const browser=await chromium.launch({channel:process.env.PLANT_BROWSER_CHANNEL||'msedge',headless:true});
  try {
    const context=await browser.newContext({viewport:{width:1024,height:768},deviceScaleFactor:1,offline:true});
    const requests=[],errors=[];
    await context.route('**/*',route=>{const url=route.request().url();if(/^https?:/.test(url)){requests.push(url);return route.abort();}return route.continue();});
    const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
    await page.goto(pathToFileURL(htmlPath).href);
    assert.equal(await page.locator('#fatal').isVisible(),false);
    assert.equal(await page.locator('.mission').count(),payload.missions.length);
    checks++;console.log('PASS initial offline file renders all missions');
    fs.mkdirSync(screenshotDir,{recursive:true});
    await page.screenshot({path:path.join(screenshotDir,'provided-package-landscape.png'),fullPage:true});
    for (const mission of payload.missions) {
      const card=page.locator('[data-mission-id="'+mission.id+'"]');
      await card.locator('.observe').click();assert.equal(await card.locator('.observe').getAttribute('aria-pressed'),'true');
      await card.locator('summary').click();assert.equal(await card.locator('.quiz-check').isDisabled(),true);
      const wrong=mission.choices.find(c=>c.key!==mission.correct_key).key;
      await card.locator('input[value="'+wrong+'"]').check();await card.locator('.quiz-check').click();assert.match(await card.locator('.feedback').textContent(),/함께 다시/);
      await card.locator('input[value="'+mission.correct_key+'"]').check();await card.locator('.quiz-check').click();assert.match(await card.locator('.feedback').textContent(),/잘 구분/);
    }
    checks++;console.log('PASS every mission observation and incorrect/correct quiz feedback');
    if(payload.guided_qa.length){await page.locator('.question-button').first().click();assert.equal(await page.locator('#answer-body').textContent(),payload.guided_qa[0].answer);}
    await page.locator('#reset-visitor').click();assert.equal(await page.locator('.observe[aria-pressed="true"]').count(),0);assert.equal(await page.locator('input:checked').count(),0);assert.match(await page.locator('#answer-title').textContent(),/어떤 점/);
    checks++;console.log('PASS visitor reset clears choices without replacing package');
    await page.locator('#import-file').setInputFiles({name:'ui-fixture.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(fixture))});
    await waitText(page,'#status','선택한 전시 패키지');assert.equal(await page.locator('#source-pill').textContent(),'합성 예시 기록');assert.equal(await page.locator('.chart-svg').count(),3);assert.equal(await page.locator('.chart-line').count(),3);
    checks++;console.log('PASS local JSON import labels synthetic data and draws isolated gap segments');
    await page.locator('.question-button').last().click();assert.equal(await page.locator('#answer-body img').count(),0);assert.equal(await page.evaluate(()=>globalThis.__offlineXss),undefined);assert.equal(await page.locator('#answer-body').textContent(),fixture.guided_qa[1].answer);
    checks++;console.log('PASS imported HTML and script strings remain inert text');
    await page.locator('#import-file').setInputFiles({name:'invalid.json',mimeType:'application/json',buffer:Buffer.from('{invalid}')});await waitText(page,'#status','파일을 열지 못했습니다');assert.equal(await page.locator('#source-pill').textContent(),'합성 예시 기록');
    checks++;console.log('PASS invalid import preserves previous package');
    for(const [label,width,height] of [['landscape',1024,768],['portrait',768,1024],['phone',390,844]]){
      await page.setViewportSize({width,height});await page.evaluate(()=>window.scrollTo(0,0));
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'horizontal overflow at '+label);
      await page.screenshot({path:path.join(screenshotDir,'fixture-synthetic-'+label+'.png'),fullPage:true});
    }
    checks++;console.log('PASS responsive landscape, portrait and phone widths');
    await page.locator('#reset-visitor').click();assert.equal(await page.locator('#source-pill').textContent(),'합성 예시 기록');
    assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);
    checks++;console.log('PASS no page errors or external requests with browser offline');
    await context.close();
    console.log(JSON.stringify({checks,html:htmlPath,screenshots:screenshotDir,external_requests:requests.length,page_errors:errors.length}));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});