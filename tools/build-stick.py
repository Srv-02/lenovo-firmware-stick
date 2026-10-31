#!/usr/bin/env python3
"""Build a Lenovo firmware stick (AMI desktops) from Lenovo downloads in import/.

Put into import/ (as downloaded or already extracted, subfolders are fine):
  UEFI  Lenovo USB package  <platform>jt<VER>usa.zip (contains efi/boot/startup.nsh,
        the flash command is taken from there)
  EC    Lenovo EC package   <platform>ct<VER>.exe (Inno Setup, needs innoextract) or its extracted folder
  TPM   Lenovo TPM firmware package from LVFS (fwupd.org, .cab or extracted, .inf + .bin)
  ME    Lenovo's ME package <ver>_corporate.exe / its extracted folder (FWUpdate.bin) plus --me-guid,
        or a Lenovo ME capsule package from the Microsoft Update Catalog (.cab or extracted, .inf + .bin)

Usage:  python3 tools/build-stick.py [--me-guid GUID] [import-dir] [output-dir]
Output: output/stick/  -> copy its CONTENT to the root of a FAT32 stick
"""

import argparse
import fnmatch
import hashlib
import pathlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import uuid
import zipfile

REPO = pathlib.Path(__file__).resolve().parent.parent
PERSIST_ACROSS_RESET = 0x00010000
HEADER_SIZE = 28


def info(msg):
    print(msg)


def warn(msg):
    print(f"WARNING: {msg}")


def fail(msg):
    sys.exit(f"ERROR: {msg}")


def is_junk(p: pathlib.Path) -> bool:
    """macOS AppleDouble files (._name) next to the real files, created when copying from macOS volumes."""
    return p.name.startswith("._")


def unpack(import_dir: pathlib.Path, tmp: pathlib.Path) -> list:
    """Return search roots: import dir plus extracted .zip/.cab/.exe archives."""
    roots = [import_dir]
    no_innoextract = []
    for i, f in enumerate(sorted(import_dir.rglob("*"))):
        if not f.is_file() or is_junk(f):
            continue
        # index prefix: archives with the same name in different subfolders must not overwrite each other
        dest = tmp / f"{i:04d}-{f.stem}"
        suffix = f.suffix.lower()
        if suffix == ".zip":
            try:
                with zipfile.ZipFile(f) as z:
                    z.extractall(dest)
            except zipfile.BadZipFile:
                fail(f"{f.name}: not a valid zip file (download incomplete?)")
        elif suffix == ".cab":
            if not shutil.which("cabextract"):
                warn(f"{f.name}: cabextract not found (brew/apt/dnf install cabextract), skipped")
                continue
            if subprocess.run(["cabextract", "-q", "-d", str(dest), str(f)], check=False, capture_output=True).returncode != 0:
                warn(f"{f.name}: cabextract failed, skipped")
                continue
        elif suffix == ".exe":
            if not shutil.which("innoextract"):
                no_innoextract.append(str(f.relative_to(import_dir)))
                continue
            r = subprocess.run(["innoextract", "-s", "-d", str(dest), str(f)], check=False, capture_output=True, text=True)
            if r.returncode != 0:
                if "not a supported inno setup installer" in (r.stdout + r.stderr).lower():
                    # extracted packages contain Windows tools (e.g. FWUpdLcl64.exe) that are no installers
                    info(f"ignored   {f.relative_to(import_dir)} (no Inno Setup package)")
                else:
                    warn(f"{f.relative_to(import_dir)}: innoextract failed, skipped ({(r.stderr or r.stdout).strip()[:200]})")
                continue
        else:
            continue
        roots.append(dest)
        info(f"unpacked  {f.name}")
    if no_innoextract:
        warn(
            f"innoextract not found (brew/apt/dnf install innoextract), .exe skipped: {', '.join(no_innoextract)}. "
            "Or extract them yourself into import/."
        )
    return roots


