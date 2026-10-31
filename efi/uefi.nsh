@echo -off
if not exist \uefi\{UEFI_IMAGE} then
  echo "uefi\{UEFI_IMAGE} missing (no BIOS package on this stick?), abort."
  goto END
endif
echo "UEFI/BIOS update: {UEFI_IMAGE}. Do not power off."
echo "Press q to abort, any other key to flash."
pause -q
echo " " >>a \update-log.txt
echo "=== uefi.nsh: flashing {UEFI_CMD}, firmware versions before:" >>a \update-log.txt
date >>a \update-log.txt
time >>a \update-log.txt
\CapsuleApp.efi -E >>a \update-log.txt
echo "Clearing stuck AMI capsule flag (error 'not found' is fine) ..."
dmpstore -d AmiCapsuleImageLoaded -guid 67a772e6-d717-4ac1-8380-4bf04b340852
cd \uefi
{UEFI_CMD}
cd \
:END
