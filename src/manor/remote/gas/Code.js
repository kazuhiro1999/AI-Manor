// manor の中継（ADR-025）。manor が `manor remote gas deploy` で clasp を使って置く。
// **ここを Apps Script のエディタで直さない**（次の deploy で上書きされる）。直すなら
// manor のリポジトリの src/manor/remote/gas/Code.js を直して deploy し直す。
//
// Config.js（manor が生成。git に入らない）に ADMIN_SHA256 と CLIENT_SOURCE がある。
//
// 鍵は本文の `token`（GET はクエリ）。GAS の doPost は要求の頭を読めないため。
//   - 管理鍵（manor だけが持つ）: pull / set_directory / add_machine / revoke_machine
//   - PCごとの鍵（machines シートに sha256 だけ）: events / ping / client

var EVENT_COLUMNS = ['seq', 'event_id', 'at', 'received_at', 'machine', 'session_id', 'kind', 'payload'];
var SESSION_COLUMNS = ['session_id', 'machine', '状態', '見出し', '段階', '進捗', '主人の次', 'タスク',
  'プロジェクト', '一言', 'リポジトリ', 'ブランチ', '最終の合図', '最終の報告', '開始', '終了', 'cwd'];
var MACHINE_COLUMNS = ['name', 'token_sha256', 'created_at', 'revoked_at'];
var DIRECTORY_COLUMNS = ['key', 'json', 'updated_at'];
var PHASE_LABELS = {
  investigating: '調査中', designing: '設計中', implementing: '実装中', fixing: '修正中',
  implemented: '実装済', testing: '試験中', blocked: '止まっている', done: '完了'
};
var ACTIVITY_LABELS = { session_start: '開始', prompt: '作業中', stop: 'あなたの番', session_end: '終了' };
var EVENT_RETENTION_DAYS = 30;
var DEDUP_WINDOW = 3000;

function doPost(e) {
  var req = {};
  try {
    req = JSON.parse((e && e.postData && e.postData.contents) || '{}');
  } catch (err) {
    return json_({ ok: false, error: 'bad_json' });
  }
  if (req.op === 'client') return client_(req);
  return handle_(req);
}

function doGet(e) {
  var req = (e && e.parameter) || {};
  if (req.op === 'client') return client_(req);
  return handle_(req);
}

function handle_(req) {
  try {
    return json_(route_(req));
  } catch (err) {
    return json_({ ok: false, error: String(err && err.message || err) });
  }
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function route_(req) {
  var op = req.op || '';
  var token = req.token || '';
  var admin = isAdmin_(token);
  var machine = admin ? null : machineOf_(token);
  if (!admin && !machine) return { ok: false, error: 'forbidden' };

  if (op === 'ping') return { ok: true, role: admin ? 'admin' : 'machine', machine: machine };
  if (op === 'events') return withLock_(function () { return acceptEvents_(req, machine); });
  if (!admin) return { ok: false, error: 'forbidden' };
  if (op === 'pull') return pull_(req);
  if (op === 'set_directory') return withLock_(function () { return setDirectory_(req); });
  if (op === 'add_machine') return withLock_(function () { return addMachine_(req); });
  if (op === 'revoke_machine') return withLock_(function () { return revokeMachine_(req); });
  return { ok: false, error: 'unknown_op' };
}

function withLock_(fn) {
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    return fn();
  } finally {
    lock.releaseLock();
  }
}

// --- 鍵 ------------------------------------------------------------------------------------

function sha256_(s) {
  var bytes = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, String(s), Utilities.Charset.UTF_8);
  return bytes.map(function (b) { return ('0' + (b & 0xff).toString(16)).slice(-2); }).join('');
}

function isAdmin_(token) {
  return !!token && sha256_(token) === ADMIN_SHA256;
}

function machineOf_(token) {
  if (!token) return null;
  var h = sha256_(token);
  var cache = CacheService.getScriptCache();
  var hit = cache.get('m:' + h);
  if (hit) return hit === '-' ? null : hit;
  var rows = sheet_('machines', MACHINE_COLUMNS).getDataRange().getValues();
  var name = null;
  for (var i = 1; i < rows.length; i++) {
    if (rows[i][1] === h && !rows[i][3]) { name = String(rows[i][0]); break; }
  }
  cache.put('m:' + h, name || '-', 300);
  return name;
}

