#!/bin/zsh
set -euo pipefail
task_root="${0:A:h:h}"
check_dir="$(mktemp -d /private/tmp/vision-video-check.XXXXXX)"
trap 'rm -rf "$check_dir"' EXIT
xcrun swiftc -swift-version 5 -module-cache-path "$check_dir/modules" \
    "$task_root/ios/VisionLab/VideoModelClient.swift" \
    "$task_root/ios/VisionLab/VideoTransferState.swift" \
    "$task_root/ios/VisionLab/BackgroundVideoTransfer.swift" \
    "$task_root/tools/check_video_wiring.swift" -o "$check_dir/check"
"$check_dir/check"
