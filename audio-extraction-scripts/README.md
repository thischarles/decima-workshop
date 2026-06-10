# Death Stranding audio-extraction toolkit
![Static Badge](https://img.shields.io/badge/vibe_coded-gray?style=for-the-badge&logo=claude)

Scripts and methodology for pulling **voice/dialogue** and **sound effects** out of
*Death Stranding: Director's Cut* (macOS) using this repo's build of Decima Workshop.

---

## TL;DR

| You want… | Do this |
|---|---|
| **"Just find me this sound/line"** (words, not names) | `python3 audio-extraction-scripts/find_audio.py "fragile codec ringtone"` |
| A character/mission/terminal's **dialogue** | `python3 audio-extraction-scripts/extract_lines.py <group>` |
| The **list of every dialogue group** | open `audio-extraction-scripts/line_groups.txt` (467 groups) |
| A **sound effect / music** clip (`sd_sfx_*`, `sd_music_*`) | `python3 audio-extraction-scripts/extract_sfx.py <name>` |
| To see it all **end-to-end** | see [Worked examples](#worked-examples-three-real-hunts) |

Outputs land in `~/decima-explorer-workspace/output/` as named `.ogg` files (dialogue groups also
get a `<group-leaf>_index.tsv`: file → speaker → transcript). They're written **outside the repo**
on purpose — it's copyrighted game audio, so it never gets committed.

---

## Setup

The scripts shell out to **this repo's own build** of the app — build it once:

```bash
./mvnw clean package -DskipTests     # -> decima-app/target/dist/decima.app
```

(Set `DECIMA=/path/to/decima` to use a different build.) This fork adds three audio
CLI subcommands on top of upstream's (`sentences`, `extract`, `export-wem`), and its
`paths` command survives the odd unreadable file instead of crashing.

The scripts read the **project id** and the **ww2ogg / codebooks / revorb** paths from the app's
settings file, so they stay in sync automatically:
`~/Library/Application Support/DecimaWorkshop/config.json`. So: open the app once, create the
project (see the main README), and set the Wwise tool paths. See **Dependencies** below for how to
build those tools.

---

## Dependencies

Three external pieces do the actual audio work. You build two of them once, then tell the app where
they are; the script picks the paths up from there.

### Oodle (decompression)
Decima needs the Oodle library to read packfiles at all. Follow the upstream
[CLI wiki → Getting Oodle](https://github.com/ShadelessFox/decima-workshop/wiki/CLI#getting-oodle)
and put the dylib where the app's settings point.

### ww2ogg — Wwise WEM → Ogg Vorbis
The converter that turns the game's `.wem` audio into `.ogg`. Self-contained (no external libraries)
and it ships the codebook file it needs:

```bash
git clone https://github.com/hcs64/ww2ogg
cd ww2ogg
make            # produces ./ww2ogg (arm64 on Apple Silicon)
```

You now have `ww2ogg/ww2ogg` and `ww2ogg/packed_codebooks_aoTuV_603.bin`.

### revorb — fix the Ogg stream timing
ww2ogg's raw output has broken page/granule timestamps, so players can't seek/scrub and some report a
wrong duration. `revorb` rewrites them. This is the classic C++ `revorb` (Jiří Hruška); it links
**libogg** + **libvorbis** and only ships a Visual Studio project, so on macOS you compile it by hand:

```bash
brew install libogg libvorbis
git clone https://github.com/ItsBranK/ReVorb     # revorb.cpp + revorb.h
cd ReVorb
clang++ -std=c++11 -O2 *.cpp -o revorb \
  -I"$(brew --prefix libogg)/include"  -I"$(brew --prefix libvorbis)/include" \
  -L"$(brew --prefix libogg)/lib"      -L"$(brew --prefix libvorbis)/lib" \
  -logg -lvorbis
```

(You may see a harmless `-Wnonportable-include-path` warning — the binary still builds.)

### wwiser (sound effects only)
Only needed for `extract_sfx.py`, to turn SoundBanks into the `.txtp` catalog it reads. Clone
[wwiser](https://github.com/bnnm/wwiser) and, for readable event names, drop a
[wwnames](https://github.com/bnnm/wwiser-utils/tree/master/wwnames) list as `wwnames.txt` in the
workspace. See **Sound effects & music → One-time setup** below for the build steps. (Dialogue
extraction doesn't need wwiser at all.)

### Point the app at the tools
Set these once in Decima Workshop's Wwise settings, or edit
`~/Library/Application Support/DecimaWorkshop/config.json` directly:

```json
"WwiseSettings": {
  "ww2oggPath":          "/path/to/ww2ogg/ww2ogg",
  "ww2oggCodebooksPath": "/path/to/ww2ogg/packed_codebooks_aoTuV_603.bin",
  "revorbPath":          "/path/to/ReVorb/revorb"
}
```

---

## How DS stores audio (the part that felt like magic)

There are **two completely different systems**, which is why there are two methods:

### 1. Localized voice / dialogue
Every spoken line is a **Wwise-Vorbis WEM**, stored once per language as a Decima "stream" file:

```
localized/sentences/<group>/sentences_sentence_<AUDIO-UUID>.wem.<lang>.core.stream
```

The **text + speaker** for each line live in that group's `sentences.core` as RTTI objects
(`SentenceResource` → `Text` → localized strings, `Voice` → speaker name). The crucial trick:

> **The audio file's UUID = the line's `SentenceResource` UUID with its first three groups byte-reversed.**
> Decima prints UUIDs in a mixed-endian "toString" form; the on-disk audio path uses the plain
> ("straight") form. So `cb928b44-83b7-f84a-…` (the line) → `448b92cb-b783-4af8-…` (its audio file).

That one relationship is what lets us go *transcript → exact audio file* automatically.

#### Anatomy of a line group
A "group" is just a folder under `localized/sentences/` that bundles related dialogue — one character,
one mission, one terminal, one cutscene. Inside each group there are only two kinds of file:

- **`sentences.core`** — the *metadata*. One `SentenceResource` per line, holding the line's text (in
  every language) and its speaker. This is what `decima sentences` decodes into a TSV.
- **`sentences_sentence_<UUID>.wem.<lang>.core.stream`** — the *audio*. One Wwise-Vorbis WEM per line
  **per language**, so `english`, `japanese`, `french`… are separate files. The `.core.stream` suffix
  means it's streamed data Decima fetches on demand rather than packing inline.

Nothing in the audio filename says *what* the line is — the only link back to the transcript is that
byte-reversed UUID. That's the entire trick the script automates. `audio-extraction-scripts/line_groups.txt` is simply
the list of every group folder (467 of them), i.e. your menu of what you can extract.

### 2. Sound effects & music
SFX and music are **Wwise SoundBanks** (HIRC graphs) that play embedded or streamed WEMs,
triggered by **named "graph sounds"** like `sd_sfx_com_radio_call_recieve_heartman` or
`sd_music_jingle_sam_sleep`. There's no tidy UUID→file rule here; you resolve the name → a Wwise
event → the WEM ids it plays, using **wwiser**. `extract_sfx.py` automates that chain (see below).

---

## Extracting dialogue — `extract_lines.py`

```bash
python3 audio-extraction-scripts/extract_lines.py <group> [--lang english] [--out DIR]
```

`<group>` is a path under `localized/sentences/` (no leading/trailing slash), e.g.
`ds_lines_npc/lines_higgs`. Pick from `audio-extraction-scripts/line_groups.txt`.

### What it does, step by step
1. **Decode** the group core: `decima sentences -c localized/sentences/<group>/sentences.core`
   → a TSV of `sentenceUUID | textUUID | speaker | gender | text`.
2. **Derive each audio UUID** (byte-reverse the first three groups — the "straight" form).
3. **Sweep the core** for *every* `sentences_sentence_*.wem.<lang>` it references, so lines whose
   text resolves in another core still get grabbed (UUID-named instead of text-named).
4. **Extract** the `.core.stream` WEMs: `decima extract -o <tmp> @paths`.
5. **Convert** each: `ww2ogg … --pcb codebooks` then `revorb`.
6. **Name & index**: files become `~/decima-explorer-workspace/output/<leaf>/<speaker>__<text-slug>.ogg`
   (or `<uuid>.ogg` if the text wasn't resolvable), with `<leaf>_index.tsv` listing them.

### Examples
```bash
python3 audio-extraction-scripts/extract_lines.py ds_lines_sam/lines_sam        # Sam's open-world voice (957 lines)
python3 audio-extraction-scripts/extract_lines.py ds_lines_npc/lines_higgs      # Higgs
python3 audio-extraction-scripts/extract_lines.py ds_lines_mission/lines_m00010 # a mission's dialogue
python3 audio-extraction-scripts/extract_lines.py ds_lines_terminal/lines_pr101 # a prepper terminal's greetings
```

### Group naming cheat-sheet (`audio-extraction-scripts/line_groups.txt`)
- `ds_lines_common/lines_global` — codec / build-hint / **tutorial** VO (Die-Hardman, Mama, Deadman…)
- `ds_lines_sam/lines_sam` — Sam's open-world shouts (the strand greeting "Hey, my name's Sam too!")
- `ds_lines_npc/lines_<char>` — Higgs, Cliff, Amelie, Mama, `lines_mule0x`, `lines_porter0x`, `lines_terrorist0x`
- `ds_lines_mission/lines_m000XX` — story/mission dialogue
- `ds_lines_cutscene/sq_csXX_…` — cutscene dialogue
- `ds_lines_terminal/lines_<facility>` — prepper & facility terminal greetings (`lines_pr101`, `lines_city_…`)

---

## Searching by words — `find_audio.py`

One search box over **both** systems. Type what you remember; it prints a ranked, numbered list of
dialogue lines (full-text over every subtitle in the game) and sound-effect names (token match over
the named Wwise events, with a small synonym table: codec→`radio_call`, notification→`ringtone`, …):

```bash
python3 audio-extraction-scripts/find_audio.py "my name's sam too"
python3 audio-extraction-scripts/find_audio.py "fragile codec ringtone"
python3 audio-extraction-scripts/find_audio.py "mail notification" --take 1     # extract hit #1
python3 audio-extraction-scripts/find_audio.py "keep on keeping on" --take all  # extract everything listed
```

The subtitle index is built once (one `decima localization export` sweep, ~10s, cached in the
workspace as `loc_<lang>.json`). Extraction goes to `<workspace>/output/found/`.

**How deterministic is it?** Dialogue search is exact — text is text. SFX search is a *candidate
list*, not mind reading: it can only rank names whose tokens (or synonyms) appear in your words, so
"fragile codec ringtone" finds `sd_sfx_com_radio_call_recieve_fragile` (via "fragile" +
codec→radio_call), but a query sharing zero vocabulary with the internal name won't. When in doubt,
`extract_sfx.py <substring> --list` to browse.

Lower-level alternatives (what `find_audio.py` automates): decode a group with
`decima sentences -c localized/sentences/<group>/sentences.core` and grep the TSV, or grep the
`paths` dump (`~/decima-explorer-workspace/all_paths.txt`) for group names.

### ⚠️ The "Good morning, Sam." gotcha
That exact line (and the cheer-hologram **"Keep on keeping on."**) is a **subtitle only** — it has
**no `SentenceResource` and no audio file**. It's the *customizable private-room greeting*, generated
at runtime by the `vr0001_radio` "voicesignal" system. You'll find the text in `lines_global` /
`lines_1p5_dsp_global`, but there's nothing to extract — the audio is assembled live and never
stored as a file. That's the one genuine dead-end we hit.

---

## Sound effects & music — `extract_sfx.py`

SFX and music (`sd_sfx_…`, `sd_music_…`) aren't dialogue — they're Wwise SoundBank events, so
`extract_lines.py` doesn't touch them. **`extract_sfx.py` automates the whole hunt** that used to be
manual (wwiser + bank-slicing + converting):

```bash
python3 audio-extraction-scripts/extract_sfx.py <name-or-substring> [--list] [--out DIR] [--workspace DIR]
```

```bash
python3 audio-extraction-scripts/extract_sfx.py sd_sfx_hud_mail_ringtone                # one sound
python3 audio-extraction-scripts/extract_sfx.py sd_sfx_com_radio_call_recieve_fragile
python3 audio-extraction-scripts/extract_sfx.py radio_call_recieve --list               # show every match
python3 audio-extraction-scripts/extract_sfx.py radio_call_recieve                      # …and extract them all
```

Output lands in `<workspace>/output/sfx/<name>.ogg`.

### How it resolves a name
1. **Name → WEM id.** It finds the sound's wwiser `.txtp`. If wwnames resolved the event, the txtp is
   literally `<name>.txtp` (Fragile's case). If not, it falls back to **hashing the name the way Wwise
   does (FNV-1)** and finds the txtp whose `CAkEvent` is that hash — that's how the mail ringtone (an
   un-named `bank_000-2128-event.txtp`) still resolves.
2. **WEM id → bytes.** It indexes every carved bank's `DIDX`/`DATA` and slices the WEM straight out by
   id (embedded "Data/bnk" audio). Streamed WEMs fall back to `decima extract`.
3. **Convert.** `ww2ogg` + `revorb`, same as dialogue.

### One-time setup: build the catalog
The script needs a **workspace** of carved banks + wwiser txtp (defaults to `~/decima-explorer-workspace`; override
with `--workspace` or `DSX_WORKSPACE`). One command builds it:

```bash
python3 audio-extraction-scripts/build_sfx_workspace.py             # add --workspace DIR to put it elsewhere
```

It finds the game's 16 Wwise bank-collection cores + 254 sound-graph cores (via a `decima paths`
dump it generates on first run — the slowest step, a few minutes), extracts them, harvests readable
event names from the graph cores into `wwnames.txt` (the `EntryPoint_<name>_Graph` strings), carves
each collection into raw `.bnk` with `carve_bnk.py`, and runs wwiser over the banks to produce the
`.txtp` catalog. Needs the wwiser checkout (default `<workspace>/wwiser`, or `--wwiser DIR`).
Events wwnames can't name still work — the FNV fallback handles them. `--only <substr>` builds a
subset of collections if you're in a hurry (e.g. `--only system_resident` covers most UI/codec
sounds).

---

## Worked examples: three real hunts

Three requests that land in three different places — a stored dialogue line, a HUD sound, and a
character "ringtone." One per system: `extract_lines.py` for the line, `extract_sfx.py` for the two
sounds (one of which needs the FNV fallback).

### 1. "Good morning, Sam." — a dialogue line that turns out to be a dead-end
The customizable private-room greeting:

```bash
python3 audio-extraction-scripts/find_audio.py "good morning, sam" --take 1,2,3
```

The search finds the **text** in three places, and the extractor tells you the truth about each:
the `lines_global` / `lines_1p5_dsp_global` copies have no `SentenceResource` and no `.wem` — the
greeting is assembled live by the `vr0001_radio` "voicesignal" system (see the gotcha above) — and
the `ds_lines_cutscene` copy is voiced only inside that scene's single per-scene voice track. The
lesson: a subtitle isn't always a file.

### 2. The mail notification — a HUD sound effect
The chime when mail arrives. **Not** dialogue — it's a named graph sound, `sd_sfx_hud_mail_ringtone`.
One command:

```bash
python3 audio-extraction-scripts/extract_sfx.py sd_sfx_hud_mail_ringtone   # -> ~/decima-explorer-workspace/output/sfx/…ogg
```

This is the case that needs the **FNV fallback**: wwnames never named this event, so its txtp is the
un-named `bank_000-2128-event.txtp`. The script hashes the name → event `2797999565` → WEM `835343686`
→ slices it out of `sysres_banks/bank_000.bnk` → ogg.

### 3. Fragile's codec ringtone — a per-character call sound
The tone when Fragile rings you. Each character's "radio call receive" sound is its own event,
`sd_sfx_com_radio_call_recieve_<name>` (yes, the game data misspells "recieve"):

```bash
python3 audio-extraction-scripts/extract_sfx.py sd_sfx_com_radio_call_recieve_fragile
python3 audio-extraction-scripts/extract_sfx.py radio_call_recieve            # …or grab the whole cast at once
```

Here wwnames *did* name the event, so it resolves straight from `…_fragile.txtp` → WEM `321365258`.
Both of these were verified to reproduce the reference oggs byte-for-byte.

---

## File map

| Path | What |
|---|---|
| `audio-extraction-scripts/find_audio.py` | search both worlds by words; `--take N` extracts |
| `audio-extraction-scripts/extract_lines.py` | the dialogue extractor |
| `audio-extraction-scripts/extract_sfx.py` | the sound-effect / music extractor |
| `audio-extraction-scripts/build_sfx_workspace.py` | one-time builder of the SFX workspace (extract → carve → wwiser) |
| `audio-extraction-scripts/carve_bnk.py` | split raw `.bnk` SoundBanks out of a Decima `.core` |
| `audio-extraction-scripts/line_groups.txt` | all 467 dialogue groups (the menu) |
| `~/decima-explorer-workspace/output/` | extracted oggs (outside the repo) |
| `~/decima-explorer-workspace/`: `banks/`, `txtp/`, `wwnames.txt` | the workspace `build_sfx_workspace.py` makes and `extract_sfx.py` reads |
| `ww2ogg/`, `ReVorb/`, `wwiser/` (wherever you built them) | the conversion / bank tools |
| `~/Library/Application Support/DecimaWorkshop/config.json` | project id + tool paths (script reads this) |