function addMachine_(req) {
  if (!req.name || !req.token_sha256) return { ok: false, error: 'name_and_hash_required' };
  var sh = sheet_('machines', MACHINE_COLUMNS);
  var rows = sh.getDataRange().getValues();
  for (var i = 1; i < rows.length; i++) {
    if (rows[i][0] === req.name && !rows[i][3]) sh.getRange(i + 1, 4).setValue(nowIso_());
  }
  sh.appendRow([req.name, req.token_sha256, nowIso_(), '']);
  CacheService.getScriptCache().remove('m:' + req.token_sha256);
  return { ok: true };
}

function revokeMachine_(req) {
  var sh = sheet_('machines', MACHINE_COLUMNS);
  var rows = sh.getDataRange().getValues();
  var n = 0;
  for (var i = 1; i < rows.length; i++) {
    if (rows[i][0] === req.name && !rows[i][3]) {
      sh.getRange(i + 1, 4).setValue(nowIso_());
      CacheService.getScriptCache().remove('m:' + rows[i][1]);
      n++;
    }
  }
  return { ok: true, revoked: n };
}

// --- イベント --------------------------------------------------------------------------------

function acceptEvents_(req, machine) {
  var events = req.events || [];
  var sh = sheet_('events', EVENT_COLUMNS);
  var last = sh.getLastRow();
  var seen = {};
  if (last > 1) {
    var from = Math.max(2, last - DEDUP_WINDOW + 1);
    var ids = sh.getRange(from, 2, last - from + 1, 1).getValues();
    for (var i = 0; i < ids.length; i++) seen[ids[i][0]] = true;
  }
  var props = PropertiesService.getScriptProperties();
  var seq = Number(props.getProperty('SEQ') || '0');
  var rows = [];
  var accepted = [];
  var dup = 0;
  var received = nowIso_();
  for (var j = 0; j < events.length; j++) {
    var ev = events[j];
    if (!ev || !ev.event_id || seen[ev.event_id]) { dup++; continue; }
    if (machine) ev.machine = machine;  // PC の鍵で来たものは、名乗りではなく鍵の持ち主の名にする
    seen[ev.event_id] = true;
    seq++;
    rows.push([seq, ev.event_id, ev.at || '', received, ev.machine || '', ev.session_id || '', ev.kind || '',
      JSON.stringify(ev)]);
    accepted.push(ev);
  }
  if (rows.length) {
    sh.getRange(sh.getLastRow() + 1, 1, rows.length, EVENT_COLUMNS.length).setValues(rows);
    props.setProperty('SEQ', String(seq));
    foldSessions_(accepted);
  }
  var out = { ok: true, accepted: rows.length, duplicates: dup, seq: seq };
  if (req.directory_key) out.directory = directoryFor_(req.directory_key);
  return out;
}

// sessions シートは主人が直接開いても読める予備のダッシュボード。正は manor 側の畳み直し。
function foldSessions_(events) {
  var sh = sheet_('sessions', SESSION_COLUMNS);
  var values = sh.getDataRange().getValues();
  var index = {};
  for (var i = 1; i < values.length; i++) index[values[i][0]] = i;
  events.sort(function (a, b) { return String(a.at).localeCompare(String(b.at)); });
  for (var j = 0; j < events.length; j++) {
    var ev = events[j];
    var row;
    if (index.hasOwnProperty(ev.session_id)) {
      row = values[index[ev.session_id]];
    } else {
      row = SESSION_COLUMNS.map(function () { return ''; });
      row[0] = ev.session_id;
      row[14] = ev.at;
      values.push(row);
      index[ev.session_id] = values.length - 1;
    }
    var repo = ev.repo || {};
    row[1] = ev.machine || row[1];
    if (ev.kind !== 'progress') row[2] = ACTIVITY_LABELS[ev.kind] || row[2];
    row[10] = repo.remote || repo.key || row[10];
    row[11] = repo.branch || row[11];
    row[12] = ev.at;
    row[16] = ev.cwd || row[16];
    if (ev.kind === 'session_end') row[15] = ev.at;
    if (ev.kind === 'session_start') row[15] = '';
    var r = ev.report;
    if (ev.kind === 'progress' && r) {
      if (r.title) row[3] = r.title;
      if (r.phase) row[4] = PHASE_LABELS[r.phase] || r.phase;
      if (r.progress !== null && r.progress !== undefined) row[5] = r.progress;
      if (r.human_next !== undefined) row[6] = r.human_next;
      if (r.task) row[7] = r.task;
      if (r.project) row[8] = r.project;
      if (r.note !== undefined) row[9] = r.note;
      row[13] = ev.at;
    }
  }
  sh.getRange(1, 1, values.length, SESSION_COLUMNS.length).setValues(values);
}

