/* FIZZY ORANGE -- SFZ subset parser, storage registry and sample voices.
 * Plain C, no heap, no libc: see sfz.h and docs/PROTOCOL.md.
 */
#include "vibe.h"
#include "vibe_dsp.h"
#include "sfz.h"

/* ------------------------------------------------------------------ helpers -- */
static void z8(uint8_t *d, int n) { while (n-- > 0) *d++ = 0; }
static void cp8(uint8_t *d, const uint8_t *s, int n) { while (n-- > 0) *d++ = *s++; }
static int slen(const char *s) { int n = 0; while (s[n]) n++; return n; }
static int seq(const char *a, const char *b)
{ while (*a && *b && *a == *b) { a++; b++; } return *a == 0 && *b == 0; }
static int seqn(const char *a, const char *b, int n)
{ while (n-- > 0) { if (*a != *b) return 0; if (!*a) break; a++; b++; } return 1; }
static char upper(char c) { return c >= 'a' && c <= 'z' ? (char)(c - 32) : c; }
static int caseq(const char *a, const char *b)
{
  while (*a && *b) { if (upper(*a) != upper(*b)) return 0; a++; b++; }
  return *a == 0 && *b == 0;
}
static int is_space(char c) { return c == ' ' || c == '\t' || c == '\r' || c == '\n'; }
static int is_digit(char c) { return c >= '0' && c <= '9'; }

static float fnum(const char *s)
{
  float v = 0.0f, frac = 0.1f;
  int neg = 0;
  if (*s == '-' || *s == '+') { neg = *s == '-'; s++; }
  while (is_digit(*s)) { v = v * 10.0f + (float)(*s - '0'); s++; }
  if (*s == '.') {
    s++;
    while (is_digit(*s)) { v += (float)(*s - '0') * frac; frac *= 0.1f; s++; }
  }
  return neg ? -v : v;
}
static int inum(const char *s) { return (int)fnum(s); }

/* A note name (C4 = 60), or a plain MIDI number. */
static int note_num(const char *s)
{
  int c, v, oct = 0, i = 1;
  static const int SEMI[7] = { 0, 2, 4, 5, 7, 9, 11 };
  if (is_digit(*s) || *s == '-') return inum(s);
  c = upper(*s) - 'A';
  if (c < 0 || c > 6) return -1;
  v = SEMI[c];
  if (s[i] == '#') { v++; i++; }
  else if (s[i] == 'b' || s[i] == 'B') { v--; i++; }
  oct = inum(s + i) + 1;
  v %= 12; if (v < 0) v += 12;
  return v + oct * 12;
}
static int text_out(char *d, int at, int cap, const char *s)
{
  int i = 0;
  if (at < 0) at = 0;
  while (s[i] && at + i < cap - 1) { d[at + i] = s[i]; i++; }
  d[at + i] = 0;
  return at + i;
}
static int int_out(char *d, int at, int cap, int v)
{
  char t[16]; int n = 0, neg = v < 0;
  unsigned u = (unsigned)(neg ? -v : v);
  if (!u) t[n++] = '0';
  while (u) { t[n++] = (char)('0' + u % 10); u /= 10; }
  if (neg) t[n++] = '-';
  while (n > 0) { d[at++] = t[--n]; if (at >= cap - 1) break; }
  d[at] = 0;
  return at;
}

/* ------------------------------------------------------------------ storage -- */
static uint8_t scratch[FO_FILE_MAX];
static uint8_t put_buf[FO_FILE_MAX + 80];
static int8_t slot_used[FO_MAX_SLOTS];
static uint8_t slot_kind[FO_MAX_SLOTS];
static int slot_len[FO_MAX_SLOTS];
static char slot_name[FO_MAX_SLOTS][FO_NAME_LEN];
static char package_name[FO_LABEL_LEN] = "FIZZY ORANGE";
static int pad_play; /* optional manifest flag: pads play notes 36..50 */

static const char *store_name(int slot, char *out, int cap)
{
  int n = 0;
  const char *p = "fizz/f";
  while (*p && n < cap - 1) out[n++] = *p++;
  n = int_out(out, n, cap, slot);
  if (n < cap - 1) out[n++] = '.';
  if (n < cap - 1) out[n++] = 'b';
  if (n < cap - 1) out[n++] = 'i';
  if (n < cap - 1) out[n++] = 'n';
  out[n] = 0;
  return out;
}
static int read_slot(int slot, uint8_t *buf, int cap)
{
  char name[48];
  return vibe_store_read(store_name(slot, name, sizeof name), buf, cap);
}
static int find_slot_by_name(const char *name, int kind)
{
  int i;
  const char *base = name;
  for (const char *p = name; *p; p++) if (*p == '/' || *p == '\\') base = p + 1;
  for (i = 0; i < FO_MAX_SLOTS; i++) {
    const char *b = slot_name[i];
    if (!slot_used[i] || (kind && slot_kind[i] != kind)) continue;
    for (const char *p = slot_name[i]; *p; p++) if (*p == '/' || *p == '\\') b = p + 1;
    if (caseq(base, b)) return i;
  }
  return -1;
}

