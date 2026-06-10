#!/usr/bin/env python3
"""
extract_sfx.py - pull a Death Stranding sound effect / music cue to .ogg by name.

Dialogue is one system (see extract_lines.py); SFX and music are a different one:
Wwise SoundBank events. This script automates the whole manual hunt:

    name -> wwiser .txtp -> source WEM id(s) -> WEM bytes -> ogg

It resolves a named graph sound (e.g. sd_sfx_hud_mail_ringtone) to its source WEM
id(s) using the wwiser .txtp catalog, pulls each WEM straight out of the carved
SoundBanks (embedded "Data/bnk" audio) or the streamed store, and runs ww2ogg +
revorb - no foobar/vgmstream, no clicking around.

Usage:
    python3 extract_sfx.py <name-or-substring> [--lang english] [--out DIR]
                           [--workspace DIR] [--list]

Examples:
    python3 extract_sfx.py sd_sfx_hud_mail_ringtone
    python3 extract_sfx.py sd_sfx_com_radio_call_recieve_fragile
    python3 extract_sfx.py radio_call_recieve          # substring -> every match
    python3 extract_sfx.py mail --list                 # just show what matches

This needs a one-time workspace of carved banks + wwiser txtp - build it with
audio-extraction-scripts/build_sfx_workspace.py. The workspace defaults to ~/decima-explorer-workspace;
override with --workspace or the DSX_WORKSPACE env var. Output goes to
<workspace>/output/sfx/. ww2ogg/revorb/codebook paths and the project id come from
the app's config.json, same as extract_lines.py.
"""
import argparse, glob, json, os, re, shutil, struct, subprocess, sys, tempfile

HOME      = os.path.expanduser("~")
REPO      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE = os.environ.get("DSX_WORKSPACE", f"{HOME}/decima-explorer-workspace")
DECIMA    = os.environ.get("DECIMA",
    os.path.join(REPO, "decima-app/target/dist/decima.app/Contents/MacOS/decima"))
CONFIG    = f"{HOME}/Library/Application Support/DecimaWorkshop/config.json"
ENV       = {**os.environ, "_JAVA_OPTIONS": "-Xmx4g"}

# Wwise SoundBank chunk FourCCs (a bank is BKHD followed by some of these).
KNOWN = {b"BKHD", b"DIDX", b"DATA", b"HIRC", b"STID", b"STMG", b"INIT", b"FXPR", b"ENVS", b"PLAT"}

def cfg():
    c = json.load(open(CONFIG)); w = c["WwiseSettings"]
    return (c["ProjectManager"][0]["id"],
            w["ww2oggPath"], w["ww2oggCodebooksPath"], w["revorbPath"])

