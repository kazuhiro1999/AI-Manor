/*
 * 小窓の口を動かす（ADR-011 D6。v1 apps/butler-board/src/butler_board/static/face-mouth.js の
 * 移植）。
 *
 * **face.html の module script とは別ファイルにしてある。**
 * ここが壊れても3Dの描画・状態3行・通話・ピン留めには影響しない（v1 も同じ理由で
 * 分けていた——小窓の描画を壊した事故があったため、壊れ方を閉じ込めておく）。
 *
 * やること:
 *   1. `GET /api/v1/face/mouth` を短い間隔で見に行く（{id, started_at, cues} または
 *      {id: null}。cue は {at_ms, viseme, weight} の列——時刻順で、状態が変わった
 *      瞬間だけを記録している。詳しくは face_speech.py の docstring）
 *   2. id が変わったら新しい予定表として再生を始め、cue どおりに
 *      window.__face.mouth(viseme, weight) を呼ぶ
 *
 * やらないこと:
 *   - 音を鳴らさない（鳴らすのは voice.py の `_play`）
 *   - 何を言ったかを知らない（cue は viseme と重みだけで、本文は含まれない）
 *   - VRM や `/api/v1/face/mouth` が無い環境では何もしない（ガードを徹底する）
 *
 * 有効化: face.html に <script src="/face-static/face-mouth.js" defer></script> を足す。
 */
(function () {
  'use strict';

  var POLL_MS = 500;   // 予定表を見に行く間隔
  var TICK_MS = 50;    // 口を動かす間隔（描画も概ね20fpsなので、これ以上細かくしても見えない）
  var EASE_MS = 60;    // 口形の切り替えにかける時間。階段状に見せない
  var TAIL_MS = 200;   // 最後の cue から、動きが収まるまで待つ余裕

  var now = {};        // いま出している重み（viseme名 -> weight）。動いた名前だけ増える
  var plan = null;     // { id, cues, t0Ms }  t0Ms = 発話が始まった local 時刻（performance.now系）
  var lastId = null;
  var ticking = false;

  function clockMs() {
    return (window.performance && performance.now) ? performance.now() : Date.now();
  }

  function apply() {
    var f = window.__face;
    if (!f || typeof f.mouth !== 'function') { return false; }
    for (var name in now) {
      if (Object.prototype.hasOwnProperty.call(now, name)) { f.mouth(name, now[name]); }
    }
    return true;
  }

  /** いまの経過時間(ms)に当たる cue を探す。**cue は at_ms の昇順に並んでいる前提**
   * （face_speech.build_cues の契約）。「いま以前で一番新しい cue」を1つ返す */
  function activeAt(cues, elapsedMs) {
    var found = null;
    for (var i = 0; i < cues.length; i++) {
      if (cues[i].at_ms > elapsedMs) { break; }
      found = cues[i];
    }
    return found;
  }

  function tick() {
    if (!plan) { ticking = false; return; }
    var elapsed = clockMs() - plan.t0Ms;
    var cue = elapsed >= 0 ? activeAt(plan.cues, elapsed) : null;
    var targetName = (cue && cue.viseme) ? cue.viseme : null;
    var targetWeight = cue ? (cue.weight || 0) : 0;

    // 目標へ寄せる。EASE_MS で 1/e まで近づく素朴な追従（v1 と同じ式）
    var k = 1 - Math.exp(-TICK_MS / EASE_MS);
    var moving = false;
    for (var name in now) {
      if (!Object.prototype.hasOwnProperty.call(now, name)) { continue; }
      var goal = (name === targetName) ? targetWeight : 0;
      now[name] += (goal - now[name]) * k;
      if (now[name] < 0.001) { now[name] = 0; }
      if (now[name] > 0) { moving = true; }
    }
    if (targetName && !Object.prototype.hasOwnProperty.call(now, targetName)) {
      now[targetName] = targetWeight * k;
      moving = true;
    }
    apply();

    var totalMs = plan.cues.length ? plan.cues[plan.cues.length - 1].at_ms : 0;
    if (elapsed > totalMs + TAIL_MS && !moving) {
      plan = null;          // 鳴り終わり、口も動き終わった
      ticking = false;
      return;
    }
    setTimeout(tick, TICK_MS);
  }

  function start(data) {
    var cues = Array.isArray(data.cues) ? data.cues : [];
    // サーバの started_at（秒）と、いまの時刻から経過を推測する。**同じPC上の
    // client/server なので時計はそろっている前提**（ADR-011 D6。この小窓は
    // 127.0.0.1 のサーバが同じ机の上のブラウザへ配っているだけ）。
    var startedAtMs = (typeof data.started_at === 'number') ? data.started_at * 1000 : Date.now();
    var elapsedMs = Math.max(0, Date.now() - startedAtMs);
    var totalMs = cues.length ? cues[cues.length - 1].at_ms : 0;
    if (elapsedMs > totalMs + TAIL_MS) { return; }   // 鳴り終わっている(再起動直後など)。動かさない
    plan = { id: data.id, cues: cues, t0Ms: clockMs() - elapsedMs };
    if (!ticking) { ticking = true; setTimeout(tick, 0); }
  }

  function poll() {
    fetch('/api/v1/face/mouth', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || d.id == null || d.id === lastId) { return; }
        lastId = d.id;
        start(d);
      })
      .catch(function () { /* 繋がらなくても黙って待つ。口は付け足しであって本体ではない */ });
  }

  function boot() {
    if (!window.__face || typeof window.__face.mouth !== 'function') {
      // face.html 側の窓口（module script）がまだ無い。**何もしない**（3Dの邪魔をしない）
      setTimeout(boot, 1000);
      return;
    }
    poll();
    setInterval(poll, POLL_MS);
  }

  boot();
})();
