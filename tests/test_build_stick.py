"""End-to-end tests for tools/build-stick.py with synthetic Lenovo packages (no real firmware)."""

import os
import pathlib
import struct
import subprocess
import sys
import tempfile
import unittest
import uuid
import zipfile

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO / "tools" / "build-stick.py"
EC_GUID = uuid.UUID("44b9f325-7ea2-4c2a-a8b1-a67e0c554932")
ME_GUID = uuid.UUID("11111111-2222-3333-4444-555555555555")
TPM_GUID = uuid.UUID("2327d974-d5a6-4f5e-909a-94229bd95964")


def fmp_body(guid: uuid.UUID) -> bytes:
    """Minimal FMP capsule body: header version 1, 0 drivers, 1 payload at offset 16."""
    return struct.pack("<IHHQ", 1, 0, 1, 16) + struct.pack("<I", 2) + guid.bytes_le + b"\0" * 64


def fmp_capsule(types, drivers=0) -> bytes:
    """FMP capsule body with dummy drivers and one payload per UpdateImageTypeId."""
    items = [b"MZ" + b"\0" * 30] * drivers + [struct.pack("<I", 2) + t.bytes_le + b"\0" * 64 for t in types]
    offset, offsets = 8 + 8 * len(items), []
    for item in items:
        offsets.append(offset)
        offset += len(item)
    return struct.pack(f"<IHH{len(items)}Q", 1, drivers, len(types), *offsets) + b"".join(items)


def inf(guid: uuid.UUID, desc: str, filename: str = "fw.bin") -> str:
    lines = ["[Firmware_AddReg]", f"HKR,,FirmwareId,,{{{guid}}}", "HKR,,FirmwareVersion,%REG_DWORD%,0x00010015"]
    if filename:
        lines.append(f"HKR,,FirmwareFilename,,{filename}")
    return "\n".join(lines + ["[Strings]", f'FirmwareDesc = "{desc}"']) + "\n"


def bios_zip(path: pathlib.Path, startup: str, files: dict):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("efi/boot/startup.nsh", startup)
        for name, data in files.items():
            z.writestr(name, data)


class BuildStickTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._tmp.name)
        self.imp = self.tmp / "import"
        self.out = self.tmp / "out"
        self.imp.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def build(self, expect_ok=True, args=(), path=None):
        cmd = [sys.executable, str(SCRIPT), *args, str(self.imp), str(self.out)]
        env = dict(os.environ, PATH=path) if path is not None else None
        r = subprocess.run(cmd, check=False, capture_output=True, text=True, env=env)
        if expect_ok:
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def stick(self, rel: str) -> pathlib.Path:
        return self.out / "stick" / rel

    def test_bios_nsh_uses_flash_command_from_lenovo_startup_nsh(self):
        bios_zip(
            self.imp / "xyzjt10usa.zip",
            "@echo -off\r\nAfuEfix64.efi imageXYZ.cap /p /b /n /r\r\n",
            {"AfuEfix64.efi": b"MZ", "imageXYZ.cap": b"cap"},
        )
        self.build()
        nsh = self.stick("uefi.nsh").read_text()
        self.assertIn("AfuEfix64.efi imageXYZ.cap /p /b /n /r", nsh)
        self.assertNotIn("flash2.efi", nsh)
        self.assertTrue(self.stick("uefi/imageXYZ.cap").is_file())

    def test_bios_m4w_command_unchanged(self):
        bios_zip(
            self.imp / "m4wjt5cusa.zip",
            "flash2.efi imageM4W.cap /bb /rsmb /bgd\n",
            {"flash2.efi": b"MZ", "AfuEfix64.efi": b"MZ", "imageM4W.cap": b"cap"},
        )
        self.build()
        self.assertIn("\r\nflash2.efi imageM4W.cap /bb /rsmb /bgd\r\n", self.stick("uefi.nsh").read_bytes().decode())

    def test_no_placeholder_left_in_any_script(self):
        self.build()
        for nsh in (self.out / "stick").rglob("*.nsh"):
            self.assertNotIn("{UEFI_", nsh.read_text(), nsh.name)

    def test_ec_capsule_gets_header_and_lands_in_ec(self):
        (self.imp / "ec").mkdir()
        (self.imp / "ec" / "fw.bin").write_bytes(fmp_body(EC_GUID))
        (self.imp / "ec" / "ec.inf").write_text(inf(EC_GUID, "Lenovo EC Firmware"))
        self.build()
        cap = self.stick("ec/EC.bin").read_bytes()
        self.assertEqual(cap[:16], EC_GUID.bytes_le)
        self.assertEqual(struct.unpack_from("<I", cap, 24)[0], len(cap))
        self.assertEqual(cap[28:], fmp_body(EC_GUID))

    def test_ec_guid_mismatch_is_refused(self):
        (self.imp / "ec").mkdir()
        (self.imp / "ec" / "fw.bin").write_bytes(fmp_body(ME_GUID))
        (self.imp / "ec" / "ec.inf").write_text(inf(EC_GUID, "Lenovo EC Firmware"))
        r = self.build(expect_ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("does not match", r.stderr)

    def test_me_fwupdate_becomes_capsule_for_me_guid(self):
        me = self.imp / "me" / "extracted"
        me.mkdir(parents=True)
        image = b"\xff" * 16 + b"$FPT" + b"\0" * 100
        (me / "FWUpdate.bin").write_bytes(image)
        r = self.build(args=("--me-guid", str(ME_GUID)))
        cap = self.stick("me/ME.bin").read_bytes()
        # same layout as the capsule used on M4W: GUID, header size 28, PERSIST_ACROSS_RESET, total size, raw image
        self.assertEqual(cap, ME_GUID.bytes_le + struct.pack("<III", 28, 0x00010000, 28 + len(image)) + image)
        self.assertIn(f"ME    FWUpdate.bin [Intel ME image], GUID {ME_GUID}", r.stdout)
        self.assertIn(r"\CapsuleApp.efi \me\ME.bin", self.stick("me.nsh").read_text())

    def test_me_fwupdate_without_me_guid_is_skipped(self):
        (self.imp / "FWUpdate.bin").write_bytes(b"\0" * 16 + b"$FPT")
        r = self.build()
        self.assertIn("no --me-guid given", r.stdout)
        self.assertFalse(self.stick("me").exists())

    def test_me_fwupdate_without_fpt_is_refused(self):
        (self.imp / "FWUpdate.bin").write_bytes(b"\0" * 64)
        r = self.build(expect_ok=False, args=("--me-guid", str(ME_GUID)))
        self.assertIn("no $FPT signature", r.stderr)

    def test_invalid_me_guid_is_refused(self):
        r = self.build(expect_ok=False, args=("--me-guid", "not-a-guid"))
        self.assertNotEqual(r.returncode, 0)

    def test_me_capsule_package_goes_to_me(self):
        (self.imp / "mecap").mkdir()
        (self.imp / "mecap" / "fw.bin").write_bytes(fmp_body(ME_GUID))
        (self.imp / "mecap" / "me.inf").write_text(inf(ME_GUID, "Intel CSME Firmware"))
        self.build()
        self.assertEqual(self.stick("me/ME.bin").read_bytes()[:16], ME_GUID.bytes_le)

    def test_me_capsule_package_and_fwupdate_abort(self):
        (self.imp / "mecap").mkdir()
        (self.imp / "mecap" / "fw.bin").write_bytes(fmp_body(ME_GUID))
        (self.imp / "mecap" / "me.inf").write_text(inf(ME_GUID, "Intel CSME Firmware"))
        (self.imp / "FWUpdate.bin").write_bytes(b"\0" * 16 + b"$FPT")
        r = self.build(expect_ok=False, args=("--me-guid", str(ME_GUID)))
        self.assertIn("keep only one", r.stderr)

    def test_inf_without_firmware_filename_is_skipped(self):
        (self.imp / "ec").mkdir()
        (self.imp / "ec" / "ec.inf").write_text(inf(EC_GUID, "Lenovo EC Firmware", filename=""))
        r = self.build()
        self.assertIn("no FirmwareFilename", r.stdout)
        self.assertFalse(self.stick("ec").exists())

    def test_inf_with_missing_payload_is_skipped(self):
        (self.imp / "ec").mkdir()
        (self.imp / "ec" / "ec.inf").write_text(inf(EC_GUID, "Lenovo EC Firmware", filename="gone.bin"))
        r = self.build()
        self.assertIn("missing, skipped", r.stdout)
        self.assertFalse(self.stick("ec").exists())

    def test_archives_with_same_name_do_not_overwrite_each_other(self):
        for sub, guid, desc in (("a", EC_GUID, "Lenovo EC Firmware"), ("b", ME_GUID, "Intel CSME Firmware")):
            (self.imp / sub).mkdir()
            with zipfile.ZipFile(self.imp / sub / "pkg.zip", "w") as z:
                z.writestr("fw.bin", fmp_body(guid))
                z.writestr("fw.inf", inf(guid, desc))
        self.build()
        self.assertTrue(self.stick("ec/EC.bin").is_file())
        self.assertTrue(self.stick("me/ME.bin").is_file())

    def test_two_bios_packages_abort(self):
        for ver in ("5c", "5d"):
            bios_zip(self.imp / f"m4wjt{ver}usa.zip", "flash2.efi imageM4W.cap /bb\n", {"flash2.efi": b"MZ", "imageM4W.cap": ver.encode()})
        r = self.build(expect_ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("more than one BIOS package", r.stderr)
        self.assertFalse(self.stick("uefi.nsh").exists())

    def test_same_bios_package_as_zip_and_folder_is_fine(self):
        bios_zip(self.imp / "m4wjt5cusa.zip", "flash2.efi imageM4W.cap /bb\n", {"flash2.efi": b"MZ", "imageM4W.cap": b"cap"})
        with zipfile.ZipFile(self.imp / "m4wjt5cusa.zip") as z:
            z.extractall(self.imp / "m4wjt5cusa")
        self.build()
        self.assertIn("\r\nflash2.efi imageM4W.cap /bb\r\n", self.stick("uefi.nsh").read_bytes().decode())

    def test_several_different_flash_lines_abort(self):
        startup = "if %1 == ROLLBACK then\n  flash2.efi image.cap /bb /rollback\nendif\nflash2.efi image.cap /bb\n"
        bios_zip(self.imp / "xyzjt10usa.zip", startup, {"flash2.efi": b"MZ", "image.cap": b"cap"})
        r = self.build(expect_ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("several different flash commands", r.stderr)

    def test_different_images_in_one_startup_nsh_abort(self):
        startup = "flash2.efi imageAAA.cap /bb\nflash2.efi imageBBB.cap /bb\n"
        bios_zip(self.imp / "xyzjt10usa.zip", startup, {"flash2.efi": b"MZ", "imageAAA.cap": b"a", "imageBBB.cap": b"b"})
        r = self.build(expect_ok=False)
        self.assertIn("several different flash commands", r.stderr)

    def test_uppercase_startup_nsh_is_found(self):
        with zipfile.ZipFile(self.imp / "xyzjt10usa.zip", "w") as z:
            z.writestr("EFI/BOOT/STARTUP.NSH", "flash2.efi image.cap /bb\n")
            z.writestr("flash2.efi", b"MZ")
            z.writestr("image.cap", b"cap")
        self.build()
        self.assertIn("flash2.efi image.cap /bb", self.stick("uefi.nsh").read_text())

    def test_appledouble_files_are_ignored(self):
        bios_zip(self.imp / "m4wjt5cusa.zip", "flash2.efi imageM4W.cap /bb\n", {"flash2.efi": b"MZ", "imageM4W.cap": b"cap"})
        (self.imp / "._m4wjt5cusa.zip").write_bytes(b"\0\x05\x16\x07Mac OS X")
        (self.imp / "._FWUpdate.bin").write_bytes(b"\0\x05\x16\x07Mac OS X")
        self.build(args=("--me-guid", str(ME_GUID)))  # a non-ignored ._FWUpdate.bin would fail the $FPT check
        self.assertTrue(self.stick("uefi/imageM4W.cap").is_file())
        self.assertFalse(self.stick("me").exists())

    def test_non_ascii_flash_command_aborts(self):
        bios_zip(self.imp / "xyzjt10usa.zip", "flash2.efi image.cap /bb /ä\n", {"flash2.efi": b"MZ", "image.cap": b"cap"})
        r = self.build(expect_ok=False)
        self.assertIn("non-ASCII", r.stderr)
        self.assertFalse((self.out / "SHA256SUMS").exists())

    def test_without_bios_package_autostart_aborts_cleanly(self):
        self.build()
        nsh = self.stick("uefi.nsh").read_text()
        self.assertIn(r"if not exist \uefi\NO_UEFI_PACKAGE then", nsh)
        self.assertIn("no BIOS package on this stick", nsh)

    def test_flat_bios_package_in_import_root_aborts(self):
        (self.imp / "efi" / "boot").mkdir(parents=True)
        (self.imp / "efi" / "boot" / "startup.nsh").write_text("flash2.efi image.cap /bb\n")
        (self.imp / "flash2.efi").write_bytes(b"MZ")
        (self.imp / "image.cap").write_bytes(b"cap")
        (self.imp / "notes.txt").write_text("unrelated")
        r = self.build(expect_ok=False)
        self.assertIn("own subfolder", r.stderr)
        self.assertFalse(self.stick("uefi").exists())

    def test_bios_package_in_subfolder_copies_only_that_package(self):
        pkg = self.imp / "abcjt10usa"
        (pkg / "efi" / "boot").mkdir(parents=True)
        (pkg / "efi" / "boot" / "startup.nsh").write_text("flash2.efi image.cap /bb\n")
        (pkg / "flash2.efi").write_bytes(b"MZ")
        (pkg / "image.cap").write_bytes(b"cap")
        (self.imp / "notes.txt").write_text("unrelated")
        self.build()
        self.assertEqual(sorted(f.name for f in self.stick("uefi").iterdir()), ["flash2.efi", "image.cap"])

    def test_bios_lookup_stays_inside_extracted_archive(self):
        # startup.nsh at the archive root: tools must be found there, never in parent dirs
        with zipfile.ZipFile(self.imp / "abcjt10usa.zip", "w") as z:
            z.writestr("startup.nsh", "flash2.efi image.cap /bb\n")
            z.writestr("flash2.efi", b"MZ")
            z.writestr("image.cap", b"cap")
        self.build()
        self.assertIn("flash2.efi image.cap /bb", self.stick("uefi.nsh").read_text())

    def test_ec_description_mentioning_bios_is_still_ec(self):
        (self.imp / "pkg").mkdir()
        (self.imp / "pkg" / "fw.bin").write_bytes(fmp_body(EC_GUID))
        (self.imp / "pkg" / "fw.inf").write_text(inf(EC_GUID, "Lenovo ThinkCentre BIOS EC Firmware"))
        self.build()
        self.assertTrue(self.stick("ec/EC.bin").is_file())

    def test_ec_and_me_in_one_description_is_skipped(self):
        (self.imp / "pkg").mkdir()
        (self.imp / "pkg" / "fw.bin").write_bytes(fmp_body(EC_GUID))
        (self.imp / "pkg" / "fw.inf").write_text(inf(EC_GUID, "EC and ME Firmware"))
        r = self.build()
        self.assertIn("is ambiguous (ec/me)", r.stdout)

    def test_failed_build_leaves_no_stick(self):
        self.build()  # a complete stick from an earlier run
        self.assertTrue(self.stick("uefi.nsh").is_file())
        for ver in ("5c", "5d"):
            bios_zip(self.imp / f"abcjt{ver}usa.zip", "flash2.efi image.cap /bb\n", {"flash2.efi": b"MZ", "image.cap": ver.encode()})
        r = self.build(expect_ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.out / "stick").exists())
        self.assertFalse((self.out / "stick.partial").exists())
        self.assertFalse((self.out / "SHA256SUMS").exists())

    def test_bios_package_subfolders_are_copied_other_downloads_not(self):
        pkg = self.imp / "abcjt10usa"
        (pkg / "efi" / "boot").mkdir(parents=True)
        (pkg / "efi" / "boot" / "startup.nsh").write_text("flash2.efi image.cap /bb\n")
        (pkg / "cfg").mkdir()
        (pkg / "cfg" / "x.ini").write_text("cfg")
        (pkg / "flash2.efi").write_bytes(b"MZ")
        (pkg / "image.cap").write_bytes(b"cap")
        (pkg / "abcct21.exe").write_bytes(b"MZ")
        r = self.build()
        self.assertTrue(self.stick("uefi/cfg/x.ini").is_file())
        self.assertFalse(self.stick("uefi/abcct21.exe").exists())
        self.assertFalse(self.stick("uefi/efi/boot/startup.nsh").exists())
        self.assertIn("not copied from the BIOS package folder", r.stdout)

    def test_unrecognised_flash_line_is_reported(self):
        bios_zip(self.imp / "abcjt10usa.zip", "fs0:\\flash2.efi image.cap /bb\n", {"flash2.efi": b"MZ", "image.cap": b"cap"})
        r = self.build()
        self.assertIn("startup.nsh found", r.stdout)
        self.assertIn("flash2.efi image.cap /bb", r.stdout)
        self.assertFalse(self.stick("uefi").exists())

    def test_corrupt_zip_gives_error_not_traceback(self):
        (self.imp / "abcjt10usa.zip").write_bytes(b"PK\x03\x04 truncated")
        r = self.build(expect_ok=False)
        self.assertIn("not a valid zip file", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertFalse((self.out / "stick.partial").exists())

    def test_undeletable_previous_build_aborts_before_building(self):
        self.build()
        locked = self.out / "stick" / "uefi"
        locked.mkdir()
        (locked / "old.cap").write_bytes(b"old")
        locked.chmod(0o500)
        try:
            r = self.build(expect_ok=False)
        finally:
            locked.chmod(0o700)
        if r.returncode == 0:
            self.skipTest("running as root, permissions are not enforced")
        self.assertIn("cannot remove the previous build", r.stderr)
        self.assertFalse((self.out / "stick.partial").exists())

    def test_startup_nsh_never_flashes(self):
        bios_zip(self.imp / "abcjt10usa.zip", "flash2.efi image.cap /bb\n", {"flash2.efi": b"MZ", "image.cap": b"cap"})
        self.build()
        lines = self.stick("EFI/BOOT/startup.nsh").read_text().splitlines()
        commands = [line.strip() for line in lines if line.strip() and not line.strip().startswith(("echo", "#", "@echo", "if ", "endif"))]
        # besides printing, startup.nsh only reads the ESRT and the clock into the log on the stick
        for cmd in commands:
            self.assertRegex(cmd, r"^(date|time|fs\d:\\CapsuleApp\.efi -E) >>a fs\d:\\update-log\.txt$")

    def test_startup_nsh_tells_which_drive_to_type(self):
        self.build()
        text = self.stick("EFI/BOOT/startup.nsh").read_text()
        for n in range(6):
            block = text[text.index(f"if exist fs{n}:\\uefi.nsh then") :]
            block = block[: block.index("endif")]
            self.assertIn(f'echo "Firmware stick found on fs{n}:"', block)
            self.assertIn(f'echo "          fs{n}:"', block)
            self.assertIn(f"fs{n}:\\CapsuleApp.efi -E >>a fs{n}:\\update-log.txt", block)

    def test_extra_payload_is_kept_and_reported(self):
        body = fmp_capsule([TPM_GUID, EC_GUID], drivers=2)
        (self.imp / "tpm").mkdir()
        (self.imp / "tpm" / "fw.bin").write_bytes(body)
        (self.imp / "tpm" / "fw.inf").write_text(inf(TPM_GUID, "ThinkCentre P3 Tiny TPM Firmware"))
        r = self.build()
        self.assertEqual(self.stick("tpm/TPM.bin").read_bytes()[28:], body)
        self.assertIn(f"WARNING: TPM: 'ThinkCentre P3 Tiny TPM Firmware' also carries payloads for {EC_GUID}", r.stdout)

    def test_matching_payload_need_not_be_first(self):
        (self.imp / "tpm").mkdir()
        (self.imp / "tpm" / "fw.bin").write_bytes(fmp_capsule([EC_GUID, TPM_GUID], drivers=1))
        (self.imp / "tpm" / "fw.inf").write_text(inf(TPM_GUID, "TPM Firmware"))
        self.build()
        self.assertTrue(self.stick("tpm/TPM.bin").is_file())

    def test_no_matching_payload_among_several_is_refused(self):
        (self.imp / "tpm").mkdir()
        (self.imp / "tpm" / "fw.bin").write_bytes(fmp_capsule([EC_GUID, ME_GUID], drivers=1))
        (self.imp / "tpm" / "fw.inf").write_text(inf(TPM_GUID, "TPM Firmware"))
        r = self.build(expect_ok=False)
        self.assertIn("does not match", r.stderr)

    def test_fmp_offset_outside_file_is_refused(self):
        body = bytearray(fmp_capsule([EC_GUID]))
        struct.pack_into("<Q", body, 8, len(body) + 100)
        (self.imp / "ec").mkdir()
        (self.imp / "ec" / "fw.bin").write_bytes(bytes(body))
        (self.imp / "ec" / "ec.inf").write_text(inf(EC_GUID, "Lenovo EC Firmware"))
        r = self.build(expect_ok=False)
        self.assertIn("offset outside the file", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_tpm_package_lands_in_tpm(self):
        (self.imp / "tpm").mkdir()
        (self.imp / "tpm" / "TPM1524_P3.bin").write_bytes(fmp_body(TPM_GUID))
        (self.imp / "tpm" / "TPM1524_P3.inf").write_text(inf(TPM_GUID, "ThinkCentre P3 Tiny TPM Firmware 15.24.18954.0", "TPM1524_P3.bin"))
        r = self.build()
        cap = self.stick("tpm/TPM.bin").read_bytes()
        self.assertEqual(cap[:16], TPM_GUID.bytes_le)
        self.assertEqual(cap[28:], fmp_body(TPM_GUID))
        self.assertIn("TPM   ThinkCentre P3 Tiny TPM Firmware", r.stdout)
        self.assertIn(r"\CapsuleApp.efi \tpm\TPM.bin", self.stick("tpm.nsh").read_text())

    def test_startup_menu_lists_tpm_before_ec(self):
        self.build()
        menu = self.stick("EFI/BOOT/startup.nsh").read_text()
        self.assertLess(menu.index("uefi.nsh"), menu.index("tpm.nsh"))
        self.assertLess(menu.index("tpm.nsh"), menu.index("ec.nsh"))
        self.assertLess(menu.index("ec.nsh"), menu.index("me.nsh"))

    def test_package_naming_ec_and_tpm_is_skipped(self):
        (self.imp / "pkg").mkdir()
        (self.imp / "pkg" / "fw.bin").write_bytes(fmp_body(TPM_GUID))
        (self.imp / "pkg" / "fw.inf").write_text(inf(TPM_GUID, "EC and TPM Firmware"))
        r = self.build()
        self.assertIn("is ambiguous (ec/tpm)", r.stdout)

    def test_uefi_nsh_changes_into_the_folder_holding_the_image(self):
        bios_zip(self.imp / "abcjt10usa.zip", "flash2.efi image.cap /bb\n", {"flash2.efi": b"MZ", "image.cap": b"cap"})
        self.build()
        lines = [line.strip() for line in self.stick("uefi.nsh").read_text().splitlines()]
        self.assertIn('echo "=== uefi.nsh: flashing flash2.efi image.cap /bb, firmware versions before:" >>a \\update-log.txt', lines)
        folder = lines[lines.index("flash2.efi image.cap /bb") - 1]
        self.assertEqual(folder, "cd \\uefi")
        self.assertTrue(self.stick("uefi/image.cap").is_file())
        self.assertTrue(self.stick("uefi/flash2.efi").is_file())

    def test_unexpected_flash_arguments_abort(self):
        for args in ("/bb %MYVAR%", "/bb > log.txt", "/bb | x", "/bb ; reset", "/bb ^x"):
            with self.subTest(args=args):
                bios_zip(self.imp / "abcjt10usa.zip", f"flash2.efi image.cap {args}\n", {"flash2.efi": b"MZ", "image.cap": b"cap"})
                r = self.build(expect_ok=False)
                self.assertIn("unexpected arguments", r.stderr)
                self.assertFalse((self.out / "stick").exists())

    def fake_innoextract(self, output: str) -> str:
        """Directory with an innoextract that fails with the given message, for PATH."""
        bindir = self.tmp / "bin"
        bindir.mkdir()
        tool = bindir / "innoextract"
        tool.write_text(f"#!/bin/sh\necho '{output}' >&2\nexit 1\n")
        tool.chmod(0o755)
        return f"{bindir}{os.pathsep}{os.environ['PATH']}"

    def test_exe_in_subfolder_without_innoextract_warns(self):
        (self.imp / "ec").mkdir()
        (self.imp / "ec" / "abcct21a.exe").write_bytes(b"MZ")
        r = self.build(path=str(self.tmp / "empty"))
        self.assertIn("innoextract not found", r.stdout)
        self.assertIn("abcct21a.exe", r.stdout)

    def test_corrupt_inno_package_in_subfolder_warns(self):
        (self.imp / "ec").mkdir()
        (self.imp / "ec" / "abcct21a.exe").write_bytes(b"MZ")
        r = self.build(path=self.fake_innoextract("Stream error: unexpected end of file"))
        self.assertIn("WARNING: ec/abcct21a.exe: innoextract failed", r.stdout)

    def test_windows_tool_in_subfolder_is_only_noted(self):
        (self.imp / "me").mkdir()
        (self.imp / "me" / "FWUpdLcl64.exe").write_bytes(b"MZ")
        r = self.build(path=self.fake_innoextract("Not a supported Inno Setup installer!"))
        self.assertIn("ignored   me/FWUpdLcl64.exe", r.stdout)
        self.assertNotIn("WARNING: me/FWUpdLcl64.exe", r.stdout)

    def test_me_guid_of_another_component_is_refused(self):
        (self.imp / "tpm").mkdir()
        (self.imp / "tpm" / "fw.bin").write_bytes(fmp_capsule([TPM_GUID, EC_GUID]))
        (self.imp / "tpm" / "fw.inf").write_text(inf(TPM_GUID, "TPM Firmware"))
        (self.imp / "FWUpdate.bin").write_bytes(b"\0" * 16 + b"$FPT")
        for guid in (TPM_GUID, EC_GUID):
            with self.subTest(guid=guid):
                r = self.build(expect_ok=False, args=("--me-guid", str(guid)))
                self.assertIn("in the TPM package" if guid == EC_GUID else "of the TPM package", r.stderr)
                self.assertFalse((self.out / "stick").exists())

    def test_flash_scripts_check_the_file_and_pause_before_flashing(self):
        self.build()
        for name in ("uefi.nsh", "tpm.nsh", "ec.nsh", "me.nsh"):
            with self.subTest(script=name):
                lines = [line.strip() for line in self.stick(name).read_text().splitlines()]
                self.assertTrue(lines[1].startswith("if not exist "), lines[1])
                flag = next(i for i, line in enumerate(lines) if line.startswith("dmpstore -d"))
                log = next(i for i, line in enumerate(lines) if line == "\\CapsuleApp.efi -E >>a \\update-log.txt")
                # quiet pause: only the script's own prompt is shown, q still aborts
                self.assertLess(lines.index("pause -q"), log)
                self.assertLess(log, flag)
                self.assertEqual(lines[-1], ":END")

    def test_checksums_cover_stick(self):
        self.build()
        sums = (self.out / "SHA256SUMS").read_text()
        self.assertIn("stick/EFI/BOOT/BOOTX64.EFI", sums)
        self.assertIn("stick/CapsuleApp.efi", sums)


if __name__ == "__main__":
    unittest.main()