static void fo_scan_store(void)
{
  char names[8192];
  int n = vibe_store_list(names, (int)sizeof names - 1);
  if (n <= 0) return;
  names[n] = 0;
  for (char *p = names; *p;) {
    char *end = p;
    while (*end && *end != '\n') end++;
    int len = (int)(end - p);
    if (len > 5 && seqn(p, "fizz/f", 6)) {
      char *q = p + 6; int slot = 0, digits = 0;
      while (is_digit(*q)) { slot = slot * 10 + (*q - '0'); q++; digits++; }
      if (digits && slot < FO_MAX_SLOTS && seqn(q, ".bin", 4)) {
        uint8_t hd[80]; char nm[48];
        const char *path = store_name(slot, nm, sizeof nm);
        int got = vibe_store_read_at(path, 0, hd, 8);
        if (got == 8 && hd[0] == 'F' && hd[1] == 'Z' && hd[2] == '1') {
          int kind = hd[3], nl = hd[4];
          if (kind >= 1 && kind <= 3 && nl > 0 && nl < FO_NAME_LEN) {
            int size = vibe_store_size(path);
            got = vibe_store_read_at(path, 8, hd, nl);
            if (got == nl && size > 8 + nl) {
              slot_used[slot] = 1; slot_kind[slot] = (uint8_t)kind;
              slot_len[slot] = size - 8 - nl;
              for (int i = 0; i < nl; i++) slot_name[slot][i] = (char)hd[i];
              slot_name[slot][nl] = 0;
            }
          }
        }
      }
    }
    if (!*end) break;
    p = end + 1;
  }
}

uint8_t *fo_scratch(void) { return scratch; }
int fo_scratch_size(void) { return (int)sizeof scratch; }

int fo_slot_put(int slot, int kind, const char *name, const uint8_t *data, int len)
{
  char nm[48]; int nl, total;
  if (slot < 0 || slot >= FO_MAX_SLOTS || kind < 1 || kind > 3 || !name || !data) return -1;
  if (len < 0 || len > FO_FILE_MAX) return -1;
  nl = slen(name); if (nl > FO_NAME_LEN - 1) nl = FO_NAME_LEN - 1;
  total = 8 + nl + len;
  put_buf[0] = 'F'; put_buf[1] = 'Z'; put_buf[2] = '1'; put_buf[3] = (uint8_t)kind;
  put_buf[4] = (uint8_t)nl; put_buf[5] = put_buf[6] = put_buf[7] = 0;
  for (int i = 0; i < nl; i++) put_buf[8 + i] = (uint8_t)name[i];
  cp8(put_buf + 8 + nl, data, len);
  if (vibe_store_write(store_name(slot, nm, sizeof nm), put_buf, total) < 0) return -1;
  slot_used[slot] = 1; slot_kind[slot] = (uint8_t)kind; slot_len[slot] = len;
  for (int i = 0; i < nl; i++) slot_name[slot][i] = name[i];
  slot_name[slot][nl] = 0;
  return 0;
}
void fo_slots_clear(void)
{
  int i;
  for (i = 0; i < FO_MAX_SLOTS; i++) {
    char nm[48];
    if (!slot_used[i]) continue;
    vibe_store_delete(store_name(i, nm, sizeof nm));
    slot_used[i] = 0; slot_len[i] = 0; slot_name[i][0] = 0;
  }
}
int fo_slot_count(void) { int i, n = 0; for (i = 0; i < FO_MAX_SLOTS; i++) if (slot_used[i]) n++; return n; }
int fo_slot_at(int i) { int j, n = 0; for (j = 0; j < FO_MAX_SLOTS; j++) if (slot_used[j]) { if (n == i) return j; n++; } return -1; }
int fo_slot_kind(int slot) { return slot >= 0 && slot < FO_MAX_SLOTS && slot_used[slot] ? slot_kind[slot] : 0; }
int fo_slot_len(int slot) { return slot >= 0 && slot < FO_MAX_SLOTS && slot_used[slot] ? slot_len[slot] : 0; }
const char *fo_slot_name(int slot) { return slot >= 0 && slot < FO_MAX_SLOTS && slot_used[slot] ? slot_name[slot] : ""; }
const char *fo_package_name(void) { return package_name; }
int fo_pad_play(void) { return pad_play; }

/* ------------------------------------------------------------------ tables -- */
static fo_sample_t samples[FO_MAX_SAMPLES];
static fo_region_t regions[FO_MAX_REGIONS];
static fo_instr_t instrs[FO_MAX_INSTR];
static fo_preset_t presets[FO_MAX_PRESETS];
static int16_t *pcm;
static int16_t pcm_pool[FO_PCM_SIZE / 2];
static int pcm_used, nsamples, nregions, ninstr, npresets, unknown_ops;
static float build_sr = 48000.0f;

int fo_instr_count(void) { return ninstr; }
const char *fo_instr_name(int i) { return i >= 0 && i < ninstr ? instrs[i].name : "-"; }
int fo_instr_regions(int i) { return i >= 0 && i < ninstr ? instrs[i].count : 0; }
int fo_instr_samples(int i)
{
  uint64_t m = 0; int k, n = 0;
  if (i < 0 || i >= ninstr) return 0;
  for (k = instrs[i].first; k < instrs[i].first + instrs[i].count && k < nregions; k++) {
    int s = regions[k].sample_index;
    if (s >= 0 && s < 64) m |= 1ULL << s;
  }
  for (k = 0; k < 64; k++) if (m & (1ULL << k)) n++;
  return n;
}
int fo_preset_count(void) { return npresets; }
const char *fo_preset_name(int i) { return i >= 0 && i < npresets ? presets[i].name : "-"; }
int fo_preset_instr(int i)
{
  if (i < 0 || i >= npresets) return 0;
  if (presets[i].instr < 0 || presets[i].instr >= ninstr) return 0;
  return presets[i].instr;
}
int fo_sample_count(void) { return nsamples; }
const char *fo_sample_name(int i) { return i >= 0 && i < nsamples ? samples[i].name : "-"; }
int fo_unknown_opcodes(void) { return unknown_ops; }

