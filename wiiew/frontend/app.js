/**
 * Wiiew — Frontend Application Controller
 * Handles live WebSocket streaming, Web Push subscriptions,
 * PWA installation, and trusted-device management.
 */

// --- Global State ---
let ws = null;
let deferredInstallPrompt = null;
let currentSettings = null;
let isAudioAlertAllowed = false;
let audioContext = null;

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

function playBeep(freq = 880, duration = 0.2) {
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
  gain.gain.setValueAtTime(0.3, audioContext.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + duration);
  osc.start();
  osc.stop(audioContext.currentTime + duration);
}

// --- PWA Installation ---
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
    console.log('[PWA] User response to install:', outcome);
    deferredInstallPrompt = null;
    document.getElementById('btn-install').style.display = 'none';
  }
});

// Register Service Worker
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js')
      .then((reg) => console.log('[SW] Registered with scope:', reg.scope))
      .catch((err) => console.warn('[SW] Registration failed:', err));
  });
}

// --- WebSocket Live Stream ---
function connectWebSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws/live`;

  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    console.log('[WS] Connected to Wiiew live stream');
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
    document.getElementById('ws-latency-text').textContent = 'Reconnecting...';
    setTimeout(connectWebSocket, 2000);
  };

  ws.onerror = () => {
    ws.close();
  };
}

// --- Dashboard UI Update ---
function updateDashboard(state) {
  const room = state.room || {};
  const sensor = state.sensor || {};
  const phone = state.phone || {};
  const sys = state.system || {};

  // 1. Hero Badge
  const heroBadge = document.getElementById('hero-badge');
  const statusTitle = document.getElementById('status-title');
  const statusSubtitle = document.getElementById('status-subtitle');

  heroBadge.className = 'hero-status';

  if (room.state === 'PRESENCE_DETECTED_INTRUDER') {
    heroBadge.classList.add('state-intruder');
    statusTitle.textContent = 'PRESENCE DETECTED';
    statusSubtitle.textContent = 'Someone may be in your room while you are away!';
    if (sys.sound_enabled && isAudioAlertAllowed) playBeep(980, 0.4);
  } else if (room.state === 'PRESENCE_DETECTED_SUPPRESSED') {
    heroBadge.classList.add('state-suppressed');
    statusTitle.textContent = 'PRESENCE DETECTED';
    statusSubtitle.textContent = 'Trusted device present — alert suppressed.';
  } else if (room.state === 'DISARMED_PRESENCE' || !sys.armed) {
    heroBadge.classList.add('state-disarmed');
    statusTitle.textContent = room.state === 'DISARMED_PRESENCE' ? 'PRESENCE (DISARMED)' : 'SYSTEM DISARMED';
    statusSubtitle.textContent = sys.armed ? room.message : 'Monitoring paused by user.';
  } else {
    heroBadge.classList.add('state-empty');
    statusTitle.textContent = 'ROOM EMPTY';
    statusSubtitle.textContent = 'Room is empty and quiet.';
  }

  // 2. Hysteresis Debounce Progress Bar
  const debounceContainer = document.getElementById('debounce-bar-container');
  const debounceFill = document.getElementById('debounce-fill');
  const debounceTimer = document.getElementById('debounce-timer');

  if (room.raw_presence && !room.sustained_presence && sys.armed) {
    debounceContainer.style.display = 'block';
    const dur = room.presence_duration_seconds || 0;
    const thresh = room.presence_threshold_seconds || 15;
    const pct = Math.min(100, (dur / thresh) * 100);
    debounceFill.style.width = `${pct}%`;
    debounceTimer.textContent = `${dur.toFixed(0)}s / ${thresh}s`;
  } else {
    debounceContainer.style.display = 'none';
  }

  // 3. CSI Sensor Quick Pill
  const badgeSensor = document.getElementById('badge-sensor');
  if (sensor.online) {
    badgeSensor.className = 'status-pill pill-green';
    badgeSensor.textContent = 'ONLINE';
  } else {
    badgeSensor.className = 'status-pill pill-red';
    badgeSensor.textContent = 'OFFLINE';
  }

  // 4. RuView Server Quick Pill
  const badgeServer = document.getElementById('badge-server');
  if (sensor.online) {
    badgeServer.className = 'status-pill pill-green';
    badgeServer.textContent = 'ONLINE';
  } else {
    badgeServer.className = 'status-pill pill-red';
    badgeServer.textContent = 'OFFLINE';
  }

  // 5. Trusted Phone Quick Pill
  const labelPhoneName = document.getElementById('label-phone-name');
  const badgePhone = document.getElementById('badge-phone');
  const phoneSubtext = document.getElementById('phone-subtext');

  labelPhoneName.textContent = phone.device_name || 'My Phone';

  if (!phone.configured) {
    badgePhone.className = 'status-pill pill-gray';
    badgePhone.textContent = 'UNSET';
    phoneSubtext.textContent = 'Configure in settings';
  } else if (phone.is_home) {
    badgePhone.className = 'status-pill pill-green';
    badgePhone.textContent = 'HOME';
    if (phone.status_label.includes('SLEEPING')) {
      const minLeft = Math.ceil((phone.remaining_grace_seconds || 0) / 60);
      phoneSubtext.textContent = `Sleep grace (${minLeft}m left)`;
    } else {
      phoneSubtext.textContent = 'Active on Wi-Fi';
    }
  } else {
    badgePhone.className = 'status-pill pill-amber';
    badgePhone.textContent = 'AWAY';
    const ago = phone.last_seen_seconds_ago;
    phoneSubtext.textContent = ago ? `Last seen ${Math.floor(ago / 60)}m ago` : 'Not seen';
  }

  // 6. Arm / Disarm Button
  const btnArm = document.getElementById('btn-arm-toggle');
  const btnArmText = document.getElementById('btn-arm-text');
  if (sys.armed) {
    btnArm.className = 'btn-primary btn-armed';
    btnArmText.textContent = 'SYSTEM ARMED';
  } else {
    btnArm.className = 'btn-primary btn-disarmed';
    btnArmText.textContent = 'SYSTEM DISARMED';
  }

  // 7. Last Activity Text
  const lastAct = room.last_activity_seconds_ago;
  const lastActElem = document.getElementById('last-activity-text');
  if (lastAct === null || lastAct === undefined) {
    lastActElem.textContent = 'Last activity: Unknown';
  } else if (lastAct < 5) {
    lastActElem.textContent = 'Last activity: Just now';
  } else if (lastAct < 60) {
    lastActElem.textContent = `Last activity: ${lastAct}s ago`;
  } else {
    const mins = Math.floor(lastAct / 60);
    lastActElem.textContent = `Last activity: ${mins} min ago`;
  }

  // 8. Subcarrier Visualizer Bars
  updateSubcarrierBars(sensor.subcarriers, sensor.rssi_dbm, sensor.mean_amplitude);
}

function updateSubcarrierBars(amps, rssi, meanAmp) {
  const container = document.getElementById('subcarrier-visualizer');
  const metricsLabel = document.getElementById('subcarrier-metrics');
  if (metricsLabel) {
    metricsLabel.textContent = `${rssi} dBm | amp: ${meanAmp.toFixed(1)}`;
  }

  if (!amps || amps.length === 0) return;

  // Render 52 bars
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

// --- Arm / Disarm Click ---
document.getElementById('btn-arm-toggle')?.addEventListener('click', async () => {
  isAudioAlertAllowed = true;
  try {
    const res = await fetch('/api/arm', { method: 'POST' });
    const data = await res.json();
    console.log('[Arm] System armed status:', data.armed);
  } catch (err) {
    console.error('[Arm] Error toggling arm:', err);
  }
});

// --- Safe-Card Heartbeat ("I'm Home") ---
document.getElementById('btn-heartbeat')?.addEventListener('click', async () => {
  try {
    await fetch('/api/heartbeat', { method: 'POST' });
    const btn = document.getElementById('btn-heartbeat');
    const orig = btn.innerHTML;
    btn.innerHTML = '<span>✓ Safe Card Updated</span>';
    setTimeout(() => btn.innerHTML = orig, 1500);
  } catch (err) {
    console.error('[Heartbeat] Error:', err);
  }
});

// Automatic Heartbeat when PWA is open on mobile
setInterval(() => {
  if (document.visibilityState === 'visible') {
    fetch('/api/heartbeat', { method: 'POST' }).catch(() => {});
  }
}, 20000);

// --- Activity Log History ---
async function loadEvents() {
  try {
    const res = await fetch('/api/events');
    const { events } = await res.json();
    const list = document.getElementById('event-list');
    if (!events || events.length === 0) {
      list.innerHTML = '<div class="event-placeholder">No intrusion events recorded.</div>';
      return;
    }

    list.innerHTML = events.slice(0, 15).map(ev => {
      let cls = '';
      if (ev.type === 'ALERT_TRIGGERED') cls = 'alert';
      else if (ev.type === 'ALERT_SUPPRESSED') cls = 'suppressed';

      return `
        <div class="event-item ${cls}">
          <div class="event-top">
            <span>${ev.type.replace('_', ' ')}</span>
            <span style="opacity: 0.6;">${ev.time_iso ? ev.time_iso.split(' ')[1] : ''}</span>
          </div>
          <div class="event-msg">${ev.message}</div>
        </div>
      `;
    }).join('');
  } catch (e) {
    console.error('[Events] Error loading history:', e);
  }
}
document.getElementById('btn-refresh-events')?.addEventListener('click', loadEvents);

// --- Settings Modal ---
const settingsModal = document.getElementById('settings-modal');
document.getElementById('btn-settings-open')?.addEventListener('click', async () => {
  settingsModal.style.display = 'flex';
  await loadSettings();
});
document.getElementById('btn-settings-close')?.addEventListener('click', () => {
  settingsModal.style.display = 'none';
});

async function loadSettings() {
  try {
    const res = await fetch('/api/settings');
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

document.getElementById('btn-save-settings')?.addEventListener('click', async () => {
  if (!currentSettings) currentSettings = {};
  currentSettings.trusted_phone_name = document.getElementById('cfg-phone-name').value.trim();
  currentSettings.trusted_phone_ip = document.getElementById('cfg-phone-ip').value.trim();
  currentSettings.trusted_phone_mac = document.getElementById('cfg-phone-mac').value.trim();
  currentSettings.phone_grace_period_seconds = parseInt(document.getElementById('cfg-grace-slider').value, 10);
  currentSettings.presence_sustained_seconds = parseFloat(document.getElementById('cfg-debounce-slider').value);

  try {
    await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(currentSettings)
    });
    settingsModal.style.display = 'none';
    console.log('[Settings] Saved successfully');
  } catch (e) {
    console.error('[Settings] Error saving:', e);
  }
});

// LAN Device Discovery for 1-click phone selection
document.getElementById('btn-discover-devices')?.addEventListener('click', async () => {
  const listContainer = document.getElementById('discovered-devices-list');
  listContainer.style.display = 'block';
  listContainer.innerHTML = '<div style="font-size: 0.75rem; color: #9ca3af; padding: 4px;">Scanning local Wi-Fi ARP cache...</div>';

  try {
    const res = await fetch('/api/devices/discover');
    const { devices } = await res.json();
    if (!devices || devices.length === 0) {
      listContainer.innerHTML = '<div style="font-size: 0.75rem; color: #9ca3af; padding: 4px;">No other devices detected on LAN.</div>';
      return;
    }

    listContainer.innerHTML = devices.map(dev => `
      <div class="dev-item" onclick="selectDiscoveredDevice('${dev.ip}', '${dev.mac}', '${dev.hostname}')">
        <div>
          <div style="font-weight: 600;">${dev.hostname || 'Device'}</div>
          <div style="opacity: 0.6; font-size: 0.7rem;">${dev.ip} (${dev.mac})</div>
        </div>
        <span class="btn-link" style="font-size: 0.75rem;">Select</span>
      </div>
    `).join('');
  } catch (e) {
    listContainer.innerHTML = '<div style="font-size: 0.75rem; color: #ef4444; padding: 4px;">Scan failed.</div>';
  }
});

window.selectDiscoveredDevice = (ip, mac, name) => {
  document.getElementById('cfg-phone-ip').value = ip;
  document.getElementById('cfg-phone-mac').value = mac;
  if (name && name !== 'Unknown Device') {
    document.getElementById('cfg-phone-name').value = name;
  }
  document.getElementById('discovered-devices-list').style.display = 'none';
};

// --- Web Push Subscription ---
document.getElementById('btn-enable-push')?.addEventListener('click', async () => {
  const statusMsg = document.getElementById('push-status-msg');
  statusMsg.textContent = 'Requesting notification permission...';

  if (!('Notification' in window) || !('serviceWorker' in navigator)) {
    statusMsg.textContent = 'Web Push is not supported in this browser.';
    return;
  }

  const permission = await Notification.requestPermission();
  if (permission !== 'granted') {
    statusMsg.textContent = 'Notification permission was denied.';
    return;
  }

  try {
    statusMsg.textContent = 'Fetching VAPID key...';
    const keyRes = await fetch('/api/push/public-key');
    const { publicKey } = await keyRes.json();
    const appServerKey = urlBase64ToUint8Array(publicKey);

    const reg = await navigator.serviceWorker.ready;
    statusMsg.textContent = 'Registering PushManager subscription...';
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: appServerKey
    });

    await fetch('/api/push/subscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(sub)
    });

    statusMsg.textContent = '✓ Web Push enabled! Ready to receive alerts.';
    console.log('[WebPush] Subscribed successfully');
  } catch (err) {
    statusMsg.textContent = `Subscription error: ${err.message}`;
    console.error('[WebPush] Error:', err);
  }
});

document.getElementById('btn-test-push')?.addEventListener('click', async () => {
  const statusMsg = document.getElementById('push-status-msg');
  statusMsg.textContent = 'Sending test push notification...';
  try {
    const res = await fetch('/api/push/test', { method: 'POST' });
    const data = await res.json();
    statusMsg.textContent = `✓ Test push sent to ${data.delivered} registered device(s).`;
  } catch (err) {
    statusMsg.textContent = 'Test notification failed.';
  }
});

// --- Boot Initializer ---
window.addEventListener('DOMContentLoaded', () => {
  connectWebSocket();
  loadEvents();
  // Request user gesture to enable web audio beeps
  document.addEventListener('click', () => {
    isAudioAlertAllowed = true;
  }, { once: true });
});
