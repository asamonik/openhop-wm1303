"""Exercise the shipped trace renderer with plain-text channel names."""

from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'Node.js is required for trace rendering')
class ManagerTraceTests(unittest.TestCase):
    def test_channel_names_are_text_in_rows_targets_and_statistics(self):
        check = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const scripts = [...source.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)];
const script = scripts.find(match => match[1].includes('function __renderTraces_real('))[1];
const rawName = process.argv[2];
const escape = text => String(text).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const expected = escape(rawName);
const elements = new Map(['traceList', 'traceNoData', 'traceFilterStatus', 'traceFilterChannel',
  'traceFilterLimit', 'traceStatsPanel', 'traceStatsGrid', 'traceStatsWindow'].map(id => [id, {
    value: '', style: {}, innerHTML: '', textContent: '',
    querySelectorAll: () => [], addEventListener() {},
  }]));
const trace = {
  pkt_hash: 'abcdef12', pkt_hash_full: 'abcdef1234567890',
  first_seen: '2026-10-27T10:00:00', channel: rawName, type: 'RAW_CUSTOM',
  total_ms: 4, hops: 0, path_hashes: [], status: 'forwarded',
  steps: [{name: 'received', channel: 'channel_a', elapsed_ms: 0, status: 'ok', detail: ''},
    {name: 'tx_send', elapsed_ms: 2, status: 'ok', detail: 'TX on channel_e (rule: fixture)'},
    {name: 'tx_send', elapsed_ms: 4, status: 'ok', detail: 'TX on channel_f (rule: fixture)'},
    {name: 'tx_send', channel: 'High Alpine', elapsed_ms: 5, status: 'ok', detail: 'sent'},
    {name: 'tx_send', elapsed_ms: 6, status: 'ok', detail: 'TX on Mountain Ridge (rule: fixture)'},
    {name: 'tx_send', channel: 'constructor', elapsed_ms: 7, status: 'ok', detail: 'sent'}],
};
const context = {
  console, Set, Date, Math, JSON, URL, Blob,
  document: {getElementById: id => elements.get(id) || null,
    querySelectorAll: () => [], addEventListener() {}},
  chData: [{name: rawName}], cheData: {name: rawName}, chfData: {friendly_name: 'Wide <fixture>'},
  showTab() {}, addEventListener() {}, setTimeout() {}, clearTimeout() {},
  setInterval() {}, clearInterval() {},
  esc: escape,
  api: async () => ({traces: [trace]}),
};
context.window = context;
vm.runInNewContext(script, context);
context.traceLoad();
setImmediate(() => {
  for (const id of ['traceList', 'traceStatsGrid']) {
    const html = elements.get(id).innerHTML;
    assert.ok(html.includes(expected), id + ' must render the complete literal channel name');
    assert.ok(!html.includes('<b data-fixture-name>'), id + ' must not interpret channel-name markup');
  }
  assert.ok(elements.get('traceList').innerHTML.includes('Wide &lt;fixture&gt;'), 'Channel F keeps its friendly name');
  for (const name of ['High Alpine', 'Mountain Ridge', 'constructor']) {
    assert.ok(elements.get('traceList').innerHTML.includes('title="TX target: '+name+'"'), 'Complete TX target: ' + name);
  }
  const stats = elements.get('traceStatsGrid').innerHTML;
  const topChannel = stats.slice(stats.indexOf('Top Channel'));
  assert.ok(topChannel.includes(expected), 'Top channel must count the literal friendly name');
  assert.ok(topChannel.includes('1 \u00d7'), 'Top channel must have a numeric count');
});
'''
        for name in ('Hill " & <b data-fixture-name>mesh</b>', 'constructor', '__proto__', 'toString'):
            with self.subTest(name=name):
                result = subprocess.run(
                    ['node', '-e', check, str(ROOT / 'overlay/openhop_repeater/repeater/web/html/wm1303.html'), name],
                    capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
