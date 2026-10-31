# Firmware Stick for Lenovo ThinkCentre and ThinkStation

> [!WARNING]
> **DISCLAIMER: USE AT YOUR OWN RISK. NO WARRANTY.**
> Flashing firmware can permanently brick a device. An interrupted or wrong flash may leave it unable to boot. This software is provided "as is", without warranty of any kind, and the authors accept no liability for any damage or data loss (see [LICENSE](LICENSE)).
>
> This is an independent project, not affiliated with, endorsed or supported by Lenovo or Intel. All product names and trademarks belong to their respective owners.

Builds a bootable UEFI Shell USB stick from Lenovo's downloads to update UEFI/BIOS, TPM, EC (Embedded Controller) and Intel ME (Management Engine) on Lenovo ThinkCentre and ThinkStation desktops with AMI BIOS, without Windows on the device (e.g. Linux servers).

Nothing in the scripts is tied to a specific model: the BIOS flash command is taken from Lenovo's own `startup.nsh` in the USB package, the TPM and EC capsule GUIDs from the `.inf` of their packages, the ME image is passed to the firmware as a capsule for the ME's ESRT GUID. The repository only contains scripts and two EDK2 tools built from source. No firmware images are included, they are downloaded from Lenovo for every update.

## Supported devices

ThinkCentre and ThinkStation desktops with AMI BIOS, for which Lenovo offers a BIOS USB package (`<platform>jt<ver>usa.zip`) and/or the TPM, EC or ME as a firmware package. Nothing in the scripts is model specific. The tool was developed on a ThinkStation P3 Tiny (platform M4W), which is where the examples in this README come from.

ThinkPads are out of scope: different firmware base, updates mostly via LVFS (`fwupdmgr update`).

