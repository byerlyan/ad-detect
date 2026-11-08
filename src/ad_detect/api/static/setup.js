const steps = [
  ['welcome', 'Welcome'], ['video', 'Video'], ['audio', 'Audio'],
  ['apple_tv', 'Apple TV'], ['mode', 'Mode'], ['calibration', 'Calibration'],
  ['readiness', 'Readiness'], ['shadow', 'Shadow'], ['auto_mute_consent', 'Consent']
];
const app = { csrf: '', state: null, config: null, pairingToken: null, selected: null };
const $ = selector => document.querySelector(selector);

function notice(message, error = false) {
  const node = $('#notice'); node.textContent = message; node.hidden = false;
  node.classList.toggle('error', error);
}
function clearNotice() { $('#notice').hidden = true; }
async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.method && options.method !== 'GET') {
    headers['Content-Type'] = 'application/json'; headers['X-CSRF-Token'] = app.csrf;
  }
  const response = await fetch(path, { cache: 'no-store', ...options, headers });
  const body = response.headers.get('content-type')?.includes('json') ? await response.json() : await response.text();
  if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
  return body;
}
async function mutate(path, body = {}) { return api(path, { method: 'POST', body: JSON.stringify(body) }); }

function completedMap() {
  return new Map((app.state?.workflow?.completed || []).map(item => [item.step, item]));
}
function render() {
  const complete = completedMap(); const current = app.state?.workflow?.current_step || 'auto_mute_consent';
  const chosen = app.selected && (complete.has(app.selected) || app.selected === current) ? app.selected : current;
  $('#steps').innerHTML = steps.map(([key, label], index) => `
    <li><button type="button" data-view="${key}" ${key === chosen ? 'aria-current="step"' : ''}>
      <span class="number">${String(index + 1).padStart(2, '0')}</span><span>${label}</span>
      <span class="done">${complete.has(key) ? '✓' : ''}</span></button></li>`).join('');
  document.querySelectorAll('.panel').forEach(panel => panel.classList.toggle('active', panel.dataset.step === chosen));
  $('#connection').textContent = 'Local service connected'; $('#connection').classList.add('online');
  $('#config-meta').textContent = `Active revision ${app.config?.digest?.slice(0, 12) || 'unknown'} · changes require restart`;
  if (app.config) {
    $('#temporal-strategy').value = app.config.settings.temporal.strategy;
    $('#evidence-deadline').value = app.config.settings.temporal.evidence_deadline_ms;
    const profile = $('#profile');
    if (profile.options.length === 1) Object.keys(app.config.profiles).forEach(name => profile.add(new Option(name[0].toUpperCase() + name.slice(1), name)));
  }
}
async function refresh() {
  [app.state, app.config] = await Promise.all([api('/api/v1/setup/state'), api('/api/v1/setup/config')]); render();
}
async function commit(patch) {
  const result = await mutate('/api/v1/setup/config/commit', { patch, expected_digest: app.config.digest });
  notice(`Saved ${result.changed_fields.length} setting change(s). Restart required before runtime use.`); await refresh();
}
async function acknowledge(step, payload = {}) { await mutate(`/api/v1/setup/steps/${step}/acknowledge`, { payload }); app.selected = null; await refresh(); }
async function skip(step, reason) { await mutate(`/api/v1/setup/steps/${step}/skip`, { reason }); app.selected = null; await refresh(); }

