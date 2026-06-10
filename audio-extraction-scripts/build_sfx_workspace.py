#!/usr/bin/env python3
"""
build_sfx_workspace.py - one-time builder of the workspace extract_sfx.py reads.

Automates the whole catalog build that used to be manual:

    1. find the Wwise bank-collection cores + sound-graph cores in the game
       (ds/sounds/wwise*bnk_collections/*.core, ds/sounds/wwise_graph_sound_resource/**)
    2. extract them with the decima CLI
    3. harvest readable event names from the graph cores -> wwnames.txt
       (graph sounds appear as EntryPoint_<name>_Graph strings)
    4. carve each collection core into raw .bnk SoundBanks (carve_bnk.py)
    5. run wwiser over the banks -> the .txtp catalog extract_sfx.py resolves names in

Usage:
    python3 build_sfx_workspace.py [--workspace DIR] [--paths FILE]
                                   [--wwiser DIR] [--only SUBSTR]

The file list comes from a `decima paths` dump. If --paths isn't given and the
workspace has no all_paths.txt yet, the script generates one (slow: a few minutes,
needs a big JVM heap - that's why ENV below asks for 10g).

--only filters the bank collections by substring (e.g. --only system_resident),
useful for a quick partial build; name harvesting still uses all graph cores.
"""
import argparse, glob, json, os, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from carve_bnk import carve

HOME   = os.path.expanduser("~")
REPO   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECIMA = os.environ.get("DECIMA",
    os.path.join(REPO, "decima-app/target/dist/decima.app/Contents/MacOS/decima"))
CONFIG = f"{HOME}/Library/Application Support/DecimaWorkshop/config.json"
ENV    = {**os.environ, "_JAVA_OPTIONS":
          "-Xmx10g -Djava.util.concurrent.ForkJoinPool.common.parallelism=3"}

COLLECTION_RE = re.compile(r"^ds/sounds/wwise[a-z_]*bnk_collections/.+\.core$")
GRAPH_RE      = re.compile(r"^ds/sounds/wwise_graph_sound_resource/.+\.core$")
NAME_RE       = re.compile(rb"EntryPoint_([a-zA-Z0-9_]+?)_Graph")

def pid():
    return json.load(open(CONFIG))["ProjectManager"][0]["id"]

def run(cmd, **kw):
    return subprocess.run(cmd, env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)

def get_paths(ws, paths_arg, project):
    cached = paths_arg or os.path.join(ws, "all_paths.txt")
    if not os.path.exists(cached):
        print(f"[paths] no file list yet - running `decima paths` (slow, please wait)")
        run([DECIMA, "paths", "-p", project, "-o", cached])
        if not os.path.exists(cached):
            sys.exit("  ! `decima paths` produced nothing - is the project id right?")
    return [ln.strip() for ln in open(cached) if ln.strip()]

def extract_batch(project, paths, outdir):
    os.makedirs(outdir, exist_ok=True)
    lst = os.path.join(outdir, "_list.txt")
    open(lst, "w").write("\n".join(paths))
    run([DECIMA, "extract", "-p", project, "-o", outdir, f"@{lst}"])
    os.remove(lst)
    return [p for p in paths if os.path.exists(os.path.join(outdir, os.path.basename(p)))]

def main():
    ap = argparse.ArgumentParser(description="Build the SFX workspace (banks + wwnames + txtp).")
    ap.add_argument("--workspace", default=os.environ.get("DSX_WORKSPACE", f"{HOME}/decima-explorer-workspace"))
    ap.add_argument("--paths", default=None, help="existing `decima paths` dump to reuse")
    ap.add_argument("--wwiser", default=None, help="wwiser checkout dir (default <workspace>/wwiser)")
    ap.add_argument("--only", default=None, help="only build collections matching this substring")
    a = ap.parse_args()

    wwiser = a.wwiser or os.path.join(a.workspace, "wwiser")
    wwiser_py = wwiser if wwiser.endswith(".py") else os.path.join(wwiser, "wwiser.py")
    if not os.path.exists(wwiser_py):
        sys.exit(f"  ! wwiser not found at {wwiser_py} - clone https://github.com/bnnm/wwiser")

    ws = os.path.abspath(a.workspace)
    os.makedirs(ws, exist_ok=True)
    project = pid()

    all_paths   = get_paths(ws, a.paths, project)
    collections = [p for p in all_paths if COLLECTION_RE.match(p)]
    graphs      = [p for p in all_paths if GRAPH_RE.match(p)]
    if a.only:
        collections = [p for p in collections if a.only in p]
    if not collections:
        sys.exit("  ! no bank-collection cores matched")
    print(f"[1/5] found {len(collections)} bank collections, {len(graphs)} sound-graph cores")

    print(f"[2/5] extracting cores")
    got_c = extract_batch(project, collections, f"{ws}/cores/collections")
    got_g = extract_batch(project, graphs, f"{ws}/cores/graphs")
    print(f"      {len(got_c)} collections, {len(got_g)} graph cores")

    print(f"[3/5] harvesting event names -> wwnames.txt")
    names = set()
    for p in glob.glob(f"{ws}/cores/graphs/*.core"):
        names.update(m.decode() for m in NAME_RE.findall(open(p, "rb").read()))
    wwnames = os.path.join(ws, "wwnames.txt")
    existing = set(open(wwnames).read().split()) if os.path.exists(wwnames) else set()
    open(wwnames, "w").write("\n".join(sorted(existing | names)) + "\n")
    print(f"      {len(names)} names from graphs ({len(existing | names)} total)")

    print(f"[4/5] carving SoundBanks")
    bank_dirs = []
    for p in got_c:
        base = os.path.splitext(os.path.basename(p))[0]
        data = open(f"{ws}/cores/collections/{os.path.basename(p)}", "rb").read()
        outdir = f"{ws}/banks/{base}"
        os.makedirs(outdir, exist_ok=True)
        banks = carve(data)
        for idx, (off, ln) in enumerate(banks):
            open(f"{outdir}/bank_{idx:03d}.bnk", "wb").write(data[off:off + ln])
        print(f"      {base}: {len(banks)} bank(s)")
        if banks:
            bank_dirs.append(base)

    print(f"[5/5] generating txtp with wwiser (this is the slow part)")
    for base in bank_dirs:
        bnks = sorted(glob.glob(f"{ws}/banks/{base}/*.bnk"))
        # cwd=ws so wwiser picks up wwnames.txt; relative paths keep txtp headers tidy
        rel = [os.path.relpath(b, ws) for b in bnks]
        r = subprocess.run([sys.executable, wwiser_py, "-g", "-go", f"txtp/{base}", *rel],
                           cwd=ws, env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        n = len(glob.glob(f"{ws}/txtp/{base}/*.txtp"))
        print(f"      {base}: {n} txtp" + ("" if r.returncode == 0 else f"  (wwiser exit {r.returncode})"))

    print(f"\ndone. workspace ready at {ws}")
    print(f"try:  python3 audio-extraction-scripts/extract_sfx.py sd_sfx_hud_mail_ringtone --workspace {ws}")

if __name__ == "__main__":
    main()