def find(roots, pattern):
    hits = []
    for r in roots:
        # case-insensitive: packages mix FWUpdate.bin / fwupdate.bin, .inf / .INF
        hits += [p for p in r.rglob("*") if p.is_file() and not is_junk(p) and fnmatch.fnmatch(p.name.lower(), pattern.lower())]
    # dedupe identical files found via several roots
    seen, out = set(), []
    for p in hits:
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        if h not in seen:
            seen.add(h)
            out.append(p)
    return out


def read_inf(path: pathlib.Path) -> str:
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8", errors="replace")


def wrap(body: bytes, guid: uuid.UUID) -> bytes:
    """Add the EFI_CAPSULE_HEADER that Windows adds itself (CapsuleGuid = ESRT FirmwareId)."""
    if body[:16] == guid.bytes_le:
        return body  # header already present
    return guid.bytes_le + struct.pack("<III", HEADER_SIZE, PERSIST_ACROSS_RESET, HEADER_SIZE + len(body)) + body


def check_payload(body: bytes, guid: uuid.UUID, name: str):
    """Accept only an FMP capsule body with a payload whose UpdateImageTypeId matches the GUID.

    Returns (format, other_guids): UpdateImageTypeIds of further payloads in the same capsule.
    """
    if body[:16] == guid.bytes_le:
        # EFI_CAPSULE_HEADER already present, check the FMP body behind it
        body = body[struct.unpack_from("<I", body, 16)[0] :]
    try:
        ver, drivers, payloads = struct.unpack_from("<IHH", body, 0)
        if ver == 1 and payloads >= 1:
            offsets = struct.unpack_from(f"<{drivers + payloads}Q", body, 8)
            table_end = 8 + 8 * (drivers + payloads)
            if any(not table_end <= o <= len(body) - 20 for o in offsets):
                fail(f"{name}: FMP item offset outside the file, refusing")
            types = [uuid.UUID(bytes_le=body[o + 4 : o + 20]) for o in offsets[drivers:]]
            if guid not in types:
                fail(f"{name}: UpdateImageTypeId does not match FirmwareId")
            return "FMP capsule", [t for t in types if t != guid]
    except (struct.error, ValueError):
        pass
    fail(f"{name}: payload is not an FMP capsule body, refusing")


KIND_RE = {
    "ec": r"\bEC\b|Embedded Controller",
    "me": r"\bME\b|CSME|Management Engine",
    "tpm": r"\bTPM\b|Trusted Platform Module",
}


def firmware_packages(roots):
    """Windows firmware driver packages (.inf with FirmwareId), classified as ec, me or tpm."""
    out = {}
    for inf_path in find(roots, "*.inf"):
        inf = read_inf(inf_path)
        fw_id = re.search(r"FirmwareId,,\{([0-9a-fA-F-]{36})\}", inf)
        if not fw_id:
            continue
        fw_file = re.search(r"FirmwareFilename,,(?:\{[0-9a-fA-F-]{36}\}\\)?([^\s\r\n]+)", inf)
        fw_ver = re.search(r"FirmwareVersion,%REG_DWORD%,(0x[0-9a-fA-F]+)", inf)
        desc = re.search(r'FirmwareDesc\s*=\s*"([^"]+)"', inf)
        desc = desc.group(1) if desc else inf_path.name
        # the description is the only hint Lenovo gives, so a package naming several components is skipped instead of guessed
        kinds = [k for k, pattern in KIND_RE.items() if re.search(pattern, desc, re.IGNORECASE)]
        if len(kinds) != 1:
            what = "is ambiguous (" + "/".join(kinds) + ")" if kinds else "is neither EC, ME nor TPM"
            warn(f"{inf_path.name}: firmware package '{desc}' {what}, skipped")
            continue
        kind = kinds[0]
        if kind in out:
            fail(f"{kind.upper()}: more than one package found in import/ ({out[kind]['desc']} / {desc}), keep only one")
        if not fw_file:
            warn(f"{inf_path.name}: '{desc}' has no FirmwareFilename, skipped")
            continue
        payload = inf_path.parent / fw_file.group(1)
        if not payload.is_file():
            warn(f"{inf_path.name}: '{desc}' payload {fw_file.group(1)} missing, skipped")
            continue
        guid = uuid.UUID(fw_id.group(1))
        body = payload.read_bytes()
        fmt, others = check_payload(body, guid, desc)
        if others:
            # kept as Lenovo ships it (Windows Update and fwupd deliver the same bytes), but never silently
            warn(
                f"{kind.upper()}: '{desc}' also carries payloads for {', '.join(map(str, others))}; "
                "the firmware may apply them as well, see README"
            )
        out[kind] = {
            "desc": desc,
            "guid": guid,
            "capsule": wrap(body, guid),
            "fmt": fmt,
            "ver": int(fw_ver.group(1), 16) if fw_ver else None,
            "others": others,
        }
    return out


