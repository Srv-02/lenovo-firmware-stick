@echo -off
if not exist \ec\EC.bin then
  echo "ec\EC.bin missing, abort."
  goto END
endif
echo "EC update. The system resets and flashes the EC. Do not power off."
echo "Press q to abort, any other key to flash."
pause -q
echo " " >>a \update-log.txt
echo "=== ec.nsh: flashing ec\EC.bin, firmware versions before:" >>a \update-log.txt
date >>a \update-log.txt
time >>a \update-log.txt
\CapsuleApp.efi -E >>a \update-log.txt
echo "Clearing stuck AMI capsule flag (error 'not found' is fine) ..."
dmpstore -d AmiCapsuleImageLoaded -guid 67a772e6-d717-4ac1-8380-4bf04b340852
\CapsuleApp.efi \ec\EC.bin
:END
