#!/usr/bin/env python3
"""Reader, patcher and verifier for the warp menu data (Movereq*.bmd).

Why this exists
---------------
The warp level requirements live in encrypted .bmd assets, not in source. The
patch was once applied only to the *build output*
(out/build/.../Release/Data/Local/), which CMake's CopyAssets target rewrites
from src/bin whenever the copy stamp is invalidated -- so a rebuild that clears
the CMake/ninja cache silently reverted it. The patch now lives in src/bin,
which is the copy source and is tracked in git, so it survives every rebuild.

This script is kept alongside it so the format never has to be re-derived by
hand the next time a level changes.

File format (established empirically, not from documentation)
-------------------------------------------------------------
    4-byte header  -- little-endian int32, the number of populated records
    50 records of 84 bytes each (trailing records are zero-filled)

Each record is XOR-encrypted with the 3-byte key {FC, CF, AB}, and the key
*restarts at the start of every record* rather than running across the file.

Within a decoded 84-byte record:
    [ 4:36]  map name, NUL-terminated (localised; the root file uses a
             different encoding than the per-locale files)
    [68:72]  int32  required character level   <- the field this script edits
    [72:76]  int32  constant 400 (350/220 for a few maps)
    [76:80]  int32  zen cost
    [80:84]  int32  gate id

Records are matched by *gate id*, never by index or name: the root file orders
its records differently from the locale files (offset by one) and the names are
localised, while the gate id is identical across all four.

Usage
-----
    python tools/patch_movereq.py list [file]   # dump the decoded table
    python tools/patch_movereq.py check         # assert all targets match
    python tools/patch_movereq.py patch         # apply TARGETS to all 4 files
"""
import struct
import sys
from pathlib import Path

KEY = bytes([0xFC, 0xCF, 0xAB])
REC = 84
HDR = 4
OFF_LEVEL = 68
OFF_ID = 80

REPO = Path(__file__).resolve().parent.parent
LOCAL = REPO / "src" / "bin" / "Data" / "Local"

LOCALES = {
    "root": LOCAL / "Movereq.bmd",
    "eng": LOCAL / "Eng" / "MoveReq_eng.bmd",
    "por": LOCAL / "Por" / "MoveReq_por.bmd",
    "spn": LOCAL / "Spn" / "movereq_spn.bmd",
}

FILES = list(LOCALES.values())

# Warp entries to append. The gate is the arrival point, taken from Gate.bmd and
# chosen by which gate other maps actually lead into -- 103 is the one Lorencia's
# gate 102 targets, and 114 is the only destination gate on the Crywolf map.
#
# max_level is 400 rather than 0: every unrestricted warp in the table uses 400,
# and no record anywhere uses 0, so 400 is how "no ceiling" is spelled here.
NEW_ENTRIES = [
    {
        "index": 24,
        "sub": "Crywolf",
        "level": 300,
        "max_level": 400,
        "zen": 15000,
        "gate": 114,
        "names": {"root": "Crywolf", "eng": "Crywolf",
                  "por": "Crywolf", "spn": "Crywolf"},
    },
    {
        "index": 26,
        "sub": "Valley_of_Loren",
        "level": 10,
        "max_level": 400,
        "zen": 500,
        "gate": 103,
        "names": {"root": "Valley of Loren", "eng": "Valley of Loren",
                  "por": "Vale de Loren", "spn": "Valle de Loren"},
    },
]

# gate id -> required level. Keeps the client's warp menu in sync with the
# server's own level gates; the labels are only for readable output.
TARGETS = {
    268: (15, "Elveland2"),
    269: (20, "Elveland3"),
    72: (25, "Devias2"),
    73: (25, "Devias3"),
    74: (25, "Devias4"),
    63: (220, "Icarus"),
    138: (170, "KanturuRuins1"),
    141: (180, "KanturuRuins2"),
    334: (190, "KanturuRuins3"),
    335: (195, "Karutan1"),
    344: (205, "Karutan2"),
    294: (300, "Vulcanus"),
}


def xor(chunk: bytes) -> bytes:
    """The key restarts per record, so this is only ever fed one record."""
    return bytes(b ^ KEY[i % 3] for i, b in enumerate(chunk))