FLASH_RE = re.compile(r"^\s*([\w.-]+\.efi)\s+\\?([\w.-]+\.(?:cap|bin|rom|fd))\b(.*)$", re.IGNORECASE)
# flash tool switches as Lenovo ships them (/bb /rsmb /bgd, /p /b /n /r); anything else (variables, redirection,
# pipes, further commands) could change what runs on the target, so the build refuses it instead of copying it
FLASH_ARG_RE = re.compile(r"[\w/.:=+-]+")


def _match_file(base: pathlib.Path, name: str):
    if not base.is_dir():
        return None
    return next((p for p in base.iterdir() if p.is_file() and p.name.lower() == name.lower()), None)


def uefi_package(roots):
    """Find a Lenovo BIOS USB package via its startup.nsh.

    Returns (package_dir, tool, image, args) or None. The flash line is the
    '<tool>.efi <image> <args>' line whose tool and image exist in the package.
    Refuses to guess: several different flash lines or several packages abort the build.
    """
    found, unmatched = {}, []
    import_dir = roots[0]
    for r in roots:
        nshs = sorted(p for p in r.rglob("*") if p.is_file() and not is_junk(p) and p.name.lower() == "startup.nsh")
        for nsh in nshs:
            cmds = [m for m in (FLASH_RE.match(line) for line in nsh.read_text(errors="replace").splitlines()) if m]
            matched = False
            # Lenovo puts startup.nsh in efi/boot/, the tools in the package root
            for base in (nsh.parent, nsh.parent.parent, nsh.parent.parent.parent):
                if base != r and r not in base.parents:
                    break  # never look above the import dir or the extracted archive
                hits = [(m, _match_file(base, m.group(1)), _match_file(base, m.group(2))) for m in cmds]
                hits = [(m, tool, image) for m, tool, image in hits if tool and image]
                if not hits:
                    continue
                lines = {f"{tool.name} {image.name} {m.group(3).strip()}".strip() for m, tool, image in hits}
                if len(lines) > 1:
                    fail(f"UEFI: {nsh.name} has several different flash commands, refusing to pick one: {' | '.join(sorted(lines))}")
                if base == import_dir:
                    fail("UEFI: the USB package is extracted directly into import/, put it into its own subfolder (import/<name>/)")
                m, tool, image = hits[0]
                # the same package as .zip and as extracted folder is fine, two different packages are not
                key = (hashlib.sha256(image.read_bytes()).hexdigest(), lines.pop().lower())
                found.setdefault(key, (base, tool.name, image.name, m.group(3).strip()))
                matched = True
                break
            if not matched:
                unmatched.append(nsh)
    if not found:
        for nsh in unmatched:
            efi_lines = [line.strip() for line in nsh.read_text(errors="replace").splitlines() if ".efi" in line.lower()]
            warn(
                f"UEFI: {nsh.name} found ({nsh.parent.name}/), but no line '<tool>.efi <image> <args>' with both files next to it: "
                + (" | ".join(efi_lines[:5]) or "no .efi lines")
            )
    if len(found) > 1:
        fail(f"UEFI: more than one BIOS package found in import/ ({', '.join(str(v[0]) for v in found.values())}), keep only one")
    return next(iter(found.values()), None)


