@echo -off
# No autostart: nothing is flashed until a script is started by hand.
# Every boot appends the firmware versions to update-log.txt on the stick.
echo " "
echo "Lenovo Firmware Stick"
echo " "
if exist fs0:\uefi.nsh then
  echo "Firmware stick found on fs0:"
  echo " "
  echo "Step 1: switch to the stick. Type this and press Enter:"
  echo "          fs0:"
  echo " "
  echo " " >>a fs0:\update-log.txt
  echo "=== boot: firmware versions (result of the last update)" >>a fs0:\update-log.txt
  date >>a fs0:\update-log.txt
  time >>a fs0:\update-log.txt
  fs0:\CapsuleApp.efi -E >>a fs0:\update-log.txt
endif
if exist fs1:\uefi.nsh then
  echo "Firmware stick found on fs1:"
  echo " "
  echo "Step 1: switch to the stick. Type this and press Enter:"
  echo "          fs1:"
  echo " "
  echo " " >>a fs1:\update-log.txt
  echo "=== boot: firmware versions (result of the last update)" >>a fs1:\update-log.txt
  date >>a fs1:\update-log.txt
  time >>a fs1:\update-log.txt
  fs1:\CapsuleApp.efi -E >>a fs1:\update-log.txt
endif
if exist fs2:\uefi.nsh then
  echo "Firmware stick found on fs2:"
  echo " "
  echo "Step 1: switch to the stick. Type this and press Enter:"
  echo "          fs2:"
  echo " "
  echo " " >>a fs2:\update-log.txt
  echo "=== boot: firmware versions (result of the last update)" >>a fs2:\update-log.txt
  date >>a fs2:\update-log.txt
  time >>a fs2:\update-log.txt
  fs2:\CapsuleApp.efi -E >>a fs2:\update-log.txt
endif
if exist fs3:\uefi.nsh then
  echo "Firmware stick found on fs3:"
  echo " "
  echo "Step 1: switch to the stick. Type this and press Enter:"
  echo "          fs3:"
  echo " "
  echo " " >>a fs3:\update-log.txt
  echo "=== boot: firmware versions (result of the last update)" >>a fs3:\update-log.txt
  date >>a fs3:\update-log.txt
  time >>a fs3:\update-log.txt
  fs3:\CapsuleApp.efi -E >>a fs3:\update-log.txt
endif
if exist fs4:\uefi.nsh then
  echo "Firmware stick found on fs4:"
  echo " "
  echo "Step 1: switch to the stick. Type this and press Enter:"
  echo "          fs4:"
  echo " "
  echo " " >>a fs4:\update-log.txt
  echo "=== boot: firmware versions (result of the last update)" >>a fs4:\update-log.txt
  date >>a fs4:\update-log.txt
  time >>a fs4:\update-log.txt
  fs4:\CapsuleApp.efi -E >>a fs4:\update-log.txt
endif
if exist fs5:\uefi.nsh then
  echo "Firmware stick found on fs5:"
  echo " "
  echo "Step 1: switch to the stick. Type this and press Enter:"
  echo "          fs5:"
  echo " "
  echo " " >>a fs5:\update-log.txt
  echo "=== boot: firmware versions (result of the last update)" >>a fs5:\update-log.txt
  date >>a fs5:\update-log.txt
  time >>a fs5:\update-log.txt
  fs5:\CapsuleApp.efi -E >>a fs5:\update-log.txt
endif
echo "Step 2: type one script name and press Enter (one update per boot):"
echo "          uefi.nsh     1. UEFI/BIOS update"
echo "          tpm.nsh      2. TPM firmware update (may erase TPM keys, see README)"
echo "          ec.nsh       3. EC update"
echo "          me.nsh       4. Intel ME update"
echo "          info.nsh     show firmware versions, changes nothing"
echo "          reset        reboot"
echo " "
echo "Each update script asks once more before flashing: q aborts."
echo "Log of all boots and updates: update-log.txt on the stick."
echo " "
