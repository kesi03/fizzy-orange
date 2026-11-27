/* FIZZY ORANGE -- an SFZ sample player for VIBE FM.
 *
 * Control flow: parameters, display, editor uploads, media loading, storage.
 * Audio flow: 32 sample voices fed by a one-way note queue (sfz.c).
 *
 * The firmware plays a built-in sine demo from startup. Drop a ZIP on the
 * custom Editor page to upload a fizzy.json + .sfz + WAV package; it is stored
 * in the firmware's own space and rebuilt on every start. An unpacked package
 * can also be read from host media with the Editor's LOAD PATH command.
 */
#include "vibe.h"
#include "vibe_dsp.h"
#include "vibe_gfx.h"
#include "sfz.h"

VIBE_PARALLEL_AUDIO

/* ------------------------------------------------------------------ params -- */
enum {
  P_SELECT = 0, P_VOLUME, P_OCTAVE, P_TRANSPOSE,
  P_ATTACK, P_RELEASE, P_SUSTAIN, P_VELOCITY,
  P_TUNE, P_SPREAD, P_COUNT
};
static const vibe_param PARAMS[P_COUNT] = {
  { "select",    "Preset",    0.0f,   31.0f,  0.0f,  "",   32, 0 },
  { "volume",    "Volume",    0.0f,    1.0f,  0.75f, "",    0, 0 },
  { "octave",    "Octave",   -2.0f,    2.0f,  0.0f,  "",    5, 0 },
  { "transpose", "Transpose",-12.0f,  12.0f,  0.0f,  "st", 25, 0 },
  { "attack",    "Attack",    0.1f,    4.0f,  1.0f,  "x",   0, 0 },
  { "release",   "Release",   0.1f,    4.0f,  1.0f,  "x",   0, 0 },
  { "sustain",   "Sustain",   0.0f,    1.0f,  0.5f,  "",    0, 0 },
  { "velocity",  "Vel curve", 0.2f,    2.0f,  1.0f,  "x",   0, 0 },
  { "tune",      "Fine tune",-100.0f,100.0f,  0.0f,  "ct",  0, 0 },
  { "spread",    "Spread",    0.0f,    1.0f,  0.25f, "",    0, 0 }
};
static const char *const GROUPS[P_COUNT] = {
  "SOUND", "SOUND", "SOUND", "SOUND", "ENV", "ENV", "ENV", "ENV", "TONE", "TONE"
};

enum { UI_SOUND, UI_ENV, UI_TONE, UI_VOICE, UI_FILES, UI_HELP, UI_N };
static const int PAGE_PARAM[UI_N][4] = {
  { P_SELECT, P_VOLUME, P_OCTAVE, P_TRANSPOSE },
  { P_ATTACK, P_RELEASE, P_SUSTAIN, P_VELOCITY },
  { P_TUNE, P_SPREAD, P_VOLUME, P_SELECT },
  { P_SELECT, P_VOLUME, P_ATTACK, P_RELEASE },
  { -1, -1, -1, -1 },
  { -1, -1, -1, -1 }
};
static const char *const PAGE_NAME[UI_N] = {
  "SOUND", "ENV", "TONE", "VOICE", "FILES", "HELP"
};
static const char *const F_LABELS[6] = { "PAGE", "NEXT", "AUD", "OFF", "HELP", "BUILD" };
static const char *const K_LABELS[6] = { "SOUND", "ENV", "TONE", "VOICE", "FILES", "HELP" };
static const char *const HELP[] = {
  "KEYS / MIDI / DAW", "play the selected SFZ instrument",
  "PADS 1-15",         "choose preset; PAD packs play 36-50",
  "PAD 16 / REC",      "all notes off",
  "F1 / F2",           "page / next preset",
  "F3 / F4",           "audition C4 / all notes off",
  "F5 / F6",           "this overview / rebuild stored package",
  "K1 - K6",           "SOUND ENV TONE VOICE FILES HELP",
  "ENCODERS 1 - 4",    "the four values shown above F1-F6",
  "A / B",             "volume / previous-next preset",
  "DATA + ENTER",      "FOCUS: walk a value, press to act",
  "HOME",              "this overview; RETURN closes it",
  "EDITOR",            "upload a ZIP with fizzy.json + SFZ + WAV"
};

/* ------------------------------------------------------------------ state --- */
static int ui_page, help, draw_tick;
static char status[192], build_text[192];
static int status_len, status_pending, build_pending;
static float level_l, level_r;
static float ui_vals[P_COUNT];
static uint8_t up_data[FO_FILE_MAX]; /* upload staging; large but only touched by events */
static int up_active, up_len, up_off, up_slot, up_kind;
static char up_name[FO_NAME_LEN];