def write_nsh(src: pathlib.Path, dst: pathlib.Path, subst: dict):
    text = src.read_text()
    for k, v in subst.items():
        text = text.replace("{" + k + "}", v)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("ascii"))


def describe(kind, pkg):
    v = pkg["ver"]
    ver = f", ESRT fw_version {v} (0x{v:08x})" if v is not None else ""
    return f"{kind.upper():4}  {pkg['desc']} [{pkg['fmt']}], GUID {pkg['guid']}{ver}"


def build(import_dir: pathlib.Path, stick: pathlib.Path, me_guid) -> list:
    """Write the complete stick content to stick/, return the summary lines."""
    summary = []
    with tempfile.TemporaryDirectory() as t:
        roots = unpack(import_dir, pathlib.Path(t))

        # base: EFI shell, CapsuleApp
        (stick / "EFI" / "BOOT").mkdir(parents=True)
        shutil.copy2(REPO / "tools" / "efi" / "BOOTX64.EFI", stick / "EFI" / "BOOT" / "BOOTX64.EFI")
        shutil.copy2(REPO / "tools" / "efi" / "CapsuleApp.efi", stick / "CapsuleApp.efi")

        # UEFI/BIOS: Lenovo USB package. The flash command is taken from its own startup.nsh,
        # so model specific tools/parameters are used as Lenovo ships them.
        uefi_image, uefi_cmd = "NO_UEFI_PACKAGE", "echo no BIOS package"
        pkg = uefi_package(roots)
        if pkg:
            base, tool, image, args = pkg
            uefi_image, uefi_cmd = image, f"{tool} {image} {args}".strip()
            if not uefi_cmd.isascii():
                fail(f"UEFI: flash command contains non-ASCII characters, refusing: {uefi_cmd}")
            bad = [a for a in args.split() if not FLASH_ARG_RE.fullmatch(a)]
            if bad:
                fail(f"UEFI: flash command has unexpected arguments {' '.join(bad)}, refusing: {uefi_cmd}")
            # the whole package as Lenovo ships it (incl. subfolders), without its startup.nsh and
            # without other downloads that were put into the same folder
            skipped = []
            for f in sorted(base.rglob("*")):
                if not f.is_file() or is_junk(f) or f.name.lower() == "startup.nsh":
                    continue
                if f.suffix.lower() in (".zip", ".exe", ".cab"):
                    skipped.append(f.name)
                    continue
                dst = stick / "uefi" / f.relative_to(base)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst)
            if skipped:
                warn(f"UEFI: not copied from the BIOS package folder (other downloads?): {', '.join(skipped)}")
            summary.append(f"UEFI  {image}, command from Lenovo startup.nsh: {uefi_cmd}")
        else:
            warn("UEFI: no Lenovo USB package (<platform>jt<VER>usa.zip with startup.nsh) found. The Windows .exe is not enough.")

        # TPM, EC and optionally ME: Windows firmware driver packages (.inf with FirmwareId), flashed via CapsuleApp
        fw = firmware_packages(roots)
        for kind in ("tpm", "ec"):  # same order as on the stick: TPM before EC
            if kind in fw:
                (stick / kind).mkdir()
                (stick / kind / f"{kind.upper()}.bin").write_bytes(fw[kind]["capsule"])
                summary.append(describe(kind, fw[kind]))
            else:
                warn(f"{kind.upper()}: no package found")

        # ME: Lenovo's ME package only has FWUpdate.bin (a raw Intel ME image, no .inf). It becomes a
        # capsule for the ME's ESRT GUID, which has to be given with --me-guid.
        fwupd = find(roots, "FWUpdate.bin")
        if len(fwupd) > 1:
            fail("ME: more than one FWUpdate.bin found in import/, keep only one")
        if fwupd and "me" in fw:
            fail(f"ME: both FWUpdate.bin and the capsule package '{fw['me']['desc']}' found in import/, keep only one")
        if fwupd and me_guid:
            # a wrong guess would send a raw ME image to another component's update handler
            for kind, pkg in fw.items():
                if me_guid == pkg["guid"]:
                    fail(f"ME: --me-guid {me_guid} is the GUID of the {kind.upper()} package '{pkg['desc']}', not of the ME")
            for kind, pkg in fw.items():
                if me_guid in pkg["others"]:
                    fail(f"ME: --me-guid {me_guid} is the GUID of a payload in the {kind.upper()} package '{pkg['desc']}', not of the ME")
            body = fwupd[0].read_bytes()
            if body[16:20] != b"$FPT":
                fail("ME: FWUpdate.bin has no $FPT signature at offset 16, not an Intel ME update image")
            fw["me"] = {
                "desc": "FWUpdate.bin",
                "guid": me_guid,
                "capsule": wrap(body, me_guid),
                "fmt": "Intel ME image",
                "ver": None,
                "others": [],
            }
        elif fwupd:
            warn("ME: FWUpdate.bin found but no --me-guid given, ME skipped. See README: find the ME's ESRT GUID with info.nsh")
        if "me" in fw:
            (stick / "me").mkdir()
            (stick / "me" / "ME.bin").write_bytes(fw["me"]["capsule"])
            summary.append(describe("me", fw["me"]))
        elif not fwupd:
            warn("ME: no package found")

    # scripts
    subst = {"UEFI_IMAGE": uefi_image, "UEFI_CMD": uefi_cmd}
    write_nsh(REPO / "efi" / "startup.nsh", stick / "EFI" / "BOOT" / "startup.nsh", subst)
    for n in ("uefi.nsh", "ec.nsh", "me.nsh", "tpm.nsh", "info.nsh"):
        write_nsh(REPO / "efi" / n, stick / n, subst)

    return summary