document.addEventListener('click', async event => {
  const view = event.target.closest('[data-view]'); if (view) { app.selected = view.dataset.view; render(); return; }
  const button = event.target.closest('[data-action]'); if (!button) return;
  clearNotice(); button.disabled = true;
  try {
    switch (button.dataset.action) {
      case 'welcome': await acknowledge('welcome', { acknowledged_unknown_safety: true }); break;
      case 'discover-capture': {
        const devices = await mutate('/api/v1/setup/capture/devices');
        for (const [id, kind] of [['video-device', 'video'], ['audio-device', 'audio']]) {
          const select = $(`#${id}`); select.innerHTML = '<option value="">Select exact device</option>';
          devices.filter(item => item.kind === kind).forEach(item => select.add(new Option(`${item.index} · ${item.name}`, item.name)));
        }
        notice(`Found ${devices.length} capture endpoint(s).`); break;
      }
      case 'probe-capture': {
        const video = $('#video-device').value; const audio = $('#audio-device').value;
        if (video && audio) await commit({ capture: { video_device: video, audio_device: audio } });
        const result = await mutate('/api/v1/setup/capture/probe');
        const output = app.state.workflow.current_step === 'audio' ? $('#audio-result') : $('#capture-result');
        output.hidden = false; output.textContent = JSON.stringify(result, null, 2);
        if (result.success) { notice('Both streams negotiated successfully.'); app.selected = null; await refresh(); }
        else notice(result.reason || 'Capture probe failed.', true); break;
      }
      case 'discover-appletv': {
        const devices = await mutate('/api/v1/setup/appletv/devices');
        $('#appletv-list').innerHTML = devices.map(item => `<div class="device"><div><strong>${escapeText(item.name)}</strong><br><span>${escapeText(item.address)} · ${item.protocols.join(', ')}</span></div><button class="secondary" data-pair="${escapeText(item.identifier)}">Pair</button></div>`).join('');
        notice(`Found ${devices.length} Apple TV device(s).`); break;
      }
      case 'skip-appletv': await skip('apple_tv', 'Detection-only or shadow setup selected'); break;
      case 'finish-pairing': {
        const pin = $('#pairing-pin').value;
        const result = await mutate('/api/v1/setup/appletv/pair/finish', { token: app.pairingToken, pin: pin ? Number(pin) : null });
        if (!result.paired) throw new Error('Pairing was not accepted.'); app.selected = null; await refresh(); break;
      }
      case 'save-mode': {
        const mode = document.querySelector('input[name="mode"]:checked').value;
        await commit({ intervention: { mode, automatic_muting: false } }); await acknowledge('mode', { mode }); break;
      }
      case 'skip-calibration': await skip('calibration', 'No server-verified preview calibration is available yet'); break;
      case 'check-readiness': {
        const result = await mutate('/api/v1/setup/readiness'); $('#readiness-result').hidden = false;
        $('#readiness-result').textContent = JSON.stringify(result, null, 2);
        if (result.ready) { notice('Readiness checks passed for shadow validation.'); app.selected = null; await refresh(); }
        else notice(result.reasons.join(' · '), true); break;
      }
      case 'apply-profile': {
        const name = $('#profile').value; if (!name) throw new Error('Choose a sensitivity profile.');
        await commit({ detection: app.config.profiles[name] }); break;
      }
      case 'save-advanced': await commit({ temporal: { strategy: $('#temporal-strategy').value, evidence_deadline_ms: Number($('#evidence-deadline').value) } }); break;
      case 'download-diagnostics': {
        const response = await fetch('/api/v1/setup/diagnostics', { headers: { 'X-CSRF-Token': app.csrf }, cache: 'no-store' });
        if (!response.ok) throw new Error('Diagnostics export failed.'); const blob = await response.blob();
        const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = 'ad-detect-diagnostics.json'; link.click(); URL.revokeObjectURL(link.href); break;
      }
    }
  } catch (error) { notice(error.message, true); }
  finally { button.disabled = false; }
});
document.addEventListener('click', async event => {
  const pair = event.target.closest('[data-pair]'); if (!pair) return;
  try { const result = await mutate('/api/v1/setup/appletv/pair/begin', { device_id: pair.dataset.pair, protocol: 'companion' }); app.pairingToken = result.token; $('#pairing').hidden = false; notice(result.device_provides_pin ? 'Enter the pairing PIN.' : 'Confirm pairing on the device.'); }
  catch (error) { notice(error.message, true); }
});
function escapeText(value) { const node = document.createElement('span'); node.textContent = value; return node.innerHTML; }

(async () => {
  try { const bootstrap = await api('/api/v1/setup/bootstrap'); app.csrf = bootstrap.csrf_token; await refresh(); }
  catch (error) { $('#connection').textContent = 'Setup service unavailable'; notice(error.message, true); }
})();
