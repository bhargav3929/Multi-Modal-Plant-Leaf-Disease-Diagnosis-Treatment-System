/* Plant Pathology Console - vanilla JS (no build step).
 * Sections: utils · quota · pipeline · uploader · diagnose · result rendering · compare ·
 *           history · report · keys · init
 */
(() => {
  'use strict';

  // ======================================================================= utils
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  /** Escape untrusted text (all model output goes through this before innerHTML). */
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const state = {
    status: null, quota: null, resetAt: 0, samples: [], maxMB: 8,
    active: null,            // the scan result currently shown
    busy: false, cmpBusy: false,
    histFilter: '', histTimer: null,
  };

  function toast(msg, kind = '') {
    const el = document.createElement('div');
    el.className = 'toast ' + kind;
    el.textContent = msg;
    $('#toasts').appendChild(el);
    setTimeout(() => el.remove(), kind === 'error' ? 7000 : 3500);
  }

  async function apiError(res) {
    let msg = `Request failed (${res.status})`;
    try {
      const j = await res.json();
      if (typeof j.detail === 'string') msg = j.detail;
      else if (Array.isArray(j.detail)) msg = j.detail.map((d) => d.msg).join('; ');
    } catch (_) { /* non-JSON body */ }
    const err = new Error(msg);
    err.status = res.status;
    return err;
  }

  async function getJSON(url, opts) {
    const res = await fetch(url, opts);
    if (!res.ok) throw await apiError(res);
    return res.json();
  }

  const postJSON = (url, body) => getJSON(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });

  const list = (items, cls = '') => (items && items.length
    ? `<ul class="${cls}">${items.map((i) => `<li>${esc(i)}</li>`).join('')}</ul>` : '');
  const clsKey = (c) => String(c || '').replace(/\W/g, '');
  const fmtDate = (sec) => (sec ? new Date(sec * 1000).toLocaleString() : new Date().toLocaleString());

  // ======================================================================= quota pill
  function setQuota(q) {
    if (!q) return;
    state.quota = q;
    state.resetAt = q.reset_in_seconds > 0 ? Date.now() + q.reset_in_seconds * 1000 : 0;
    renderQuota();
  }

  async function refreshQuota() {
    try { setQuota(await getJSON('/api/quota')); } catch (_) { /* ignore */ }
  }

  function renderQuota() {
    const q = state.quota;
    if (!q) return;
    const pill = $('#quotaPill');
    $('#quotaText').textContent = `${q.remaining}/${q.limit} Free Demo Scans Left This Hour`;
    pill.classList.toggle('empty', q.remaining === 0);
    pill.classList.toggle('low', q.remaining > 0 && q.remaining <= Math.ceil(q.limit * 0.4));
    const resetEl = $('#quotaReset');
    if (state.resetAt) {
      const secs = Math.max(0, Math.ceil((state.resetAt - Date.now()) / 1000));
      resetEl.textContent = `· next slot ${String(Math.floor(secs / 60)).padStart(2, '0')}:${String(secs % 60).padStart(2, '0')}`;
      if (secs === 0) { state.resetAt = 0; refreshQuota(); }
    } else {
      resetEl.textContent = '';
    }
  }

  // ======================================================================= pipeline visualizer
  const NODES = [
    { id: 'specimen_gatekeeper_node', title: 'Specimen Gatekeeper', sub: 'Is this really a plant leaf?' },
    { id: 'pathogen_vision_node', title: 'Pathogen Vision', sub: 'Disease · class · severity' },
    { id: 'weather_telemetry_node', title: 'Weather Telemetry', sub: 'Open-Meteo spread risk' },
    { id: 'treatment_protocol_node', title: 'Treatment Protocol', sub: 'Organic + chemical tracks' },
    { id: 'preventive_advisor_node', title: 'Preventive Advisor', sub: 'Rotation · soil · save to DB' },
  ];
  const ICONS = { running: '', done: '✓', skipped: '–', halted: '!', failed: '!', notrun: '' };

  function resetPipeline() {
    $('#pipeline').innerHTML = NODES.map((n, i) => `
      <li class="pipe" id="pipe-${n.id}">
        <span class="ico">${i + 1}</span>
        <div><h4>${n.title}</h4><p class="pdetail">${n.sub}</p></div>
        <span class="ms mono"></span>
      </li>`).join('');
    $('#pipeTotal').textContent = 'idle';
  }

  function setPipe(id, cls, ms, detail) {
    const el = $('#pipe-' + id);
    if (!el) return;
    el.className = 'pipe ' + cls;
    const idx = NODES.findIndex((n) => n.id === id);
    $('.ico', el).textContent = ICONS[cls] || String(idx + 1);
    if (ms != null) $('.ms', el).textContent = `${ms} ms`;
    if (detail) $('.pdetail', el).textContent = detail;
  }

  function pipeFromTrace(trace) {
    resetPipeline();
    let total = 0, halted = false;
    (trace || []).forEach((t) => {
      const cls = t.status === 'skipped' ? 'skipped' : t.status === 'halted' ? 'halted' : 'done';
      halted = halted || t.status === 'halted';
      total += t.ms || 0;
      setPipe(t.node, cls, t.ms, t.detail);
    });
    if (halted) NODES.forEach((n) => { if (!(trace || []).some((t) => t.node === n.id)) setPipe(n.id, 'notrun'); });
    $('#pipeTotal').textContent = total ? `${(total / 1000).toFixed(1)} s total` : 'idle';
  }

  // ======================================================================= live timer + debug logs
  const run = { t0: 0, timer: null, log: [] };

  function fmtSecs(ms) { return (ms / 1000).toFixed(1) + 's'; }

  function startTimer() {
    stopTimer();
    run.t0 = performance.now(); run.log = [];
    const banner = $('#runBanner');
    banner.className = 'run-banner running';
    $('#runState').textContent = 'Pipeline running…';
    $('#runTimer').textContent = '0.0s';
    run.timer = setInterval(() => { $('#runTimer').textContent = fmtSecs(performance.now() - run.t0); }, 100);
    logLine('run started');
  }

  function stopTimer(label, failed) {
    if (run.timer) { clearInterval(run.timer); run.timer = null; }
    if (label === undefined) return;
    const el = performance.now() - run.t0;
    $('#runTimer').textContent = fmtSecs(el);
    $('#runBanner').className = 'run-banner' + (failed ? ' fail' : '');
    $('#runState').textContent = label;
  }

  function logLine(msg) {
    const t = run.t0 ? ((performance.now() - run.t0) / 1000).toFixed(2).padStart(6, ' ') : '  0.00';
    run.log.push(`[+${t}s] ${msg}`);
  }

  function renderDebug(trace, extra = {}) {
    const rows = (trace || []).map((t) => `<tr><td>${esc(t.label)}</td><td>${t.ms} ms</td><td>${esc(t.provider || '-')}</td><td>${esc(t.status)}</td></tr>`).join('');
    const sum = (trace || []).reduce((a, x) => a + (x.ms || 0), 0);
    $('#debugBody').innerHTML = `
      <table><thead><tr><th>Node</th><th>Time</th><th>Provider / model</th><th>Status</th></tr></thead><tbody>${rows || '<tr><td colspan="4">no nodes ran</td></tr>'}</tbody></table>
      <div class="muted" style="margin-bottom:8px">Sum of node times ${sum} ms${extra.elapsed ? ` · wall-clock ${extra.elapsed} ms (nodes 2 &amp; 3 run in parallel)` : ''}${extra.kb ? ` · image payload ${extra.kb} KB` : ''}</div>
      <pre>${esc(run.log.join('\n') || 'no live trace (loaded from history)')}</pre>`;
  }

  /** Downscale in the browser (max 768px, JPEG q0.82) so uploads stay ~100 KB instead of megabytes. */
  async function downscale(file, maxSide = 768, quality = 0.82) {
    try {
      const bmp = await createImageBitmap(file, { imageOrientation: 'from-image' });
      const k = Math.min(1, maxSide / Math.max(bmp.width, bmp.height));
      const c = document.createElement('canvas');
      c.width = Math.max(1, Math.round(bmp.width * k)); c.height = Math.max(1, Math.round(bmp.height * k));
      const ctx = c.getContext('2d');
      ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, c.width, c.height);   // flatten transparency
      ctx.drawImage(bmp, 0, 0, c.width, c.height);
      const blob = await new Promise((res) => c.toBlob(res, 'image/jpeg', quality));
      if (!blob) return file;
      return new File([blob], (file.name || 'leaf').replace(/\.\w+$/, '') + '.jpg', { type: 'image/jpeg' });
    } catch (_) { return file; }   // fall back to the original; the server resizes too
  }

  // ======================================================================= uploader (drag/drop + zoom)
  function createUploader(root, { onChange } = {}) {
    root.innerHTML = `
      <div class="dz" tabindex="0" role="button" aria-label="Choose or drop a leaf photo">
        <svg viewBox="0 0 24 24" width="34" height="34" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 16V4m0 0L7 9m5-5 5 5"/><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/></svg>
        <b>Drop a leaf photo here</b><small>or click to browse · JPG, PNG, WEBP</small>
        <input type="file" accept="image/*" hidden>
      </div>
      <div class="pv" hidden>
        <img alt="Uploaded specimen preview" draggable="false">
        <span class="pv-name"></span>
        <div class="pv-tools">
          <button type="button" data-z="in" aria-label="Zoom in">+</button>
          <button type="button" data-z="out" aria-label="Zoom out">−</button>
          <button type="button" data-z="reset" aria-label="Reset zoom">⟲</button>
          <button type="button" data-z="clear" aria-label="Remove image">✕</button>
        </div>
      </div>`;
    const dz = $('.dz', root), input = $('input', root), pv = $('.pv', root), img = $('img', pv);
    let file = null, url = null, scale = 1, tx = 0, ty = 0, drag = null;

    const apply = () => {
      img.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`;
      pv.classList.toggle('zoomed', scale > 1);
    };
    const zoom = (s) => { scale = Math.min(5, Math.max(1, s)); if (scale === 1) { tx = 0; ty = 0; } apply(); };

    function setFile(f) {
      if (!f) return;
      if (!f.type.startsWith('image/')) { toast('Please choose an image file (JPG, PNG or WEBP).', 'error'); return; }
      if (f.size > state.maxMB * 1024 * 1024) { toast(`Image is larger than ${state.maxMB} MB.`, 'error'); return; }
      if (url) URL.revokeObjectURL(url);
      file = f; url = URL.createObjectURL(f);
      img.src = url; $('.pv-name', pv).textContent = f.name;
      dz.hidden = true; pv.hidden = false; zoom(1);
      onChange && onChange(file);
    }
    function clear() {
      if (url) URL.revokeObjectURL(url);
      file = null; url = null; input.value = ''; img.removeAttribute('src');
      pv.hidden = true; dz.hidden = false;
      onChange && onChange(null);
    }

    dz.addEventListener('click', () => input.click());
    dz.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
    input.addEventListener('change', () => setFile(input.files[0]));
    ['dragenter', 'dragover'].forEach((ev) => root.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add('drag'); }));
    ['dragleave', 'drop'].forEach((ev) => root.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove('drag'); }));
    root.addEventListener('drop', (e) => setFile(e.dataTransfer.files[0]));

    $('.pv-tools', pv).addEventListener('click', (e) => {
      const z = e.target.closest('button')?.dataset.z;
      if (z === 'in') zoom(scale + 0.5); else if (z === 'out') zoom(scale - 0.5);
      else if (z === 'reset') zoom(1); else if (z === 'clear') clear();
    });
    pv.addEventListener('wheel', (e) => { e.preventDefault(); zoom(scale + (e.deltaY < 0 ? 0.25 : -0.25)); }, { passive: false });
    img.addEventListener('dblclick', () => zoom(scale > 1 ? 1 : 2.5));
    img.addEventListener('pointerdown', (e) => {
      if (scale <= 1) return;
      drag = { x: e.clientX - tx, y: e.clientY - ty }; img.setPointerCapture(e.pointerId);
    });
    img.addEventListener('pointermove', (e) => { if (drag) { tx = e.clientX - drag.x; ty = e.clientY - drag.y; apply(); } });
    ['pointerup', 'pointercancel'].forEach((ev) => img.addEventListener(ev, () => { drag = null; }));

    return { setFile, clear, get file() { return file; } };
  }

  // ======================================================================= samples
  async function fileFromSample(name) {
    const s = state.samples.find((x) => x.file === name);
    if (!s) throw new Error('Sample not found');
    const blob = await (await fetch(s.url)).blob();
    return new File([blob], s.file, { type: blob.type || 'image/jpeg' });
  }

  async function loadSamples() {
    try { state.samples = await getJSON('/api/samples'); } catch (_) { state.samples = []; }
    $('#sampleBar').innerHTML = state.samples.map((s) => `
      <button type="button" class="chip" data-sample="${esc(s.file)}" title="${esc(s.hint)}">
        <img src="${esc(s.url)}" alt="" loading="lazy">${esc(s.label)}</button>`).join('');
  }

  // ======================================================================= diagnose flow
  let single, base, follow;

  function emptyState() {
    $('#resultPane').innerHTML = `
      <div class="empty">
        <h3>Awaiting specimen</h3>
        <p>Upload a leaf photo (or tap a sample), optionally add a crop and city, then run the 5-node LangGraph pipeline.<br>
        Results appear here: diagnosis, confidence, microclimate risk and dual organic / chemical protocols.</p>
      </div>`;
  }

  async function streamAnalyze(fd, onEvent) {
    const ctl = new AbortController();
    const wd = setTimeout(() => ctl.abort(), 60000);   // watchdog: never hang the UI
    const res = await fetch('/api/analyze', { method: 'POST', body: fd, signal: ctl.signal }).finally(() => clearTimeout(wd));
    if (!res.ok) throw await apiError(res);
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (line) onEvent(JSON.parse(line));
      }
    }
    if (buf.trim()) onEvent(JSON.parse(buf));
  }

  async function runAnalyze() {
    if (state.busy || !single.file) return;
    state.busy = true;
    const btn = $('#btnAnalyze');
    btn.disabled = true; btn.textContent = 'Analysing…';
    resetPipeline();
    startTimer();
    $('#resultPane').innerHTML = `<div class="empty"><h3>Pipeline running…</h3><p>Watch the nodes on the left light up as each agent finishes.</p></div>`;

    let finished = false, kb = 0;
    const done = new Set();
    const finish = (n, cls, t) => { done.add(n); setPipe(n, cls, t.ms, t.detail); };
    try {
      const small = await downscale(single.file);
      kb = Math.round(small.size / 1024);
      logLine(`image resized in browser: ${Math.round(single.file.size / 1024)} KB -> ${kb} KB`);
      const fd = new FormData();
      fd.append('file', small);
      fd.append('crop', $('#cropInput').value);
      fd.append('city', $('#cityInput').value);
      setPipe(NODES[0].id, 'running');

      await streamAnalyze(fd, (ev) => {
        if (ev.event === 'start') {
          setQuota(ev.quota); logLine(`server accepted upload (${ev.payload_kb} KB to the AI)`);
        } else if (ev.event === 'node') {
          const t = ev.trace;
          const cls = t.status === 'skipped' ? 'skipped' : t.status === 'halted' ? 'halted' : 'done';
          finish(ev.node, cls, t);
          logLine(`${t.label}: ${t.status} in ${t.ms} ms via ${t.provider || '-'} - ${t.detail}`);
          if (t.status === 'halted') {
            NODES.slice(NODES.findIndex((n) => n.id === ev.node) + 1).forEach((n) => setPipe(n.id, 'notrun'));
          } else if (ev.node === NODES[0].id) {          // fan-out: nodes 2 and 3 start together
            setPipe(NODES[1].id, 'running'); setPipe(NODES[2].id, 'running');
          } else if (done.has(NODES[1].id) && done.has(NODES[2].id) && !done.has(NODES[3].id)) {
            setPipe(NODES[3].id, 'running');
          } else if (ev.node === NODES[3].id) {
            setPipe(NODES[4].id, 'running');
          }
        } else if (ev.event === 'result') {
          finished = true; setQuota(ev.quota);
          logLine(`done - server ${ev.result.elapsed_ms} ms`);
          stopTimer(ev.result.halted ? 'Stopped - not a real leaf photo' : 'Completed', false);
          setActive(ev.result);
          $('#pipeTotal').textContent = fmtSecs(performance.now() - run.t0);
          renderDebug(ev.result.trace, { elapsed: ev.result.elapsed_ms, kb: ev.result.payload_kb });
          if (ev.result.halted) toast('That image is not a real leaf photo - see the warning.', 'error');
        } else if (ev.event === 'error') {
          finished = true; setQuota(ev.quota); logLine('error: ' + ev.message);
          stopTimer('Failed', true); showError('Analysis failed', ev.message); renderDebug([], { kb });
        }
      });
      if (!finished) { logLine('stream closed early'); stopTimer('Connection lost', true); showError('Connection lost', 'The server closed the stream before finishing. Please try again.'); renderDebug([], { kb }); }
    } catch (err) {
      if (err.name === 'AbortError') err.message = 'No response from the server after 60 s. The AI providers may be busy - please try again in a minute.';
      logLine('request failed: ' + err.message);
      stopTimer('Failed', true);
      if (err.status === 429) { refreshQuota(); showError('Free demo limit reached', err.message); }
      else showError('Could not start analysis', err.message);
      renderDebug([], { kb });
    } finally {
      state.busy = false;
      btn.textContent = 'Run Diagnosis Pipeline';
      btn.disabled = !single.file;
    }
  }

  function showError(title, msg) {
    const running = $('.pipe.running');
    if (running) { running.className = 'pipe failed'; $('.ico', running).textContent = '!'; }
    $('#resultPane').innerHTML = `<div class="err-card fade"><h2>${esc(title)}</h2><p>${esc(msg)}</p></div>`;
    toast(msg, 'error');
  }

  // ======================================================================= result rendering
  function setActive(r) {
    state.active = r;
    renderResult(r);
    if (r.trace && r.trace.length) {
      // keep live timings from streaming if present; otherwise rebuild from stored trace
      if (!state.busy) { pipeFromTrace(r.trace); stopTimer(); $('#runBanner').className = 'run-banner'; $('#runState').textContent = 'Loaded from history'; $('#runTimer').textContent = fmtSecs((r.trace || []).reduce((a, x) => a + (x.ms || 0), 0)); }
    }
  }

  function gaugeSvg(pct) {
    const C = 276.46;
    const color = pct >= 75 ? 'var(--emerald)' : pct >= 50 ? '#D97706' : 'var(--crimson)';
    return `<div class="gauge"><svg width="150" height="150" viewBox="0 0 100 100" aria-hidden="true">
      <circle class="track" cx="50" cy="50" r="44"/>
      <circle class="bar" cx="50" cy="50" r="44" style="stroke:${color}" stroke-dasharray="${(pct / 100 * C).toFixed(1)} ${C}"/></svg>
      <div class="num"><span>${Math.round(pct)}%</span><small>confidence</small></div></div>`;
  }

  function severityMeter(sev) {
    const lvl = { None: 0, Mild: 1, Moderate: 2, Severe: 3 }[sev] || 0;
    const segs = [1, 2, 3].map((j) => `<i class="${lvl >= j ? 'on' + lvl : ''}"></i>`).join('');
    return `<div><h3 class="sec">Severity stage</h3>
      <div><span class="badge ${lvl === 0 ? 'good' : lvl === 1 ? 'warn' : 'bad'}">${lvl === 0 ? 'No infection' : esc(sev)}</span></div>
      <div class="sev" role="img" aria-label="Severity ${esc(sev)}">${segs}</div>
      <div class="sev-labels"><span>Mild</span><span>Moderate</span><span>Severe</span></div></div>`;
  }

  function weatherBanner(w) {
    if (!w || !w.available) {
      return `<div class="risk off"><b>Microclimate risk unavailable.</b> ${esc(w?.reason || 'Add a city to include live weather.')}</div>`;
    }
    return `<div class="risk ${esc(w.risk_level)}">
      <div class="score">${w.risk_score}<small class="mono" style="font-size:12px;opacity:.7">/100</small></div>
      <div><b>${esc(w.risk_level)} outbreak spread risk</b> · ${esc(w.city)}${w.country ? ', ' + esc(w.country) : ''}
        <div class="small muted">${esc(w.risk_note)}</div>
        <div class="risk-grid">
          <span>Temperature<b>${w.temperature_c}°C</b></span>
          <span>Humidity now<b>${w.humidity_pct}%</b></span>
          <span>Avg RH 48 h<b>${w.avg_humidity_48h_pct}%</b></span>
          <span>Rain 48 h<b>${w.rain_48h_mm} mm</b></span>
        </div></div></div>`;
  }

  function organicCard(o) {
    return `<div class="proto organic"><h3>🌿 Organic &amp; Bio-Control</h3>
      <p>${esc(o.summary)}</p>
      <h4>Steps</h4>${list(o.steps)}
      ${o.products?.length ? `<h4>Materials</h4>${list(o.products)}` : ''}</div>`;
  }

  function chemicalCard(c) {
    if (!c.required) {
      return `<div class="proto chemical"><h3>🧪 Chemical Protocol</h3>
        <p><span class="badge good">Not required</span></p>
        <p>${esc(c.application_method || 'No chemical treatment is needed for this specimen.')}</p></div>`;
    }
    return `<div class="proto chemical"><h3>🧪 Chemical Fungicide / Bactericide</h3>
      <h4>Active ingredient class</h4><p>${esc(c.active_ingredient_class)}</p>
      ${c.examples?.length ? list(c.examples) : ''}
      <h4>Application method</h4><p>${esc(c.application_method)}</p>
      <h4>PPE precautions</h4>${list(c.ppe, 'ppe')}
      <h4>Harvest waiting guidance</h4><p>${esc(c.harvest_waiting_guidance)}</p>
      ${c.resistance_management ? `<h4>Resistance management</h4><p>${esc(c.resistance_management)}</p>` : ''}</div>`;
  }

  function renderResult(r) {
    const pane = $('#resultPane');
    if (r.halted) {
      pane.innerHTML = `<div class="warn-card fade"><h2>Not a plant leaf</h2>
        <p>${esc(r.warning)}</p><p class="muted small">${esc(r.gatekeeper?.reason || '')}</p>
        <div class="actions"><button class="btn ghost" type="button" data-act="again">Try another photo</button></div></div>`;
      return;
    }
    const d = r.diagnosis, t = r.treatment, p = r.prevention;
    const totalMs = (r.trace || []).reduce((a, x) => a + (x.ms || 0), 0);
    pane.innerHTML = `
      <div class="card fade">
        <div class="dx-head">
          <div>
            <div class="crop-line">${esc(d.crop)}${r.scan_id ? ` · Scan #${r.scan_id}` : ''}</div>
            <h2>${esc(d.disease)}</h2>
            <div class="sci">${esc(d.scientific_name || (d.pathogen_class === 'Healthy' ? 'No pathogen detected' : '—'))}</div>
          </div>
          <span class="badge ${clsKey(d.pathogen_class)}">${esc(d.pathogen_class)}</span>
        </div>
        <p>${esc(d.summary)}</p>
        <div class="metrics">${gaugeSvg(d.confidence)}${severityMeter(d.severity)}</div>
        ${d.lesion_markers?.length ? `<h3 class="sec" style="margin-top:14px">Visual lesion markers</h3>${list(d.lesion_markers, 'markers')}` : ''}
        <div class="kv mono-kv">
          <span class="muted">Engine<b>${esc(r.provider || '—')}</b></span>
          <span class="muted">Pipeline<b>${(totalMs / 1000).toFixed(1)} s</b></span>
          <span class="muted">Recorded<b>${esc(fmtDate(r.created_at))}</b></span>
        </div>
      </div>
      ${weatherBanner(r.weather)}
      <div class="card fade">
        <div class="card-head"><h2>Dual Prescription</h2><span class="tag">${esc(t.urgency ? 'Urgency' : 'Plan')}</span></div>
        ${t.urgency ? `<p><b>${esc(t.urgency)}</b></p>` : ''}
        <div class="protocols">${organicCard(t.organic)}${chemicalCard(t.chemical)}</div>
        ${t.monitoring?.length ? `<h3 class="sec" style="margin-top:16px">Monitoring</h3>${list(t.monitoring)}` : ''}
      </div>
      <div class="card fade prev">
        <div class="card-head"><h2>Preventive Advisor</h2></div>
        <div class="prev-grid">
          <div><h3 class="sec">Crop rotation</h3>${list(p.crop_rotation)}</div>
          <div><h3 class="sec">Resistant varieties</h3>${list(p.resistant_varieties)}</div>
          <div><h3 class="sec">Soil health</h3>${list(p.soil_health)}</div>
          <div><h3 class="sec">Cultural practices</h3>${list(p.cultural_practices)}</div>
        </div>
      </div>
      <div class="actions">
        <button class="btn primary" type="button" data-act="report">Print / Save PDF Report</button>
        <button class="btn ghost" type="button" data-act="again">New scan</button>
      </div>`;
  }

  // ======================================================================= compare mode
  async function runCompare() {
    if (state.cmpBusy || !base.file || !follow.file) return;
    state.cmpBusy = true;
    const btn = $('#btnCompare');
    btn.disabled = true; btn.textContent = 'Comparing…';
    const out = $('#compareResult');
    out.innerHTML = `<div class="empty"><h3>Comparing scans…</h3><p>The vision model is measuring lesion spread between both photos.</p></div>`;
    const fd = new FormData();
    fd.append('baseline', await downscale(base.file)); fd.append('followup', await downscale(follow.file)); fd.append('crop', $('#cmpCrop').value);
    try {
      const res = await getJSON('/api/compare', { method: 'POST', body: fd });
      setQuota(res.quota);
      renderCompare(res);
    } catch (err) {
      if (err.status === 429) refreshQuota();
      out.innerHTML = `<div class="err-card fade"><h2>${err.status === 429 ? 'Free demo limit reached' : 'Comparison failed'}</h2><p>${esc(err.message)}</p></div>`;
      toast(err.message, 'error');
    } finally {
      state.cmpBusy = false; btn.textContent = 'Compare Scans'; btn.disabled = !(base.file && follow.file);
    }
  }

  function renderCompare({ comparison: c, thumbs, provider }) {
    const arrow = { Improving: '↗', Worsening: '↘', Stable: '→', 'Not Comparable': '?' }[c.trend] || '?';
    const col = (title, s, img) => `
      <div class="card cmp-col"><h3 class="sec">${title}</h3>
        <img src="${esc(img)}" alt="${esc(title)}">
        <div><b>${esc(s.disease)}</b> <span class="badge ${clsKey(s.pathogen_class)}">${esc(s.pathogen_class)}</span></div>
        <div class="small muted" style="margin:8px 0 4px">Estimated leaf area affected: <b class="mono">${s.infected_area_pct}%</b> · ${esc(s.severity === 'None' ? 'No infection' : s.severity)}</div>
        <div class="bar-pct"><i style="width:${s.infected_area_pct}%"></i></div>
        ${list(s.observations)}</div>`;
    $('#compareResult').innerHTML = `
      <div class="trend ${clsKey(c.trend)} fade"><span class="arrow">${arrow}</span>
        <div><h2>${esc(c.trend)}</h2><div>${esc(c.change_summary)}</div></div></div>
      <div class="cmp-cols fade">${col('Day 1 Baseline', c.baseline, thumbs.baseline)}${col('Follow-Up Scan', c.followup, thumbs.followup)}</div>
      <div class="card fade">
        ${c.evidence.length ? `<h3 class="sec">Visual evidence</h3>${list(c.evidence)}` : ''}
        ${c.recommendation ? `<h3 class="sec" style="margin-top:12px">Recommendation</h3><p>${esc(c.recommendation)}</p>` : ''}
        <div class="small muted mono">Engine: ${esc(provider)}</div>
      </div>`;
  }

  // ======================================================================= drawers / modals
  function openPanel(id) {
    const el = document.getElementById(id);
    if (el.classList.contains('drawer')) {
      $$('.drawer.open').forEach((d) => closePanel(d.id));
      el.classList.add('open'); $('#scrim').hidden = false;
    } else { el.hidden = false; }
    el.setAttribute('aria-hidden', 'false');
  }
  function closePanel(id) {
    const el = document.getElementById(id);
    if (el.classList.contains('drawer')) { el.classList.remove('open'); $('#scrim').hidden = true; }
    else { el.hidden = true; }
    el.setAttribute('aria-hidden', 'true');
  }
  function closeAll() { ['historyDrawer', 'keysModal', 'reportModal'].forEach(closePanel); }

  // ======================================================================= history
  const FILTERS = ['', 'Fungal', 'Bacterial', 'Viral', 'Pest/Nutrient', 'Healthy'];

  function renderFilters() {
    $('#historyFilters').innerHTML = FILTERS.map((f) =>
      `<button type="button" class="chip text ${state.histFilter === f ? 'on' : ''}" data-f="${esc(f)}">${f || 'All'}</button>`).join('');
  }

  async function loadHistory() {
    const q = encodeURIComponent($('#historySearch').value.trim());
    const c = encodeURIComponent(state.histFilter);
    const box = $('#historyList');
    try {
      const rows = await getJSON(`/api/history?q=${q}&pathogen_class=${c}`);
      box.innerHTML = rows.length ? rows.map((h) => `
        <div class="h-item" data-id="${h.id}" tabindex="0">
          <img src="${esc(h.thumb)}" alt="">
          <div><h4>${esc(h.disease || 'Unknown')}</h4>
            <div class="h-meta">${esc(h.crop || '—')}${h.city ? ' · ' + esc(h.city) : ''} · <span class="mono">${Math.round(h.confidence)}%</span></div>
            <div class="h-meta">${esc(fmtDate(h.created_at))}</div>
            <div class="h-actions"><span class="badge ${clsKey(h.pathogen_class)}">${esc(h.pathogen_class)}</span>
              <button class="link-btn" style="color:var(--emerald)" data-hact="report" type="button">Report</button>
              <button class="link-btn" data-hact="delete" type="button">Delete</button></div></div>
        </div>`).join('')
        : `<p class="muted" style="text-align:center;padding:30px 10px">No scans found.</p>`;
    } catch (err) { box.innerHTML = `<p class="muted">${esc(err.message)}</p>`; }
  }

  async function openScan(id, thenReport) {
    try {
      const r = await getJSON(`/api/history/${id}`);
      $$('.mode')[0].click();
      setActive(r);
      closePanel('historyDrawer');
      if (thenReport) openReport(r);
    } catch (err) { toast(err.message, 'error'); }
  }

  // ======================================================================= printable report
  function openReport(r) {
    if (!r || r.halted || !r.diagnosis) { toast('Run a diagnosis first.', 'error'); return; }
    const d = r.diagnosis, t = r.treatment, p = r.prevention, w = r.weather, c = t.chemical;
    const row = (k, v) => `<tr><td>${esc(k)}</td><td>${v}</td></tr>`;
    $('#reportBody').innerHTML = `
      <div class="r-head">
        <div><h1>Plant Pathology Diagnostic Report</h1>
          <div class="muted">Agentic Multi-Modal Plant Pathology &amp; Treatment System · LangGraph</div>
          <table>
            ${row('Report date', esc(fmtDate(r.created_at)))}
            ${row('Scan ID', r.scan_id ? '#' + r.scan_id : '—')}
            ${row('Crop', esc(d.crop))}
            ${row('Location', esc(r.city || 'Not supplied'))}
            ${row('Engine', esc(r.provider || '—'))}
          </table></div>
        ${r.thumb ? `<img class="r-img" src="${esc(r.thumb)}" alt="Specimen">` : ''}
      </div>
      <h2>Diagnosis</h2>
      <table>
        ${row('Disease', `<b>${esc(d.disease)}</b>`)}
        ${row('Scientific / pathogen name', `<i>${esc(d.scientific_name || '—')}</i>`)}
        ${row('Pathogen class', esc(d.pathogen_class))}
        ${row('Confidence', `${Math.round(d.confidence)}%`)}
        ${row('Severity stage', esc(d.severity === 'None' ? 'No infection' : d.severity))}
        ${row('Summary', esc(d.summary))}
      </table>
      ${d.lesion_markers?.length ? `<h2>Visual lesion markers</h2>${list(d.lesion_markers)}` : ''}
      <h2>Microclimate (Open-Meteo)</h2>
      ${w && w.available ? `<table>
        ${row('Location', esc(`${w.city}${w.country ? ', ' + w.country : ''}`))}
        ${row('Temperature / humidity', `${w.temperature_c}°C / ${w.humidity_pct}% (48 h avg ${w.avg_humidity_48h_pct}%)`)}
        ${row('Rainfall next 48 h', `${w.rain_48h_mm} mm`)}
        ${row('Spread risk', `<b>${esc(w.risk_level)}</b> (${w.risk_score}/100) - ${esc(w.risk_note)}`)}
      </table>` : `<p>${esc(w?.reason || 'Not requested.')}</p>`}
      <h2>Treatment plan</h2>
      ${t.urgency ? `<p><b>${esc(t.urgency)}</b></p>` : ''}
      <div class="r-cols">
        <div><b>Organic &amp; bio-control</b><p>${esc(t.organic.summary)}</p>${list(t.organic.steps)}${list(t.organic.products)}</div>
        <div><b>Chemical protocol</b>${c.required ? `
          <p>${esc(c.active_ingredient_class)}</p>${list(c.examples)}
          <p><i>Application:</i> ${esc(c.application_method)}</p>
          <p><i>PPE:</i></p>${list(c.ppe)}
          <p><i>Harvest waiting:</i> ${esc(c.harvest_waiting_guidance)}</p>
          <p><i>Resistance:</i> ${esc(c.resistance_management)}</p>` : '<p>Not required.</p>'}</div>
      </div>
      ${t.monitoring?.length ? `<h2>Monitoring</h2>${list(t.monitoring)}` : ''}
      <h2>Prevention</h2>
      <div class="r-cols">
        <div><b>Crop rotation</b>${list(p.crop_rotation)}<b>Resistant varieties</b>${list(p.resistant_varieties)}</div>
        <div><b>Soil health</b>${list(p.soil_health)}<b>Cultural practices</b>${list(p.cultural_practices)}</div>
      </div>
      <div class="r-foot"><b>Disclaimer:</b> AI-generated decision support, not a substitute for a qualified agronomist. Always follow pesticide labels,
        wear required PPE, observe pre-harvest intervals and local regulations.</div>`;
    openPanel('reportModal');
  }

  // ======================================================================= API keys
  function paintKeyState(provider, info) {
    const el = $('#' + provider + 'State');
    el.className = 'badge ' + (info.configured ? 'good' : 'neutral');
    el.textContent = info.configured ? `active · ${info.source === 'ui' ? 'browser key' : '.env'} · ${info.masked}` : 'not set';
  }

  async function refreshStatus(openIfMissing) {
    try {
      const s = await getJSON('/api/status');
      state.status = s; state.maxMB = s.max_upload_mb; setQuota(s.quota);
      $('#keyDot').className = 'status-dot ' + (s.any_configured ? 'ok' : 'bad');
      paintKeyState('gemini', s.providers.gemini); paintKeyState('groq', s.providers.groq);
      $('#btnSaveKeys').disabled = !s.can_edit_keys;
      $('#keysNote').textContent = s.can_edit_keys
        ? 'Gemini is tried first; Groq takes over automatically if Gemini is unavailable or rate-limited.'
        : 'Keys can only be edited from the machine running the server.';
      if (openIfMissing && !s.any_configured) openPanel('keysModal');
    } catch (err) { toast('Could not reach the server: ' + err.message, 'error'); }
  }

  async function saveKeys() {
    const body = { gemini_api_key: $('#geminiKey').value.trim(), groq_api_key: $('#groqKey').value.trim() };
    try {
      const res = await postJSON('/api/keys', body);
      $('#geminiKey').value = ''; $('#groqKey').value = '';
      toast(`Saved ${res.saved.join(' + ')} key.`, 'ok');
      await refreshStatus(false);
      closePanel('keysModal');
    } catch (err) { toast(err.message, 'error'); }
  }

  async function clearKey(provider) {
    try {
      await getJSON('/api/keys/' + provider, { method: 'DELETE' });
      toast('Browser-saved key removed.', 'ok');
      await refreshStatus(false);
    } catch (err) { toast(err.message, 'error'); }
  }

  // ======================================================================= init
  function setMode(mode) {
    $$('.mode').forEach((b) => { const on = b.dataset.mode === mode; b.classList.toggle('active', on); b.setAttribute('aria-selected', on); });
    $('#viewDiagnose').hidden = mode !== 'diagnose';
    $('#viewCompare').hidden = mode !== 'compare';
  }

  function init() {
    single = createUploader($('#uploadSingle'), { onChange: (f) => { $('#btnAnalyze').disabled = !f || state.busy; } });
    base = createUploader($('#uploadBase'), { onChange: () => { $('#btnCompare').disabled = !(base?.file && follow?.file) || state.cmpBusy; } });
    follow = createUploader($('#uploadFollow'), { onChange: () => { $('#btnCompare').disabled = !(base?.file && follow?.file) || state.cmpBusy; } });
    resetPipeline(); emptyState(); renderFilters();

    // mode tabs
    $$('.mode').forEach((b) => b.addEventListener('click', () => setMode(b.dataset.mode)));

    // diagnose
    $('#btnAnalyze').addEventListener('click', runAnalyze);
    $('#sampleBar').addEventListener('click', async (e) => {
      const name = e.target.closest('[data-sample]')?.dataset.sample;
      if (!name) return;
      try { single.setFile(await fileFromSample(name)); } catch (err) { toast(err.message, 'error'); }
    });
    $('#resultPane').addEventListener('click', (e) => {
      const act = e.target.closest('[data-act]')?.dataset.act;
      if (act === 'report') openReport(state.active);
      else if (act === 'again') { single.clear(); emptyState(); resetPipeline(); window.scrollTo({ top: 0, behavior: 'smooth' }); }
    });

    // compare
    $('#btnCompare').addEventListener('click', runCompare);
    $$('[data-pair]').forEach((b) => b.addEventListener('click', async () => {
      const pair = b.dataset.pair === 'improving' ? ['early_blight_leaf.jpg', 'healthy_leaf.jpg'] : ['healthy_leaf.jpg', 'powdery_mildew_leaf.jpg'];
      try { base.setFile(await fileFromSample(pair[0])); follow.setFile(await fileFromSample(pair[1])); }
      catch (err) { toast(err.message, 'error'); }
    }));

    // history
    $('#btnHistory').addEventListener('click', () => { openPanel('historyDrawer'); loadHistory(); });
    $('#historySearch').addEventListener('input', () => { clearTimeout(state.histTimer); state.histTimer = setTimeout(loadHistory, 250); });
    $('#historyFilters').addEventListener('click', (e) => {
      const f = e.target.closest('[data-f]'); if (!f) return;
      state.histFilter = f.dataset.f; renderFilters(); loadHistory();
    });
    $('#historyList').addEventListener('click', async (e) => {
      const item = e.target.closest('.h-item'); if (!item) return;
      const id = item.dataset.id, act = e.target.closest('[data-hact]')?.dataset.hact;
      if (act === 'delete') {
        e.stopPropagation();
        if (!confirm('Delete this scan from history?')) return;
        try { await getJSON('/api/history/' + id, { method: 'DELETE' }); if (state.active?.scan_id == id) { state.active = null; emptyState(); resetPipeline(); } loadHistory(); }
        catch (err) { toast(err.message, 'error'); }
      } else openScan(id, act === 'report');
    });
    $('#historyList').addEventListener('keydown', (e) => { if (e.key === 'Enter') e.target.closest('.h-item')?.click(); });

    // keys + report + generic close
    $('#btnKeys').addEventListener('click', () => { refreshStatus(false); openPanel('keysModal'); });
    $('#btnSaveKeys').addEventListener('click', saveKeys);
    $$('[data-clear]').forEach((b) => b.addEventListener('click', () => clearKey(b.dataset.clear)));
    $('#btnDoPrint').addEventListener('click', () => window.print());
    $$('[data-close]').forEach((b) => b.addEventListener('click', () => closePanel(b.dataset.close)));
    $('#scrim').addEventListener('click', closeAll);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeAll(); });
    ['keysModal', 'reportModal'].forEach((id) => document.getElementById(id).addEventListener('click', (e) => { if (e.target.id === id) closePanel(id); }));

    setInterval(renderQuota, 1000);
    refreshStatus(true);
    loadSamples();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