Platform M4W covers these models (source: Lenovo BIOS readme `m4wjy5cusa.html` and Lenovo's update catalog `catalogv2.xml`; ThinkCentre M70q Gen 4 is a different platform, M4V):

| Model | Machine types |
|---|---|
| ThinkCentre M80q Gen 4 | 12E9, 12EA, 12EB, 12EC |
| ThinkCentre M90q Gen 4 | 12EH, 12EJ, 12EK, 12EL |
| ThinkStation P3 Tiny | 30H0, 30H1, 30H2, 30H3, 30H4, 30H5, 30H6, 30H7 |

Results on further models are welcome as issues or pull requests.

Lenovo's BIOS flash tool checks the platform ID in the BIOS image and rejects images for other models. EC, TPM and ME capsules are only addressed to an ESRT GUID, and that GUID is not model specific: Lenovo uses the same EC GUID on several platforms. Whether the firmware checks the platform inside these images is not known, so only use packages Lenovo lists for the exact machine type. Before the first update on a model, run `info.nsh` and read the console output.

## Why this approach

Lenovo ships these updates primarily for Windows. On a Linux host, only one path per component works. The details below come from the P3 Tiny (M4W).

**UEFI/BIOS**
- Lenovo's USB package (on M4W `flash2.efi` + `AfuEfix64.efi`, other models may use other tools) works from the UEFI Shell.
- Pitfall: a capsule update that was staged but never processed leaves the UEFI variable `AmiCapsuleImageLoaded-67a772e6-d717-4ac1-8380-4bf04b340852` set to `01`. As long as it exists, every further update fails with `Capsule flash process is already in progress / Load firmware image fail`. `uefi.nsh` deletes this flag before flashing. On firmware without this variable, the delete just reports "not found".

**EC (Embedded Controller)**
- Lenovo ships the EC only as a Windows firmware driver package (`.inf` + `.bin`). The `.bin` is an FMP capsule body without the outer `EFI_CAPSULE_HEADER`, which Windows adds itself.
- **Linux does not work:** passing the capsule via `/dev/efi_capsule_loader` crashes the AMI firmware inside the runtime service `UpdateCapsule` (`[Firmware Bug]: Page fault caused by firmware`). The kernel then disables EFI runtime services. fwupd does not use this path: it applies capsules at the next boot with its own EFI program, before the OS starts. That route was not tested here. Some distributions do not ship the `capsule-loader` module at all.
- **Capsule on Disk does not work:** the firmware does not support it (`OsIndicationsSupported` = `0x1B`, bit `0x04` missing).
- **What works:** calling `UpdateCapsule` from the UEFI Shell before any OS is running, with `CapsuleApp.efi` (EDK2). `build-stick.py` adds the capsule header.

**Intel ME (Management Engine)**
- Lenovo ships the ME update as `FWUpdate.bin` (a raw Intel ME image, starting with an `$FPT` partition table) together with Intel's Windows tool `FWUpdLcl64.exe`. The UEFI version of that tool is not publicly available.
- The firmware also accepts the ME image as an update capsule for the ME's ESRT entry. `build-stick.py` puts an `EFI_CAPSULE_HEADER` with the ME's ESRT GUID in front of `FWUpdate.bin`, and `me.nsh` passes it to `CapsuleApp.efi`, the same way as the EC.
- The ME's ESRT GUID is not part of Lenovo's package and has to be given with `--me-guid`. On M4W it is `7fd2a343-8917-48a5-8aa6-b47edd8431cd`. On other models, boot the stick and run `info.nsh`, or read `/sys/firmware/efi/esrt/entries/*/fw_class` on Linux. On M4W the ESRT has three entries: BIOS, EC (its GUID is printed by `build-stick.py`) and ME. If it is not clear which entry is the ME, do not guess.
- If Lenovo offers the ME for the model as a Windows firmware driver package (`.inf` + `.bin`, e.g. via the Microsoft Update Catalog), `build-stick.py` takes the GUID from the `.inf` instead, like for the EC.

**TPM (Trusted Platform Module)**
- Lenovo publishes TPM firmware updates for these desktops on LVFS (fwupd.org), as a Windows firmware driver package (`.inf` + `.bin`) like the EC. `build-stick.py` adds the capsule header and `tpm.nsh` passes it to `CapsuleApp.efi`. fwupd offers the same file and applies it at the next boot with its own EFI program; that route was not tested here. This stick is an alternative where fwupd is not available or not wanted.
- **Be careful, a TPM firmware update is different from the other updates:**
  - It may erase all keys stored in the TPM. Lenovo does not state whether a given update keeps them. Even if the keys survive, the TPM's measurements change, so anything sealed to them can no longer be unsealed.
  - Before flashing, make sure nothing depends on the TPM or that there is another way in: disk encryption unlocked by the TPM (BitLocker, LUKS with `systemd-cryptenroll`/clevis), TPM-bound SSH or VPN keys, attestation. Have recovery keys or passphrases ready. Virtual TPMs of VMs (swtpm) are software and not affected.
  - Infineon TPMs allow only a limited number of firmware updates over their lifetime. Do not flash the same TPM update repeatedly.
- Lenovo's TPM package for M4W (`TPM1524_P3.bin`, TPM 15.24.18954.0) contains a second payload for the EC GUID, together with an EC update driver: an EC image built in September 2021 whose platform tag is `M4G`, not `M4W`. Windows Update and fwupd deliver this capsule unchanged, and `build-stick.py` keeps it as Lenovo ships it, but prints a warning for every payload that does not belong to the package's own GUID. Whether the firmware ignores or applies the EC payload is not known. That is why the EC is flashed **after** the TPM: `ec.nsh` brings the EC to the intended version, as long as the machine still boots.

## Requirements

- Build host: macOS or Linux (Debian, Ubuntu, Fedora, Arch, …) with Python 3.9 or newer
- `innoextract` for Lenovo's `.exe` packages: `brew install innoextract`, `apt install innoextract` or `dnf install innoextract`. Alternatively extract the `.exe` elsewhere and put the folder into `import/`.
- `cabextract` only for `.cab` packages (TPM from LVFS, ME from the Microsoft Update Catalog)
- USB stick, FAT32, MBR (will be erased)
- Recovery keys or passphrases for disk encryption unlocked by the TPM (BitLocker, LUKS with `systemd-cryptenroll`/clevis). Turning Secure Boot off and BIOS updates change the TPM measurements, and a TPM update may erase its keys, so the TPM may refuse to unlock the disk on the next boot.
- In the BIOS: Secure Boot **off** (the UEFI Shell is unsigned), and back on after the last update. No supervisor password, or the BIOS option that requires it for flashing (on M4W "Require SVP when Flashing") set to No.

## 1. Downloads into `import/`

`<platform>` is Lenovo's platform code of the model (usually three characters), it is the start of all its firmware file names (e.g. `m4w`). The names below follow Lenovo's usual scheme; take the actual files from the support page of the model.

| Component | File | Source |
|---|---|---|
| BIOS | USB package `<platform>jt<ver>usa.zip`, **not** the `.exe` | Lenovo support page of the model. If only the Windows `.exe` is listed, the name can usually be derived by replacing `y` with `t` (`<platform>jy<ver>usa.exe` becomes `<platform>jt<ver>usa.zip`) and downloading from the same directory as the `.exe` (for ThinkCentre e.g. `https://download.lenovo.com/pccbbs/thinkcentre_bios/`) |
| EC | `<platform>ct<ver>.exe` | Lenovo support page of the model, "EC Firmware Update" |
| ME | `<version>_corporate.exe` (contains `FWUpdate.bin`), needs `--me-guid` | Lenovo support page of the model, Intel ME Firmware |
| TPM | Lenovo's TPM firmware package from LVFS (`.cab` or extracted, `.inf` + `.bin`), e.g. `ThinkCentre-Tiny9_8L-TPMFirmware-1524.cab` | [fwupd.org](https://fwupd.org/lvfs/search?value=TPM), search for the model; `fwupdmgr get-updates` on Linux shows whether one is offered. Needs `cabextract` for `.cab` |
| ME (alternative) | Windows firmware driver package with `.inf` + `.bin` (`.cab` or extracted) | Microsoft Update Catalog, if available for the model |

Only add the packages you want to update, and only one package per component. Downloads can go anywhere into `import/`, subfolders are fine. An already extracted BIOS USB package needs its own subfolder (`import/<name>/`). File names are matched case-insensitively.

## 2. Build the stick

```sh
python3 tools/build-stick.py                               # BIOS, TPM and EC
python3 tools/build-stick.py --me-guid <ME-ESRT-GUID>      # plus ME
```

`<ME-ESRT-GUID>` is the ME's ESRT GUID of your model (on M4W `7fd2a343-8917-48a5-8aa6-b47edd8431cd`, see Intel ME under [Why this approach](#why-this-approach)). The build refuses a GUID that belongs to the TPM or EC package.

The script unpacks the packages, takes the BIOS flash command from Lenovo's `startup.nsh` and writes it into `uefi.nsh`, validates the TPM and EC capsules (FMP format, a payload for the GUID from the `.inf`, warns about payloads for other GUIDs) and turns `FWUpdate.bin` into an ME capsule (checks the `$FPT` signature). It refuses to guess: more than one BIOS package, several different flash commands in `startup.nsh` or a firmware package that is not clearly TPM, EC or ME stop the build or are skipped with a warning. Output in `output/`:
- `output/stick/`: stick content
- `output/SHA256SUMS`: checksums of the stick content

The stick is built in `output/stick.partial/` and only renamed to `output/stick/` when everything succeeded, so after an aborted build there is no `output/stick/` at all.

At the end it prints what it found, including the BIOS flash command and the TPM and EC versions with the expected ESRT values.

Copy the content to the stick. `diskX` and `sdX` are placeholders: replace them with the device of the USB stick and double-check it, the commands erase the whole disk.

**macOS:**
```sh
diskutil list                                     # find the stick, e.g. /dev/diskX
diskutil eraseDisk FAT32 FWSTICK MBRFormat /dev/diskX   # ERASES diskX
cp -R output/stick/ /Volumes/FWSTICK/             # content, not the folder
diskutil eject /dev/diskX
```

**Linux:**
```sh
lsblk                                             # find the stick, e.g. /dev/sdX
sudo umount /dev/sdX1                             # only if mounted (desktops mount sticks automatically), repeat for other partitions
sudo parted /dev/sdX --script mklabel msdos mkpart primary fat32 1MiB 100%   # ERASES sdX
sudo mkfs.vfat -F 32 -n FWSTICK /dev/sdX1
sudo mount /dev/sdX1 /mnt && sudo cp -r output/stick/. /mnt/ && sudo umount /mnt
```

## 3. Flashing

**Order: UEFI/BIOS, then TPM, then EC, then ME.** The EC comes after the TPM because Lenovo's TPM package may also contain an EC image (see TPM under [Why this approach](#why-this-approach)); flashing the EC afterwards makes sure it ends up at the version you chose. One update per boot. Never cut power while flashing. **Nothing is flashed automatically**, every update is started by hand.

For each update:

1. Plug in the stick, press **F12** at boot and select the USB stick. The UEFI Shell starts and shows the drive of the stick, e.g. `Firmware stick found on fs1:`. If several drives are shown (e.g. two firmware sticks plugged in), unplug the others and reboot.
2. Switch to that drive. Type the drive name exactly as shown, including the colon, and press **Enter**:
   ```
   fs1:
   ```
   The prompt changes to `FS1:\>`.
3. Optional check: type `ls` and press **Enter**. The list must show `uefi.nsh`, `tpm.nsh`, `ec.nsh` etc.
4. Type the script for the update and press **Enter**, e.g.:
   ```
   uefi.nsh
   ```
5. The script shows what it is going to flash and waits: press **q** to abort, any other key to flash.
6. Wait for the reboot. For the next update, start again at step 1.

Every boot of the stick and every started update is appended to `update-log.txt` on the stick, with date, time and the firmware versions from the ESRT. An update script writes the versions before the flash, the next boot the versions after it, so the log shows the result of each update. Open the file on any computer after the last update.

| Command | Effect |
|---|---|
| `uefi.nsh` | deletes the AMI flag, runs the flash command from Lenovo's USB package, as printed by `build-stick.py` (M4W: `flash2.efi imageM4W.cap /bb /rsmb /bgd`). Reboots on its own |
| `tpm.nsh` | **read the TPM notes under [Why this approach](#why-this-approach) first.** Deletes the AMI flag, runs `CapsuleApp.efi tpm\TPM.bin`. The system reboots and flashes the TPM |
| `ec.nsh` | deletes the AMI flag, runs `CapsuleApp.efi ec\EC.bin`. The system reboots and flashes the EC |
| `me.nsh` | deletes the AMI flag, runs `CapsuleApp.efi me\ME.bin`. The system reboots and flashes the ME |
| `info.nsh` | firmware versions from the ESRT, AMI flag, capsule results, on screen only. Changes nothing |
| `reset` | reboot |

## 4. Verify on Linux

Works on any common distribution (Debian, Ubuntu, Fedora, RHEL, Arch, …) booted in UEFI mode:

```sh
for e in /sys/firmware/efi/esrt/entries/*; do echo "$(cat $e/fw_class) ver=$(cat $e/fw_version) last=$(cat $e/last_attempt_status)"; done
ls /sys/firmware/efi/efivars/ | grep -i capsule      # AmiCapsuleImageLoaded must NOT exist
cat /sys/class/dmi/id/bios_version
cat /sys/class/mei/mei0/fw_ver                       # needs the mei_me module
```

The EC GUID is printed by `build-stick.py` (taken from the `.inf`). Example M4W: BIOS `8a15883b-8f20-4aaf-a7d8-ceb35316fb3d`, EC `44b9f325-7ea2-4c2a-a8b1-a67e0c554932`, ME `7fd2a343-8917-48a5-8aa6-b47edd8431cd`. On M4W the version is `major << 16 | minor` (EC 1.21 = 65557). `last_attempt_status` (UEFI specification): 0 = success, 1 = unsuccessful, 2 = insufficient resources, 3 = incorrect version, 4 = invalid format, 5 = authentication error, 6 = power (AC not connected), 7 = power (battery too low), 8 = unsatisfied dependencies.

## Repository contents

| Path | Purpose |
|---|---|
| `tools/build-stick.py` | builds `output/` from `import/` |
| `efi/*.nsh` | shell script templates, `{UEFI_IMAGE}` and `{UEFI_CMD}` are filled in at build time |
| `tools/efi/BOOTX64.EFI` | EDK2 UEFI Shell (full build including `dmpstore`) |
| `tools/efi/CapsuleApp.efi` | EDK2 CapsuleApp |
| `tools/efi/LICENSE-EDK2.txt` | EDK2 license (BSD-2-Clause-Patent) for both binaries |
| `tools/build-edk2.sh` | rebuilds both EDK2 tools from source (Linux) |
| `tests/` | tests for `build-stick.py` with synthetic packages: `python3 -m unittest discover -s tests` |

The two EDK2 tools are kept as binaries because EDK2 publishes no official builds of them and building requires a Linux toolchain. They are built unmodified from tag `edk2-stable202605` (commit `b03a21a63e3bd001f52c527e5a57feddb53a690b`), GCC 13.3, RELEASE X64.

The firmware images are signed by Lenovo or Intel and verified by the firmware or the flash tool, so a tampered image is expected to be rejected, not flashed. This does not cover Lenovo's flash tools themselves (`flash2.efi`, `AfuEfix64.efi`): with Secure Boot off they run unchecked, so only use them from Lenovo's own download.

### Rebuilding the EDK2 tools

The build directory ends up in the binaries (debug path, source file names). To keep it neutral, build in a container:

```sh
docker run --rm -v "$PWD":/src -w /src -e EDK2_WORK=/build ubuntu:24.04 bash -c \
  'apt-get update && apt-get install -y git gcc g++ make nasm python3 python-is-python3 uuid-dev && tools/build-edk2.sh'
```

This container build is reproducible: it overwrites `tools/efi/BOOTX64.EFI` and `tools/efi/CapsuleApp.efi` with bit-identical files, so `git status` showing no change afterwards verifies the committed binaries. On a Linux host with these packages installed, `tools/build-edk2.sh` also works directly, but embeds the host's build path.

## Recovery

If a BIOS flash is interrupted and the system no longer boots: boot block recovery via the Clear CMOS jumper. Copy the content of Lenovo's recovery ISO of the model (`<platform>j9<ver>usa.iso`) to a FAT32 stick, set the jumper to "Clear", plug the stick into a rear port, power on. Details are in the Hardware Maintenance Manual of the model.

There is no such recovery for the TPM, EC or ME: if one of these flashes fails and the system no longer powers on, only Lenovo service can repair it.

## License

MIT, see [LICENSE](LICENSE). The EDK2 binaries in `tools/efi/` are licensed under BSD-2-Clause-Patent, see [tools/efi/LICENSE-EDK2.txt](tools/efi/LICENSE-EDK2.txt). Lenovo and Intel firmware and tools are not part of this repository and remain under their vendors' terms.

Written by Michael Mayer with love and the help of Claude Code.
