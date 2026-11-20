/**
 * 国連の定期起動を監視する。既存の専用OFAC外部監視プロジェクトへ追加する。
 * OFACのGitHub接続設定のみを共用し、救済の状態・トリガーは別に持つ。
 */
const UN_WATCHDOG = Object.freeze({
  workflow: 'watch-un.yml',
  stateKey: 'UN_WATCHDOG_STATE',
  handler: 'unWatchdogTick',
  rescueMinutes: 45,
  cooldownMinutes: 60,
  // Apps Script limits each execution to six minutes.
  leaseMinutes: 10,
});

function unWatchdogState_(props, now) {
  const state = JSON.parse(props.getProperty(UN_WATCHDOG.stateKey) || '{}');
  if (!state || Array.isArray(state) || typeof state !== 'object' ||
      ['lastDispatchAttemptAt', 'lastDispatchAt', 'runningSince'].some(key => {
        if (state[key] === undefined) return false;
        const stamp = ofacWatchdogTime_(state[key]);
        return !Number.isFinite(stamp) || stamp > now + 30000;
      })) {
    throw new Error('UN_WATCHDOG_STATEの救済時刻が不正です');
  }
  return state;
}

function unWatchdogHeartbeat_(cfg, now) {
  const current = new Date(now);
  const previous = new Date(Date.UTC(current.getUTCFullYear(), current.getUTCMonth() - 1, 1));
  const header = 'checked_at,source,status,content_hash,source_updated,record_count,raw_path';
  const statuses = ['unchanged', 'review_required', 'error'];
  let latest = -Infinity;
  let sources = {};

  for (const date of [current, previous]) {
    const month = date.toISOString().slice(0, 7);
    const text = ofacWatchdogFile_(cfg, 'data/heartbeat/' + month + '.csv');
    if (text === null) continue;
    const rows = Utilities.parseCsv(text.replace(/^\uFEFF/, ''));
    if (!rows.length || rows[0].join(',') !== header) throw new Error('UN heartbeatの列定義が不正: ' + month);
    for (const row of rows.slice(1)) {
      const [checkedAt, source, status] = row;
      if (source !== 'un_xml' && source !== 'un_press') continue;
      const checked = ofacWatchdogTime_(checkedAt);
      if (!Number.isFinite(checked) || row.length !== 7 || !statuses.includes(status)) {
        throw new Error('UN heartbeatの記録が不正: ' + month);
      }
      if (checked > now + 30000 || checked < latest) continue;
      if (checked > latest) {
        latest = checked;
        sources = {};
      }
      if (sources[source] && sources[source] !== status) throw new Error('UN heartbeatの状態が矛盾: ' + month);
      sources[source] = status;
    }
    if (Number.isFinite(latest)) break;
  }

  if (!sources.un_xml || !sources.un_press) throw new Error('UN heartbeatの最新記録が2系統揃っていません');
  return {
    lastCheckedAt: ofacWatchdogIso_(latest), ageMinutes: Math.max(0, now - latest) / 60000,
    xmlStatus: sources.un_xml, pressStatus: sources.un_press,
  };
}

function unWatchdogActive_(cfg) {
  for (const status of ['queued', 'in_progress', 'pending', 'waiting', 'requested']) {
    const path = '/actions/workflows/' + UN_WATCHDOG.workflow + '/runs?branch=main&status=' + status + '&per_page=1';
    const runs = ofacWatchdogApi_(cfg, path);
    if (!Array.isArray(runs.workflow_runs)) throw new Error('UN workflow runs応答が不正');
    if (runs.workflow_runs.length) return true;
  }
  return false;
}

function unWatchdogTriggers_() {
  return ScriptApp.getProjectTriggers().filter(trigger => trigger.getHandlerFunction() === UN_WATCHDOG.handler);
}

function checkUnWatchdog() {
  const cfg = ofacWatchdogConfig_();
  const now = Date.now();
  const result = Object.assign(unWatchdogHeartbeat_(cfg, now), {
    activeRun: unWatchdogActive_(cfg),
    triggerCount: unWatchdogTriggers_().length,
    savedState: unWatchdogState_(cfg.props, now),
  });
  console.log(JSON.stringify(result));
  return result;
}

function installUnWatchdog() {
  const cfg = ofacWatchdogConfig_();
  const now = Date.now();
  unWatchdogHeartbeat_(cfg, now);
  unWatchdogActive_(cfg);
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) throw new Error('外部監視の実行が終わってから再実行してください');
  try {
    unWatchdogState_(cfg.props, Date.now());
    const triggers = unWatchdogTriggers_();
    if (!triggers.length) ScriptApp.newTrigger(UN_WATCHDOG.handler).timeBased().everyMinutes(10).create();
    triggers.slice(1).forEach(trigger => ScriptApp.deleteTrigger(trigger));
    console.log('unWatchdogTickの10分間隔トリガーを設定しました');
  } finally {
    lock.releaseLock();
  }
}

function unWatchdogClaim_(props, now) {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return {decision: 'lock_busy'};
  try {
    const state = unWatchdogState_(props, now);
    if (now - ofacWatchdogTime_(state.runningSince) < UN_WATCHDOG.leaseMinutes * 60000) {
      return {decision: 'tick_in_progress'};
    }
    state.runningSince = ofacWatchdogIso_(now);
    state.lastTickAt = state.runningSince;
    state.decision = 'checking';
    props.setProperty(UN_WATCHDOG.stateKey, JSON.stringify(state));
    return {state};
  } finally {
    lock.releaseLock();
  }
}

function unWatchdogTick() {
  const props = PropertiesService.getScriptProperties();
  const now = Date.now();
  const claim = unWatchdogClaim_(props, now);
  if (!claim.state) return claim;
  const state = claim.state;
  const save = () => props.setProperty(UN_WATCHDOG.stateKey, JSON.stringify(state));
  try {
    const cfg = ofacWatchdogConfig_();
    Object.assign(state, unWatchdogHeartbeat_(cfg, now));
    const attempt = ofacWatchdogTime_(state.lastDispatchAttemptAt);
    if (state.ageMinutes < UN_WATCHDOG.rescueMinutes) {
      state.decision = 'recent_check';
    } else if (unWatchdogActive_(cfg)) {
      state.decision = 'active_run';
    } else if (now - attempt < UN_WATCHDOG.cooldownMinutes * 60000) {
      state.decision = 'cooldown';
    } else {
      // A scheduled run may have published since our first read.
      Object.assign(state, unWatchdogHeartbeat_(cfg, Date.now()));
      if (state.ageMinutes < UN_WATCHDOG.rescueMinutes) {
        state.decision = 'recent_check';
      } else {
        state.decision = 'dispatching';
        state.lastDispatchAttemptAt = ofacWatchdogIso_(Date.now());
        save();
        ofacWatchdogApi_(cfg, '/actions/workflows/' + UN_WATCHDOG.workflow + '/dispatches',
          'post', {ref: 'main', return_run_details: false});
        state.lastDispatchAt = ofacWatchdogIso_(Date.now());
        state.decision = 'accepted';
      }
    }
    state.lastError = '';
  } catch (error) {
    state.decision = state.decision === 'dispatching' ? 'dispatch_error' : 'check_error';
    state.lastError = ofacWatchdogError_(error, props);
    throw new Error(state.lastError);
  } finally {
    delete state.runningSince;
    save();
  }
  console.log(JSON.stringify(state));
  return state;
}
