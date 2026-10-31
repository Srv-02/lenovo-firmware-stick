@echo -off
echo "=== ESRT: firmware versions and last attempt status ==="
\CapsuleApp.efi -E
echo "=== AMI capsule flag (should NOT exist) ==="
dmpstore AmiCapsuleImageLoaded -guid 67a772e6-d717-4ac1-8380-4bf04b340852
echo "=== Capsule result variables ==="
\CapsuleApp.efi -S
