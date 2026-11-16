const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const NOW = Date.parse('2026-11-01T00:35:00Z');
const HEADER = 'checked_at,source,status,content_hash,source_updated,record_count,raw_path\n';
const iso = age => new Date(NOW - age * 60000).toISOString().replace('.000Z', 'Z');
const row = (age, source, status) => `${iso(age)},${source},${status},hash,,1,\n`;
const pair = (age, press = 'error') => row(age, 'un_xml', 'review_required') + row(age, 'un_press', press);

function setup({recentAge = 5, csv, previous, active, status = 204, lookup = 200,
                prior = {}, initialTriggers = [], lock = true, refuseStateWrite = false} = {}) {
  const values = {OFAC_WATCHDOG_TOKEN: 'example-token', OFAC_WATCHDOG_EMAIL: 'operator@example.com',
    OFAC_WATCHDOG_STATE: '{"example":true}', ...prior};
  const calls = [];
  const triggers = initialTriggers.map(handler => ({getHandlerFunction: () => handler}));
  const response = (code, body) => ({getResponseCode: () => code, getContentText: () => JSON.stringify(body)});
  const c = vm.createContext({
    Date: class extends Date { static now() {return NOW;} },
    console: {log() {}},
    PropertiesService: {getScriptProperties: () => ({
      getProperty: key => values[key] ?? null,
      setProperty: (key, value) => {
        if (refuseStateWrite) throw Error('state unavailable');
        values[key] = value;
      },
    })},
    LockService: {getScriptLock: () => ({tryLock: () => lock, releaseLock() {}})},
    Utilities: {parseCsv: text => text.trim().split('\n').map(r => r.split(',')),
      base64Decode: text => Buffer.from(text, 'base64'), newBlob: data => ({getDataAsString: () => data.toString('utf8')})},
    UrlFetchApp: {fetch: (url, config) => {
      const path = new URL(url).pathname;
      calls.push({path, ...config});
      if (path.includes('/heartbeat/')) {
        const text = path.endsWith('2026-10.csv') ? previous : (csv ?? HEADER + pair(recentAge));
        return text ? response(200, {encoding: 'base64', content: Buffer.from(text).toString('base64')}) : response(404);
      }
      assert.match(path, /\/actions\/workflows\/watch-un\.yml\//);
      if (path.endsWith('/dispatches')) return response(status);
      assert.match(path, /\/runs$/);
      return response(lookup, {workflow_runs: new URL(url).searchParams.get('status') === active ? [{id: 123}] : []});
    }},
    ScriptApp: {
      getProjectTriggers: () => [...triggers],
      deleteTrigger: trigger => triggers.splice(triggers.indexOf(trigger), 1),
      newTrigger: handler => ({timeBased() {return this;}, everyMinutes(n) {assert.equal(n, 10); return this;},
        create() {triggers.push({getHandlerFunction: () => handler});}}),
    },
  });
  vm.runInContext(['OfacWatchdog.gs', 'UnWatchdog.gs'].filter(name => fs.existsSync('apps_script/' + name))
    .map(name => fs.readFileSync('apps_script/' + name, 'utf8')).join('\n'), c);
  return {c, values, calls, triggers, dispatches: () => calls.filter(call => call.method === 'post'),
    state: () => JSON.parse(values.UN_WATCHDOG_STATE || '{}')};
}

test('recent heartbeat differentiates an active poll from a committee fetch error', () => {
  const e = setup();
  assert.equal(e.c.unWatchdogTick().decision, 'recent_check');
  assert.equal(e.state().lastCheckedAt, iso(5));
  assert.equal(e.state().pressStatus, 'error');
  assert.equal(e.state().xmlStatus, 'review_required');
  assert.equal(e.values.OFAC_WATCHDOG_STATE, '{"example":true}');
  assert.equal(e.dispatches().length, 0);
});

test('dispatches watch-un at 45 minutes and suppresses duplicates for 60 minutes', () => {
  const e = setup({csv: HEADER, previous: HEADER + pair(45)});
  assert.equal(e.c.unWatchdogTick().decision, 'accepted');
  assert.deepEqual(JSON.parse(e.dispatches()[0].payload), {ref: 'main', return_run_details: false});
  assert.equal(e.state().lastDispatchAt, iso(0));
  assert.equal(e.c.unWatchdogTick().decision, 'cooldown');
  assert.equal(e.dispatches().length, 1);
});

test('every active run state suppresses UN rescue', () => {
  for (const active of ['queued', 'in_progress', 'pending', 'waiting', 'requested']) {
    const e = setup({csv: HEADER, previous: HEADER + pair(50), active});
    assert.equal(e.c.unWatchdogTick().decision, 'active_run', active);
    assert.equal(e.dispatches().length, 0);
  }
});

test('does not dispatch blindly after GitHub read, state or heartbeat errors', () => {
  for (const [options, expected] of [
    [{csv: HEADER}, /heartbeat/],
    [{csv: HEADER, previous: HEADER + pair(60), lookup: 403}, /403/],
    [{csv: HEADER, previous: HEADER + pair(60), refuseStateWrite: true}, /state unavailable/],
    [{prior: {UN_WATCHDOG_STATE: '{"lastDispatchAttemptAt":"malformed"}'}}, /UN_WATCHDOG_STATE/],
    [{csv: HEADER + row(5, 'un_xml', 'unchanged'), previous: HEADER + pair(60)}, /heartbeat/],
  ]) {
    const e = setup(options);
    assert.throws(() => e.c.unWatchdogTick(), expected);
    assert.equal(e.dispatches().length, 0);
  }
});

test('ambiguous dispatch failure keeps its attempt timestamp and rate limits the next tick', () => {
  const e = setup({csv: HEADER, previous: HEADER + pair(45), status: 502});
  assert.throws(() => e.c.unWatchdogTick(), /502/);
  assert.equal(e.state().lastDispatchAt, undefined);
  assert.equal(e.state().lastDispatchAttemptAt, iso(0));
  assert.equal(e.state().decision, 'dispatch_error');
  assert.equal(e.c.unWatchdogTick().decision, 'cooldown');
  assert.equal(e.dispatches().length, 1);
});

test('check and install never dispatch and preserve existing OFAC trigger', () => {
  const e = setup({csv: HEADER, previous: HEADER + pair(90), initialTriggers: ['ofacWatchdogTick']});
  assert.equal(e.c.checkUnWatchdog().lastCheckedAt, iso(90));
  assert.equal(e.values.UN_WATCHDOG_STATE, undefined);
  e.c.installUnWatchdog();
  e.c.installUnWatchdog();
  assert.deepEqual(e.triggers.map(t => t.getHandlerFunction()), ['ofacWatchdogTick', 'unWatchdogTick']);
  assert.equal(e.dispatches().length, 0);
});

test('a future heartbeat cannot hide an older valid paired check', () => {
  const e = setup({csv: HEADER + pair(-10), previous: HEADER + pair(45)});
  assert.equal(e.c.unWatchdogTick().decision, 'accepted');
  assert.equal(e.state().lastCheckedAt, iso(45));
});
