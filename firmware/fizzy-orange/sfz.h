/* FIZZY ORANGE -- SFZ subset parser, sample pool and voice engine.
 *
 * The control flow owns the file registry and the instrument tables. The audio
 * flow owns the voices and a small SPSC message queue. A three-state exchange
 * (idle / audio / build) keeps the two apart while a new package is built.
 */
#ifndef FIZZY_ORANGE_SFZ_H
#define FIZZY_ORANGE_SFZ_H

#include "vibe.h"

#define FO_MAX_SLOTS    64
#define FO_MAX_SAMPLES  64
#define FO_MAX_REGIONS  512
#define FO_MAX_INSTR    16
#define FO_MAX_PRESETS  32
#define FO_MAX_VOICES   32
#define FO_PCM_SIZE     (16 * 1024 * 1024)
#define FO_FILE_MAX     (4 * 1024 * 1024)
#define FO_NAME_LEN     64
#define FO_LABEL_LEN    40

enum { FO_KIND_SFZ = 1, FO_KIND_WAV = 2, FO_KIND_JSON = 3 };
enum { FO_LOOP_NONE = 0, FO_LOOP_ONE_SHOT = 1, FO_LOOP_CONT = 2, FO_LOOP_SUSTAIN = 3 };
enum { FO_MSG_ON = 1, FO_MSG_OFF = 2, FO_MSG_ALL_OFF = 3 };

typedef struct {
  int16_t *data;                  /* into the PCM pool; 0 until decoded */
  int frames, channels, rate;
  int loop_start, loop_end;       /* frames */
  char name[FO_NAME_LEN];
} fo_sample_t;

typedef struct {
  char sample[FO_NAME_LEN];
  int sample_index, lokey, hikey, keycenter, lovel, hivel;
  float volume, pan, tune;
  int start, end;                 /* frames; end = -1 means the sample's end */
  int loop_mode, loop_start, loop_end;
  float attack, decay, sustain, release, hold;
} fo_region_t;

typedef struct { char name[FO_LABEL_LEN]; int first, count; } fo_instr_t;
typedef struct { char name[FO_LABEL_LEN]; int instr; } fo_preset_t;
typedef struct { uint8_t type, note, vel; } fo_msg_t;

typedef struct {
  float volume, attack, release, sustain, velocity, tune, spread;
  int octave, transpose, instr;
} fo_ctrl_t;

/* ---- control flow ---------------------------------------------------------- */
void fo_init(float sr);                       /* scan the store, build (or make the demo) */
uint8_t *fo_scratch(void);                    /* FO_FILE_MAX bytes; also used for uploads */
int fo_scratch_size(void);

int fo_slot_put(int slot, int kind, const char *name, const uint8_t *data, int len);
void fo_slots_clear(void);
int fo_slot_count(void);
int fo_slot_at(int i);                        /* slot number of the i-th stored file */
int fo_slot_kind(int slot);
int fo_slot_len(int slot);
const char *fo_slot_name(int slot);
const char *fo_package_name(void);
int fo_pad_play(void);                       /* 1 = pads trigger notes 36..50 */

/* 0 = built, -1 = error (status explains), -2 = the audio flow is busy: retry later */
int fo_build(char *status, int cap);
int fo_instr_count(void);
const char *fo_instr_name(int i);
int fo_instr_regions(int i);
int fo_instr_samples(int i);
int fo_preset_count(void);
const char *fo_preset_name(int i);
int fo_preset_instr(int i);
int fo_sample_count(void);
const char *fo_sample_name(int i);
int fo_unknown_opcodes(void);

/* ---- audio flow ------------------------------------------------------------ */
void fo_audio_reset(float sr);
void fo_queue(int type, int note, int vel);   /* FO_MSG_ON / OFF / ALL_OFF */
void fo_render(float *l, float *r, int frames, const fo_ctrl_t *c);
int fo_voice_count(void);
int fo_level_l(void);                         /* 0..1000, smoothed for the display */
int fo_level_r(void);

#endif
