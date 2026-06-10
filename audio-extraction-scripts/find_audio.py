#!/usr/bin/env python3
"""
find_audio.py - one search box for Death Stranding audio: dialogue AND sound effects.

    python3 find_audio.py "good morning, sam"            # search, show ranked hits
    python3 find_audio.py "fragile codec ringtone"
    python3 find_audio.py "mail notification" --take 1   # extract hit #1
    python3 find_audio.py "my name's sam too" --take all # extract every hit shown

Searches two worlds at once and prints one numbered list:
  (line) full-text search over every subtitle in the game - exact and deterministic.
         Index = one `decima localization export` sweep (~10s, cached in the workspace).
  (sfx)  keyword search over the named Wwise events (wwnames + txtp catalog), with a
         small synonym table for gamer vocabulary (codec->radio_call, notification->
         ringtone, ...). Ranked by token overlap - a candidate list, not mind reading:
         if none of your words appear in the sound's internal name, it can't match.

--take N extracts: sfx hits are delegated to extract_sfx.py; dialogue hits decode the
line's group for the speaker, pull the single WEM, and convert (ww2ogg + revorb) into
<workspace>/output/found/. If a line has no stored audio (e.g. the private-room
"Good morning, Sam." - assembled at runtime by the voicesignal system), it says so.
"""
import argparse, json, os, re, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_lines import DECIMA, ENV, cfg, straight, slug, q

HOME      = os.path.expanduser("~")
SCRIPTS   = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.environ.get("DSX_WORKSPACE", f"{HOME}/decima-explorer-workspace")

STOP = {"the", "a", "an", "of", "for", "when", "that", "sound", "sounds", "effect",
        "effects", "sfx", "audio", "noise", "clip", "file"}
# gamer-vocabulary -> name-fragment bridges (the game never says "codec" or "notification")
SYNONYMS = {
    "codec":        ["radio_call", "radio"],
    "call":         ["radio_call"],
    "ringtone":     ["radio_call_recieve", "ringtone", "ring"],
    "notification": ["ringtone", "mail", "alert"],
    "chime":        ["ringtone", "mail"],
    "song":         ["music"],
    "theme":        ["music", "theme"],
    "jingle":       ["jingle"],
    "receive":      ["recieve"],          # the game data misspells it
}

def tokens(query):
    return [t for t in re.findall(r"[a-z0-9']+", query.lower()) if t not in STOP]

def load_line_index(ws, lang):
    """{core path: {uuid: text}} via the stock `localization export` (cached)."""
    cache = os.path.join(ws, f"loc_{lang}.json")
    if not os.path.exists(cache):
        print(f"[index] building subtitle index (one-time, ~10s)")
        pid, *_ = cfg()
        q([DECIMA, "localization", "export", "-p", pid,
           "-s", lang.capitalize(), "-t", lang.capitalize(), "-o", cache])
        if not os.path.exists(cache):
            sys.exit("  ! `decima localization export` failed - is the app built?")
    return json.load(open(cache))["files"]

def load_sfx_names(ws):
    names = set()
    wwnames = os.path.join(ws, "wwnames.txt")
    if os.path.exists(wwnames):
        names.update(ln.strip() for ln in open(wwnames) if ln.strip())
    for root, _, files in os.walk(os.path.join(ws, "txtp")):
        for f in files:
            if f.endswith(".txtp") and not re.match(r"bank_\d+-", f):
                names.add(f[:-5].split(" {")[0])   # drop wwiser variant markers like " {r}"
    return sorted(names)

def search_lines(files, query, limit=10):
    needle, toks = query.lower(), tokens(query)
    exact, partial = [], []
    for core, entries in files.items():
        group = core.removeprefix("localized/sentences/").removesuffix("/sentences.core")
        for uuid, e in entries.items():
            text = (e.get("source") or "").strip()
            low = text.lower()
            if not text:
                continue
            if needle in low:
                exact.append((group, uuid, text))
            elif toks and all(t in low for t in toks):
                partial.append((group, uuid, text))
    return (exact + partial)[:limit]

def search_sfx(names, query, limit=10):
    toks = tokens(query)
    if not toks:
        return []
    scored = []
    for nm in names:
        score = sum(1 for t in toks if t in nm)
        score += sum(0.5 for t in toks for s in SYNONYMS.get(t, []) if s in nm)
        if score > 0:
            scored.append((score, nm))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [nm for _, nm in scored[:limit]]