static void reset_tables(void)
{
  z8((uint8_t *)samples, (int)sizeof samples);
  z8((uint8_t *)regions, (int)sizeof regions);
  z8((uint8_t *)instrs, (int)sizeof instrs);
  z8((uint8_t *)presets, (int)sizeof presets);
  pcm = pcm_pool;
  pcm_used = nsamples = nregions = ninstr = npresets = unknown_ops = 0;
}
static void defaults(fo_region_t *r)
{
  r->sample_index = -1; r->lokey = 0; r->hikey = 127; r->keycenter = 60;
  r->lovel = 0; r->hivel = 127; r->volume = 1.0f; r->pan = 0.0f; r->tune = 0.0f;
  r->start = 0; r->end = -1; r->loop_mode = FO_LOOP_NONE;
  r->loop_start = -1; r->loop_end = -1;
  r->attack = 0.005f; r->decay = 0.20f; r->sustain = 0.70f;
  r->release = 0.30f; r->hold = 0.0f;
  r->sample[0] = 0;
}

/* ------------------------------------------------------------------ JSON ---- */
/* The manifest is deliberately tiny: strings and the instruments/presets arrays. */
static int find_key(const char *s, const char *key)
{
  int kl = slen(key);
  for (const char *p = s; *p; p++) {
    if (!seqn(p, key, kl)) continue;
    const char *q = p + kl;
    if (*q == '"') q++;  /* JSON keys are quoted: "name": ... */
    while (*q == ' ' || *q == '\t' || *q == '\r' || *q == '\n') q++;
    if (*q == ':') return (int)(q + 1 - s);
  }
  return -1;
}
static int json_string_at(const char *s, int at, char *out, int cap)
{
  int n = 0;
  const char *p = s + at;
  while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
  if (*p != '"') { out[0] = 0; return 0; }
  p++;
  while (*p && *p != '"' && n < cap - 1) {
    if (*p == '\\' && p[1]) { p++; out[n++] = *p++; }
    else out[n++] = *p++;
  }
  out[n] = 0;
  return n;
}
static int json_string(const char *s, const char *key, char *out, int cap)
{
  int at = find_key(s, key);
  if (at < 0) { out[0] = 0; return 0; }
  return json_string_at(s, at, out, cap);
}
/* The only boolean in the manifest today is "pads": true. A list of pad notes
 * would mean the same thing – either way the firmware just enables pad play. */
static int json_flag(const char *s, const char *key)
{
  int at = find_key(s, key);
  const char *p;
  if (at < 0) return 0;
  p = s + at;
  while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
  if (seqn(p, "false", 5) || seqn(p, "0", 1)) return 0;
  return seqn(p, "true", 4) || seqn(p, "[", 1) || (*p >= '1' && *p <= '9');
}
static int json_range(const char *s, const char *key, const char **a, const char **b)
{
  int at = find_key(s, key);
  const char *p, *end;
  int depth;
  if (at < 0) return 0;
  p = s + at;
  while (*p && *p != '[' && *p != '{') p++;
  if (!*p) return 0;
  *a = p + 1;
  depth = 1; end = p + 1;
  while (*end && depth) {
    if (*end == '"') { end++; while (*end && *end != '"') { if (*end == '\\' && end[1]) end++; end++; } }
    if (*end == '[' || *end == '{') depth++;
    else if (*end == ']' || *end == '}') depth--;
    if (*end) end++;
  }
  *b = end - 1;
  return 1;
}
static int json_object(const char **p, const char *end, const char **a, const char **b)
{
  const char *q = *p, *open, *close;
  int depth;
  while (q < end && *q != '{') q++;
  if (q >= end) { *p = end; return 0; }
  open = q + 1; depth = 1; close = open;
  while (close < end && depth) {
    if (*close == '"') { close++; while (close < end && *close != '"') { if (*close == '\\' && close + 1 < end) close++; close++; } }
    if (*close == '{' || *close == '[') depth++;
    else if (*close == '}' || *close == ']') depth--;
    if (close < end) close++;
  }
  *a = open; *b = close - 1; *p = close;
  return 1;
}
static char lowercase(char c) { return c >= 'A' && c <= 'Z' ? (char)(c + 32) : c; }
static char uppercase_name(char c) { return c >= 'a' && c <= 'z' ? (char)(c - 32) : c; }

static int find_key_range(const char *a, const char *b, const char *key)
{
  int kl = slen(key);
  for (const char *p = a; p + kl < b; p++) {
    const char *q;
    if (!seqn(p, key, kl)) continue;
    q = p + kl;
    if (q < b && *q == '"') q++;
    while (q < b && is_space(*q)) q++;
    if (q < b && *q == ':') return (int)(q + 1 - a);
  }
  return -1;
}
static int json_string_range(const char *a, const char *b, const char *key, char *out, int cap)
{
  int at = find_key_range(a, b, key);
  if (at < 0) { out[0] = 0; return 0; }
  return json_string_at(a, at, out, cap);
}

/* ------------------------------------------------------------------ WAV ----- */
static int read_slot_into(int slot, uint8_t *buf, int cap)
{
  /* Stored slots are: FZ1 header (8 bytes) + name + payload. Callers always
   * want the payload – the manifest parser and the WAV decoder both expect it
   * to start at byte 0. */
  uint8_t hd[80];
  char name[48];
  const char *path = store_name(slot, name, sizeof name);
  int nl, n;
  if (vibe_store_read_at(path, 0, hd, 8) != 8 ||
      hd[0] != 'F' || hd[1] != 'Z' || hd[2] != '1') return -1;
  nl = hd[4];
  if (nl < 1 || nl >= FO_NAME_LEN) return -1;
  if (vibe_store_read_at(path, 8, hd, nl) != nl) return -1;
  n = vibe_store_read_at(path, 8 + nl, buf, cap);
  return n > 0 ? n : -1;
}
static uint32_t le32(const uint8_t *p)
{ return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24); }

