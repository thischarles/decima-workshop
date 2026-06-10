#!/usr/bin/env python3
"""Carve embedded Wwise .bnk soundbanks out of a Decima .core file.

Decima wraps each Wwise SoundBank as a `BankData` blob inside a WwiseBankResource
object; a "collection" core packs several. wwiser needs raw .bnk files, so we
locate each bank by its `BKHD` header and walk the chunk chain (magic + LE u32
length) to find its exact end. Parsing by length means audio bytes inside DATA
chunks can't cause false splits.
"""
import os
import struct
import sys

# Known Wwise SoundBank chunk FourCCs (a bank is BKHD followed by some of these).
KNOWN = {b"BKHD", b"DIDX", b"DATA", b"HIRC", b"STID", b"STMG",
         b"ENVS", b"PLAT", b"INIT", b"FXPR"}


def carve(data: bytes):
    banks = []
    i, n = 0, len(data)
    while True:
        j = data.find(b"BKHD", i)
        if j < 0:
            break
        p, first, ok = j, True, True
        while p + 8 <= n:
            magic = data[p:p + 4]
            (length,) = struct.unpack_from("<I", data, p + 4)
            if first:
                first = False
                if magic != b"BKHD":
                    ok = False
                    break
            elif magic not in KNOWN:
                break  # end of this bank's chunk chain
            if p + 8 + length > n:
                break
            p += 8 + length
        if ok and p > j + 8:
            banks.append((j, p - j))
            i = p
        else:
            i = j + 4
    return banks


def main():
    if len(sys.argv) < 2:
        print("usage: carve_bnk.py <input.core> [outdir]")
        return 1
    inp = sys.argv[1]
    outdir = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(inp)[0] + "_banks"
    os.makedirs(outdir, exist_ok=True)
    with open(inp, "rb") as f:
        data = f.read()
    banks = carve(data)
    print(f"input: {inp} ({len(data):,} bytes)")
    print(f"found {len(banks)} bank(s)")
    for idx, (off, ln) in enumerate(banks):
        out = os.path.join(outdir, f"bank_{idx:03d}.bnk")
        with open(out, "wb") as g:
            g.write(data[off:off + ln])
        print(f"  bank_{idx:03d}.bnk  offset={off:,}  len={ln:,}")
    print(f"wrote {len(banks)} file(s) to {outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
