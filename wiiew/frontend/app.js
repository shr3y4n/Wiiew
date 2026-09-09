/**
 * Wiiew — Frontend Application Controller
 * Handles live WebSocket streaming, Room Visualization, Web Push subscriptions,
 * PWA installation, and dynamic backend URL configuration.
 */

// --- Configuration & Endpoints ---
const DEFAULT_LAN_BACKEND = 'http://192.168.1.100:8000';

try {
  const _params = new URLSearchParams(window.location.search);
  const _qApi = _params.get('api') || _params.get('backend');
  if (_qApi && _qApi.trim()) {
    let _clean = _qApi.trim().replace(/\/+$/, '');
    if (!_clean.startsWith('http://') && !_clean.startsWith('https://')) _clean = 'https://' + _clean;
    localStorage.setItem('wiiew_api', _clean);
    localStorage.setItem('wiiew_backend_url', _clean);
  }
} catch (e) {}

function getBackendBaseUrl() {
  let saved = localStorage.getItem('wiiew_backend_url') || localStorage.getItem('wiiew_api');
  if (saved && saved.trim()) {
    saved = saved.trim().replace(/\/+$/, '');
    if (!saved.startsWith('http://') && !saved.startsWith('https://')) {
      saved = 'https://' + saved;
    }
    return saved;
  }

  // Automatic heuristic:
  const host = window.location.hostname;
  if (host === 'localhost' || host === '127.0.0.1' || host.startsWith('192.168.')) {
    return window.location.origin.replace(/\/+$/, '');
  }

  // Running on GitHub Pages or external origin:
  return DEFAULT_LAN_BACKEND;
}

function getApiUrl(path) {
  const base = getBackendBaseUrl();
  const cleanPath = path.startsWith('/') ? path : `/${path}`;
  return `${base}${cleanPath}`;
}