static int decode_wav(int slot, int *out)
{
  uint8_t *b = scratch;
  int n = read_slot_into(slot, b, FO_FILE_MAX);
  int channels = 0, rate = 0, bits = 0, frames, data_at = -1, data_len = 0;
  int loop_start = -1, loop_end = -1, pos;
  if (n < 44 || !seqn((const char *)b, "RIFF", 4) || !seqn((const char *)b + 8, "WAVE", 4)) return -1;
  pos = 12;
  while (pos + 8 <= n) {
    uint32_t sz = le32(b + pos + 4);
    int len = (int)sz, body = pos + 8;
    if (len < 0 || body > n) break;
    if (len > n - body) len = n - body;
    if (seqn((const char *)b + pos, "fmt ", 4) && len >= 16) {
      int format = b[body] | (b[body + 1] << 8);
      channels = b[body + 2] | (b[body + 3] << 8);
      rate = (int)le32(b + body + 4);
      bits = b[body + 14] | (b[body + 15] << 8);
      if (format != 1 || bits != 16 || (channels != 1 && channels != 2)) return -1;
    } else if (seqn((const char *)b + pos, "data", 4)) {
      data_at = body; data_len = len;
    } else if (seqn((const char *)b + pos, "smpl", 4) && len >= 36) {
      uint32_t nloops = le32(b + body + 28);
      if (nloops > 0 && len >= 52) {
        loop_start = (int)le32(b + body + 40);
        loop_end = (int)le32(b + body + 44);
      }
    }
    int step = 8 + len + (len & 1);
    if (step <= 0) break;
    pos += step;
  }
  if (data_at < 0 || data_len < 2 || !channels || rate < 1000) return -1;
  frames = data_len / (channels * 2);
  if (frames < 2 || nsamples >= FO_MAX_SAMPLES) return -1;
  if (pcm_used + data_len > FO_PCM_SIZE) return -1;
  cp8((uint8_t *)(pcm + pcm_used), b + data_at, frames * channels * 2);
  {
    fo_sample_t *s = &samples[nsamples];
    s->data = pcm + pcm_used; s->frames = frames; s->channels = channels; s->rate = rate;
    s->loop_start = loop_start >= 0 && loop_start < frames ? loop_start : 0;
    s->loop_end = loop_end > s->loop_start && loop_end <= frames ? loop_end : frames;
    int nl = slen(slot_name[slot]); if (nl > FO_NAME_LEN - 1) nl = FO_NAME_LEN - 1;
    for (int i = 0; i < nl; i++) s->name[i] = slot_name[slot][i];
    s->name[nl] = 0;
  }
  pcm_used += frames * channels * 2;
  *out = nsamples++;
  return 0;
}
static int resolve_sample(const char *name)
{
  int slot = find_slot_by_name(name, FO_KIND_WAV);
  int idx;
  if (slot < 0) return -1;
  for (idx = 0; idx < nsamples; idx++) {
    const char *base = samples[idx].name;
    for (const char *p = samples[idx].name; *p; p++) if (*p == '/' || *p == '\\') base = p + 1;
    const char *want = name;
    for (const char *p = name; *p; p++) if (*p == '/' || *p == '\\') want = p + 1;
    if (caseq(base, want)) return idx;
  }
  idx = -1;
  if (decode_wav(slot, &idx) < 0) return -1;
  return idx;
}