function pull_(req) {
  var since = Number(req.since || 0);
  var limit = Math.min(Number(req.limit || 500), 2000);
  var sh = sheet_('events', EVENT_COLUMNS);
  var last = sh.getLastRow();
  var out = [];
  if (last > 1) {
    var seqs = sh.getRange(2, 1, last - 1, 1).getValues();
    var start = -1;
    for (var i = 0; i < seqs.length; i++) {
      if (Number(seqs[i][0]) > since) { start = i; break; }
    }
    if (start >= 0) {
      var n = Math.min(limit, seqs.length - start);
      var rows = sh.getRange(start + 2, 1, n, EVENT_COLUMNS.length).getValues();
      for (var k = 0; k < rows.length; k++) {
        var payload = {};
        try { payload = JSON.parse(rows[k][7]); } catch (err) { continue; }
        payload.seq = Number(rows[k][0]);
        payload.received_at = rows[k][3];
        out.push(payload);
      }
    }
  }
  cleanupIfDue_();
  var lastSeq = out.length ? out[out.length - 1].seq : since;
  var total = Number(PropertiesService.getScriptProperties().getProperty('SEQ') || '0');
  return { ok: true, events: out, last_seq: lastSeq, more: lastSeq < total };
}

function cleanupIfDue_() {
  var props = PropertiesService.getScriptProperties();
  var lastClean = Number(props.getProperty('LAST_CLEANUP') || '0');
  if (Date.now() - lastClean < 24 * 3600 * 1000) return;
  props.setProperty('LAST_CLEANUP', String(Date.now()));
  var cutoff = new Date(Date.now() - EVENT_RETENTION_DAYS * 24 * 3600 * 1000).toISOString();
  var sh = sheet_('events', EVENT_COLUMNS);
  var last = sh.getLastRow();
  if (last < 2) return;
  var recv = sh.getRange(2, 4, last - 1, 1).getValues();
  var drop = 0;
  while (drop < recv.length && String(recv[drop][0]) < cutoff) drop++;
  if (drop > 0) sh.deleteRows(2, drop);
}

// --- 紐づけ表 --------------------------------------------------------------------------------

function setDirectory_(req) {
  var entries = req.entries || {};
  var sh = sheet_('directory', DIRECTORY_COLUMNS);
  var now = nowIso_();
  var rows = [DIRECTORY_COLUMNS];
  Object.keys(entries).sort().forEach(function (k) { rows.push([k, JSON.stringify(entries[k]), now]); });
  sh.clearContents();
  sh.getRange(1, 1, rows.length, DIRECTORY_COLUMNS.length).setValues(rows);
  CacheService.getScriptCache().remove('directory');
  return { ok: true, count: rows.length - 1 };
}

function directoryFor_(key) {
  var cache = CacheService.getScriptCache();
  var all = null;
  var hit = cache.get('directory');
  if (hit) {
    try { all = JSON.parse(hit); } catch (err) { all = null; }
  }
  if (!all) {
    all = {};
    var rows = sheet_('directory', DIRECTORY_COLUMNS).getDataRange().getValues();
    for (var i = 1; i < rows.length; i++) all[rows[i][0]] = rows[i][1];
    var s = JSON.stringify(all);
    if (s.length < 90000) cache.put('directory', s, 120);
  }
  var raw = all[key];
  if (!raw) return null;
  try { return JSON.parse(raw); } catch (err) { return null; }
}

// --- 送る側の道具の配布（他のPCの導入を1行にするため） ------------------------------------------

function client_(req) {
  var token = req.token || '';
  if (!isAdmin_(token) && !machineOf_(token)) {
    return ContentService.createTextOutput('forbidden').setMimeType(ContentService.MimeType.TEXT);
  }
  return ContentService.createTextOutput(CLIENT_SOURCE).setMimeType(ContentService.MimeType.TEXT);
}

// --- 共通 ------------------------------------------------------------------------------------

function sheet_(name, columns) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(name);
  if (!sh) {
    sh = ss.insertSheet(name);
    sh.getRange(1, 1, 1, columns.length).setValues([columns]);
    sh.setFrozenRows(1);
  } else if (sh.getLastRow() === 0) {
    sh.getRange(1, 1, 1, columns.length).setValues([columns]);
    sh.setFrozenRows(1);
  }
  return sh;
}

function nowIso_() {
  return new Date().toISOString();
}

// エディタから一度走らせて権限を許可するための関数（中身は無害な読み取りだけ）。
function authorize() {
  sheet_('events', EVENT_COLUMNS);
  sheet_('sessions', SESSION_COLUMNS);
  sheet_('machines', MACHINE_COLUMNS);
  sheet_('directory', DIRECTORY_COLUMNS);
  Logger.log('ok');
}
