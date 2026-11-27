/* FIZZY ORANGE · custom Editor page.
 * Plain JS in the VIBE FM sandbox: only window.vibe talks to the firmware.
 * No network, no host file drop, no build step.
 */
(async () => {
  'use strict';

  await vibe.ready;

  const $ = (id) => document.getElementById(id);
  const enc = new TextEncoder();
  const dec = new TextDecoder();
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const yieldNow = () => new Promise((r) => setTimeout(r, 0));
  const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);

  const MAX_SLOTS = 64;
  const MAX_FRAMES = 1500000;       // per decoded sample
  const MAX_PCM_TOTAL = 16 * 1024 * 1024; // firmware PCM budget
  const MAX_FILE_BYTES = 24 * 1024 * 1024; // one input entry
  const CHUNK = 2048;
  const AUDIO_RE = /\.(wav|mp3|flac|ogg|oga|aif|aiff|opus|m4a|mp4)$/i;
  const BUSY_RE = /\b(busy|loading|uploading|parsing|building|working|please wait|wait)\b/i;
  const NO_DEFLATE =
    'This host cannot inflate deflated ZIP entries. Repack with `sfzpkg pack` ' +
    '(stored ZIPs are the default) or unpack and upload the .sfz and 16-bit WAV ' +
    'files one by one. `sfzpkg pack --deflate` makes ZIPs this page cannot open here.';

  const state = {
    status: 'waking up', error: '', pkg: '', selected: 0,
    files: [], instruments: [], presets: [], samples: [], log: [],
    pcmBytes: 0, statusSeq: 0, errorSeq: 0, busy: false, uploading: false
  };
  let audioCtx = null;

  /* ---------- byte helpers ---------- */

  function le32(v) {
    const a = new Uint8Array(4);
    new DataView(a.buffer).setUint32(0, v >>> 0, true);
    return a;
  }
  function cat(...parts) {
    let n = 0;
    for (const p of parts) n += p.length;
    const out = new Uint8Array(n);
    let k = 0;
    for (const p of parts) { out.set(p, k); k += p.length; }
    return out;
  }
  function ascii(u8, at, len) {
    let s = '';
    for (let i = at; i < at + len; i++) s += String.fromCharCode(u8[i]);
    return s;
  }
  function writeAscii(u8, at, s) {
    for (let i = 0; i < s.length; i++) u8[at + i] = s.charCodeAt(i);
  }
  function copyU8(u8) {
    const out = new Uint8Array(u8.length);
    out.set(u8);
    return out;
  }
  function toArrayBuffer(u8) {
    const out = new Uint8Array(u8.length);
    out.set(u8);
    return out.buffer;
  }
  function safeName(original, index) {
    let base = String(original).replace(/\\/g, '/').split('/').pop() || ('f' + index + '.bin');
    base = base.replace(/[^A-Za-z0-9._-]+/g, '_').replace(/^_+|_+$/g, '');
    const m = base.match(/(\.[A-Za-z0-9_-]+)$/);
    const ext = m ? m[1] : '';
    let stem = ext ? base.slice(0, -ext.length) : base;
    stem = stem.slice(0, Math.max(1, 80 - ext.length)) || ('f' + index);
    return (stem + ext).slice(0, 96) || ('f' + index + '.bin');
  }

  /* ---------- display of firmware state ---------- */

  function busyNow() { return BUSY_RE.test(state.status); }

  function setStatus(text, isError) {
    state.status = String(text || '');
    if (isError) state.error = state.status;
    renderStatus();
  }
  function renderStatus() {
    const el = $('status');
    const busy = state.busy || state.uploading || busyNow();
    el.textContent = state.error ? (state.error + (state.status && state.status !== state.error ? ' · ' + state.status : '')) : state.status;
    el.classList.toggle('error', !!state.error);
    $('pkg-pill').textContent = 'PKG ' + (state.pkg || '—');
    $('sel-pill').textContent = 'SEL ' + (state.selected || 0);
    const pill = $('busy-pill');
    pill.textContent = busy ? 'BUSY' : 'READY';
    pill.className = 'pill ' + (busy ? 'busy' : 'hot');
  }
  function setProgress(text, pct) {
    $('progress').textContent = String(text);
    $('bar').style.width = (pct < 0 ? 0 : clamp(pct, 0, 100)) + '%';
  }
  function pushLog(text, isError) {
    state.log.unshift({ text, isError: !!isError });
    if (state.log.length > 40) state.log.length = 40;
    renderLists();
  }
  function fillSelect(sel, items, placeholder) {
    const keep = document.activeElement === sel ? sel.value : null;
    sel.replaceChildren();
    if (!items.length) {
      sel.add(new Option(placeholder, '0'));
    } else {
      for (const it of items) sel.add(new Option(it.label, String(it.index)));
    }
    const wanted = keep !== null ? keep : String(state.selected || 0);
    if ([...sel.options].some((o) => o.value === wanted)) sel.value = wanted;
  }
  function renderSelected() {
    $('sel-pill').textContent = 'SEL ' + (state.selected || 0);
    for (const sel of [$('instr'), $('preset')]) {
      if (document.activeElement === sel) continue;
      const v = String(state.selected || 0);
      if ([...sel.options].some((o) => o.value === v)) sel.value = v;
    }
  }
  function renderLists() {
    fillSelect($('instr'), state.instruments, '(no instrument)');
    fillSelect($('preset'), state.presets, '(no preset)');
    renderSelected();

    const files = $('files');
    files.replaceChildren();
    if (!state.files.length) {
      const d = document.createElement('div');
      d.className = 'empty'; d.textContent = 'no files reported';
      files.append(d);
    } else {
      for (const f of state.files) {
        const row = document.createElement('div');
        row.className = 'item';
        const b = document.createElement('b'); b.textContent = String(f.slot);
        const s = document.createElement('span'); s.textContent = f.name;
        const k = document.createElement('small'); k.textContent = ({ 1: 'SFZ', 2: 'WAV', 3: 'JSON' })[f.kind] || ('kind ' + f.kind);
        row.append(b, s, k); files.append(row);
      }
    }

    const samples = $('samples');
    samples.replaceChildren();
    if (!state.samples.length) {
      const d = document.createElement('div');
      d.className = 'empty'; d.textContent = 'no samples reported';
      samples.append(d);
    } else {
      for (const s of state.samples) {
        const row = document.createElement('div');
        row.className = 'item';
        const b = document.createElement('b'); b.textContent = String(s.index);
        const n = document.createElement('span'); n.textContent = s.name;
        row.append(b, n); samples.append(row);
      }
    }

    const log = $('log');
    log.replaceChildren();
    if (!state.log.length) {
      const d = document.createElement('div');
      d.className = 'empty'; d.textContent = 'waiting for status…';
      log.append(d);
    } else {
      for (const l of state.log) {
        const row = document.createElement('div');
        row.className = 'item' + (l.isError ? ' err' : '');
        const s = document.createElement('span'); s.textContent = l.text;
        row.append(s); log.append(row);
      }
    }
    $('count').textContent = state.files.length + ' / ' + MAX_SLOTS + ' slots';
  }

  /* ---------- firmware replies: 0x02-prefixed ASCII status lines ---------- */

  vibe.onData((bytes) => {
    if (!bytes || !bytes.length || bytes[0] !== 0x02) return;
    const lines = dec.decode(bytes.subarray(1)).replace(/\0/g, '').split('\n');
    for (const raw of lines) {
      const line = raw.replace(/\r$/, '').trim();
      if (!line) continue;
      const sp = line.indexOf(' ');
      const cmd = (sp < 0 ? line : line.slice(0, sp)).toUpperCase();
      const rest = sp < 0 ? '' : line.slice(sp + 1).trim();
      pushLog(line, cmd === 'ERROR');

      if (cmd === 'STATUS' || cmd === 'OK') {
        state.status = rest || (cmd === 'OK' ? 'OK' : 'ready');
        state.error = '';
        state.statusSeq++;
      } else if (cmd === 'ERROR') {
        state.error = rest || 'firmware error';
        state.status = 'ERROR';
        state.errorSeq++;
      } else if (cmd === 'PKG') {
        state.pkg = rest;
        state.files = []; state.instruments = []; state.presets = []; state.samples = [];
      } else if (cmd === 'FILE') {
        const m = rest.match(/^(\d+)\s+(\d+)\s+(.*)$/);
        if (m) state.files.push({ slot: +m[1], kind: +m[2], name: m[3] });
      } else if (cmd === 'INSTR' || cmd === 'PRESET' || cmd === 'SAMPLE') {
        const t = rest.split(/\s+/);
        const index = parseInt(t[0], 10);
        if (!Number.isFinite(index)) continue;
        if (cmd === 'INSTR') {
          let end = t.length, regions = 0, samples = 0;
          if (t.length > 3 && /^\d+$/.test(t[t.length - 1]) && /^\d+$/.test(t[t.length - 2])) {
            samples = +t[t.length - 1]; regions = +t[t.length - 2]; end = t.length - 2;
          }
          state.instruments.push({ index, label: index + ': ' + t.slice(1, end).join(' ') + ' · ' + regions + 'r/' + samples + 's' });
        } else {
          const bucket = cmd === 'PRESET' ? state.presets : state.samples;
          bucket.push({ index, label: index + ': ' + t.slice(1).join(' '), name: t.slice(1).join(' ') });
        }
      } else if (cmd === 'SELECTED') {
        const n = parseInt(rest, 10);
        if (Number.isFinite(n)) state.selected = n;
      }
    }
    renderStatus();
    renderLists();
  });

  /* ---------- parameters: sliders + rotary knobs from vibe.params ---------- */

  function fmt(p, v) {
    if (p.steps > 1 && p.choices && p.choices.length) {
      const step = (p.max - p.min) / (p.steps - 1) || 1;
      const k = clamp(Math.round((v - p.min) / step), 0, p.choices.length - 1);
      return p.choices[k] || '';
    }
    const u = p.unit || '';
    if (u === 'dB') return (v > 0 ? '+' : '') + v.toFixed(1) + ' dB';
    if (u === 'st' || u === 'ct' || u === 'semitones') return (v > 0 ? '+' : '') + v.toFixed(0) + ' ' + u;
    if (u === 'Hz') return v.toFixed(v >= 100 ? 0 : 1) + ' Hz';
    if (u === 'ms') return v.toFixed(0) + ' ms';
    if (u === 'x') return v.toFixed(2) + 'x';
    return v.toFixed(2) + (u ? ' ' + u : '');
  }

  // Parameters are found by id, never by array position.
  const octaveParam = typeof vibe.byId === 'function' ? vibe.byId('octave') : null;
  const volumeParam = typeof vibe.byId === 'function' ? vibe.byId('volume') : null;

  const controls = [];
  for (const p of vibe.params) {
    if (!p || p.i === undefined || p.i === null) continue;
    const row = document.createElement('div');
    row.className = 'ctl';
    const label = document.createElement('label');
    label.textContent = p.name || p.id || ('P' + p.i);
    label.title = p.id || '';
    const knob = document.createElement('div');
    knob.className = 'knob';
    knob.title = 'drag up/down · Shift = fine · double-click = default';
    knob.append(document.createElement('i'));

    const discrete = p.steps > 1 && Array.isArray(p.choices) && p.choices.length > 0;
    let input;
    if (discrete) {
      input = document.createElement('select');
      p.choices.forEach((choice, k) => input.add(new Option(choice, String(k))));
      input.onchange = () => {
        const k = clamp(+input.value || 0, 0, p.choices.length - 1);
        vibe.set(p.i, p.min + k * ((p.max - p.min) / (p.steps - 1) || 1));
      };
    } else {
      input = document.createElement('input');
      input.type = 'range'; input.min = '0'; input.max = '1000'; input.step = '1';
      input.oninput = () => vibe.set(p.i, p.min + (+input.value / 1000) * (p.max - p.min));
    }
    const out = document.createElement('output');

    let y0 = 0, v0 = 0;
    knob.onpointerdown = (e) => {
      if (knob.setPointerCapture) { try { knob.setPointerCapture(e.pointerId); } catch (_) { /* optional */ } }
      y0 = e.clientY; v0 = vibe.values[p.i];
      e.preventDefault();
    };
    knob.onpointermove = (e) => {
      const held = knob.hasPointerCapture ? knob.hasPointerCapture(e.pointerId) : e.buttons > 0;
      if (!held) return;
      const span = p.max - p.min || 1;
      const fine = e.shiftKey ? 0.25 : 1;
      vibe.set(p.i, clamp(v0 + ((y0 - e.clientY) / 180) * fine * span, p.min, p.max));
    };
    knob.ondblclick = () => { if (typeof p.def === 'number') vibe.set(p.i, p.def); };

    row.append(label, knob, input, out);
    $('params').append(row);
    controls.push({ p, knob, input, out, discrete });
  }

  vibe.onValues((values) => {
    for (const c of controls) {
      const v = values[c.p.i];
      if (v === undefined || v === null) continue;
      const norm = clamp((v - c.p.min) / (c.p.max - c.p.min || 1), 0, 1);
      c.knob.style.setProperty('--a', (norm * 270).toFixed(1) + 'deg');
      if (document.activeElement !== c.input) {
        c.input.value = c.discrete ? String(Math.round(norm * ((c.p.steps || 1) - 1))) : String(Math.round(norm * 1000));
      }
      c.out.textContent = fmt(c.p, v);
    }
    if (octaveParam && values[octaveParam.i] !== undefined) {
      $('octave-pill').textContent = 'OCT ' + (values[octaveParam.i] >= 0 ? '+' : '') + Math.round(values[octaveParam.i]);
    } else if (volumeParam && values[volumeParam.i] !== undefined) {
      $('octave-pill').textContent = 'VOL ' + values[volumeParam.i].toFixed(2);
    }
  });

  /* ---------- on-screen keyboard C3–C5 (MIDI 48..72) ---------- */

  const LOW = 48, HIGH = 72;
  const WHITE_PCS = [0, 2, 4, 5, 7, 9, 11];
  const BLACK_PCS = [1, 3, 6, 8, 10];
  const whiteTotal = Array.from({ length: HIGH - LOW + 1 }, (_, i) => LOW + i)
    .filter((n) => WHITE_PCS.includes(n % 12)).length;
  const whitePct = 100 / whiteTotal;
  const activeNotes = new Set();
  let whitesBefore = 0;
  for (let n = LOW; n <= HIGH; n++) {
    const black = BLACK_PCS.includes(n % 12);
    const key = document.createElement('button');
    key.className = black ? 'black' : 'white';
    key.type = 'button';
    key.title = 'MIDI ' + n;
    key.setAttribute('aria-label', 'MIDI note ' + n);
    if (black) {
      key.style.left = (whitesBefore * whitePct).toFixed(4) + '%';
      key.style.width = (whitePct * 0.62).toFixed(4) + '%';
    } else {
      if (n % 12 === 0) key.textContent = 'C' + (Math.floor(n / 12) - 1);
      whitesBefore++;
    }
    const on = (e) => {
      try { key.setPointerCapture(e.pointerId); } catch (_) { /* capture is optional */ }
      if (activeNotes.has(n)) return;
      activeNotes.add(n);
      key.classList.add('down');
      vibe.note(n, clamp(+$('velocity').value || 96, 1, 127) / 127);
      e.preventDefault();
    };
    const off = () => {
      if (!activeNotes.has(n)) return;
      activeNotes.delete(n);
      key.classList.remove('down');
      vibe.note(n, 0);
    };
    key.onpointerdown = on;
    key.onpointerup = off;
    key.onpointercancel = off;
    key.onpointerleave = off;
    key.onlostpointercapture = off;
    $('keys').append(key);
  }

  /* ---------- device display ---------- */

  vibe.onFrame((fb) => vibe.paint($('display'), fb));

  /* ---------- buttons ---------- */

  function statusRefresh() { vibe.send(new Uint8Array([0x20])); }
  function selectFrom(sel) {
    const index = Math.max(0, parseInt(sel.value, 10) || 0);
    state.selected = index;
    vibe.send(cat(new Uint8Array([0x21]), le32(index)));
    renderSelected();
  }
  $('instr').onchange = () => selectFrom($('instr'));
  $('preset').onchange = () => selectFrom($('preset'));
  $('refresh').onclick = statusRefresh;
  $('panic').onclick = () => {
    vibe.send(new Uint8Array([0x22]));
    for (const n of activeNotes) vibe.note(n, 0);
    activeNotes.clear();
    $('keys').querySelectorAll('.down').forEach((el) => el.classList.remove('down'));
  };
  $('audition').onclick = () => {
    const note = clamp(parseInt($('audition-note').value, 10) || 60, 0, 127);
    const vel = clamp(parseInt($('velocity').value, 10) || 96, 1, 127);
    vibe.send(new Uint8Array([0x23, note, vel]));
  };
  $('velocity').oninput = () => { $('velocity-out').textContent = $('velocity').value; };
  $('velocity-out').textContent = $('velocity').value;
  $('load').onclick = () => {
    const path = $('path').value.trim();
    if (!path) return;
    vibe.send(cat(new Uint8Array([0x30]), enc.encode(path)));
    setStatus('requested media path ' + path, false);
    statusRefresh();
  };

  /* ---------- ZIP central directory (stored + raw deflate) ---------- */

  async function readStream(stream) {
    const reader = stream.getReader();
    const chunks = [];
    let total = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      total += value.length;
      if (total > MAX_FILE_BYTES * 2) throw new Error('ZIP entry expands beyond the upload limit');
    }
    const out = new Uint8Array(total);
    let at = 0;
    for (const c of chunks) { out.set(c, at); at += c.length; }
    return out;
  }
  function byteStream(bytes) {
    if (typeof Blob === 'function' && typeof Blob.prototype.stream === 'function') {
      return new Blob([bytes]).stream();
    }
    return new ReadableStream({
      start(ctrl) { ctrl.enqueue(bytes); ctrl.close(); }
    });
  }
  async function inflateRaw(bytes) {
    if (typeof DecompressionStream !== 'function') throw new Error(NO_DEFLATE);
    let last = null;
    for (const format of ['deflate-raw', 'deflate']) {
      try {
        const stream = byteStream(bytes).pipeThrough(new DecompressionStream(format));
        if (typeof Response === 'function') {
          return new Uint8Array(await new Response(stream).arrayBuffer());
        }
        return await readStream(stream);
      } catch (err) { last = err; }
    }
    throw new Error(NO_DEFLATE + (last && last.message ? ' (' + last.message + ')' : ''));
  }
  function zipName(bytes) {
    try {
      return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    } catch (_) {
      let s = '';
      for (const b of bytes) s += b < 128 ? String.fromCharCode(b) : '_';
      return s;
    }
  }
  async function readZip(buffer) {
    const u8 = new Uint8Array(buffer);
    const dv = new DataView(buffer);
    const floor = Math.max(0, u8.length - 65557);
    let eocd = -1;
    for (let i = u8.length - 22; i >= floor; i--) {
      if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
    }
    if (eocd < 0) throw new Error('not a ZIP file (no central directory found)');
    const count = dv.getUint16(eocd + 10, true);
    const cdSize = dv.getUint32(eocd + 12, true);
    let at = dv.getUint32(eocd + 16, true);
    if (count === 0xffff || cdSize === 0xffffffff || at === 0xffffffff) {
      throw new Error('ZIP64 packages are not supported; repack with `sfzpkg pack`');
    }
    if (at + cdSize > u8.length) throw new Error('truncated ZIP central directory');
    const entries = [];
    for (let i = 0; i < count; i++) {
      if (at + 46 > u8.length || dv.getUint32(at, true) !== 0x02014b50) {
        throw new Error('bad ZIP central directory at entry ' + i);
      }
      const flags = dv.getUint16(at + 8, true);
      const method = dv.getUint16(at + 10, true);
      const compSize = dv.getUint32(at + 20, true);
      const rawSize = dv.getUint32(at + 24, true);
      const nameLen = dv.getUint16(at + 28, true);
      const extraLen = dv.getUint16(at + 30, true);
      const commentLen = dv.getUint16(at + 32, true);
      const local = dv.getUint32(at + 42, true);
      const name = zipName(u8.subarray(at + 46, at + 46 + nameLen));
      at += 46 + nameLen + extraLen + commentLen;

      if (name.endsWith('/') || /(^|\/)__MACOSX(\/|$)/i.test(name) ||
          /(^|\/)(\._[^/]*|\.DS_Store)$/i.test(name)) continue;
      if (compSize === 0xffffffff || rawSize === 0xffffffff || local === 0xffffffff) {
        throw new Error(name + ': ZIP64 entries are not supported');
      }
      if (rawSize > MAX_FILE_BYTES) throw new Error(name + ': file exceeds the ' + (MAX_FILE_BYTES / 1048576) + ' MB input limit');
      if (local + 30 > u8.length || dv.getUint32(local, true) !== 0x04034b50) {
        throw new Error(name + ': bad local file header');
      }
      const localNameLen = dv.getUint16(local + 26, true);
      const localExtraLen = dv.getUint16(local + 28, true);
      const start = local + 30 + localNameLen + localExtraLen;
      if (start + compSize > u8.length) throw new Error(name + ': truncated ZIP entry');
      const packed = u8.subarray(start, start + compSize);
      let data;
      if (method === 0) {
        data = copyU8(packed);
      } else if (method === 8) {
        data = await inflateRaw(packed);
      } else {
        throw new Error(name + ': unsupported ZIP compression method ' + method + ' (use `sfzpkg pack`)');
      }
      if (data.length !== rawSize) throw new Error(name + ': ZIP size mismatch (compressed package?)');
      entries.push({ name, data, utf8: !!(flags & 0x800) });
    }
    return entries;
  }

  /* ---------- WAV / AudioBuffer conversion ---------- */

  function pcmWavInfo(u8) {
    if (u8.length < 44 || ascii(u8, 0, 4) !== 'RIFF' || ascii(u8, 8, 4) !== 'WAVE') return null;
    const dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
    let at = 12, fmt = null, data = null;
    while (at + 8 <= u8.length) {
      const id = ascii(u8, at, 4);
      const size = dv.getUint32(at + 4, true);
      if (id === 'fmt ' && size >= 16 && at + 24 <= u8.length &&
          dv.getUint16(at + 8, true) === 1 &&
          (dv.getUint16(at + 10, true) === 1 || dv.getUint16(at + 10, true) === 2) &&
          dv.getUint16(at + 22, true) === 16) {
        fmt = { channels: dv.getUint16(at + 10, true), rate: dv.getUint32(at + 12, true) };
      }
      if (id === 'data') { data = { at: at + 8, size }; break; }
      at += 8 + size + (size & 1);
    }
    if (!fmt || !data || data.at + data.size > u8.length) return null;
    const frames = Math.floor(data.size / (fmt.channels * 2));
    return frames > 0 ? { channels: fmt.channels, rate: fmt.rate, frames } : null;
  }

  async function audioToWav(src, label) {
    const plain = pcmWavInfo(src);
    if (plain) {
      if (plain.frames > MAX_FRAMES) {
        throw new Error(label + ': ' + plain.frames + ' frames exceeds the ' + MAX_FRAMES + ' frame limit');
      }
      return copyU8(src); // already 16-bit PCM, 1 or 2 channels: pass it through
    }
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) {
      throw new Error(label + ': this host has no audio decoder. Repack with `sfzpkg pack` ' +
        'so samples are 16-bit WAV in a stored ZIP, or upload a WAV file directly.');
    }
    if (!audioCtx) audioCtx = new Ctx();
    let buffer;
    try {
      buffer = await audioCtx.decodeAudioData(toArrayBuffer(src));
    } catch (err) {
      throw new Error(label + ': cannot decode audio (' + (err && err.message ? err.message : 'unsupported format') + ')');
    }
    if (buffer.length > MAX_FRAMES) {
      throw new Error(label + ': ' + buffer.length + ' frames exceeds the ' + MAX_FRAMES + ' frame limit');
    }
    const channels = clamp(buffer.numberOfChannels || 1, 1, 2);
    const frames = buffer.length;
    const dataBytes = frames * channels * 2;
    if (dataBytes > MAX_PCM_TOTAL) {
      throw new Error(label + ': decoded PCM would be ' + Math.round(dataBytes / 1048576) + ' MB (16 MB total limit)');
    }
    const out = new Uint8Array(44 + dataBytes);
    const dv = new DataView(out.buffer);
    writeAscii(out, 0, 'RIFF'); dv.setUint32(4, 36 + dataBytes, true);
    writeAscii(out, 8, 'WAVE'); writeAscii(out, 12, 'fmt ');
    dv.setUint32(16, 16, true); dv.setUint16(20, 1, true);
    dv.setUint16(22, channels, true); dv.setUint32(24, buffer.sampleRate, true);
    dv.setUint32(28, buffer.sampleRate * channels * 2, true);
    dv.setUint16(32, channels * 2, true); dv.setUint16(34, 16, true);
    writeAscii(out, 36, 'data'); dv.setUint32(40, dataBytes, true);

    const chans = [];
    for (let c = 0; c < channels; c++) chans.push(buffer.getChannelData(c));
    let at = 44;
    for (let i = 0; i < frames; i++) {
      for (let c = 0; c < channels; c++) {
        let v = chans[c][i];
        if (!Number.isFinite(v)) v = 0;
        v = v < -1 ? -1 : v > 1 ? 1 : v;
        dv.setInt16(at, Math.round(v < 0 ? v * 0x8000 : v * 0x7fff), true);
        at += 2;
      }
    }
    return out;
  }

  /* ---------- protocol 0x10 / 0x11 / 0x12 / 0x14 ---------- */

  async function waitForIdle(label, timeout) {
    const startSeq = state.statusSeq;
    const startErr = state.errorSeq;
    const start = Date.now();
    let sawReply = false;
    while (Date.now() - start < (timeout || 30000)) {
      vibe.send(new Uint8Array([0x20]));
      await sleep(140);
      if (state.statusSeq > startSeq) sawReply = true;
      if (state.errorSeq > startErr) throw new Error(state.error || (label + ' was rejected'));
      if (sawReply && !busyNow()) return;
    }
    throw new Error(label + ': firmware did not report ready');
  }
  async function sendFile(entry, kind, slot) {
    const name = safeName(entry.name, slot);
    const data = entry.data;
    const nameBytes = enc.encode(name);
    const errorSeq = state.errorSeq;
    vibe.send(cat(new Uint8Array([0x10]), le32(data.length),
      new Uint8Array([slot & 0xff, kind & 0xff, nameBytes.length & 0xff]), nameBytes));
    for (let off = 0; off < data.length; off += CHUNK) {
      const part = data.subarray(off, Math.min(off + CHUNK, data.length));
      vibe.send(cat(new Uint8Array([0x11]), le32(off), part));
      setProgress('uploading ' + (slot + 1) + ' · ' + name + ' · ' +
        Math.round((off + part.length) * 100 / data.length) + '%',
        (off + part.length) * 100 / data.length);
      if (state.errorSeq > errorSeq) throw new Error(state.error || ('firmware rejected ' + name));
      await yieldNow(); // throttle: let the host drain the event queue
    }
    vibe.send(new Uint8Array([0x12]));
    await waitForIdle('commit ' + name, 20000);
  }

  /* ---------- file input + page-local drop zone ---------- */

  async function runUpload(files) {
    if (state.uploading) { setStatus('an upload is already running', true); return; }
    state.uploading = true;
    state.error = '';
    state.pcmBytes = 0;
    renderStatus();
    try {
      setProgress('reading files…', 0);
      const entries = [];
      for (const f of files) {
        if (f.size > MAX_FILE_BYTES) throw new Error(f.name + ': file exceeds the ' + (MAX_FILE_BYTES / 1048576) + ' MB limit');
        if (/\.zip$/i.test(f.name)) {
          const found = await readZip(await f.arrayBuffer());
          entries.push(...found);
          setProgress(f.name + ': ' + found.length + ' entries', -1);
        } else {
          entries.push({ name: f.name, data: new Uint8Array(await f.arrayBuffer()) });
        }
      }

      const queue = [];
      for (const entry of entries) {
        const name = entry.name.replace(/\\/g, '/');
        if (name.endsWith('/') || /(^|\/)__MACOSX(\/|$)/i.test(name) ||
            /(^|\/)(\._[^/]*|\.DS_Store)$/i.test(name)) continue;
        const base = name.split('/').pop();
        let kind = 0;
        if (/\.sfz$/i.test(base)) kind = 1;
        else if (/\.json$/i.test(base)) kind = 3;
        else if (AUDIO_RE.test(base)) kind = 2;
        else continue;
        const data = kind === 2 ? await audioToWav(entry.data, base) : entry.data;
        if (data.length > MAX_FILE_BYTES) throw new Error(base + ': file exceeds the upload limit');
        state.pcmBytes += data.length;
        if (state.pcmBytes > MAX_PCM_TOTAL) {
          throw new Error('16 MB PCM total exceeded; remove samples or use shorter loops');
        }
        if (kind === 3 && /^fizzy\.json$/i.test(base)) {
          try {
            const manifest = JSON.parse(dec.decode(entry.data));
            if (manifest && manifest.name) state.pkg = String(manifest.name);
          } catch (_) { /* the firmware will report any manifest problem */ }
        }
        queue.push({ name: base, data, kind });
      }
      if (!queue.length) {
        throw new Error('nothing uploadable: expected .sfz, fizzy.json / preset .json or audio');
      }
      if (queue.length > MAX_SLOTS) throw new Error(queue.length + ' files exceeds the 64 file limit');

      let slot = 0;
      for (const item of queue) {
        await sendFile(item, item.kind, slot);
        slot++;
      }
      setProgress('building instruments from ' + queue.length + ' file(s)…', 100);
      state.busy = true; renderStatus();
      vibe.send(new Uint8Array([0x14]));
      await waitForIdle('build', 45000);
      statusRefresh();
      setProgress('done · ' + queue.length + ' file(s) committed', 100);
      state.error = '';
      setStatus('package ready', false);
    } catch (err) {
      const text = err && err.message ? err.message : String(err);
      setStatus(text, true);
      setProgress('failed · ' + text, -1);
      statusRefresh();
    } finally {
      state.uploading = false;
      state.busy = false;
      renderStatus();
    }
  }

  const drop = $('drop');
  const fileInput = $('file');
  drop.onclick = (e) => { if (e.target !== fileInput) fileInput.click(); };
  drop.onkeydown = (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.click(); }
  };
  fileInput.onchange = () => {
    const list = [...(fileInput.files || [])];
    fileInput.value = '';
    if (list.length) runUpload(list);
  };
  for (const type of ['dragenter', 'dragover']) {
    drop.addEventListener(type, (e) => {
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
      drop.classList.add('over');
    });
  }
  for (const type of ['dragleave', 'dragend']) {
    drop.addEventListener(type, () => drop.classList.remove('over'));
  }
  drop.addEventListener('drop', (e) => {
    e.preventDefault();
    drop.classList.remove('over');
    const list = [...((e.dataTransfer && e.dataTransfer.files) || [])];
    if (list.length) runUpload(list);
  });

  /* ---------- start ---------- */

  renderStatus();
  renderLists();
  statusRefresh();
  setInterval(statusRefresh, 5000);
})();