static int append_txt(char *d, int at, int cap, const char *s)
{
  int i = 0;
  if (at < 0) at = 0;
  while (s[i] && at + i < cap - 1) { d[at + i] = s[i]; i++; }
  d[at + i] = 0;
  return at + i;
}
static int append_int(char *d, int at, int cap, int v)
{
  char t[24]; int n = 0, neg = v < 0;
  unsigned u = (unsigned)(neg ? -v : v);
  if (!u) t[n++] = '0';
  while (u) { t[n++] = (char)('0' + u % 10); u /= 10; }
  if (neg) t[n++] = '-';
  while (n > 0 && at < cap - 1) d[at++] = t[--n];
  d[at] = 0;
  return at;
}
static uint32_t rd32(const uint8_t *p)
{ return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24); }
static float clampf(float v, float lo, float hi) { return v < lo ? lo : v > hi ? hi : v; }
static int selected_preset(void)
{
  int n = fo_preset_count();
  int v = (int)(vibe_param_get(P_SELECT) + 0.5f);
  if (n <= 0) return 0;
  if (v < 0) v = 0;
  if (v >= n) v = n - 1;
  return v;
}
static void set_param(int id, float v)
{
  if (id < 0 || id >= P_COUNT) return;
  v = clampf(v, PARAMS[id].min, PARAMS[id].max);
  if (PARAMS[id].steps > 1) {
    float st = (PARAMS[id].max - PARAMS[id].min) / (float)(PARAMS[id].steps - 1);
    v = PARAMS[id].min + st * (float)(int)((v - PARAMS[id].min) / st + 0.5f);
  }
  vibe_param_set(id, v);
  ui_vals[id] = v;
}
static void nudge(int id, float dir)
{
  float v = vibe_param_get(id), step;
  if (PARAMS[id].steps > 1) {
    float st = (PARAMS[id].max - PARAMS[id].min) / (float)(PARAMS[id].steps - 1);
    v += dir * st;
    if (v > PARAMS[id].max) v = PARAMS[id].min;
    if (v < PARAMS[id].min) v = PARAMS[id].max;
  } else {
    step = (PARAMS[id].max - PARAMS[id].min) * 0.02f;
    if (step < 0.001f) step = 0.001f;
    v += dir * step;
  }
  set_param(id, v);
}

static void store_state(void)
{
  int32_t buf[P_COUNT];
  int i;
  for (i = 0; i < P_COUNT; i++) buf[i] = (int32_t)(vibe_param_get(i) * 1000.0f + 0.5f);
  vibe_store_write("fizz/state.bin", buf, (int)sizeof buf);
}
static void load_state(void)
{
  int32_t buf[P_COUNT];
  int n = vibe_store_read("fizz/state.bin", buf, (int)sizeof buf);
  if (n != (int)sizeof buf) return;
  for (int i = 0; i < P_COUNT; i++) {
    float v = (float)buf[i] * 0.001f;
    v = clampf(v, PARAMS[i].min, PARAMS[i].max);
    if (PARAMS[i].steps > 1) {
      float st = (PARAMS[i].max - PARAMS[i].min) / (float)(PARAMS[i].steps - 1);
      v = PARAMS[i].min + st * (float)(int)((v - PARAMS[i].min) / st + 0.5f);
    }
    vibe_param_set(i, v);
    ui_vals[i] = v;
  }
}

/* -------------------------------------------------------------- editor I/O -- */
/* One SDK outbox holds one message, so collect a whole reply and post it once.
 * The first byte 0x02 is sent only once; the Editor splits on newlines. */
static char ed_batch[4096]; static int ed_batch_len;

static void ed_flush(void)
{
  if (ed_batch_len <= 1) { ed_batch_len = 0; return; }
  if (vibe_editor_post((const uint8_t *)ed_batch, ed_batch_len) >= 0) ed_batch_len = 0;
}
static void ed_line(const char *cmd, const char *text)
{
  char b[256]; int n = 0, i;
  n = append_txt(b, n, (int)sizeof b, cmd);
  if (text && *text) { b[n++] = ' '; n = append_txt(b, n, (int)sizeof b, text); }
  b[n++] = '\n';
  if (ed_batch_len == 0) { ed_batch[0] = 0x02; ed_batch_len = 1; }
  if (ed_batch_len + n > (int)sizeof ed_batch) { ed_flush(); ed_batch[0] = 0x02; ed_batch_len = 1; }
  for (i = 0; i < n; i++) ed_batch[ed_batch_len + i] = b[i];
  ed_batch_len += n;
}
static void ed_int_line(const char *cmd, int a, int b, int c, const char *tail)
{
  char t[192]; int n = 0;
  n = append_int(t, n, (int)sizeof t, a);
  if (b >= 0) { t[n++] = ' '; n = append_int(t, n, (int)sizeof t, b); }
  if (c >= 0) { t[n++] = ' '; n = append_int(t, n, (int)sizeof t, c); }
  if (tail && *tail) { t[n++] = ' '; n = append_txt(t, n, (int)sizeof t, tail); }
  ed_line(cmd, t);
}
static void post_status(void);