function getWsUrl(path) {
  const base = getBackendBaseUrl();
  const cleanPath = path.startsWith('/') ? path : `/${path}`;
  try {
    const url = new URL(base);
    const proto = url.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${url.host}${cleanPath}`;
  } catch (err) {
    console.error('[WS] Invalid backend URL:', err);
    return '';
  }
}

// --- Global State ---
let ws = null;
let deferredInstallPrompt = null;
let currentSettings = null;
let isAudioAlertAllowed = false;
let audioContext = null;
let connectionFailures = 0;

// --- Helper Functions ---
function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

function playBeep(freq = 740, duration = 0.18) {
  if (!audioContext) {
    audioContext = new (window.AudioContext || window.webkitAudioContext)();
  }
  if (audioContext.state === 'suspended') {
    audioContext.resume();
  }
  const osc = audioContext.createOscillator();
  const gain = audioContext.createGain();
  osc.type = 'sine';
  osc.frequency.value = freq;
  osc.connect(gain);
  gain.connect(audioContext.destination);
  gain.gain.setValueAtTime(0.2, audioContext.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + duration);
  osc.start();
  osc.stop(audioContext.currentTime + duration);
}

// --- PWA Installation Prompt ---
window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  deferredInstallPrompt = e;
  const btnInstall = document.getElementById('btn-install');
  if (btnInstall) btnInstall.style.display = 'flex';
});

document.getElementById('btn-install')?.addEventListener('click', async () => {
  if (deferredInstallPrompt) {
    deferredInstallPrompt.prompt();
    const { outcome } = await deferredInstallPrompt.userChoice;
    console.log('[PWA] Install prompt outcome:', outcome);
    deferredInstallPrompt = null;
    document.getElementById('btn-install').style.display = 'none';
  }
});

// Register Service Worker with relative scope
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('./sw.js', { scope: './' })
      .then((reg) => console.log('[SW] Registered with scope:', reg.scope))
      .catch((err) => console.warn('[SW] Registration warning:', err));
  });
}

// --- WebSocket Live Stream ---
function connectWebSocket() {
  if (ws) {
    try { ws.close(); } catch (_) {}
  }

  const wsUrl = getWsUrl('/ws/live');
  console.log('[WS] Connecting to:', wsUrl);

  try {
    ws = new WebSocket(wsUrl);
  } catch (err) {
    console.error('[WS] Connection init error:', err);
    handleBackendOffline();
    setTimeout(connectWebSocket, 3000);
    return;
  }

  ws.onopen = () => {
    console.log('[WS] Connected to Wiiew stream');
    connectionFailures = 0;
    const badge = document.getElementById('badge-backend-status');
    if (badge) {
      badge.className = 'status-pill pill-green';
      badge.textContent = 'CONNECTED';
    }
    document.getElementById('ws-latency-text').textContent = 'Live 1 Hz';
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      updateDashboard(data);
    } catch (e) {
      console.error('[WS] Parse error:', e);
    }
  };

  ws.onclose = () => {
    connectionFailures++;
    if (connectionFailures >= 2) {
      handleBackendOffline();
    }
    setTimeout(connectWebSocket, 2500);
  };

  ws.onerror = () => {
    ws.close();
  };
}

function handleBackendOffline() {
  const badge = document.getElementById('badge-backend-status');
  if (badge) {
    badge.className = 'status-pill pill-red';
    badge.textContent = 'OFFLINE';
  }
  document.getElementById('ws-latency-text').textContent = 'Backend Offline';

  const roomCard = document.getElementById('room-card');
  const title = document.getElementById('room-status-title');
  const desc = document.getElementById('room-status-desc');

  roomCard.className = 'room-card state-offline';
  title.textContent = 'BACKEND OFFLINE';
  desc.textContent = 'Cannot reach Wiiew server. Check connection in Settings.';

  document.getElementById('human-silhouette').style.display = 'none';
  document.getElementById('checking-indicator').style.display = 'none';
  document.getElementById('trusted-home-icon').style.display = 'none';
  document.getElementById('rf-waves-container').style.display = 'none';
}

// --- Dashboard & Room Visualization Update ---
function updateDashboard(state) {
  const room = state.room || {};
  const sensor = state.sensor || {};
  const phone = state.phone || {};
  const sys = state.system || {};

  const roomCard = document.getElementById('room-card');
  const title = document.getElementById('room-status-title');
  const desc = document.getElementById('room-status-desc');

  const sil = document.getElementById('human-silhouette');
  const checking = document.getElementById('checking-indicator');
  const trustedVisual = document.getElementById('trusted-home-icon');
  const rfWaves = document.getElementById('rf-waves-container');

  // Reset visuals
  sil.style.display = 'none';
  checking.style.display = 'none';
  trustedVisual.style.display = 'none';
  rfWaves.style.display = 'flex';

  roomCard.className = 'room-card';

  // State Resolver
  const st = room.state || 'EMPTY';

  switch (st) {
    case 'PRESENCE_UNTRUSTED':
      roomCard.classList.add('state-untrusted');
      title.textContent = 'SOMEONE IS HERE';
      desc.textContent = 'Someone has entered your room.';
      sil.style.display = 'flex';
      rfWaves.style.display = 'none';
      if (sys.sound_enabled && isAudioAlertAllowed) playBeep(780, 0.25);
      break;

    case 'PRESENCE_TRUSTED':
      roomCard.classList.add('state-trusted');
      title.textContent = 'YOU ARE HOME';
      desc.textContent = 'Trusted device present — alert suppressed.';
      trustedVisual.style.display = 'flex';
      break;

    case 'CHECKING_PRESENCE':
      roomCard.classList.add('state-checking');
      title.textContent = 'CHECKING PRESENCE';
      const dur = room.presence_duration_seconds || 0;
      const thresh = room.presence_threshold_seconds || 15;
      desc.textContent = `Confirming sustained activity... (${dur.toFixed(0)}s / ${thresh}s)`;
      checking.style.display = 'flex';
      document.getElementById('checking-timer').textContent = `${dur.toFixed(0)}s`;

      // Update radial ring
      const ringFill = document.getElementById('progress-ring-fill');
      if (ringFill) {
        const circumference = 301.59;
        const pct = Math.min(1, dur / thresh);
        const offset = circumference - (pct * circumference);
        ringFill.style.strokeDashoffset = offset;
      }
      break;

    case 'DISARMED':
      roomCard.classList.add('state-disarmed');
      title.textContent = 'SYSTEM DISARMED';
      desc.textContent = 'Monitoring paused by user.';
      break;

    case 'SENSOR_OFFLINE':
      roomCard.classList.add('state-offline');
      title.textContent = 'SENSOR OFFLINE';
      desc.textContent = 'ESP32 CSI sensor stream is disconnected.';
      break;

    case 'EMPTY':
    default:
      roomCard.classList.add('state-empty');
      title.textContent = 'ROOM EMPTY';
      desc.textContent = 'Room is empty and quiet.';
      break;
  }

  // Quick Telemetry Pills
  const badgeSensor = document.getElementById('badge-sensor');
  if (sensor.online) {
    badgeSensor.className = 'status-pill pill-green';
    badgeSensor.textContent = 'ONLINE';
  } else {
    badgeSensor.className = 'status-pill pill-red';
    badgeSensor.textContent = 'OFFLINE';
  }

  const badgeServer = document.getElementById('badge-server');
  if (sensor.online) {
    badgeServer.className = 'status-pill pill-green';
    badgeServer.textContent = 'ONLINE';
  } else {
    badgeServer.className = 'status-pill pill-red';
    badgeServer.textContent = 'OFFLINE';
  }

  const labelPhoneName = document.getElementById('label-phone-name');
  const badgePhone = document.getElementById('badge-phone');
  const phoneSubtext = document.getElementById('phone-subtext');

  labelPhoneName.textContent = phone.device_name || 'My Phone';

  const prox = phone.proximity || {};
  const proxState = prox.state || 'UNKNOWN';
  const proxText = proxState === 'NEAR' ? 'Near (<15 ft)' : proxState === 'FAR' ? 'Far (>15 ft)' : 'Proximity unavail.';

  if (!phone.configured || phone.phone_state === 'UNKNOWN') {
    badgePhone.className = 'status-pill pill-gray';
    badgePhone.textContent = 'UNSET';
    phoneSubtext.textContent = 'Configure in settings';
  } else if (phone.is_trusted_in_room) {
    badgePhone.className = 'status-pill pill-green';
    badgePhone.textContent = 'IN ROOM';
    phoneSubtext.textContent = 'Near (<15 ft boundary)';
  } else if (phone.phone_state === 'PHONE_PRESENT' || phone.is_home) {
    badgePhone.className = (proxState === 'FAR' ? 'status-pill pill-amber' : 'status-pill pill-green');
    badgePhone.textContent = (proxState === 'FAR' ? 'AWAY (FAR)' : 'ON WI-FI');
    phoneSubtext.textContent = (proxState === 'FAR' ? 'Outside 15 ft boundary' : `${proxText}`);
  } else if (phone.phone_state === 'PHONE_MAYBE_AWAY') {
    badgePhone.className = 'status-pill pill-amber';
    badgePhone.textContent = 'SLEEPING';
    const minLeft = Math.ceil((phone.remaining_grace_seconds || 0) / 60);
    phoneSubtext.textContent = `Sleep grace (${minLeft}m left)`;
  } else {
    badgePhone.className = 'status-pill pill-gray';
    badgePhone.textContent = 'AWAY';
    const ago = phone.last_seen_seconds_ago;
    phoneSubtext.textContent = ago ? `Last seen ${Math.floor(ago / 60)}m ago` : 'Not seen';
  }

  // Update live settings modal fields if open
  if (document.getElementById('cfg-prox-method')) {
    document.getElementById('cfg-prox-method').textContent = prox.method === 'router_rssi' ? 'Router RSSI' : 'Unavailable';
    document.getElementById('cfg-prox-msg').textContent = prox.message || '15 ft proximity detection requires router client RSSI.';
    document.getElementById('cfg-prox-signal').textContent = prox.signal_strength != null ? `${prox.signal_strength} dBm` : '— dBm';
    document.getElementById('cfg-prox-dist').textContent = prox.distance_estimate_ft != null ? `${prox.distance_estimate_ft} ft` : '— ft';
    document.getElementById('cfg-prox-state').textContent = prox.state || 'Unknown';
  }

  // CSI Movement Telemetry & Direction
  const mState = room.movement_state || (room.motion_level === 'active' ? 'MOVEMENT_DETECTED' : room.raw_presence ? 'STATIONARY' : 'NONE');
  sil.classList.toggle('movement-active', mState === 'MOVEMENT_DETECTED');
  sil.classList.toggle('movement-stationary', mState === 'STATIONARY');

  const badgeMotion = document.getElementById('badge-motion');
  const motionSubtext = document.getElementById('motion-subtext');
  if (badgeMotion) {
    if (mState === 'MOVEMENT_DETECTED') {
      badgeMotion.className = 'status-pill pill-red';
      badgeMotion.textContent = 'MOVEMENT';
    } else if (mState === 'STATIONARY') {
      badgeMotion.className = 'status-pill pill-green';
      badgeMotion.textContent = 'STATIONARY';
    } else {
      badgeMotion.className = 'status-pill pill-gray';
      badgeMotion.textContent = 'QUIET';
    }
  }
  if (motionSubtext) {
    motionSubtext.textContent = 'Direction: Unknown (single node)';
  }

  // Multi-Node CSI Localization Rendering (Phase 3)
  const loc = state.localization || {};
  const stageLocChip = document.getElementById('stage-loc-chip');
  if (stageLocChip) {
    if (loc.state === 'VALID_ESTIMATE' && loc.valid && loc.x != null && loc.y != null) {
      stageLocChip.textContent = `Pos: (${loc.x}m, ${loc.y}m) ±${Math.round((1 - loc.confidence) * 100)}cm`;
      stageLocChip.style.color = '#34d399';
    } else if (loc.state === 'SINGLE_NODE') {
      stageLocChip.textContent = 'Single node · Coarse presence';
      stageLocChip.style.color = 'var(--text-dim)';
    } else if (loc.state === 'LOW_CONFIDENCE') {
      stageLocChip.textContent = '2 nodes · Insufficient for 2D position';
      stageLocChip.style.color = '#fbbf24';
    } else {
      stageLocChip.textContent = 'No active nodes';
      stageLocChip.style.color = 'var(--text-dim)';
    }
  }

  const multiNodeLayer = document.getElementById('multi-node-layer');
  if (multiNodeLayer && loc.nodes && loc.nodes.length > 0) {
    const w = loc.room_width_m || 4, d = loc.room_depth_m || 5;
    multiNodeLayer.innerHTML = loc.nodes.map(n => {
      const left = Math.max(6, Math.min(94, (n.x / w) * 100));
      const top = Math.max(6, Math.min(94, (n.y / d) * 100));
      return `<div class="node-dot ${n.active ? 'active' : ''}" style="left:${left}%;top:${top}%;" title="${n.name || n.node_id}"><small>${n.name ? n.name.split(' ')[0] : n.node_id}</small></div>`;
    }).join('');
  }

  // Zero Fabrication Policy: Position human silhouette ONLY if loc.valid is true
  if (loc.valid && loc.x != null && loc.y != null) {
    sil.style.position = 'absolute';
    const w = loc.room_width_m || 4, d = loc.room_depth_m || 5;
    sil.style.left = `${Math.max(12, Math.min(88, (loc.x / w) * 100))}%`;
    sil.style.top = `${Math.max(12, Math.min(88, (loc.y / d) * 100))}%`;
    sil.style.transform = 'translate(-50%, -50%)';
  } else {
    sil.style.position = 'relative';
    sil.style.left = '';
    sil.style.top = '';
    sil.style.transform = '';
  }

  // Arm / Disarm Button
  const btnArm = document.getElementById('btn-arm-toggle');
  const btnArmText = document.getElementById('btn-arm-text');
  if (sys.armed) {
    btnArm.className = 'btn-primary btn-armed';
    btnArmText.textContent = 'SYSTEM ARMED';
  } else {
    btnArm.className = 'btn-primary btn-disarmed';
    btnArmText.textContent = 'SYSTEM DISARMED';
  }

  // Last Activity Text
  const lastAct = room.last_activity_seconds_ago;
  const lastActElem = document.getElementById('last-activity-text');
  if (lastAct === null || lastAct === undefined) {
    lastActElem.textContent = 'Last activity: None';
  } else if (lastAct < 5) {
    lastActElem.textContent = 'Last activity: Just now';
  } else if (lastAct < 60) {
    lastActElem.textContent = `Last activity: ${lastAct}s ago`;
  } else {
    const mins = Math.floor(lastAct / 60);
    lastActElem.textContent = `Last activity: ${mins} min ago`;
  }

  // Subcarrier Visualizer Bars
  updateSubcarrierBars(sensor.subcarriers, sensor.rssi_dbm, sensor.mean_amplitude);
}

function updateSubcarrierBars(amps, rssi, meanAmp) {
  const container = document.getElementById('subcarrier-visualizer');
  const metricsLabel = document.getElementById('subcarrier-metrics');
  if (metricsLabel) {
    metricsLabel.textContent = `${rssi} dBm | amp: ${(meanAmp || 0).toFixed(1)}`;
  }

  if (!amps || amps.length === 0) return;

  const displayAmps = amps.slice(0, 52);
  const maxAmp = Math.max(1.0, ...displayAmps);

  if (container.children.length !== displayAmps.length) {
    container.innerHTML = '';
    displayAmps.forEach(() => {
      const bar = document.createElement('div');
      bar.className = 'sc-bar';
      container.appendChild(bar);
    });
  }

  for (let i = 0; i < displayAmps.length; i++) {
    const bar = container.children[i];
    if (bar) {
      const h = Math.max(4, Math.round((displayAmps[i] / maxAmp) * 36));
      bar.style.height = `${h}px`;
    }
  }
}

// --- Arm / Disarm Toggle ---
document.getElementById('btn-arm-toggle')?.addEventListener('click', async () => {
  isAudioAlertAllowed = true;
  try {
    const res = await fetch(getApiUrl('/api/arm'), { method: 'POST' });
    const data = await res.json();
    console.log('[Arm] Toggled system arming:', data.armed);
  } catch (err) {
    console.error('[Arm] Error toggling arm:', err);
  }
});

// --- Safe-Card Heartbeat ("I'm Home") ---
document.getElementById('btn-heartbeat')?.addEventListener('click', async () => {
  try {
    await fetch(getApiUrl('/api/heartbeat'), { method: 'POST' });
    const btn = document.getElementById('btn-heartbeat');
    const orig = btn.innerHTML;
    btn.innerHTML = '<span>✓ Safe Card Registered</span>';
    setTimeout(() => btn.innerHTML = orig, 1800);
  } catch (err) {
    console.error('[Heartbeat] Error:', err);
  }
});

// Auto foreground heartbeat
setInterval(() => {
  if (document.visibilityState === 'visible') {
    fetch(getApiUrl('/api/heartbeat'), { method: 'POST' }).catch(() => {});
  }
}, 20000);

// --- Activity Log History ---
async function loadEvents() {
  try {
    const res = await fetch(getApiUrl('/api/events'));
    const { events } = await res.json();
    const list = document.getElementById('event-list');
    if (!events || events.length === 0) {
      list.innerHTML = '<div class="event-placeholder">No activity events recorded yet.</div>';
      return;
    }

    list.innerHTML = events.slice(0, 15).map(ev => {
      let cls = '';
      if (ev.type === 'ENTRY_DETECTED') cls = 'entry';
      else if (ev.type === 'ALERT_SUPPRESSED') cls = 'suppressed';

      return `
        <div class="event-item ${cls}">
          <span class="event-msg">${ev.message}</span>
          <span class="event-time">${ev.time_short || (ev.time_iso ? ev.time_iso.split(' ')[1] : '')}</span>
        </div>
      `;
    }).join('');
  } catch (e) {
    console.error('[Events] Error loading:', e);
  }
}
document.getElementById('btn-refresh-events')?.addEventListener('click', loadEvents);

// --- Settings Modal ---
const settingsModal = document.getElementById('settings-modal');
document.getElementById('btn-settings-open')?.addEventListener('click', async () => {
  settingsModal.style.display = 'flex';
  document.getElementById('cfg-backend-url').value = getBackendBaseUrl();
  await loadSettings();
});
document.getElementById('btn-settings-close')?.addEventListener('click', () => {
  settingsModal.style.display = 'none';
});

// Test Backend Connection Button
document.getElementById('btn-test-backend')?.addEventListener('click', async () => {
  const inputUrl = document.getElementById('cfg-backend-url').value.trim().replace(/\/+$/, '');
  const msgElem = document.getElementById('backend-test-msg');
  msgElem.textContent = 'Testing connection...';

  const startTime = performance.now();
  try {
    const res = await fetch(`${inputUrl}/api/status`, { mode: 'cors' });
    if (res.ok) {
      const elapsed = Math.round(performance.now() - startTime);
      msgElem.style.color = '#34d399';
      msgElem.textContent = `✓ Connected successfully (${elapsed}ms)!`;
    } else {
      msgElem.style.color = '#f87171';
      msgElem.textContent = `HTTP error: ${res.status}`;
    }
  } catch (err) {
    msgElem.style.color = '#f87171';
    msgElem.textContent = `Connection failed: ${err.message}`;
  }
});

async function loadSettings() {
  try {
    const res = await fetch(getApiUrl('/api/settings'));
    currentSettings = await res.json();

    document.getElementById('cfg-phone-name').value = currentSettings.trusted_phone_name || 'My Phone';
    document.getElementById('cfg-phone-ip').value = currentSettings.trusted_phone_ip || '';
    document.getElementById('cfg-phone-mac').value = currentSettings.trusted_phone_mac || '';

    const grace = currentSettings.phone_grace_period_seconds || 180;
    document.getElementById('cfg-grace-slider').value = grace;
    document.getElementById('cfg-grace-val').textContent = `${Math.round(grace / 60)} min`;

    const deb = currentSettings.presence_sustained_seconds || 15;
    document.getElementById('cfg-debounce-slider').value = deb;
    document.getElementById('cfg-debounce-val').textContent = `${deb} sec`;

    if (document.getElementById('cfg-prox-enabled')) {
      document.getElementById('cfg-prox-enabled').checked = (currentSettings.phone_proximity_enabled ?? true);
    }
    if (document.getElementById('cfg-prox-boundary')) {
      document.getElementById('cfg-prox-boundary').value = currentSettings.phone_boundary_ft || 15;
    }
  } catch (e) {
    console.error('[Settings] Error loading:', e);
  }
}

document.getElementById('cfg-grace-slider')?.addEventListener('input', (e) => {
  document.getElementById('cfg-grace-val').textContent = `${Math.round(e.target.value / 60)} min`;
});
document.getElementById('cfg-debounce-slider')?.addEventListener('input', (e) => {
  document.getElementById('cfg-debounce-val').textContent = `${e.target.value} sec`;
});

// Calibration Listeners
document.getElementById('btn-cfg-cal-near')?.addEventListener('click', async () => {
  const msg = document.getElementById('cfg-cal-msg');
  if (msg) msg.textContent = 'Sampling signal near router...';
  try {
    const res = await fetch(getApiUrl('/api/proximity/calibrate/near'), { method: 'POST' });
    const d = await res.json();
    if (msg) msg.textContent = d.message || '';
  } catch (e) {
    if (msg) msg.textContent = 'Calibration error: ' + e.message;
  }
});
document.getElementById('btn-cfg-cal-boundary')?.addEventListener('click', async () => {
  const msg = document.getElementById('cfg-cal-msg');
  if (msg) msg.textContent = 'Sampling signal at 15 ft boundary...';
  try {
    const res = await fetch(getApiUrl('/api/proximity/calibrate/boundary'), { method: 'POST' });
    const d = await res.json();
    if (msg) msg.textContent = d.message || '';
  } catch (e) {
    if (msg) msg.textContent = 'Calibration error: ' + e.message;
  }
});

document.getElementById('btn-save-settings')?.addEventListener('click', async () => {
  // 1. Save Backend URL if modified
  const newBackend = document.getElementById('cfg-backend-url').value.trim().replace(/\/+$/, '');
  const prevBackend = getBackendBaseUrl();
  if (newBackend) {
    localStorage.setItem('wiiew_backend_url', newBackend);
  }

  // 2. Save Server Settings
  if (!currentSettings) currentSettings = {};
  currentSettings.trusted_phone_name = document.getElementById('cfg-phone-name').value.trim();
  currentSettings.trusted_phone_ip = document.getElementById('cfg-phone-ip').value.trim();
  currentSettings.trusted_phone_mac = document.getElementById('cfg-phone-mac').value.trim();
  currentSettings.phone_grace_period_seconds = parseInt(document.getElementById('cfg-grace-slider').value, 10);
  currentSettings.presence_sustained_seconds = parseFloat(document.getElementById('cfg-debounce-slider').value);
  currentSettings.phone_proximity_enabled = document.getElementById('cfg-prox-enabled')?.checked ?? true;
  currentSettings.phone_boundary_ft = parseFloat(document.getElementById('cfg-prox-boundary')?.value || 15);

  try {
    await fetch(getApiUrl('/api/settings'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(currentSettings)
    });
    settingsModal.style.display = 'none';

    // Reconnect WebSocket if backend URL changed
    if (newBackend !== prevBackend) {
      connectWebSocket();
    }
  } catch (e) {
    console.error('[Settings] Error saving:', e);
  }
});

// LAN Device Discovery
document.getElementById('btn-discover-devices')?.addEventListener('click', async () => {
  const listContainer = document.getElementById('discovered-devices-list');
  listContainer.style.display = 'block';
  listContainer.innerHTML = '<div style="font-size: 0.74rem; color: #9ca3af; padding: 4px;">Scanning local network...</div>';

  try {
    const res = await fetch(getApiUrl('/api/devices/discover'));
    const { devices } = await res.json();
    if (!devices || devices.length === 0) {
      listContainer.innerHTML = '<div style="font-size: 0.74rem; color: #9ca3af; padding: 4px;">No other devices detected on LAN.</div>';
      return;
    }

    listContainer.innerHTML = devices.map(dev => `
      <div class="dev-item" onclick="selectDiscoveredDevice('${dev.ip}', '${dev.mac}', '${dev.hostname}')">
        <div>
          <div style="font-weight: 600;">${dev.hostname || 'Device'}</div>
          <div style="opacity: 0.6; font-size: 0.68rem;">${dev.ip} (${dev.mac})</div>
        </div>
        <span class="btn-link" style="font-size: 0.72rem;">Select</span>
      </div>
    `).join('');
  } catch (e) {
    listContainer.innerHTML = '<div style="font-size: 0.74rem; color: #f87171; padding: 4px;">Scan failed.</div>';
  }
});

window.selectDiscoveredDevice = (ip, mac, name) => {
  document.getElementById('cfg-phone-ip').value = ip;
  document.getElementById('cfg-phone-mac').value = mac;
  if (name && !name.includes('Device')) {
    document.getElementById('cfg-phone-name').value = name;
  }
  document.getElementById('discovered-devices-list').style.display = 'none';
};

// --- Web Push Subscriptions ---
document.getElementById('btn-enable-push')?.addEventListener('click', async () => {
  const statusMsg = document.getElementById('push-status-msg');
  statusMsg.textContent = 'Requesting permission...';

  if (!('Notification' in window) || !('serviceWorker' in navigator)) {
    statusMsg.textContent = 'Web Push is not supported in this browser.';
    return;
  }

  const permission = await Notification.requestPermission();
  if (permission !== 'granted') {
    statusMsg.textContent = 'Notification permission denied.';
    return;
  }

  try {
    statusMsg.textContent = 'Fetching VAPID public key...';
    const keyRes = await fetch(getApiUrl('/api/push/public-key'));
    const { publicKey } = await keyRes.json();
    const appServerKey = urlBase64ToUint8Array(publicKey);

    const reg = await navigator.serviceWorker.ready;
    statusMsg.textContent = 'Registering PushManager subscription...';
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: appServerKey
    });

    await fetch(getApiUrl('/api/push/subscribe'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(sub)
    });

    statusMsg.style.color = '#34d399';
    statusMsg.textContent = '✓ Web Push enabled! Ready to receive entry notifications.';
  } catch (err) {
    statusMsg.style.color = '#f87171';
    statusMsg.textContent = `Subscription error: ${err.message}`;
  }
});

document.getElementById('btn-test-push')?.addEventListener('click', async () => {
  const statusMsg = document.getElementById('push-status-msg');
  statusMsg.textContent = 'Sending test notification...';
  try {
    const res = await fetch(getApiUrl('/api/push/test'), { method: 'POST' });
    const data = await res.json();
    statusMsg.textContent = `✓ Test sent to ${data.delivered} registered device(s).`;
  } catch (err) {
    statusMsg.textContent = 'Test notification failed.';
  }
});

// --- Boot Initializer ---
window.addEventListener('DOMContentLoaded', () => {
  connectWebSocket();
  loadEvents();

  document.addEventListener('click', () => {
    isAudioAlertAllowed = true;
  }, { once: true });
});