def extract_line(group, uuid, text, lang, outdir):
    pid, ww2ogg, codebooks, revorb = cfg()
    tmp = tempfile.mkdtemp(prefix="dsx_find_")
    # The localization index keys lines by their TEXT uuid; audio is derived from the
    # SENTENCE uuid. Decode the group to map text->sentence (and learn the speaker).
    q([DECIMA, "sentences", "-p", pid, "-o", f"{tmp}/s.tsv", "-l", lang.capitalize(),
       "-c", f"localized/sentences/{group}/sentences.core"])
    sentence, speaker = None, "unknown"
    if os.path.exists(f"{tmp}/s.tsv"):
        for ln in open(f"{tmp}/s.tsv"):
            f = ln.rstrip("\n").split("\t")
            if len(f) >= 5 and uuid in (f[0], f[1]):
                sentence, speaker = f[0], f[2]
                break
    if sentence is None:
        print(f"  ! no per-line audio for: \"{text}\" ({group})")
        if group.startswith("ds_lines_cutscene/"):
            print(f"    cutscene dialogue is voiced as ONE track per scene, not per line - look under")
            print(f"    ds/sounds/wwise_cinematics_sound_resource/.../wav/{lang}/mac/<scene>_voice_track.{lang}.core.stream")
        else:
            print(f"    no SentenceResource for this text - subtitle-only (runtime voicesignal / unvoiced)")
        return False
    wem_rel = f"localized/sentences/{group}/sentences_sentence_{straight(sentence)}.wem.{lang}.core.stream"
    q([DECIMA, "extract", "-p", pid, "-o", tmp, wem_rel])
    wem = os.path.join(tmp, os.path.basename(wem_rel))
    if not os.path.exists(wem):
        print(f"  ! no per-line audio for: \"{text}\" ({group})")
        if group.startswith("ds_lines_cutscene/"):
            print(f"    cutscene dialogue is voiced as ONE track per scene, not per line - look under")
            print(f"    ds/sounds/wwise_cinematics_sound_resource/.../wav/{lang}/mac/<scene>_voice_track.{lang}.core.stream")
        else:
            print(f"    this line is subtitle-only (runtime voicesignal / unvoiced text)")
        return False
    os.makedirs(outdir, exist_ok=True)
    ogg = os.path.join(outdir, f"{slug(speaker, text)}.ogg")
    q([ww2ogg, wem, "-o", ogg, "--pcb", codebooks]); q([revorb, ogg])
    print(f"  + {os.path.basename(ogg)}")
    return True

def main():
    ap = argparse.ArgumentParser(description="Search DS dialogue + sound effects by words.")
    ap.add_argument("query", help='e.g. "good morning, sam" or "fragile codec ringtone"')
    ap.add_argument("--take", default=None, help="extract hits: a number, list (1,3), or 'all'")
    ap.add_argument("--lang", default="english")
    ap.add_argument("--workspace", default=WORKSPACE)
    a = ap.parse_args()

    ws = a.workspace
    hits = []   # (kind, payload, display)
    for group, uuid, text in search_lines(load_line_index(ws, a.lang), a.query):
        short = text if len(text) <= 90 else text[:87] + "..."
        hits.append(("line", (group, uuid, text), f'(line) {group} :: "{short}"'))
    for nm in search_sfx(load_sfx_names(ws), a.query):
        hits.append(("sfx", nm, f"(sfx)  {nm}"))

    if not hits:
        sys.exit(f"no matches for {a.query!r} - try fewer/other words, or "
                 f"`extract_sfx.py <substring> --list` to browse names")
    for i, (_, _, disp) in enumerate(hits, 1):
        print(f"[{i:2}] {disp}")

    if not a.take:
        print(f"\nre-run with --take N (or 'all') to extract")
        return

    picks = range(1, len(hits) + 1) if a.take == "all" else [int(x) for x in a.take.split(",")]
    outdir = os.path.join(ws, "output", "found")
    for i in picks:
        kind, payload, _ = hits[i - 1]
        if kind == "sfx":
            subprocess.run([sys.executable, os.path.join(SCRIPTS, "extract_sfx.py"),
                            payload, "--workspace", ws, "--out", outdir], env=ENV)
        else:
            extract_line(*payload, a.lang, outdir)
    print(f"\noutput -> {outdir}/")

if __name__ == "__main__":
    main()