static void build_status(void)
{
  int n = 0;
  n = append_txt(status, n, (int)sizeof status, fo_package_name());
  n = append_txt(status, n, (int)sizeof status, " | ");
  n = append_txt(status, n, (int)sizeof status, fo_preset_name(selected_preset()));
  n = append_txt(status, n, (int)sizeof status, " | voices ");
  n = append_int(status, n, (int)sizeof status, fo_voice_count());
  n = append_txt(status, n, (int)sizeof status, " / 32 | samples ");
  n = append_int(status, n, (int)sizeof status, fo_sample_count());
  n = append_txt(status, n, (int)sizeof status, " | files ");
  n = append_int(status, n, (int)sizeof status, fo_slot_count());
  if (fo_pad_play()) n = append_txt(status, n, (int)sizeof status, " | pad play");
  n = append_txt(status, n, (int)sizeof status, " | ready");
  status_len = n;
  status_pending = 1;
}
static void try_build(void)
{
  int r, i;
  for (i = 0; i < 200; i++) { r = fo_build(build_text, (int)sizeof build_text); if (r != -2) break; }
  if (r == -2) {
    build_pending = 1;
    ed_line("STATUS", "waiting for the audio flow, then building");
    return;
  }
  build_pending = 0;
  if (r < 0) { ed_line("ERROR", build_text); return; }
  set_param(P_SELECT, 0.0f);
  build_status();
  ed_line("OK", build_text);
  post_status();
  store_state();
}
static void post_status(void)
{
  int i;
  build_status();
  ed_line("PKG", fo_package_name());
  ed_line("STATUS", status);
  for (i = 0; i < FO_MAX_SLOTS; i++) {
    if (!fo_slot_kind(i)) continue;
    char t[96]; int n = 0;
    n = append_int(t, n, (int)sizeof t, i);
    t[n++] = ' '; n = append_int(t, n, (int)sizeof t, fo_slot_kind(i));
    t[n++] = ' '; n = append_txt(t, n, (int)sizeof t, fo_slot_name(i));
    ed_line("FILE", t);
  }
  for (i = 0; i < fo_instr_count(); i++)
    ed_int_line("INSTR", i, fo_instr_regions(i), fo_instr_samples(i), fo_instr_name(i));
  for (i = 0; i < fo_preset_count(); i++) ed_int_line("PRESET", i, -1, -1, fo_preset_name(i));
  for (i = 0; i < fo_sample_count(); i++) ed_int_line("SAMPLE", i, -1, -1, fo_sample_name(i));
  ed_int_line("SELECTED", selected_preset(), -1, -1, 0);
}

/* ------------------------------------------------------------ media loader -- */
enum { M_IDLE, M_LIST, M_READ, M_STORE };
static char m_dirs[8][VIBE_MEDIA_PATH], m_files[64][VIBE_MEDIA_PATH];
static int n_dirs, dir_at, n_files, file_at;
static int m_state, m_skip, m_slot, m_kind, m_len;
static char m_name[FO_NAME_LEN];