/* ------------------------------------------------------------------ SFZ ----- */
static void apply_opcode(fo_region_t *r, const char *k, const char *v, int scope, int *unknown)
{
  if (seq(k, "sample")) { int n = slen(v); if (n > FO_NAME_LEN - 1) n = FO_NAME_LEN - 1; for (int i = 0; i < n; i++) r->sample[i] = v[i]; r->sample[n] = 0; }
  else if (seq(k, "key")) { int n = note_num(v); r->lokey = n; r->hikey = n; r->keycenter = n; }
  else if (seq(k, "lokey")) r->lokey = note_num(v);
  else if (seq(k, "hikey")) r->hikey = note_num(v);
  else if (seq(k, "pitch_keycenter")) r->keycenter = note_num(v);
  else if (seq(k, "lovel")) r->lovel = inum(v);
  else if (seq(k, "hivel")) r->hivel = inum(v);
  else if (seq(k, "volume")) r->volume *= vd_exp2(fnum(v) * 0.1660964f);
  else if (seq(k, "gain")) r->volume *= vd_exp2(fnum(v) * 0.1660964f);
  else if (seq(k, "pan")) { float p = fnum(v); r->pan = p > 1.0f || p < -1.0f ? p * 0.01f : p; if (r->pan > 1.0f) r->pan = 1.0f; if (r->pan < -1.0f) r->pan = -1.0f; }
  else if (seq(k, "tune")) r->tune += fnum(v);
  else if (seq(k, "finetune")) r->tune += fnum(v) * 0.01f;
  else if (seq(k, "offset")) r->start = inum(v);
  else if (seq(k, "end")) r->end = inum(v);
  else if (seq(k, "loop_mode")) {
    if (seq(v, "no_loop")) r->loop_mode = FO_LOOP_NONE;
    else if (seq(v, "one_shot")) r->loop_mode = FO_LOOP_ONE_SHOT;
    else if (seq(v, "loop_continuous") || seq(v, "loop_continuous")) r->loop_mode = FO_LOOP_CONT;
    else if (seq(v, "loop_sustain")) r->loop_mode = FO_LOOP_SUSTAIN;
    else (*unknown)++;
  } else if (seq(k, "loop_start")) r->loop_start = inum(v);
  else if (seq(k, "loop_end")) r->loop_end = inum(v);
  else if (seq(k, "ampeg_attack")) r->attack = fnum(v);
  else if (seq(k, "ampeg_decay")) r->decay = fnum(v);
  else if (seq(k, "ampeg_sustain")) { float s = fnum(v); r->sustain = s > 1.0f ? s * 0.01f : s; if (r->sustain > 1.0f) r->sustain = 1.0f; }
  else if (seq(k, "ampeg_release")) r->release = fnum(v);
  else if (seq(k, "ampeg_hold")) r->hold = fnum(v);
  else if (*k) (*unknown)++;
  (void)scope;
}
static void strip_comments(uint8_t *b, int n)
{
  int i = 0, block = 0;
  while (i < n) {
    if (!block && b[i] == '/' && i + 1 < n && b[i + 1] == '/') {
      while (i < n && b[i] != '\n') b[i++] = ' ';
    } else if (!block && b[i] == '/' && i + 1 < n && b[i + 1] == '*') {
      b[i] = b[i + 1] = ' '; i += 2; block = 1;
    } else if (block && b[i] == '*' && i + 1 < n && b[i + 1] == '/') {
      b[i] = b[i + 1] = ' '; i += 2; block = 0;
    } else {
      if (block) b[i] = ' ';
      i++;
    }
  }
}
static void finish_region(const fo_region_t *r)
{
  fo_region_t out;
  if (nregions >= FO_MAX_REGIONS) return;
  out = *r;
  /* An SFZ region names its sample; the built-in demo passes an already decoded
   * index instead. Keep that index when no sample opcode is present. */
  out.sample_index = r->sample[0] ? resolve_sample(r->sample) : r->sample_index;
  if (out.sample_index < 0 || out.sample_index >= nsamples) return;
  if (out.lokey < 0) out.lokey = 0;
  if (out.hikey > 127) out.hikey = 127;
  if (out.hikey < out.lokey) out.hikey = out.lokey;
  if (out.keycenter < 0 || out.keycenter > 127) out.keycenter = 60;
  if (out.start < 0) out.start = 0;
  if (out.end < 0) out.end = -1;
  if (out.attack < 0.0f || out.attack > 10.0f) out.attack = 0.005f;
  if (out.decay < 0.0f || out.decay > 20.0f) out.decay = 0.2f;
  if (out.release < 0.0f || out.release > 20.0f) out.release = 0.3f;
  if (out.hold < 0.0f || out.hold > 20.0f) out.hold = 0.0f;
  if (out.sustain < 0.0f || out.sustain > 1.0f) out.sustain = 0.7f;
  if (out.volume > 8.0f) out.volume = 8.0f;
  regions[nregions++] = out;
}
static void parse_sfz(int slot)
{
  /* Keep the SFZ text in put_buf: finish_region() decodes samples into scratch. */
  uint8_t *b = put_buf;
  int n = read_slot_into(slot, b, FO_FILE_MAX);
  fo_region_t global, group, reg;
  int scope = 0, have = 0, pos = 0;
  if (n <= 0) return;
  strip_comments(b, n);
  defaults(&global); defaults(&group); defaults(&reg);
  while (pos < n) {
    int e = pos, line_start, line_end;
    while (e < n && b[e] != '\n') e++;
    line_start = pos; line_end = e; pos = e + 1;
    while (line_start < line_end && is_space((char)b[line_start])) line_start++;
    while (line_end > line_start && is_space((char)b[line_end - 1])) line_end--;
    if (line_start >= line_end) continue;
    if (b[line_start] == '<') {
      const char *line = (const char *)b + line_start;
      if (have && scope == 2) finish_region(&reg);
      have = 0;
      if (seqn(line, "<global", 7)) { scope = 0; group = global; reg = global; }
      else if (seqn(line, "<group", 6)) { scope = 1; group = global; reg = group; }
      else if (seqn(line, "<region", 7)) { scope = 2; reg = group; have = 1; }
      continue;
    }
    {
      char *p = (char *)b + line_start, *end = (char *)b + line_end;
      while (p < end) {
        char *k = p, *v;
        while (p < end && !is_space(*p) && *p != '=') p++;
        int kl = (int)(p - k);
        while (p < end && is_space(*p)) p++;
        if (p >= end || *p != '=') { while (p < end && !is_space(*p)) p++; continue; }
        p++;
        while (p < end && is_space(*p)) p++;
        v = p;
        while (p < end && !is_space(*p)) p++;
        if (kl > 0 && p > v) {
          char key[40], val[FO_NAME_LEN];
          int j;
          for (j = 0; j < kl && j < (int)sizeof key - 1; j++) key[j] = lowercase(k[j]);
          key[j] = 0;
          int vl = (int)(p - v); if (vl > FO_NAME_LEN - 1) vl = FO_NAME_LEN - 1;
          for (j = 0; j < vl; j++) val[j] = v[j];
          val[j] = 0;
          if (scope == 0) apply_opcode(&global, key, val, scope, &unknown_ops);
          else if (scope == 1) apply_opcode(&group, key, val, scope, &unknown_ops);
          else apply_opcode(&reg, key, val, scope, &unknown_ops);
        }
      }
    }
  }
  if (have) finish_region(&reg);
}

/* ------------------------------------------------------------------ build --- */
static volatile int fo_state; /* 0 = free, 1 = audio, 2 = build */
static int fo_lock(void)
{
  int expected = 0;
  return __atomic_compare_exchange_n(&fo_state, &expected, 2, 0,
                                     __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE);
}
static void fo_unlock(void) { __atomic_store_n(&fo_state, 0, __ATOMIC_RELEASE); }