def q(cmd):                            # quiet subprocess
    return subprocess.run(cmd, env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def fnv1_32(name):                     # Wwise hashes event names with FNV-1 (32-bit, lowercased)
    h = 2166136261
    for b in name.lower().encode():
        h = (h * 16777619) & 0xFFFFFFFF
        h ^= b
    return h

def bank_index(workspace):
    """Map every embedded WEM id -> (bankfile, absolute DATA offset, size)."""
    idx = {}
    for bnk in glob.glob(f"{workspace}/**/*.bnk", recursive=True):
        try:
            data = open(bnk, "rb").read()
        except OSError:
            continue
        i, didx, data_off = 0, None, None
        while i + 8 <= len(data):
            magic = data[i:i+4]; (ln,) = struct.unpack_from("<I", data, i+4)
            if magic == b"DIDX":   didx = (i+8, ln)
            elif magic == b"DATA": data_off = i+8
            elif magic not in KNOWN: break
            i += 8 + ln
        if not didx or data_off is None:
            continue
        base, ln = didx
        for k in range(ln // 12):
            mid, off, sz = struct.unpack_from("<III", data, base + 12*k)
            idx.setdefault(mid, (bnk, data_off + off, sz))   # first bank wins (dupes are identical)
    return idx

def resolve(workspace, name):
    """{label: [txtp paths]} for a sound name.

    1. exact basename match against the readable wwiser names (works when wwnames
       resolved the event, e.g. sd_sfx_com_radio_call_recieve_fragile),
    2. else substring match across basenames,
    3. else FNV-1 fallback: hash the name the way Wwise does and find the txtp whose
       CAkEvent is that hash - this catches events wwnames couldn't name, whose txtp
       are called bank_NNN-<idx>-event.txtp (e.g. sd_sfx_hud_mail_ringtone)."""
    everything = glob.glob(f"{workspace}/**/*.txtp", recursive=True)
    by_name = {}
    for p in everything:
        by_name.setdefault(os.path.splitext(os.path.basename(p))[0], []).append(p)

    if name in by_name:
        return {name: by_name[name]}
    subs = {b: ps for b, ps in by_name.items() if name in b}
    if subs:
        return subs

    h = str(fnv1_32(name))
    pat = re.compile(rf"CAkEvent\[\d+\]\s+{h}\b")
    hits = [p for p in everything
            if (txt := _read(p)) and h in txt and pat.search(txt)]
    return {name: hits} if hits else {}

def _read(path):
    try:
        return open(path, errors="ignore").read()
    except OSError:
        return ""

def txtp_ids(paths):
    """Distinct source WEM ids referenced by a txtp (the ##<id>.wem / <id>.wem tokens)."""
    ids = []
    for path in paths:
        for line in open(path, errors="ignore"):
            for m in re.findall(r"(\d+)\.wem", line):
                if m not in ids:
                    ids.append(m)
    return ids

def wem_bytes(idx, wid, pid, tmp):
    """Embedded -> slice from the bank; otherwise -> decima extract the streamed store."""
    if int(wid) in idx:                       # idx is keyed by int, wid comes in as a str
        bnk, off, sz = idx[int(wid)]
        with open(bnk, "rb") as f:
            f.seek(off); return f.read(sz)
    # streamed fallback (rare for SFX): ds/sounds/streamed_wem_in_bank/generated/macos/<id>.core.stream
    rel = f"ds/sounds/streamed_wem_in_bank/generated/macos/{wid}.core.stream"
    q([DECIMA, "extract", "-p", pid, "-o", f"{tmp}/stream", rel])
    got = f"{tmp}/stream/{wid}.core.stream"
    return open(got, "rb").read() if os.path.exists(got) else None

def main():
    ap = argparse.ArgumentParser(description="Extract a DS sound effect / music cue to ogg by name.")
    ap.add_argument("name", help="event name or substring, e.g. sd_sfx_hud_mail_ringtone")
    ap.add_argument("--lang", default="english")   # only used for the streamed fallback path
    ap.add_argument("--out", default=None, help="output dir (default <workspace>/output/sfx)")
    ap.add_argument("--workspace", default=WORKSPACE, help="carved banks + txtp (default ~/decima-explorer-workspace)")
    ap.add_argument("--list", action="store_true", help="just list matching events, don't extract")
    a = ap.parse_args()

    matches = resolve(a.workspace, a.name)
    if not matches:
        sys.exit(f"  ! no txtp matched {a.name!r} under {a.workspace}\n"
                 f"    (build the catalog first - see audio-extraction-scripts/README.md)")

    if a.list:
        for name in sorted(matches):
            print(name)
        print(f"\n{len(matches)} match(es)")
        return

    pid, ww2ogg, codebooks, revorb = cfg()
    out = a.out or os.path.join(a.workspace, "output", "sfx")   # outside the repo: copyrighted audio
    os.makedirs(out, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="dsx_sfx_")

    print(f"[1/3] indexing soundbanks under {a.workspace}")
    idx = bank_index(a.workspace)
    print(f"      {len(idx)} embedded WEMs across the carved banks")

    total = 0
    for name in sorted(matches):
        ids = txtp_ids(matches[name])
        if not ids:
            print(f"  - {name}: no WEM source in txtp, skipping"); continue
        for n, wid in enumerate(ids, 1):
            data = wem_bytes(idx, wid, pid, tmp)
            if not data:
                print(f"  - {name}: WEM {wid} not found (embedded or streamed)"); continue
            wem = f"{tmp}/{wid}.wem"; open(wem, "wb").write(data)
            ogg = os.path.join(out, f"{name}.ogg" if len(ids) == 1 else f"{name}_{n}.ogg")
            q([ww2ogg, wem, "-o", ogg, "--pcb", codebooks]); q([revorb, ogg])
            if os.path.exists(ogg):
                total += 1; print(f"  + {os.path.basename(ogg)}  (WEM {wid})")

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"[3/3] done: {total} ogg -> {out}/")

if __name__ == "__main__":
    main()
