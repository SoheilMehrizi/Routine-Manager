// Run: node tests/ui.test.js   (no browser needed: a tiny fake DOM and a fake API)
const fs = require('fs'), assert = require('assert');
const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const js = html.match(/<script>([\s\S]*?)<\/script>/g).pop().replace(/<\/?script>/g, '');
const EXPORTS = 'blSchedulePreview,blConflicts,blSubAdd,blToggleSubtask,blCompleteLocal,blQueue,blOpen,blReorderTask,blReorderCategory,blToggle,blResetQueue,blDeleteCategory,blCategoryForm,blSaveCategory,blTaskForm,blSaveTask,blDeleteTask,blMove,pBacklog,openBacklog,saveBacklog,archiveBacklog,deleteBacklog,initDemoBacklogs,D,S,boot,R,openF,saveF,parseQ,streakOf,hmCounts,heat,aiCard,aiRun,aiStats,insights,loginView,integCard,quickLog,todayPanel,qaAdd,linkTg,toast,undo,pDash,pTasks,pRoutines,pGoals,pAct,pCal,pSet,tog,del,TODAY,addD,fmt,pick,fillR,setv,testEmail,previewEmail';

function load({ backend = false, token = '', api = {} } = {}) {
  const els = {}, attrs = {}, timers = [], calls = [], toasts = [];
  const el = id => els[id] || (els[id] = { id, value: '', checked: false, hidden: false, dataset: {}, classList: { toggle() {}, add() {}, remove() {} }, focus() {}, set innerHTML(v) { this._h = v }, get innerHTML() { return this._h || '' } });
  const g = global;
  g.window = backend ? { MEL_CONFIG: ['client-id', ''] } : {};
  g.localStorage = { _d: token ? { mel_token: token } : {}, getItem(k) { return this._d[k] || null }, setItem(k, v) { this._d[k] = v }, removeItem(k) { delete this._d[k] } };
  g.document = { getElementById: el, querySelectorAll: () => [], createElement: () => ({}), head: { appendChild() {} }, body: { classList: { add() {}, remove() {} } },
    documentElement: { getAttribute: k => attrs[k] || null, setAttribute: (k, v) => { attrs[k] = v }, removeAttribute: k => { delete attrs[k] } } };
  g.location = { search: '', hash: '', pathname: '/', reload() {}, set href(v) { calls.push('NAV ' + v) } };
  g.history = { replaceState() {} };
  g.confirm = () => true; g.alert = m => calls.push('ALERT ' + m);
  g.setInterval = fn => { timers.push(fn); return timers.length }; g.clearInterval = () => {};
  g.fetch = async (url, o = {}) => {
    const m = o.method || 'GET', key = m + ' ' + url; calls.push(key);
    if (url === '/auth/google') return { ok: true, status: 200, json: async () => ({ token: 'tok' }) };
    if (o.headers.Authorization !== 'Bearer tok') return { ok: false, status: 401, json: async () => ({}) };
    const h = api[key] !== undefined ? api[key] : api[url];
    if (h !== undefined && typeof h !== 'function') return { ok: true, status: 200, json: async () => h };
    if (typeof h === 'function') { const r = h(o.body ? JSON.parse(o.body) : null); if (r && r.__err) return { ok: false, status: r.status || 400, json: async () => ({ detail: r.__err }) }; return { ok: true, status: 200, json: async () => r }; }
    if (url === '/api/me') return { ok: true, status: 200, json: async () => ({ prefs: { tz: 'Asia/Tehran' } }) };
    if (url === '/api/integrations' && m === 'GET') return { ok: true, status: 200, json: async () => [] };
    if (url.startsWith('/api/') && m === 'GET') return { ok: true, status: 200, json: async () => [] };
    return { ok: true, status: m === 'DELETE' ? 204 : 200, json: async () => ({}) };
  };
  const X = new Function(js + ';return {' + EXPORTS.split(',').map(n => n + ':typeof ' + n + '!=="undefined"?' + n + ':undefined').join(',') + ',get AIS(){return AIS},get LOADED(){return LOADED},get UNDO(){return UNDO}}')();
  return { X, els, el, attrs, timers, calls, tick: () => new Promise(r => setTimeout(r, 15)) };
}
const results = []; const t = (name, fn) => results.push([name, fn]);

