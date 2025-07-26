#!/bin/bash
# Test script to verify voicepipe-fast locking mechanism

echo "Testing rapid toggle calls to voicepipe-fast..."
echo "This will attempt to call toggle 5 times in rapid succession"
echo ""

# Call voicepipe-fast toggle multiple times in background
for i in {1..5}; do
    echo "Calling toggle #$i"
    /home/pepper/.local/src/voicepipe/voicepipe-fast toggle &
done

# Wait for all background processes to complete
wait

echo ""
echo "Test complete. Check if only one recording was started."
echo "You can verify with: systemctl --user status voicepipe.service"