def decode(raw: bytes):
    """Yields (index, decoded_record) for every fixed-size record."""
    for i in range((len(raw) - HDR) // REC):
        start = HDR + i * REC
        yield i, xor(raw[start:start + REC])


def name_of(rec: bytes) -> str:
    return rec[4:36].split(b"\x00")[0].decode("latin-1", "replace")


def fields(rec: bytes):
    level, const, zen, gid = struct.unpack_from("<4i", rec, OFF_LEVEL)
    return level, const, zen, gid


def locate(raw: bytes):
    """Maps gate id -> list of record indices. Duplicates are a hard error, so
    the caller can refuse to write rather than patch an ambiguous record."""
    found = {}
    for i, rec in decode(raw):
        found.setdefault(fields(rec)[3], []).append(i)
    return found


def cmd_list(argv) -> int:
    paths = [Path(argv[0])] if argv else FILES
    for path in paths:
        raw = path.read_bytes()
        count = struct.unpack_from("<i", raw, 0)[0]
        print(f"== {path.relative_to(REPO) if path.is_relative_to(REPO) else path}")
        print(f"   size={len(raw)} populated={count} records={(len(raw) - HDR) // REC}")
        print(f"   {'#':>3}  {'name':<20} {'level':>6} {'zen':>7} {'id':>6}")
        for i, rec in decode(raw):
            level, _, zen, gid = fields(rec)
            if gid == 0 and level == 0:
                continue
            print(f"   {i:>3}  {name_of(rec):<20} {level:>6} {zen:>7} {gid:>6}")
        print()
    return 0


def cmd_check(_argv) -> int:
    bad = 0
    for path in FILES:
        raw = path.read_bytes()
        found = locate(raw)
        problems = []
        for gid, (want, label) in TARGETS.items():
            hits = found.get(gid, [])
            if len(hits) != 1:
                problems.append(f"{label}(id={gid}): {len(hits)} records")
                continue
            _, rec = list(decode(raw))[hits[0]]
            got = fields(rec)[0]
            if got != want:
                problems.append(f"{label}(id={gid}): level {got}, expected {want}")
        status = "OK  " if not problems else "FAIL"
        print(f"[{status}] {path.name}")
        for p in problems:
            print(f"         {p}")
        bad += bool(problems)
    return 1 if bad else 0


def cmd_patch(_argv) -> int:
    for path in FILES:
        raw = bytearray(path.read_bytes())
        found = locate(bytes(raw))

        missing = [TARGETS[g][1] for g in TARGETS if len(found.get(g, [])) != 1]
        if missing:
            print(f"ABORT {path.name}: not uniquely present: {', '.join(missing)}")
            return 1

        changes = []
        for gid, (want, label) in sorted(TARGETS.items(), key=lambda kv: kv[1][1]):
            i = found[gid][0]
            start = HDR + i * REC
            rec = bytearray(xor(raw[start:start + REC]))
            was = struct.unpack_from("<i", rec, OFF_LEVEL)[0]
            if was == want:
                continue
            struct.pack_into("<i", rec, OFF_LEVEL, want)
            raw[start:start + REC] = xor(bytes(rec))
            changes.append((label, was, want))

        path.write_bytes(bytes(raw))
        print(f"{path.name}: {len(changes)} record(s) changed")
        for label, was, want in changes:
            print(f"    {label:<15} {was} -> {want}")
    return 0


def encode_record(entry, locale):
    """Builds one 84-byte plaintext record in the layout MoveCommandData.cpp
    reads (MOVEREQINFO_FILE, pack(1)): index, two 32-byte UTF-8 name fields,
    then level, max level, zen and gate as int32."""
    name = entry["names"][locale].encode("utf-8")
    sub = entry["sub"].encode("utf-8")
    if len(name) > 31 or len(sub) > 31:
        raise ValueError(f"name too long for the 32-byte field: {name!r} / {sub!r}")

    rec = bytearray(REC)
    struct.pack_into("<i", rec, 0, entry["index"])
    rec[4:4 + len(name)] = name
    rec[36:36 + len(sub)] = sub
    struct.pack_into("<4i", rec, OFF_LEVEL,
                     entry["level"], entry["max_level"], entry["zen"], entry["gate"])
    return bytes(rec)


def cmd_add(_argv) -> int:
    """Appends NEW_ENTRIES to each locale file and bumps the header count.

    Refuses to write if an index is already taken or the 50 record slots would
    overflow, so running it twice cannot produce duplicates."""
    for locale, path in LOCALES.items():
        raw = bytearray(path.read_bytes())
        count = struct.unpack_from("<i", raw, 0)[0]
        capacity = (len(raw) - HDR) // REC

        taken = {struct.unpack_from("<i", rec, 0)[0] for _, rec in decode(bytes(raw))}
        clash = [e["index"] for e in NEW_ENTRIES if e["index"] in taken]
        if clash:
            print(f"ABORT {path.name}: index already in use: {clash}")
            return 1
        if count + len(NEW_ENTRIES) > capacity:
            print(f"ABORT {path.name}: only {capacity - count} free slots")
            return 1

        for n, entry in enumerate(NEW_ENTRIES):
            start = HDR + (count + n) * REC
            raw[start:start + REC] = xor(encode_record(entry, locale))

        struct.pack_into("<i", raw, 0, count + len(NEW_ENTRIES))
        path.write_bytes(bytes(raw))

        print(f"{path.name}: {count} -> {count + len(NEW_ENTRIES)} registros")
        for entry in NEW_ENTRIES:
            print(f"    idx {entry['index']:<3} {entry['names'][locale]:<18} "
                  f"lvl={entry['level']:<4} zen={entry['zen']:<6} gate={entry['gate']}")
    return 0


COMMANDS = {"list": cmd_list, "check": cmd_check, "patch": cmd_patch, "add": cmd_add}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd not in COMMANDS:
        print(__doc__)
        sys.exit(2)
    sys.exit(COMMANDS[cmd](sys.argv[2:]))
