/* Shared node/owner editor for the Console and WM1303 Manager. */
(() => {
  const panelId = 'wm1303-node-info';
  const fields = ['node_name', 'owner_info', 'latitude', 'longitude'];
  let scheduled = false;

  function createPanel() {
    const panel = document.createElement('section');
    panel.id = panelId;
    panel.className = 'glass-card rounded-[10px] p-4 lg:p-6';
    panel.style.cssText = 'padding:20px;margin:16px 0;border:1px solid var(--brd,var(--color-border-subtle));border-radius:8px;background:var(--card,var(--color-surface));color:var(--tx,var(--color-text-primary))';
    panel.innerHTML = `
      <h3 style="margin:0 0 8px;font-size:17px">Node &amp; Owner Information</h3>
      <p style="font-size:12px;margin:0 0 16px">Public information shared with mesh clients. Owner information can include a callsign, contact details, or a short description.</p>
      <form>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px">
          <label>Node name<input name="node_name" type="text" required autocomplete="off"></label>
          <label>Owner information<textarea name="owner_info" rows="3" aria-describedby="wm1303-owner-limit"></textarea>
            <small id="wm1303-owner-limit" style="display:block;margin-top:4px">Up to 115 UTF-8 bytes. Clear this field to remove the saved owner information.</small></label>
          <label>Latitude<input name="latitude" type="number" min="-90" max="90" step="any" required></label>
          <label>Longitude<input name="longitude" type="number" min="-180" max="180" step="any" required></label>
        </div>
        <div style="display:flex;gap:10px;margin-top:14px">
          <button class="btn pri cfg-btn-primary" type="submit">Save node information</button>
          <button class="btn sec cfg-btn-secondary" type="button" data-reload>Reload</button>
        </div>
        <p data-status role="status" aria-live="polite" style="font-size:12px;margin-top:10px"></p>
      </form>`;
    panel.querySelectorAll('input,textarea').forEach(input => {
      input.className = 'cfg-input';
      input.style.cssText = 'display:block;width:100%;box-sizing:border-box;margin-top:5px;padding:8px;border:1px solid var(--brd,var(--color-border-subtle));border-radius:5px;background:var(--bg,var(--color-surface));color:inherit';
    });
    const form = panel.querySelector('form');
    const status = panel.querySelector('[data-status]');
    let loaded = false, busy = false;
    const message = (text, failed = false) => {
      status.textContent = text;
      status.style.color = failed ? '#ef4444' : 'inherit';
    };
    const controls = () => {
      form.querySelectorAll('input,textarea,button').forEach(input => {
        input.disabled = busy || (!loaded && !input.hasAttribute('data-reload'));
      });
    };
    async function request(path, data) {
      const headers = {Accept: 'application/json', Authorization: 'Bearer ' + (localStorage.getItem('pymc_jwt_token') || '')};
      if (data) headers['Content-Type'] = 'application/json';
      const response = await fetch(path, {method: data ? 'POST' : 'GET', headers, ...(data ? {body: JSON.stringify(data)} : {})});
      const result = await response.json();
      if (!response.ok || result.success === false || result.error) throw new Error(result.error || 'Unable to save node information');
      return result;
    }
    panel.loadState = async () => {
      if (busy) return;
      busy = true; controls(); message('Loading node information…');
      try {
        const result = await request('/api/repeater_info');
        fields.forEach(name => { form.elements[name].value = result.data[name] ?? ''; });
        loaded = true; message('Node information loaded.');
      } catch (error) { message(error.message, true); }
      finally { busy = false; controls(); }
    };
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (!loaded || busy) return;
      try {
        const data = Object.fromEntries(fields.map(name => [name, form.elements[name].value]));
        if (!data.node_name.trim() || new TextEncoder().encode(data.node_name.trim()).length > 31) throw new Error('Node name must be 1–31 UTF-8 bytes.');
        if (new TextEncoder().encode(data.owner_info.trim()).length > 115) throw new Error('Owner information must be at most 115 UTF-8 bytes.');
        data.latitude = Number(data.latitude); data.longitude = Number(data.longitude);
        busy = true; controls(); message('Saving node information…');
        const result = await request('/api/update_radio_config', data);
        message(result.data?.restart_required ? 'Saved. Restart the repeater to apply the changes.' : 'Node and owner information saved and applied.');
      } catch (error) { message(error.message, true); }
      finally { busy = false; controls(); }
    });
    panel.querySelector('[data-reload]').addEventListener('click', panel.loadState);
    controls();
    return panel;
  }

  function mount() {
    const manager = document.getElementById('wm1303-node-info-host');
    const route = location.pathname.replace(/\/$/, '');
    const tab = new URLSearchParams(location.search).get('tab');
    const consoleRoute = (route === '/configuration' && tab === 'repeater') || route === '/system/configuration/radio/repeater';
    const existing = document.getElementById(panelId);
    if (!manager && !consoleRoute) { existing?.remove(); return; }
    if (existing) return;
    const heading = [...document.querySelectorAll('h3')].find(element => element.textContent.trim() === 'General Settings');
    const settings = heading?.parentElement;
    if (!manager && !settings) return;
    const panel = createPanel();
    if (manager) manager.appendChild(panel);
    else settings.insertAdjacentElement('afterend', panel);
    panel.loadState();
  }
  function schedule() {
    if (scheduled) return;
    scheduled = true;
    queueMicrotask(() => { scheduled = false; mount(); });
  }
  function start() {
    new MutationObserver(schedule).observe(document.body, {childList: true, subtree: true});
    addEventListener('popstate', schedule);
    mount();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
  else start();
})();