static void make_fallback(void)
{
  fo_region_t r;
  int i, idx;
  if (pcm_used + 4096 * 2 > FO_PCM_SIZE || nsamples >= FO_MAX_SAMPLES) return;
  for (i = 0; i < 4096; i++) pcm[i] = (int16_t)(vd_sin((float)i * 440.0f / build_sr) * 12000.0f);
  samples[0].data = pcm; samples[0].frames = 4096; samples[0].channels = 1;
  samples[0].rate = (int)build_sr; samples[0].loop_start = 0; samples[0].loop_end = 4096;
  {
    const char *nm = "DEMO SINE";
    for (i = 0; nm[i]; i++) samples[0].name[i] = nm[i];
    samples[0].name[i] = 0;
  }
  idx = nsamples++; pcm_used += 4096 * 2;
  defaults(&r); r.sample_index = idx; r.lokey = 0; r.hikey = 127; r.keycenter = 60;
  r.loop_mode = FO_LOOP_CONT; r.attack = 0.01f; r.decay = 0.6f;
  r.sustain = 0.65f; r.release = 0.35f; r.volume = 0.7f;
  finish_region(&r);
  {
    fo_instr_t *in = &instrs[ninstr++];
    const char *nm = "FIZZY DEMO";
    for (i = 0; nm[i]; i++) in->name[i] = nm[i];
    in->name[i] = 0; in->first = 0; in->count = nregions;
  }
  {
    fo_preset_t *p = &presets[npresets++];
    for (i = 0; instrs[0].name[i]; i++) p->name[i] = instrs[0].name[i];
    p->name[i] = 0; p->instr = 0;
  }
  for (i = 0; i < 12; i++) package_name[i] = "FIZZY ORANGE"[i];
  package_name[12] = 0;
}

int fo_build(char *status, int cap)
{
  const char *manifest = 0;
  char sfz_names[FO_MAX_INSTR][FO_NAME_LEN];
  char ins_names[FO_MAX_INSTR][FO_LABEL_LEN];
  int nman = 0, i, first_region = 0;
  if (!fo_lock()) return -2;
  reset_tables();
  pad_play = 0;
  for (i = 0; i < FO_MAX_SLOTS; i++) {
    if (!slot_used[i] || slot_kind[i] != FO_KIND_JSON) continue;
    const char *base = slot_name[i];
    for (const char *p = slot_name[i]; *p; p++) if (*p == '/' || *p == '\\') base = p + 1;
    if (caseq(base, "fizzy.json")) { manifest = slot_name[i]; break; }
  }
  if (manifest) {
    int slot = find_slot_by_name(manifest, FO_KIND_JSON);
    int n = slot >= 0 ? read_slot_into(slot, scratch, FO_FILE_MAX) : -1;
    if (n > 0 && n < FO_FILE_MAX) {
      char tmp[FO_LABEL_LEN];
      scratch[n] = 0;
      if (json_string((const char *)scratch, "name", tmp, sizeof tmp) > 0) {
        int k; for (k = 0; tmp[k]; k++) package_name[k] = uppercase_name(tmp[k]);
        package_name[k] = 0;
      }
      if (json_flag((const char *)scratch, "pads")) pad_play = 1;
      const char *a, *b;
      if (json_range((const char *)scratch, "instruments", &a, &b)) {
        const char *p = a;
        while (p < b && nman < FO_MAX_INSTR) {
          const char *oa, *ob;
          if (!json_object(&p, b, &oa, &ob)) break;
          json_string_range(oa, ob, "name", ins_names[nman], sizeof ins_names[nman]);
          json_string_range(oa, ob, "sfz", sfz_names[nman], sizeof sfz_names[nman]);
          if (!ins_names[nman][0]) {
            int k; for (k = 0; sfz_names[nman][k] && k < FO_LABEL_LEN - 1; k++) ins_names[nman][k] = uppercase_name(sfz_names[nman][k]);
            ins_names[nman][k] = 0;
          }
          if (sfz_names[nman][0]) nman++;
        }
      }
      if (json_range((const char *)scratch, "presets", &a, &b)) {
        const char *p = a;
        while (p < b && npresets < FO_MAX_PRESETS) {
          const char *oa, *ob;
          if (!json_object(&p, b, &oa, &ob)) break;
          json_string_range(oa, ob, "name", presets[npresets].name, FO_LABEL_LEN);
          int at = find_key_range(oa, ob, "instrument");
          presets[npresets].instr = at >= 0 ? inum(oa + at) : 0;
          npresets++;
        }
      }
    }
  }
  if (!nman || !npresets) {
    int k = 0;
    for (i = 0; i < FO_MAX_SLOTS && k < FO_MAX_INSTR; i++) {
      if (!slot_used[i] || slot_kind[i] != FO_KIND_SFZ) continue;
      if (!nman) {
        const char *base = slot_name[i], *dot;
        for (const char *p = slot_name[i]; *p; p++) if (*p == '/' || *p == '\\') base = p + 1;
        dot = base; for (const char *p = base; *p; p++) if (*p == '.') dot = p;
        int n = (int)(dot - base); if (n > FO_LABEL_LEN - 1) n = FO_LABEL_LEN - 1;
        for (int j = 0; j < n; j++) ins_names[k][j] = uppercase_name(base[j]);
        ins_names[k][n] = 0;
        int m = slen(slot_name[i]); if (m > FO_NAME_LEN - 1) m = FO_NAME_LEN - 1;
        for (int j = 0; j < m; j++) sfz_names[k][j] = slot_name[i][j];
        sfz_names[k][m] = 0;
        k++;
      }
    }
    if (!nman) nman = k;
  }
  for (i = 0; i < nman && ninstr < FO_MAX_INSTR; i++) {
    int slot = find_slot_by_name(sfz_names[i], FO_KIND_SFZ);
    if (slot < 0 && nman == 1) {
      for (int j = 0; j < FO_MAX_SLOTS; j++) if (slot_used[j] && slot_kind[j] == FO_KIND_SFZ) { slot = j; break; }
    }
    if (slot < 0) continue;
    first_region = nregions;
    parse_sfz(slot);
    if (nregions <= first_region) continue;
    {
      fo_instr_t *in = &instrs[ninstr++];
      int k;
      for (k = 0; ins_names[i][k] && k < FO_LABEL_LEN - 1; k++) in->name[k] = ins_names[i][k];
      in->name[k] = 0; in->first = first_region; in->count = nregions - first_region;
    }
  }
  if (!npresets) {
    for (i = 0; i < ninstr && npresets < FO_MAX_PRESETS; i++) {
      int k;
      for (k = 0; instrs[i].name[k] && k < FO_LABEL_LEN - 1; k++) presets[npresets].name[k] = instrs[i].name[k];
      presets[npresets].name[k] = 0; presets[npresets].instr = i; npresets++;
    }
  }
  if (!ninstr || !npresets) {
    nregions = 0; ninstr = npresets = 0; nsamples = 0; pcm_used = 0;
    make_fallback();
  }
  {
    int at = 0;
    at = text_out(status, at, cap, package_name);
    at = text_out(status, at, cap, " | ");
    at = int_out(status, at, cap, ninstr);
    at = text_out(status, at, cap, " instr ");
    at = int_out(status, at, cap, nregions);
    at = text_out(status, at, cap, " regions ");
    at = int_out(status, at, cap, nsamples);
    at = text_out(status, at, cap, " samples");
    if (unknown_ops) { at = text_out(status, at, cap, " "); at = int_out(status, at, cap, unknown_ops); at = text_out(status, at, cap, " unknown"); }
  }
  fo_unlock();
  return 0;
}

