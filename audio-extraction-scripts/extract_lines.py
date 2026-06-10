#!/usr/bin/env python3
"""
extract_lines.py - pull a Death Stranding dialogue line-group to named .ogg files.

Usage:
    python3 extract_lines.py <group> [--lang english] [--out DIR]

<group> is a path under localized/sentences/. Examples:
    ds_lines_sam/lines_sam
    ds_lines_npc/lines_higgs
    ds_lines_terminal/lines_pr101
    ds_lines_mission/lines_m00010

The full list of groups is in audio-extraction-scripts/line_groups.txt (467 groups).
Output is written to ~/decima-explorer-workspace/output/<group-leaf>/
(override with --out) - kept outside the repo since it's copyrighted
game audio.

Project id + ww2ogg/revorb/codebook paths are read from the Decima config.json,
so it stays in sync with the app's settings. Set DECIMA=/path/to/decima to
override the launcher location.

Note: this names the lines the decoder can resolve text for. A few lines whose
text lives in another core get skipped (no name) - that's expected.
"""
import argparse, csv, json, os, re, shutil, subprocess, sys, tempfile

HOME   = os.path.expanduser("~")
REPO   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECIMA = os.environ.get("DECIMA",
    os.path.join(REPO, "decima-app/target/dist/decima.app/Contents/MacOS/decima"))
CONFIG = f"{HOME}/Library/Application Support/DecimaWorkshop/config.json"
ENV    = {**os.environ, "_JAVA_OPTIONS": "-Xmx4g"}

WORKSPACE  = os.environ.get("DSX_WORKSPACE", os.path.join(HOME, "decima-explorer-workspace"))
OUT_BASE   = os.path.join(WORKSPACE, "output")   # extracted oggs land here (outside the repo)

def cfg():
    c = json.load(open(CONFIG))
    w = c["WwiseSettings"]
    return (c["ProjectManager"][0]["id"],
            w["ww2oggPath"], w["ww2oggCodebooksPath"], w["revorbPath"])

def straight(u):                       # SentenceResource uuid -> audio-file uuid
    g = u.split("-"); rev = lambda h: bytes.fromhex(h)[::-1].hex()
    return f"{rev(g[0])}-{rev(g[1])}-{rev(g[2])}-{g[3]}-{g[4]}"

def slug(spk, txt):
    return re.sub(r"[^a-z0-9]+", "_", f"{spk}__{txt[:46]}".lower()).strip("_") or "line"

def q(cmd):                            # quiet subprocess
    return subprocess.run(cmd, env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def main():
    ap = argparse.ArgumentParser(description="Extract a DS dialogue group to named oggs.")
    ap.add_argument("group", help="e.g. ds_lines_sam/lines_sam")
    ap.add_argument("--lang", default="english")
    ap.add_argument("--out", default=None, help="output dir (default <workspace>/output/<group-leaf>)")
    a = ap.parse_args()

    pid, ww2ogg, codebooks, revorb = cfg()
    group = a.group.strip("/")
    out   = a.out or os.path.join(OUT_BASE, group.split('/')[-1])
    tmp   = tempfile.mkdtemp(prefix="dsx_")
    core  = f"localized/sentences/{group}/sentences.core"

    print(f"[1/4] decoding  {core}")
    q([DECIMA, "sentences", "-p", pid, "-c", core, "-o", f"{tmp}/s.tsv", "-l", a.lang.capitalize()])
    if not os.path.exists(f"{tmp}/s.tsv"):
        sys.exit(f"  ! not found / no lines: {core}")

    rows = [r for r in csv.reader(open(f"{tmp}/s.tsv"), delimiter="\t")
            if len(r) >= 5 and r[0] != "sentenceUUID"]
    seen, items = {}, []
    for r in rows:
        nm = slug(r[2], r[4]); seen[nm] = seen.get(nm, 0) + 1
        if seen[nm] > 1: nm = f"{nm}_{seen[nm]}"
        items.append((straight(r[0]), nm, r[2], r[4]))
    print(f"      {len(items)} lines")

    # also pick up audio whose text resolves in another core (named by uuid)
    q([DECIMA, "extract", "-p", pid, "-o", f"{tmp}/core", core])
    named_uuids = {au for au, *_ in items}
    cb = open(f"{tmp}/core/sentences.core", "rb").read()
    rx = re.compile(rb"sentences_sentence_([0-9a-f-]{36})\.wem\." + a.lang.encode())
    for m in set(rx.findall(cb)):
        au = m.decode()
        if au not in named_uuids:
            items.append((au, au, "?", ""))   # unnamed -> file is <uuid>.ogg
    print(f"      {len(items)} audio refs ({len(named_uuids)} named)")

    paths = [f"localized/sentences/{group}/sentences_sentence_{au}.wem.{a.lang}.core.stream"
             for au, *_ in items]
    open(f"{tmp}/p.txt", "w").write("\n".join(paths))
    print(f"[2/4] extracting wem")
    q([DECIMA, "extract", "-p", pid, "-o", f"{tmp}/wem", f"@{tmp}/p.txt"])

    os.makedirs(out, exist_ok=True)
    idx = open(f"{out}_index.tsv", "w"); idx.write("file\tspeaker\ttext\n")
    print(f"[3/4] converting to ogg")
    n = 0
    for au, nm, spk, txt in items:
        wem = f"{tmp}/wem/sentences_sentence_{au}.wem.{a.lang}.core.stream"
        if not os.path.exists(wem):
            continue
        ogg = f"{out}/{nm}.ogg"
        q([ww2ogg, wem, "-o", ogg, "--pcb", codebooks]); q([revorb, ogg])
        idx.write(f"{nm}.ogg\t{spk}\t{txt}\n"); n += 1
    idx.close(); shutil.rmtree(tmp, ignore_errors=True)
    print(f"[4/4] done: {n} ogg -> {out}/")
    print(f"      index -> {out}_index.tsv")

if __name__ == "__main__":
    main()
