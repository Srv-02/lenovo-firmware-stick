@echo -off
if not exist \tpm\TPM.bin then
  echo "tpm\TPM.bin missing, abort."
  goto END
endif
echo "TPM firmware update. This may erase all keys stored in the TPM."
echo "Anything sealed to the TPM (e.g. disk encryption unlocked by the TPM) must have another way in."
echo "The system resets and flashes the TPM. Do not power off."
echo "Press q to abort, any other key to flash."
pause -q
echo " " >>a \update-log.txt
echo "=== tpm.nsh: flashing tpm\TPM.bin, firmware versions before:" >>a \update-log.txt
date >>a \update-log.txt
time >>a \update-log.txt
\CapsuleApp.efi -E >>a \update-log.txt
echo "Clearing stuck AMI capsule flag (error 'not found' is fine) ..."
dmpstore -d AmiCapsuleImageLoaded -guid 67a772e6-d717-4ac1-8380-4bf04b340852
\CapsuleApp.efi \tpm\TPM.bin
:END