def main():
    ap = argparse.ArgumentParser(description="Build the Lenovo firmware stick from import/.")
    ap.add_argument("import_dir", nargs="?", default=str(REPO / "import"))
    ap.add_argument("out_dir", nargs="?", default=str(REPO / "output"))
    ap.add_argument(
        "--me-guid",
        type=uuid.UUID,
        help="ESRT GUID of the Intel ME, needed for Lenovo's FWUpdate.bin (M4W: 7fd2a343-8917-48a5-8aa6-b47edd8431cd)",
    )
    a = ap.parse_args()
    import_dir, out = pathlib.Path(a.import_dir), pathlib.Path(a.out_dir)
    if not import_dir.is_dir():
        fail(f"{import_dir} not found")
    final, partial = out / "stick", out / "stick.partial"
    # a stick is either complete or absent: build into stick.partial, rename only after success
    try:
        for d in (final, partial):
            if d.exists():
                shutil.rmtree(d)
        (out / "SHA256SUMS").unlink(missing_ok=True)
    except OSError as e:
        fail(f"cannot remove the previous build ({e}), delete {out} manually (built with sudo before?)")
    try:
        summary = build(import_dir, partial, a.me_guid)
        partial.rename(final)
    except BaseException:  # also SystemExit from fail()
        shutil.rmtree(partial, ignore_errors=True)
        raise

    # checksums
    lines = []
    for f in sorted(p for p in final.rglob("*") if p.is_file()):
        lines.append(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.relative_to(out)}")
    (out / "SHA256SUMS").write_text("\n".join(lines) + "\n")

    print()
    for s in summary:
        print(s)
    print(f"\nStick content: {final}")
    print(f"Copy the CONTENT of {final}/ to the root of a FAT32 stick.")


if __name__ == "__main__":
    main()