static int media_kind(const char *path, char *base, int cap)
{
  const char *p = path, *dot = 0, *b = path;
  for (; *p; p++) if (*p == '/' || *p == '\\') b = p + 1;
  for (p = b; *p; p++) if (*p == '.') dot = p;
  if (!dot) return 0;
  {
    int i = 0;
    while (b[i] && b[i] != '.' && i < cap - 1) { base[i] = b[i]; i++; }
    base[i] = 0;
  }
  {
    const char *e = dot + 1;
    if (e[0] == 's' || e[0] == 'S') {
      if (e[1] == 'f' || e[1] == 'F') return FO_KIND_SFZ;
    }
    if (e[0] == 'j' || e[0] == 'J') return FO_KIND_JSON;
    if (e[0] == 'w' || e[0] == 'W' || e[0] == 'm' || e[0] == 'M' ||
        e[0] == 'f' || e[0] == 'F' || e[0] == 'o' || e[0] == 'O') return FO_KIND_WAV;
  }
  return 0;
}
static int push_dir(const char *path)
{
  int i;
  if (n_dirs >= 8) return 0;
  for (i = 0; path[i] && i < VIBE_MEDIA_PATH - 1; i++) m_dirs[n_dirs][i] = path[i];
  m_dirs[n_dirs][i] = 0;
  n_dirs++;
  return 1;
}
static int push_file(const char *path)
{
  char base[FO_NAME_LEN]; int kind;
  if (n_files >= 64) return 0;
  kind = media_kind(path, base, (int)sizeof base);
  if (!kind) return 0;
  for (int i = 0; path[i] && i < VIBE_MEDIA_PATH - 1; i++) m_files[n_files][i] = path[i];
  m_files[n_files][VIBE_MEDIA_PATH - 1] = 0;
  n_files++;
  return 1;
}
static void media_start(const char *path)
{
  if (!path || !*path) return;
  fo_slots_clear();
  n_dirs = dir_at = n_files = file_at = m_skip = m_slot = 0;
  m_state = M_LIST;
  push_dir(path);
  ed_line("STATUS", "loading package from media");
  build_status();
}
static void media_fail(const char *why)
{
  m_state = M_IDLE;
  ed_line("ERROR", why);
  build_status();
  ed_line("STATUS", status);
}
static void work(void)
{
  int i;
  if (m_state == M_IDLE) return;
  if (m_state == M_LIST) {
    static char buf[4096];
    int r, nlines = 0;
    if (dir_at >= n_dirs) {
      if (!n_files) { media_fail("no SFZ or WAV files at that media path"); return; }
      m_state = M_READ; m_skip = 0; file_at = 0; m_len = 0;
      return;
    }
    r = vibe_media_list(m_dirs[dir_at], m_skip, buf, (int)sizeof buf - 1);
    if (r == VIBE_MEDIA_PENDING) return;
    if (r < 0) {
      dir_at++; m_skip = 0;
      return;
    }
    buf[r] = 0;
    for (char *p = buf; *p && nlines < 64; ) {
      char *end = p; int len, isdir = *p == 'd';
      while (*end && *end != '\n') end++;
      len = (int)(end - p);
      if (len > 3 && p[1] == '\t') {
        const char *name = p + 2;
        while (name < end && *name != '\t') name++;
        if (name < end) name++;
        if (name < end) {
          char full[VIBE_MEDIA_PATH]; int n = 0;
          for (i = 0; m_dirs[dir_at][i] && n < VIBE_MEDIA_PATH - 1; i++) full[n++] = m_dirs[dir_at][i];
          if (n > 0 && full[n - 1] != '/') full[n++] = '/';
          for (i = 0; name[i] && n < VIBE_MEDIA_PATH - 1; i++) full[n++] = name[i];
          full[n] = 0;
          if (isdir) push_dir(full); else push_file(full);
        }
      }
      nlines++;
      if (!*end) break;
      p = end + 1;
    }
    if (r > 0 && buf[r - 1] != '\n') { m_skip += nlines; }
    else { dir_at++; m_skip = 0; }
    return;
  }
  if (m_state == M_READ) {
    static uint8_t chunk[8192];
    int reads;
    if (file_at >= n_files) { m_state = M_IDLE; try_build(); return; }
    {
      char base[FO_NAME_LEN]; int kind = media_kind(m_files[file_at], base, (int)sizeof base);
      if (kind != m_kind && m_len == 0) {
        m_kind = kind;
        for (i = 0; base[i] && i < FO_NAME_LEN - 1; i++) m_name[i] = base[i];
        m_name[i] = 0;
      }
    }
    if (m_len == 0) m_kind = media_kind(m_files[file_at], m_name, (int)sizeof m_name);
    reads = 0;
    while (reads < 16 && m_state == M_READ) {
      int want = (int)sizeof chunk;
      if (m_len + want > FO_FILE_MAX) want = FO_FILE_MAX - m_len;
      if (want <= 0) { media_fail("media file exceeds the 4 MB per-file limit"); return; }
      {
        int r = vibe_media_read(m_files[file_at], (uint64_t)m_len, chunk, want);
        if (r == VIBE_MEDIA_PENDING) break;
        if (r < 0) { media_fail("a media file disappeared while reading"); return; }
        if (r == 0) { m_state = M_STORE; break; }
        for (i = 0; i < r; i++) up_data[m_len + i] = chunk[i];
        m_len += r; reads++;
      }
    }
    return;
  }
  if (m_state == M_STORE) {
    if (!m_kind || m_slot >= FO_MAX_SLOTS) { media_fail("too many files or a bad media file"); return; }
    if (fo_slot_put(m_slot, m_kind, m_name, up_data, m_len) < 0) { media_fail("firmware storage is full"); return; }
    m_slot++; file_at++; m_len = 0; m_kind = 0;
    if (file_at >= n_files) { m_state = M_IDLE; try_build(); }
    else m_state = M_READ;
  }
}