/* ------------------------------------------------------------------ audio --- */
typedef struct {
  int active, sample, note, age;
  double pos;
  float step, env, release_rate, vel, pan;
  int stage, loop_mode, start, end, loop_start, loop_end;
  float sustain, attack_inc, decay_inc, hold_left;
} fo_voice_t;
static fo_voice_t voices[FO_MAX_VOICES];
static fo_msg_t q[64];
static volatile uint32_t qw, qr;
static volatile int voice_count, level_l, level_r;
static float arate;

void fo_audio_reset(float sr)
{
  build_sr = sr > 1000.0f ? sr : 48000.0f;
  arate = build_sr;
  z8((uint8_t *)voices, (int)sizeof voices);
  qw = qr = 0; voice_count = level_l = level_r = 0;
}
void fo_init(float sr)
{
  build_sr = sr > 1000.0f ? sr : 48000.0f;
  fo_scan_store();
  fo_audio_reset(sr);
  fo_build((char *)scratch, 64);
}
void fo_queue(int type, int note, int vel)
{
  uint32_t w = qw;
  if (w - qr >= 64) return;
  q[w % 64].type = (uint8_t)type; q[w % 64].note = (uint8_t)note; q[w % 64].vel = (uint8_t)vel;
  VIBE_RELEASE();
  qw = w + 1;
}
int fo_voice_count(void) { return __atomic_load_n(&voice_count, __ATOMIC_RELAXED); }
int fo_level_l(void) { return __atomic_load_n(&level_l, __ATOMIC_RELAXED); }
int fo_level_r(void) { return __atomic_load_n(&level_r, __ATOMIC_RELAXED); }

