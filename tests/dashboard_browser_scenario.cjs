/* Real compiled Console and Manager checks with radio-free API fixtures.
 * Usage: node tests/dashboard_browser_scenario.cjs /path/to/console/html
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const puppeteer = require('puppeteer-core');

const root = path.resolve(__dirname, '..');
const overlayHtml = path.join(root, 'overlay/openhop_repeater/repeater/web/html');
const originalHtml = process.argv[2];
if (!originalHtml || !fs.existsSync(path.join(originalHtml, 'index.html'))) throw new Error('Supply the upstream compiled Console HTML directory');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'wm1303-dashboard-'));
const htmlDir = path.join(temporary, 'html');
fs.cpSync(originalHtml, htmlDir, {recursive: true});
execFileSync(process.env.WM1303_TEST_PYTHON || 'python3', [path.join(root, 'overlay/openhop_repeater/repeater/web/console_assets.py'), htmlDir]);
const requests = [], saves = [], errors = [];
let scanEnabled = false, scopeKnown = true, failOwnerSave = false;
const now = Math.floor(Date.now() / 1000);
let info = {node_name: 'Hill', owner_info: 'OE1ABC', latitude: 48.2, longitude: 16.3};
const channel = {name: 'Hill channel', friendly_name: 'Hill channel', active: true, frequency: 869618000, bandwidth: 125000, spreading_factor: 8, coding_rate: '4/5'};
const packet = {id: 1, timestamp: now, type: 15, route: 0, length: 10, rssi: -80, snr: 7, score: 0.8, transmitted: false, is_duplicate: false, packet_hash: 'ab'.repeat(16), raw_packet: '3c010200000074657874', original_path: '[]', forwarded_path: '[]'};
const config = {radio_type: 'wm1303', node_name: 'Hill', repeater: {mode: 'forward', node_name: 'Hill', latitude: 48.2, longitude: 16.3}, radio: {frequency: 869618000, bandwidth: 125000, spreading_factor: 8, coding_rate: 5, tx_power: 22}, duty_cycle: {enforcement_enabled: true, max_airtime_percent: 6.062}, mesh: {}, web: {auth: {enabled: true}}};
const stats = {version: 'fixture', core_version: 'fixture', uptime_seconds: 660, rx_count: 1234567, forwarded_count: 43210, current_airtime_ms: 1818.6, utilization_percent: 50, tx_duty_pct: 3.031, rx_duty_pct: 1.234, rx_availability_pct: 96.969, config};
const success = data => ({success: true, data});

function response(url, body) {
  requests.push(url.pathname + url.search);
  if (/setup/.test(url.pathname)) return {success: true, setup_required: false, setup_complete: true};
  if (/auth\/verify/.test(url.pathname)) return {success: true, valid: true, username: 'admin', role: 'admin'};
  if (url.pathname === '/api/stats') return stats;
  if (url.pathname === '/api/repeater_info') return success({...info});
  if (url.pathname === '/api/update_radio_config') {
    saves.push(body);
    if (failOwnerSave) return {success: false, error: 'Fixture persistence failure'};
    info = {...info, ...body};
    return success({persisted: true, live_update: true, restart_required: false});
  }
  if (url.pathname === '/api/packet_stats') return success({total_packets: 1234567, transmitted_packets: 43210, dropped_packets: 65432, drop_reasons: [{reason: 'Policy blocked packet: fixture', count: 12345}], type_distribution: []});
  if (url.pathname === '/api/recent_packets' || url.pathname === '/api/filtered_packets') return success([packet]);
  if (/packet_by_(id|hash)$/.test(url.pathname)) return success({...packet, scope: scopeKnown ? '#vienna' : 'unknown scope', scope_known: scopeKnown});
  if (/packet_type_graph_data|metrics_graph_data/.test(url.pathname)) return success({series: []});
  if (url.pathname === '/api/airtime_chart_data') return success({buckets: [], rx_total: 0, tx_total: 0});
  if (/crc_error/.test(url.pathname)) return success([]);
  if (url.pathname === '/api/noise_floor_history') return success({history: []});
  if (url.pathname === '/api/wm1303/status') return {service: 'active', packets_received: 1234567, packets_sent: 43210, active_channels: 1, pi_info: {}};
  if (url.pathname === '/api/wm1303/spectrum') {
    if (body?.action === 'toggle') {scanEnabled = body.enable; return {status: 'ok', enabled: scanEnabled, saved: true, restart_required: false};}
    const scan = {timestamp: scanEnabled ? now : null, scan_points: scanEnabled ? [{freq_mhz: 869.6, rssi_dbm: -104}] : [], freq_range: {start_mhz: 868.8, stop_mhz: 870.4, region: 'EU868'}, note: 'Measured fixture'};
    if (body?.action === 'scan') return scan;
    return {spectral_scan_enabled: scanEnabled, spectral_scan_requested: scanEnabled, scan_pace_seconds: 300, scan_channel_count: 8, lbt_enabled: false, lbt_channels: [], channels: [], last_scan: scan};
  }
  if (url.pathname === '/api/wm1303/channels') return {channels: [channel]};
  if (url.pathname === '/api/wm1303/channels/live') return {channels: [{...channel, rx_packets: 1234567, tx_packets: 43210, tx_duty_pct: 3.031, rx_duty_pct: 1.234}], duty_cycle: {tx_duty_pct: 3.031, rx_duty_pct: 1.234, rx_availability_pct: 96.969}};
  if (/\/channel_[ef]$/.test(url.pathname)) return {enabled: false};
  if (url.pathname === '/api/wm1303/duty_cycle_history') {
    const hours = Number(url.searchParams.get('hours'));
    const data = [{t: now - 600, bucket_seconds: 60, tx_duty_pct: 3.031, tx_airtime_ms: 1818.6}, {t: now - 540, bucket_seconds: 60, tx_duty_pct: 2, tx_airtime_ms: 1200}];
    return {hours, start_time: now - hours * 3600, end_time: now, interval_tx_duty_pct: 0.01 / hours, channels: [{id: 'channel_a', label: 'Hill channel', data}], data, note: 'TX airtime / elapsed time'};
  }
  if (url.pathname === '/api/wm1303/adv_config') return {};
  if (/wm1303\//.test(url.pathname)) return {channels: [], queues: {}, data: [], stats: {}, enabled: false};
  if (/config/.test(url.pathname)) return success(config);
  if (/neighbors/.test(url.pathname)) return success({neighbors: {}});
  return success({});
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/auth/')) {
    let text = ''; for await (const chunk of req) text += chunk;
    const data = response(url, text ? JSON.parse(text) : null);
    res.writeHead(200, {'Content-Type': 'application/json'}); res.end(JSON.stringify(data)); return;
  }
  const relative = url.pathname.startsWith('/assets/') ? url.pathname.slice(1) : url.pathname.startsWith('/wm1303') ? url.pathname.slice(1) : 'index.html';
  const base = relative.startsWith('wm1303') ? overlayHtml : htmlDir;
  const target = path.resolve(base, relative);
  if (!target.startsWith(base + path.sep) || !fs.existsSync(target)) {res.writeHead(404); res.end(); return;}
  let content = fs.readFileSync(target);
  if (relative === 'index.html') content = content.toString().replace('</head>', '<script src="/wm1303-access.js" defer></script><script src="/wm1303-node-info.js" defer></script></head>');
  res.writeHead(200, {'Content-Type': {'.js': 'application/javascript', '.css': 'text/css', '.html': 'text/html'}[path.extname(target)] || 'application/octet-stream'});
  res.end(content);
});

(async () => {
  let browser;
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const origin = 'http://127.0.0.1:' + server.address().port;
    browser = await puppeteer.launch({executablePath: process.env.CHROMIUM_PATH || '/etc/profiles/per-user/andreas/bin/chromium', headless: true, args: ['--no-sandbox']});
    const page = await browser.newPage();
    page.on('pageerror', error => errors.push(error.message));
    const token='fixture.'+Buffer.from(JSON.stringify({exp: now+36000, sub: 'admin', role: 'admin'})).toString('base64url')+'.signature';
    await page.evaluateOnNewDocument(token => {
      localStorage.setItem('pymc_jwt_token', token);
      class FixtureWebSocket extends EventTarget {
        constructor() {
          super(); this.readyState=0;
          setTimeout(()=>{this.readyState=1;const event=new Event('open');this.dispatchEvent(event);if(this.onopen)this.onopen(event);},30);
        }
        send() {}
        close() {this.readyState=3;}
      }
      Object.assign(FixtureWebSocket,{CONNECTING:0,OPEN:1,CLOSING:2,CLOSED:3});
      window.WebSocket=FixtureWebSocket;
    }, token);
    await page.setViewport({width: 1280, height: 900});
    await page.goto(origin, {waitUntil: 'networkidle0'});
    await page.waitForFunction(() => [...document.querySelectorAll('.card-title')].some(e => e.textContent === 'RX Duty (60s)') && !document.body.innerText.includes('Starting up'));
    const layout = await page.evaluate(() => [...document.querySelectorAll('.sparkline-card')].map(card => {
      const label = card.querySelector('.card-title'), value = card.querySelector('.card-value');
      return {label: label.textContent, value: value.textContent, below: value.getBoundingClientRect().top >= label.getBoundingClientRect().bottom, fits: value.getBoundingClientRect().right <= card.getBoundingClientRect().right};
    }));
    assert.equal(layout.length, 8);
    assert.ok(layout.every(item => item.below && item.fits), JSON.stringify(layout));
    assert.equal(layout.find(item => item.label === 'RX Packets').value, '1,234,567');
    assert.equal(layout.find(item => item.label === 'RX Duty (60s)').value, '1.234%');
    assert.ok((await page.evaluate(() => document.body.textContent)).includes('3.031% / 6.1% limit'));
    assert.ok((await page.evaluate(() => document.body.textContent)).includes('TX budget used: 50.0%'));
    await page.screenshot({path: path.join(temporary, 'console-desktop.png')});
    await page.setViewport({width: 390, height: 844});
    await page.waitForTimeout(400);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({path: path.join(temporary, 'console-mobile.png')});

    await page.goto(origin + '/configuration?tab=repeater', {waitUntil: 'networkidle0'});
    await page.waitForSelector('#wm1303-node-info');
    await page.waitForFunction(() => !document.querySelector('#wm1303-node-info button[type=submit]').disabled);
    await page.evaluate(() => {document.querySelector('#wm1303-node-info textarea').value = 'OE1ABC\nContact: owner@example.test'; document.querySelector('#wm1303-node-info form').requestSubmit();});
    await page.waitForFunction(() => document.querySelector('#wm1303-node-info [data-status]').textContent.includes('saved and applied'));
    assert.equal(saves.at(-1).owner_info, 'OE1ABC\nContact: owner@example.test');
    failOwnerSave = true;
    await page.evaluate(() => {document.querySelector('#wm1303-node-info textarea').value = 'Retain this draft'; document.querySelector('#wm1303-node-info form').requestSubmit();});
    await page.waitForFunction(() => document.querySelector('#wm1303-node-info [data-status]').textContent.includes('persistence failure'));
    assert.equal(await page.$eval('#wm1303-node-info textarea', e => e.value), 'Retain this draft');
    failOwnerSave = false;

    await page.setViewport({width: 1280, height: 900});
    await page.goto(origin, {waitUntil: 'networkidle0'});
    const openPacket = async () => {
      await page.waitForSelector('.packet-row', {visible: true});
      await page.$eval('.packet-row', element => element.click());
      try {
        await page.waitForFunction(() => document.body.textContent.includes('Scope:'), {timeout: 5000});
      } catch(error) {
        console.error('Packet view:', await page.evaluate(() => document.body.innerText.slice(-2500)));
        console.error('Packet requests:', requests.filter(url => /packet_by|filtered_packets/.test(url)).slice(-10));
        throw error;
      }
    };
    await openPacket();
    await page.waitForFunction(() => document.body.textContent.includes('Scope: #vienna'));
    scopeKnown = false;
    await page.reload({waitUntil: 'networkidle0'}); await openPacket();
    await page.waitForFunction(() => document.body.textContent.includes('Scope: unknown scope'));

    // Use the real Chart.js/adapter for the Manager while avoiding unrelated CDN dependencies.
    const cdn = new Map();
    for (const url of ['https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js', 'https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js']) cdn.set(url, execFileSync('curl', ['-fsSL', url]));
    await page.setRequestInterception(true);
    page.on('request', request => {
      if (cdn.has(request.url())) request.respond({status: 200, contentType: 'application/javascript', body: cdn.get(request.url())});
      else if (request.url().startsWith(origin)) request.continue();
      else request.abort();
    });
    await page.goto(origin + '/wm1303.html', {waitUntil: 'networkidle0'});
    await page.waitForFunction(() => document.getElementById('st-rx-duty').textContent === '1.234%');
    await page.evaluate(() => showTab('spectrum'));
    await page.waitForFunction(() => document.getElementById('specEnableLabel').textContent === 'Disabled');
    assert.ok((await page.$eval('#specScanHelp', e => e.textContent)).includes('disabled by default'));
    await page.click('#specEnableCheck');
    await page.evaluate(() => specLoad());
    await page.waitForFunction(() => Chart.getChart(document.getElementById('spectrumChart'))?.data.datasets[0].data[0] === -104);
    for (const hours of [1, 6, 12, 24, 72, 168]) {
      await page.evaluate(hours => setSigHours(hours), hours);
      await page.waitForFunction(expectedMin => Chart.getChart(document.getElementById('dutyHistoryChart'))?.options.scales.x.min === expectedMin, {}, (now-hours*3600)*1000);
      assert.ok(requests.includes('/api/wm1303/duty_cycle_history?hours=' + hours));
    }
    assert.ok((await page.$eval('#specScanHelp', e => e.textContent)).includes('40 minutes'));
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({passed: true, dutyIntervals: [1, 6, 12, 24, 72, 168], screenshots: temporary}, null, 2));
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => {console.error(error); console.error('Browser errors:', errors); process.exitCode = 1;});