/* --------------------------------------------------------------- protocol --- */
static void handle_editor(const uint8_t *p, int n)
{
  if (!p || n < 1) return;
  switch (p[0]) {
    case 0x10: {
      int nl;
      if (n < 8) { ed_line("ERROR", "bad upload header"); return; }
      up_len = (int)rd32(p + 1);
      if (p[5] >= FO_MAX_SLOTS || p[6] < 1 || p[6] > 3 || p[7] < 1 ||
          n < 8 + p[7] || up_len < 1 || up_len > FO_FILE_MAX) {
        up_active = 0;
        ed_line("ERROR", "bad upload header");
        return;
      }
      if (p[5] == 0) fo_slots_clear();
      up_slot = p[5]; up_kind = p[6]; nl = p[7];
      if (nl > FO_NAME_LEN - 1) nl = FO_NAME_LEN - 1;
      for (int i = 0; i < nl; i++) {
        char c = (char)p[8 + i];
        if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
              (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-')) c = '_';
        up_name[i] = c;
      }
      up_name[nl] = 0;
      up_off = 0; up_active = 1;
      ed_line("OK", "upload ready");
      break;
    }
    case 0x11: {
      int off, len;
      if (!up_active || n < 5) return;
      off = (int)rd32(p + 1); len = n - 5;
      if (len < 1 || off != up_off || off + len > up_len || len > 2048 || off + len > FO_FILE_MAX) {
        up_active = 0;
        ed_line("ERROR", "bad upload chunk");
        return;
      }
      for (int i = 0; i < len; i++) up_data[off + i] = p[5 + i];
      up_off += len;
      break;
    }
    case 0x12:
      if (!up_active || up_off != up_len) { ed_line("ERROR", "upload incomplete"); return; }
      if (fo_slot_put(up_slot, up_kind, up_name, up_data, up_len) < 0) {
        ed_line("ERROR", "firmware storage is full");
      } else {
        up_active = 0;
        ed_int_line("FILE", up_slot, up_kind, -1, up_name);
        ed_line("OK", "stored");
      }
      up_active = 0;
      break;
    case 0x13:
      up_active = 0;
      ed_line("OK", "upload cancelled");
      break;
    case 0x14:
      try_build();
      break;
    case 0x20:
      if (build_pending) try_build();
      if (m_state != M_IDLE) {
        char t[32]; int k = 0;
        t[k++] = 'l'; t[k++] = 'o'; t[k++] = 'a'; t[k++] = 'd'; t[k++] = 'i'; t[k++] = 'n'; t[k++] = 'g';
        t[k++] = ' '; t[k++] = 'm'; t[k++] = 'e'; t[k++] = 'd'; t[k++] = 'i'; t[k++] = 'a';
        t[k] = 0;
        ed_line("STATUS", t);
      } else post_status();
      break;
    case 0x21:
      if (n >= 5) {
        int idx = (int)rd32(p + 1), max = fo_preset_count() - 1;
        if (max < 0) max = 0;
        if (idx > max) idx = max;
        if (idx < 0) idx = 0;
        set_param(P_SELECT, (float)idx);
        ed_int_line("SELECTED", idx, -1, -1, 0);
        store_state();
      }
      break;
    case 0x22:
      fo_queue(FO_MSG_ALL_OFF, 0, 0);
      ed_line("OK", "notes off");
      break;
    case 0x23:
      fo_queue(FO_MSG_ON, n >= 2 ? p[1] : 60, n >= 3 ? p[2] : 100);
      ed_line("OK", "audition");
      break;
    case 0x30: {
      char path[VIBE_MEDIA_PATH]; int len = n - 1;
      if (len < 1 || len >= (int)sizeof path) { ed_line("ERROR", "bad media path"); return; }
      for (int i = 0; i < len; i++) path[i] = (char)p[1 + i];
      path[len] = 0;
      media_start(path);
      break;
    }
    default: break;
  }
}

/* ------------------------------------------------------------------ events - */
static void audition(void) { fo_queue(FO_MSG_ON, 60, 100); }
static void panic(void) { fo_queue(FO_MSG_ALL_OFF, 0, 0); }

static void handle_button(int id)
{
  if (id == VB_HOME) { help = !help; return; }
  if (id == VB_RETURN) { help = 0; ui_page = UI_SOUND; return; }
  if (help) return;
  if (id == VB_REC) { panic(); return; }
  if (id == VB_STOP) { panic(); return; }
  if (id == VB_PLAY) { audition(); return; }
  if (id >= VB_F1 && id <= VB_F6) {
    switch (id - VB_F1) {
      case 0: ui_page = (ui_page + 1) % UI_N; break;
      case 1: set_param(P_SELECT, vibe_param_get(P_SELECT) + 1.0f); break;
      case 2: audition(); break;
      case 3: panic(); break;
      case 4: help = 1; break;
      case 5: try_build(); break;
    }
    return;
  }
  if (id >= VB_K1 && id <= VB_K6) { ui_page = id - VB_K1; return; }
}

static void handle_event(const vibe_event *e)
{
  int fid, r;
  if (!e) return;
  r = vf_event(e, &fid);                          /* FOCUS first */
  if (r == VF_PRESS) {
    if (fid == 900) audition();
    else if (fid == 901) panic();
    return;
  }
  if (r == VF_CHANGED) {
    if (fid >= 0 && fid < P_COUNT) set_param(fid, ui_vals[fid]);
    return;
  }
  if (r != VF_NO) return;

  if (e->type == VIBE_EV_NOTE) {
    fo_queue(e->down ? FO_MSG_ON : FO_MSG_OFF, (int)e->id,
             (int)(e->value * 127.0f + 0.5f));
    return;
  }
  if (e->type == VIBE_EV_KEY) {
    int note = 48 + (int)e->id;
    fo_queue(e->down ? FO_MSG_ON : FO_MSG_OFF, note, e->down ? 100 : 0);
    return;
  }
  if (e->type == VIBE_EV_PAD) {
    int p = (int)e->id;
    if (p < 0 || p >= 16) return;
    if (p == 15) { if (e->down) panic(); return; }
    if (fo_pad_play()) {
      /* Percussion packages opt in with "pads": true in fizzy.json: pads 1-15
       * become drum hits on notes 36-50. Preset selection stays on F2 and A/B. */
      int note = 36 + p;
      int vel = (int)(e->value * 127.0f + 0.5f);
      if (vel < 1) vel = 1;
      fo_queue(e->down ? FO_MSG_ON : FO_MSG_OFF, note, e->down ? vel : 0);
      return;
    }
    if (e->down) set_param(P_SELECT, (float)p);
    return;
  }
  if (e->type == VIBE_EV_PARAM) {
    if (e->id == P_SELECT) build_status();
    return;
  }
  if (e->type == VIBE_EV_EDITOR_DATA) {
    handle_editor(vibe_editor_data(), (int)e->value);
    return;
  }
  if (e->type == VIBE_EV_SAVE) { store_state(); return; }
  if (e->type == VIBE_EV_BUTTON) { if (e->down) handle_button((int)e->id); return; }
  if (e->type == VIBE_EV_ENCODER) {
    float d = e->value;
    if (d == 0.0f) return;
    if (e->id == VE_A) { nudge(P_VOLUME, d); return; }
    if (e->id == VE_B) { set_param(P_SELECT, vibe_param_get(P_SELECT) + (d > 0 ? 1.0f : -1.0f)); return; }
    if (e->id >= VE_1 && e->id <= VE_4) {
      int p = PAGE_PARAM[ui_page][e->id - VE_1];
      if (p >= 0) nudge(p, d);
      else if (d > 0) ui_page = (ui_page + 1) % UI_N;
      else ui_page = (ui_page + UI_N - 1) % UI_N;
      return;
    }
    return;
  }
  if (e->type == VIBE_EV_MEDIA) {
    if (e->id == VIBE_MEDIA_EV_READY || e->id == VIBE_MEDIA_EV_SOURCES) work();
  }
}
void vibe_on_event(const vibe_event *e)
{
  handle_event(e);
  ed_flush();
}

/* ------------------------------------------------------------------- audio -- */
void vibe_on_audio(const float *in, float *out_l, float *out_r, int frames)
{
  fo_ctrl_t c;
  (void)in;
  c.volume    = vibe_param_get(P_VOLUME);
  c.attack    = vibe_param_get(P_ATTACK);
  c.release   = vibe_param_get(P_RELEASE);
  c.sustain   = vibe_param_get(P_SUSTAIN);
  c.velocity  = vibe_param_get(P_VELOCITY);
  c.tune      = vibe_param_get(P_TUNE);
  c.spread    = vibe_param_get(P_SPREAD);
  c.octave    = (int)(vibe_param_get(P_OCTAVE) + 0.5f);
  c.transpose = (int)(vibe_param_get(P_TRANSPOSE) + 0.5f);
  c.instr     = selected_preset();   /* resolved to an instrument inside the audio lock */
  fo_render(out_l, out_r, frames, &c);
}

/* -------------------------------------------------------------------- draw -- */
static void draw_param(int id, int y, int idx)
{
  char val[32];
  float v, t;
  ui_vals[id] = vibe_param_get(id);
  v = ui_vals[id];
  vg_round(10, y, 460, 36, 4, idx & 1 ? 0x231A13 : 0x1B1510);
  vg_text(PARAMS[id].name, 20, y + 3, VG_SMALL, 0xC9B9A6, VG_LEFT);
  if (PARAMS[id].steps > 1) {
    int k = (int)(v + 0.5f);
    char t2[16];
    append_int(t2, 0, (int)sizeof t2, k);
    vg_text(t2, 460, y + 2, VG_MED, 0xFFD299, VG_RIGHT);
  } else {
    vg_text(vg_ftoa(v, 2, val), 460, y + 2, VG_BIG, 0xFFD299, VG_RIGHT);
  }
  t = (v - PARAMS[id].min) / (PARAMS[id].max - PARAMS[id].min);
  if (t < 0.0f) t = 0.0f; else if (t > 1.0f) t = 1.0f;
  vg_rect(20, y + 27, 440, 4, 0x100C08);
  vg_rect(20, y + 27, (int)(440.0f * t), 4, 0xFF8A1F);
  vf_value(id, 10, y, 460, 36, &ui_vals[id], PARAMS[id].min, PARAMS[id].max,
           (PARAMS[id].max - PARAMS[id].min) / 100.0f);
}
void vibe_on_draw(uint16_t *fb)
{
  int i;
  vg_begin(fb);
  if (help) { vg_controls("FIZZY ORANGE", HELP, (int)(sizeof HELP / sizeof HELP[0] / 2)); return; }
  work();
  vg_clear(0x0D0906);
  vg_rect(0, 0, 480, 44, 0x1B1510);
  vg_rect(0, 43, 480, 2, 0xFF8A1F);
  vg_text("FIZZY ORANGE", 12, 5, VG_BIG, 0xFF8A1F, VG_LEFT);
  vg_text(fo_package_name(), 14, 27, VG_SMALL, 0xC9B9A6, VG_LEFT);
  vg_text(PAGE_NAME[ui_page], 466, 12, VG_BOLD, 0xFFC857, VG_RIGHT);
  vg_text(fo_preset_name(selected_preset()), 466, 28, VG_SMALL, 0xFFD299, VG_RIGHT);

  vf_begin();
  for (i = 0; i < 4; i++) {
    int p = PAGE_PARAM[ui_page][i];
    if (p >= 0) draw_param(p, 54 + i * 43, i);
    else {
      char t[96]; int n = 0;
      vg_round(10, 54 + i * 43, 460, 36, 4, 0x1B1510);
      if (ui_page == UI_FILES) {
        n = append_txt(t, n, (int)sizeof t, "Editor: LOAD PATH with a folder of SFZ + 16-bit WAV");
        vg_text(t, 20, 60 + i * 43, VG_SMALL, 0xC9B9A6, VG_LEFT);
        vg_text(i == 0 ? build_text : (i == 1 ? "ZIPs: use the Editor page" :
                (i == 2 ? "F6 rebuilds stored files" : "Files stay after restart")),
                20, 76 + i * 43, VG_SMALL, 0xFFD299, VG_LEFT);
      } else {
        n = append_txt(t, n, (int)sizeof t, "HOME shows all controls; F6 rebuilds a package");
        vg_text(t, 20, 60 + i * 43, VG_SMALL, 0xC9B9A6, VG_LEFT);
      }
    }
  }

  {
    char t[12]; int n = 0;
    float ml = level_l, mr = level_r;
    if (ml > 1.0f) ml = 1.0f; if (mr > 1.0f) mr = 1.0f;
    vg_text("L", 14, 232, VG_SMALL, 0x8A7C6B, VG_LEFT);
    vg_rect(28, 234, 190, 7, 0x1B1510);
    vg_rect(28, 234, (int)(190.0f * ml), 7, ml > 0.85f ? 0xFF4D3D : 0xFFB41F);
    vg_text("R", 232, 232, VG_SMALL, 0x8A7C6B, VG_LEFT);
    vg_rect(246, 234, 190, 7, 0x1B1510);
    vg_rect(246, 234, (int)(190.0f * mr), 7, mr > 0.85f ? 0xFF4D3D : 0xFFB41F);
    vg_text("VOICES", 466, 232, VG_SMALL, 0x8A7C6B, VG_RIGHT);
    n = append_int(t, n, (int)sizeof t, fo_voice_count());
    vg_text(t, 466, 244, VG_BOLD, 0xFFC857, VG_RIGHT);
  }
  vg_rect(10, 258, 460, 20, 0x231A13);
  vg_text(status, 18, 260, VG_SMALL, 0xFFD299, VG_LEFT);
  vf_button(900, 418, 258, 48, 22);

  vg_rect(0, 282, 480, 2, 0xFF8A1F);
  for (i = 0; i < 6; i++) {
    vg_text(F_LABELS[i], 8 + i * 79, 286, VG_SMALL, i == 1 ? 0xFFC857 : 0x8A7C6B, VG_LEFT);
    vf_key(VB_F1 + i, 4 + i * 79, 283, 76, 18);
    vg_text(K_LABELS[i], 10 + i * 79, 304, VG_SMALL, 0xFF8A1F, VG_LEFT);
    vf_key(VB_K1 + i, 4 + i * 79, 301, 76, 18);
  }
  vf_end(0xFF8A1F);

  for (i = 0; i < 6; i++) { vibe_label(VB_F1 + i, F_LABELS[i]); vibe_label(VB_K1 + i, K_LABELS[i]); }
  vibe_link_page(PAGE_NAME[ui_page]);
  for (i = 0; i < 4; i++) {
    int p = PAGE_PARAM[ui_page][i];
    char v[24];
    if (p >= 0) {
      float x = (vibe_param_get(p) - PARAMS[p].min) / (PARAMS[p].max - PARAMS[p].min);
      if (x < 0.0f) x = 0.0f; else if (x > 1.0f) x = 1.0f;
      vg_ftoa(x, 2, v);
      vibe_link_encoder(VE_1 + i, PARAMS[p].name, v);
    } else vibe_link_encoder(VE_1 + i, "PAGE", PAGE_NAME[ui_page]);
  }
  for (i = 0; i < 16; i++) {
    if (i == 15) continue;
    vibe_led_pad(i, 0xFF8A1F, i == selected_preset() ? 0.55f : 0.08f);
  }
  vibe_led_pad(15, 0xFF4D3D, 0.25f);

  level_l = fo_level_l() * 0.001f;
  level_r = fo_level_r() * 0.001f;
  draw_tick++;
  if ((draw_tick & 7) == 0) build_status();
  if (status_pending && m_state == M_IDLE) ed_line("STATUS", status);
  ed_flush();
  if (ed_batch_len > 0) status_pending = 1;
}

/* -------------------------------------------------------------------- init -- */
void vibe_on_init(float sample_rate)
{
  int i, r;
  fo_init(sample_rate);
  fo_audio_reset(sample_rate);
  vibe_params(PARAMS, P_COUNT);
  vibe_param_groups(GROUPS);
  r = fo_build(build_text, (int)sizeof build_text);
  if (r < 0) { for (i = 0; build_text[i]; i++) { } }
  load_state();
  for (i = 0; i < P_COUNT; i++) {
    float v = vibe_param_get(i);
    if (v < PARAMS[i].min) v = PARAMS[i].min;
    if (v > PARAMS[i].max) v = PARAMS[i].max;
    set_param(i, v);
  }
  set_param(P_SELECT, (float)selected_preset());
  build_status();
  vibe_notes(VIBE_NOTES);
  vibe_keys_octave(2);
  vf_mode(VF_ON_HOLD);
  vf_clicks(1);
  ui_page = UI_SOUND;
  help = 0; draw_tick = 0; build_pending = 0;
  up_active = 0; m_state = M_IDLE;
  level_l = level_r = 0.0f;
  vibe_param_help(P_SELECT, "Select a preset from the uploaded SFZ package. Pads 1-15 do the same.");
  vibe_param_help(P_VOLUME, "Master output level for the sample voices.");
  vibe_param_help(P_ATTACK, "Multiplies every region's SFZ ampeg_attack time.");
  vibe_param_help(P_RELEASE, "Multiplies every region's SFZ ampeg_release time.");
  vibe_param_help(P_SUSTAIN, "Blends the SFZ ampeg_sustain level; 0.5 leaves it unchanged.");
  vibe_param_help(P_TUNE, "Fine tune in cents, added to the SFZ tune / finetune opcodes.");
  vibe_param_help(P_SPREAD, "Widens alternate voices to the left and right.");
}