// ---------------- preview / standalone mode ----------------
t('every page renders in preview mode, incl. all calendar views', () => {
  const { X } = load();
  for (const [k, f] of Object.entries({ dashboard: X.pDash, tasks: X.pTasks, routines: X.pRoutines, goals: X.pGoals, activities: X.pAct, settings: X.pSet })) assert(f().length > 100, k);
  for (const v of ['year', 'month', 'week', 'day']) { X.S.cv = v; assert(X.pCal().length > 100, v); }
});
t('heatmap: 53 weeks x 7 days, month labels, legend, streak numbers', () => {
  const { X } = load(); const h = X.heat();
  assert.strictEqual((h.match(/<i class="hc /g) || []).length, 53 * 7 + 5, 'cells + legend');
  assert(/class="hmm"/.test(h) && /Longest streak/.test(h) && /Less/.test(h));
  assert(/title="\d+ completed · /.test(h));
});
t('streak logic: today empty keeps yesterday’s streak; gap resets; longest tracked', () => {
  const { X } = load(); const d = n => X.addD(X.TODAY, n);
  let c = { [d(-1)]: 1, [d(-2)]: 2, [d(-3)]: 1, [d(-10)]: 1, [d(-11)]: 1, [d(-12)]: 1, [d(-13)]: 1, [d(-14)]: 1 };
  assert.deepStrictEqual(X.streakOf(c), { cur: 3, best: 5 });
  c[d(0)] = 1; assert.strictEqual(X.streakOf(c).cur, 4);
  assert.deepStrictEqual(X.streakOf({ [d(-5)]: 1 }), { cur: 0, best: 1 });
  assert.deepStrictEqual(X.streakOf({}), { cur: 0, best: 0 });
});
t('quick-add parser', () => {
  const { X } = load(); const p = X.parseQ('Pay electricity bill tomorrow 14:00 30m #Finance !high');
  assert.deepStrictEqual([p.title, p.due, p.time, p.dur, p.cat, p.pri], ['Pay electricity bill', X.addD(X.TODAY, 1), '14:00', 30, 'Finance', 'High']);
  assert.strictEqual(X.parseQ('Gym 1h').dur, 60);
  assert.strictEqual(X.parseQ('Read #Nope today').title, 'Read #Nope', 'unknown category stays in the title');
  assert.strictEqual(X.parseQ('Call mom 25:99').time, '', 'invalid time ignored');
  assert.strictEqual(X.parseQ('monitor the server').due, '', 'weekday words need to be whole words');
  const w = X.parseQ('Review friday'); assert(w.due > X.TODAY && w.title === 'Review');
  assert.strictEqual(X.parseQ('فردا کتاب').due, X.addD(X.TODAY, 1));
});
t('quick-add creates a task, can be undone', () => {
  const { X, el } = load(); const n = X.D.tasks.length; el('qa').value = 'Buy milk 15m !low';
  X.qaAdd(); assert.strictEqual(X.D.tasks.length, n + 1);
  const t0 = X.D.tasks[X.D.tasks.length - 1]; assert.deepStrictEqual([t0.title, t0.dur, t0.pri, t0.due], ['Buy milk', 15, 'Low', X.TODAY]);
  X.undo(); assert.strictEqual(X.D.tasks.length, n);
});
t('one-tap routine logging marks it done and is undoable', () => {
  const { X } = load(); const r = X.D.routines.find(r => !X.D.logs.some(l => l.routine === r.id && l.date === X.TODAY && l.result === 'Completed')) || X.D.routines[1];
  X.D.logs = X.D.logs.filter(l => !(l.routine === r.id && l.date === X.TODAY)); const n = X.D.logs.length;
  X.quickLog(r.id); assert.strictEqual(X.D.logs.length, n + 1); assert(/✓ Done/.test(X.todayPanel()));
  X.undo(); assert.strictEqual(X.D.logs.length, n);
});
t('forms: friendly controls render; Enter/Esc hooks; required title is validated', () => {
  const { X, el } = load();
  X.openF('task'); let h = el('ov').innerHTML;
  for (const w of ['data-k="cat"', 'data-k="pri"', 'Tomorrow', 'More options', 'id="adv" class="fg"', 'hidden', 'Enter to save', 'mdKey(']) assert(h.includes(w), 'task: ' + w);
  X.openF('log'); h = el('ov').innerHTML; for (const w of ['Quick pick', 'Yesterday', 'data-k="result"', 'Completed', 'Skipped']) assert(h.includes(w), 'log: ' + w);
  X.openF('goal'); h = el('ov').innerHTML; for (const w of ['Activity-based', '+1 month', 'Linked routine']) assert(h.includes(w), 'goal: ' + w);
  X.openF('routine'); assert(el('ov').innerHTML.includes('data-k="rec"'));
  X.openF('task'); const n = X.D.tasks.length; el('f_title').value = '   '; X.saveF('task', ''); assert.strictEqual(X.D.tasks.length, n, 'blank title rejected');
});
t('forms: saving a task works end to end and remembers the category', () => {
  const { X, el } = load(); X.openF('task'); const n = X.D.tasks.length;
  Object.assign(el('f_title'), { value: 'Write report' }); el('f_due').value = X.TODAY; el('f_time').value = '09:00'; el('f_dur').value = '45'; el('f_cat').value = 'Work'; el('f_pri').value = 'High';
  ['f_description', 'f_goal', 'f_status', 'f_est', 'f_rec', 'f_target', 'f_tags'].forEach(i => el(i).value = ''); el('f_status').value = 'To do'; el('f_rec').value = 'none';
  X.saveF('task', ''); assert.strictEqual(X.D.tasks.length, n + 1);
  const last = X.D.tasks[X.D.tasks.length - 1]; assert.deepStrictEqual([last.title, last.dur, last.cat, last.pri], ['Write report', 45, 'Work', 'High']);
  X.openF('task'); assert(/class="ch on"/.test(el('ov').innerHTML), 'last category preselected');
});
t('insights are computed locally and never empty', () => { const { X } = load(); assert(X.insights().length >= 2); });
t('AI card in preview explains it runs on the server; data preview excludes titles', () => {
  const { X } = load(); const h = X.aiCard(); assert(/generated by the server/.test(h) && /What data is sent/.test(h));
  const st = JSON.stringify(X.aiStats()); assert(!/Algorithm|Exercise|Budget|Read 20/.test(st), 'no titles in payload'); assert(/completion_rate_pct/.test(st));
});
t('login page (preview): hero, features, demo button; sign out returns to it', () => {
  const { X, el } = load(); X.loginView(true); const h = el('pg').innerHTML;
  for (const w of ['Welcome back', 'Plan your day. Keep your streak.', 'A GitHub-style streak tracker', 'Open the demo', 'Preview only']) assert(h.includes(w), w);
});

// ---------------- server mode (fake API) ----------------
t('server mode without a token shows the sign-in page, with Google button slot and privacy note', async () => {
  const L = load({ backend: true }); L.X.boot(); await L.tick(); const h = L.el('pg').innerHTML;
  assert(h.includes('id="gbtn"') && h.includes('Welcome back') && h.includes('read-only'));
});
t('server mode boot: loads data, pushes default categories and time zone once', async () => {
  const L = load({ backend: true, token: 'tok', api: { '/api/me': { prefs: { lang: 'en' } } } }); L.X.boot(); await L.tick(); await L.tick();
  assert(L.X.LOADED); assert(L.calls.filter(c => c.startsWith('PUT /api/cats/')).length === 5); assert(L.calls.includes('PUT /api/me/prefs'));
});
t('AI: server error is shown inline; success is labelled AI-generated and escapes HTML', async () => {
  const L = load({ backend: true, token: 'tok', api: { '/api/integrations': [{ name: 'llm', config: { url: 'https://x/v1', model: 'm', consent: true, scheme: 'auto' }, has_secret: true }],
    'POST /api/ai/recommendations': () => ({ __err: 'The provider rejected the API key', status: 502 }) } });
  L.X.boot(); await L.tick(); await L.tick(); L.X.aiRun(); await L.tick(); assert(/rejected the API key/.test(L.X.aiCard()) && L.X.AIS.st === 'err');
  const L2 = load({ backend: true, token: 'tok', api: { '/api/integrations': [{ name: 'llm', config: { url: 'https://x/v1', model: 'qwen', consent: true }, has_secret: true }],
    'POST /api/ai/recommendations': () => ({ text: '- Walk after lunch\n- <script>x</script> **bold**', model: 'qwen' }) } });
  L2.X.boot(); await L2.tick(); await L2.tick(); L2.X.aiRun(); await L2.tick(); const h = L2.X.aiCard();
  assert(/AI-generated/.test(h) && /<li>Walk after lunch<\/li>/.test(h) && !/<script>x/.test(h) && /<b>bold<\/b>/.test(h));
});
t('AI: without consent the first click grants it (PUT) and then generates (POST)', async () => {
  const L = load({ backend: true, token: 'tok', api: { '/api/integrations': [{ name: 'llm', config: { url: 'https://x/v1', model: 'm', consent: false }, has_secret: true }],
    'PUT /api/integrations/llm': b => ({ name: 'llm', config: b.config }), 'POST /api/ai/recommendations': () => ({ text: '- ok', model: 'm' }) } });
  L.X.boot(); await L.tick(); await L.tick(); assert(/Allow and generate/.test(L.X.aiCard())); L.X.aiRun(); await L.tick(); await L.tick();
  assert(L.calls.indexOf('PUT /api/integrations/llm') < L.calls.indexOf('POST /api/ai/recommendations') && L.X.AIS.st === 'ok');
});
t('AI: not connected offers the Settings shortcut', async () => {
  const L = load({ backend: true, token: 'tok' }); L.X.boot(); await L.tick(); await L.tick(); assert(/Connect a model/.test(L.X.aiCard()));
});
t('Telegram: link code shown, polling links the chat, agenda row turns Active', async () => {
  const L = load({ backend: true, token: 'tok', api: { '/api/integrations': [{ name: 'telegram', config: { bot: 'mybot', linked: false }, has_secret: true }],
    'POST /api/telegram/link': () => ({ code: 'AB12CD34', bot: 'mybot', expires_in: 600 }), 'POST /api/telegram/link/check': () => ({ linked: true }) } });
  L.X.boot(); await L.tick(); await L.tick(); L.X.linkTg(); await L.tick(); const h = L.el('ov').innerHTML;
  assert(h.includes('/start AB12CD34') && h.includes('t.me/mybot')); assert.strictEqual(L.timers.length, 1);
  await L.timers[0](); await L.tick(); assert(L.X.D.integ.telegram.linked); assert(/Every day at 07:00/.test(L.X.integCard()));
});
t('Email agenda preview works in standalone demo without a backend', () => {
  const L = load(); const h = L.X.integCard(); assert(/Preview email/.test(h) && /disabled title=/.test(h) && /Test email/.test(h));
  L.X.D.tasks.push({ id: 'html-safe', title: '<script>demo</script>', due: L.X.TODAY, status: 'To do' });
  L.X.previewEmail(); const html = L.el('mailpreview').srcdoc;
  assert(/My EveryDay Life/.test(html) && /EMAIL PREVIEW/.test(html) && /&lt;script&gt;demo&lt;\/script&gt;/.test(html));
  assert(/This preview does not send an email/.test(L.el('ov').innerHTML));
});
t('Email agenda: configured Settings shows an email test action and sends the sample', async () => {
  const L = load({ backend: true, token: 'tok', api: {
    '/api/me': { email: 'planner@example.com', prefs: { tz: 'Asia/Tehran' } },
    '/api/agenda/status': { email: 'planner@example.com', email_configured: true, email_enabled: false, linked: false, deliveries: [] },
    'POST /api/agenda/test-email': { sent: true, to: 'planner@example.com' }
  } });
  L.X.boot(); await L.tick(); await L.tick(); L.X.integCard(); await L.tick();
  const h = L.X.integCard(); assert(/Email delivery/.test(h) && /Test email/.test(h) && /planner@example.com/.test(h));
  L.X.testEmail(); await L.tick(); assert(L.calls.includes('POST /api/agenda/test-email'));
});
t('Telegram: Link button only while not linked; agenda row inactive until linked', async () => {
  const L = load({ backend: true, token: 'tok', api: { '/api/integrations': [{ name: 'telegram', config: { bot: 'mybot', linked: false }, has_secret: true }] } });
  L.X.boot(); await L.tick(); await L.tick(); const h = L.X.integCard(); assert(/Link chat/.test(h) && /Link Telegram to enable/.test(h) && /Inactive/.test(h));
});


t('backlog defaults initialize once, rename safely, archive, restore and delete', async () => {
  const {X,el}=load(); assert.strictEqual(X.D.backlogs.length,4);
  const id=X.D.backlogs[0].id;
  X.openBacklog(id); el('bl_title').value=' <b>My project</b> ';
  await X.saveBacklog(id); assert.strictEqual(X.D.backlogs[0].title,'<b>My project</b>');
  assert(X.pBacklog().includes('&lt;b&gt;My project&lt;/b&gt;'));
  await X.archiveBacklog(id); assert(X.D.backlogs[0].archived); assert(!X.pBacklog().includes('&lt;b&gt;My project&lt;/b&gt;'));
  await X.archiveBacklog(id); assert(!X.D.backlogs[0].archived);
  for(const item of [...X.D.backlogs]) await X.deleteBacklog(item.id);
  X.initDemoBacklogs(); assert.strictEqual(X.D.backlogs.length,0);
  X.openBacklog();el('bl_title').value=' ';await X.saveBacklog('');assert.strictEqual(X.D.backlogs.length,0);
  el('bl_title').value='New project';await X.saveBacklog('');assert.strictEqual(X.D.backlogs.length,1);
});
t('backlog failed server writes preserve saved state',async()=>{
  const item={id:'list1234',title:'Original',archived:false};
  const L=load({backend:true,token:'tok',api:{'/api/backlogs':[item],'PUT /api/backlogs/list1234':()=>({__err:'offline'}),'DELETE /api/backlogs/list1234':()=>({__err:'offline'})}});
  await L.tick();await L.tick();assert.strictEqual(L.X.D.backlogs.length,1);
  await L.X.archiveBacklog(item.id);assert.strictEqual(L.X.D.backlogs[0].archived,false);
  await L.X.deleteBacklog(item.id);assert.strictEqual(L.X.D.backlogs.length,1);
});

t('backlog category order and mixed queue survive renames and category changes',async()=>{
 const L=load(),X=L.X,id=X.D.backlogs[0].id;
 Object.assign(X.D.backlogs[0],{categories:[{id:'cat1',title:'A'},{id:'cat2',title:'B'}],tasks:[
 {id:'task1',title:'A1',category:'cat1'},{id:'task2',title:'B1',category:'cat2'},
 {id:'task3',title:'B2',category:'cat2'},{id:'task4',title:'A2',category:'cat1'}]});
 assert.deepStrictEqual(X.blQueue(X.D.backlogs[0]),['task1','task4','task2','task3']);
 await X.blReorderTask(id,'task4',100,true); // out of range: unchanged
 await X.blReorderTask(id,'task4',1,true);
 await X.blReorderTask(id,'task4',1,true);
 assert.deepStrictEqual(X.D.backlogs[0].queue,['task1','task2','task3','task4']);
 await X.blReorderCategory(id,'cat2',-1);
 assert.deepStrictEqual(X.D.backlogs[0].queue,['task1','task2','task3','task4']);
 X.openBacklog(id);L.el('bl_title').value='Renamed list';await X.saveBacklog(id);
 assert.strictEqual(X.D.backlogs[0].tasks.length,4);
 await X.blToggle(id,'task3');assert(X.D.backlogs[0].tasks.find(t=>t.id==='task3').done);
 await X.blResetQueue(id);assert.deepStrictEqual(X.D.backlogs[0].queue,['task2','task3','task1','task4']);
 await X.blDeleteCategory(id,'cat2');assert.strictEqual(X.D.backlogs[0].tasks.find(t=>t.id==='task3').category,null);
 assert.strictEqual(X.D.backlogs[0].tasks.length,4);
 X.blOpen(id);assert(X.pBacklog().includes('Uncategorized'));
});
t('backlog forms create categories and tasks, edit, move, and delete',async()=>{
 const L=load(),X=L.X,[a,b]=X.D.backlogs.map(x=>x.id);
 X.blCategoryForm(a);L.el('bl_name').value='Research';await X.blSaveCategory(a,'');
 const cat=X.D.backlogs[0].categories[0].id;
 X.blTaskForm(a,'',cat);L.el('bl_name').value='<Read>';L.el('bl_description').value='Notes';L.el('bl_target').value=a;L.el('bl_category').value=cat;
 await X.blSaveTask(a,'');let task=X.D.backlogs[0].tasks[0];assert.strictEqual(task.description,'Notes');
 X.blOpen(a);assert(X.pBacklog().includes('&lt;Read&gt;'));
 X.blTaskForm(a,task.id);L.el('bl_name').value='Read';L.el('bl_description').value='Notes';L.el('bl_target').value=b;L.el('bl_category').value='';
 await X.blSaveTask(a,task.id);assert.strictEqual(X.D.backlogs[0].tasks.length,0);assert.strictEqual(X.D.backlogs[1].tasks.length,1);
 await X.blDeleteTask(b,task.id);assert.strictEqual(X.D.backlogs[1].tasks.length,0);
});
t('backlog drag operations change queue independently and move between categories/lists',async()=>{
 const L=load(),X=L.X,[a,b]=X.D.backlogs.map(x=>x.id);
 Object.assign(X.D.backlogs[0],{categories:[{id:'cat1',title:'A'},{id:'cat2',title:'B'}],tasks:[{id:'task1',title:'A1',category:'cat1'},{id:'task2',title:'B1',category:'cat2'}]});
 await X.blMove({kind:'task',list:a,id:'task2'},a,'cat1','task1','queue');
 assert.deepStrictEqual(X.D.backlogs[0].queue,['task2','task1']);assert.strictEqual(X.D.backlogs[0].tasks[1].category,'cat2');
 await X.blMove({kind:'task',list:a,id:'task2'},a,'cat1',null,'category');
 assert.strictEqual(X.D.backlogs[0].tasks.find(t=>t.id==='task2').category,'cat1');
 await X.blMove({kind:'category',list:a,id:'cat2'},a,'cat1',null,'category');
 assert.strictEqual(X.D.backlogs[0].categories[0].id,'cat2');
 await X.blMove({kind:'task',list:a,id:'task2'},b,null,null,'queue');
 assert.strictEqual(X.D.backlogs[0].tasks.length,1);assert.strictEqual(X.D.backlogs[1].tasks[0].category,null);
 assert.deepStrictEqual(X.D.backlogs[1].queue,['task2']);
});
t('backlog atomic move failures leave both lists unchanged',async()=>{
 const a={id:'list1111',title:'A',tasks:[{id:'task1',title:'Keep',category:null}]},b={id:'list2222',title:'B'};
 const L=load({backend:true,token:'tok',api:{'/api/backlogs':[a,b],'POST /api/backlogs/batch':()=>({__err:'offline'})}});
 await L.tick();await L.tick();const before=JSON.stringify(L.X.D.backlogs);
 await L.X.blMove({kind:'task',list:a.id,id:'task1'},b.id,null,null,'list');
 assert.strictEqual(JSON.stringify(L.X.D.backlogs),before);
});

t('backlog scheduling allows conflicting dates and displays warning tags',async()=>{
 const L=load(),X=L.X,id=X.D.backlogs[0].id;
 X.openBacklog(id);L.el('bl_title').value='Scheduled';L.el('bl_start').value='2026-11-07T09:00';L.el('bl_deadline').value='2026-11-07T18:00';await X.saveBacklog(id);
 X.blCategoryForm(id);L.el('bl_name').value='Research';L.el('bl_start').value='2026-11-07T08:00';L.el('bl_deadline').value='2026-11-07T19:00';X.blSchedulePreview();assert(L.el('bl_date_warning').innerHTML.includes('Saving is allowed'));
 await X.blSaveCategory(id,'');let cat=X.D.backlogs[0].categories[0];assert(cat.date_conflicts.includes('outside_list'));
 X.blTaskForm(id,'',cat.id);L.el('bl_name').value='Read';L.el('bl_description').value='';L.el('bl_target').value=id;L.el('bl_category').value=cat.id;L.el('bl_start').value='2026-11-08T10:00';L.el('bl_deadline').value='2026-11-07T07:00';
 X.blSchedulePreview();assert(L.el('bl_date_warning').innerHTML.includes('Start is after deadline'));await X.blSaveTask(id,'');let task=X.D.backlogs[0].tasks[0];assert.deepStrictEqual(task.date_conflicts,['reversed_dates','outside_list','outside_category']);
 X.blOpen(id);assert(X.pBacklog().includes('Outside category dates'));assert(X.pBacklog().includes('Outside list dates'));
});
t('backlog task forms save estimates, recurrence and independent subtasks',async()=>{
 const L=load(),X=L.X,id=X.D.backlogs[0].id;
 X.blTaskForm(id);L.el('bl_name').value='Study';L.el('bl_description').value='';L.el('bl_target').value=id;L.el('bl_category').value='';L.el('bl_estimate').value='45';L.el('bl_repeat').value='week';L.el('bl_every').value='2';
 L.el('bl_sub_new').value='<Read>';X.blSubAdd();await X.blSaveTask(id,'');let task=X.D.backlogs[0].tasks[0];assert.strictEqual(task.estimate,45);assert.deepStrictEqual(task.recurrence,{unit:'week',every:2});assert.strictEqual(task.subtasks[0].title,'<Read>');
 await X.blToggleSubtask(id,task.id,task.subtasks[0].id,true);task=X.D.backlogs[0].tasks[0];assert(task.subtasks[0].done);assert(!task.done);
 X.blOpen(id);assert(X.pBacklog().includes('&lt;Read&gt;'));
});
t('backlog recurrence clamps months, preserves anchors, skips missed dates and avoids duplicates',()=>{
 const {X}=load();let doc={id:'list1',title:'Repeat',categories:[],tasks:[{id:'task1',title:'Monthly',deadline:'2027-01-31T10:00',recurrence:{unit:'month',every:1},subtasks:[{id:'sub1',title:'Read',done:true}]}]};
 doc=X.blCompleteLocal(doc,'task1',true,'2027-01-31T11:00');let child=doc.tasks[1];assert.strictEqual(child.deadline,'2027-02-28T10:00');assert(!child.subtasks[0].done);
 doc=X.blCompleteLocal(doc,'task1',true,'2027-01-31T11:00');doc=X.blCompleteLocal(doc,'task1',false,'2027-01-31T11:00');doc=X.blCompleteLocal(doc,'task1',true,'2027-01-31T11:00');assert.strictEqual(doc.tasks.length,2);
 doc=X.blCompleteLocal(doc,child.id,true,'2027-02-28T11:00');assert.strictEqual(doc.tasks[2].deadline,'2027-03-31T10:00');
 let late={id:'list2',title:'Late',tasks:[{id:'task2',title:'Late',deadline:'2020-01-01T10:00',recurrence:{unit:'day',every:1}}]};late=X.blCompleteLocal(late,'task2',true,'2026-11-07T11:00');assert.strictEqual(late.tasks[1].deadline,'2026-11-08T10:00');
});
t('backlog schedule validation leaves saved tasks intact',async()=>{
 const L=load(),X=L.X,id=X.D.backlogs[0].id;
 X.blTaskForm(id);L.el('bl_name').value='Invalid';L.el('bl_target').value=id;L.el('bl_description').value='';L.el('bl_category').value='';L.el('bl_start').value='2026-02-30T10:00';
 await X.blSaveTask(id,'');assert.strictEqual((X.D.backlogs[0].tasks||[]).length,0);assert(L.el('bl_error').textContent.includes('valid schedule'));
 L.el('bl_start').value='';L.el('bl_estimate').value='1.5';await X.blSaveTask(id,'');assert.strictEqual((X.D.backlogs[0].tasks||[]).length,0);
});
t('backlog server completion failure preserves task and recurrence state',async()=>{
 const item={id:'list1111',title:'A',tasks:[{id:'task1',title:'Keep',done:false,recurrence:{unit:'day',every:1}}]};
 const L=load({backend:true,token:'tok',api:{'/api/backlogs':[item],'POST /api/backlogs/list1111/tasks/task1/completion':()=>({__err:'offline'})}});await L.tick();await L.tick();const before=JSON.stringify(L.X.D.backlogs);await L.X.blToggle(item.id,'task1');assert.strictEqual(JSON.stringify(L.X.D.backlogs),before);
});

(async () => { let bad = 0; for (const [n, f] of results) { try { await f(); console.log('ok   ' + n) } catch (e) { bad++; console.log('FAIL ' + n + '\n     ' + String(e.stack).split('\n').slice(0, 3).join('\n     ')) } } console.log(bad ? bad + ' failed' : 'all ' + results.length + ' passed'); process.exit(bad ? 1 : 0) })();