static void start_voice(const fo_region_t *r, int note, int vel, const fo_ctrl_t *c)
{
  fo_voice_t *v = 0;
  int i;
  for (i = 0; i < FO_MAX_VOICES; i++) {
    if (!voices[i].active) { v = &voices[i]; break; }
    if (!v || voices[i].age < v->age) v = &voices[i];
  }
  if (!v) return;
  {
    fo_sample_t *s = &samples[r->sample_index];
    float cents = (float)(note - r->keycenter) + r->tune + c->tune + (float)c->transpose * 100.0f;
    v->active = 1; v->sample = r->sample_index; v->note = note; v->age++;
    v->pos = (double)r->start;
    v->step = (float)s->rate / arate * vd_exp2(cents / 12.0f);
    if (v->step < 0.02f) v->step = 0.02f;
    if (v->step > 32.0f) v->step = 32.0f;
    v->vel = vd_pow((float)vel / 127.0f, c->velocity < 0.1f ? 0.1f : c->velocity) * r->volume;
    v->pan = r->pan;
    v->stage = 0; v->env = 0.0f;
    v->sustain = r->sustain * (0.5f + c->sustain);
    if (v->sustain > 1.0f) v->sustain = 1.0f;
    v->attack_inc = 1.0f / (r->attack * c->attack * arate > 0.001f ? r->attack * c->attack * arate : 0.001f);
    v->decay_inc = (1.0f / (r->decay * arate > 0.001f ? r->decay * arate : 0.001f));
    v->hold_left = r->hold;
    v->release_rate = 0.0f;
    v->loop_mode = r->loop_mode;
    v->start = r->start; v->end = r->end >= 0 ? r->end : s->frames;
    v->loop_start = r->loop_start >= 0 ? r->loop_start : s->loop_start;
    v->loop_end = r->loop_end >= 0 ? r->loop_end : s->loop_end;
    if (v->loop_start < 0 || v->loop_start >= s->frames) v->loop_start = 0;
    if (v->loop_end <= v->loop_start || v->loop_end > s->frames) v->loop_end = s->frames;
    if (v->end <= v->start || v->end > s->frames) v->end = s->frames;
  }
}
static void release_voice(fo_voice_t *v, float fast)
{
  if (!v->active || v->stage == 4) return;
  v->stage = 4;
  v->release_rate = v->env / (fast ? 0.02f : 0.30f);
  if (v->release_rate < 0.0001f) v->release_rate = 0.0001f;
}
static void handle_note(int type, int note, int vel, const fo_ctrl_t *c)
{
  if (c->instr < 0 || c->instr >= ninstr) return;
  if (type == FO_MSG_ALL_OFF) {
    for (int i = 0; i < FO_MAX_VOICES; i++) release_voice(&voices[i], 1);
    return;
  }
  if (type == FO_MSG_OFF) {
    for (int i = 0; i < FO_MAX_VOICES; i++)
      if (voices[i].active && voices[i].note == note) release_voice(&voices[i], 0);
    return;
  }
  for (int k = instrs[c->instr].first; k < instrs[c->instr].first + instrs[c->instr].count && k < nregions; k++) {
    const fo_region_t *r = &regions[k];
    int n = note + c->octave * 12;
    if (n < r->lokey || n > r->hikey || vel < r->lovel || vel > r->hivel) continue;
    if (r->sample_index < 0 || r->sample_index >= nsamples) continue;
    start_voice(r, n, vel, c);
  }
}
static void frame_sample(const fo_sample_t *s, double pos, float *l, float *r)
{
  int i0 = (int)pos;
  float f = (float)(pos - i0), a, b;
  const int16_t *d = s->data;
  int ch = s->channels;
  if (i0 < 0) i0 = 0;
  if (i0 >= s->frames - 1) i0 = s->frames - 1;
  a = (float)d[i0 * ch] * (1.0f / 32768.0f);
  b = (float)d[(i0 + 1) * ch] * (1.0f / 32768.0f);
  *l = a + (b - a) * f;
  if (ch == 2) {
    a = (float)d[i0 * ch + 1] * (1.0f / 32768.0f);
    b = (float)d[(i0 + 1) * ch + 1] * (1.0f / 32768.0f);
    *r = a + (b - a) * f;
  } else *r = *l;
}
void fo_render(float *l, float *r, int frames, const fo_ctrl_t *c)
{
  int previous = __atomic_exchange_n(&fo_state, 1, __ATOMIC_ACQ_REL);
  uint32_t w, k;
  int n, active = 0;
  float smooth_l = (float)__atomic_load_n(&level_l, __ATOMIC_RELAXED) * 0.001f;
  float smooth_r = (float)__atomic_load_n(&level_r, __ATOMIC_RELAXED) * 0.001f;
  if (previous != 0) {
    for (n = 0; n < frames; n++) l[n] = r[n] = 0.0f;
    return;
  }
  /* Resolve the preset to an instrument under the build lock. The control flow
   * only ever hands over the small preset index. */
  {
    fo_ctrl_t local = *c;
    local.instr = fo_preset_instr(c->instr);
    c = &local;
  }
  w = qw;
  VIBE_ACQUIRE();
  for (k = qr; k != w; k++) handle_note(q[k % 64].type, q[k % 64].note, q[k % 64].vel, c);
  VIBE_RELEASE();
  qr = w;
  for (n = 0; n < frames; n++) {
    float ml = 0.0f, mr = 0.0f;
    for (k = 0; k < FO_MAX_VOICES; k++) {
      fo_voice_t *v = &voices[k];
      fo_sample_t *s;
      float sl, sr, pan, gl, gr, env;
      if (!v->active) continue;
      s = &samples[v->sample];
      if (!s->data || s->frames < 2) { v->active = 0; continue; }
      switch (v->stage) {
        case 0: v->env += v->attack_inc; if (v->env >= 1.0f) { v->env = 1.0f; v->stage = 1; } break;
        case 1: v->env -= (1.0f - v->sustain) * v->decay_inc;
                if (v->env <= v->sustain) { v->env = v->sustain; v->stage = v->hold_left > 0.0f ? 2 : 3; } break;
        case 2: v->hold_left -= 1.0f / arate; if (v->hold_left <= 0.0f) v->stage = 3; break;
        case 3: v->env = v->sustain; break;
        default: v->env -= v->release_rate; if (v->env <= 0.0f) { v->active = 0; continue; } break;
      }
      frame_sample(s, v->pos, &sl, &sr);
      v->pos += v->step;
      if ((v->loop_mode == FO_LOOP_CONT || v->loop_mode == FO_LOOP_SUSTAIN) && v->pos >= v->loop_end)
        v->pos -= (double)(v->loop_end - v->loop_start);
      else if (v->pos >= v->end) { v->active = 0; continue; }
      env = v->env * v->vel;
      pan = v->pan + c->spread * (k & 1 ? 0.28f : -0.28f);
      if (pan > 1.0f) pan = 1.0f; else if (pan < -1.0f) pan = -1.0f;
      gl = vd_sqrt(0.5f * (1.0f - pan));
      gr = vd_sqrt(0.5f * (1.0f + pan));
      ml += sl * env * gl;
      mr += sr * env * gr;
      active++;
    }
    l[n] = vd_tanh(ml * c->volume * 0.55f);
    r[n] = vd_tanh(mr * c->volume * 0.55f);
    {
      float al = l[n] < 0.0f ? -l[n] : l[n];
      float ar = r[n] < 0.0f ? -r[n] : r[n];
      smooth_l += (al - smooth_l) * 0.04f;
      smooth_r += (ar - smooth_r) * 0.04f;
    }
  }
  __atomic_store_n(&voice_count, active, __ATOMIC_RELEASE);
  __atomic_store_n(&level_l, (int)(smooth_l * 1000.0f + 0.5f), __ATOMIC_RELEASE);
  __atomic_store_n(&level_r, (int)(smooth_r * 1000.0f + 0.5f), __ATOMIC_RELEASE);
  __atomic_store_n(&fo_state, 0, __ATOMIC_RELEASE);
}
