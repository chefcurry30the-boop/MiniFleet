#!/usr/bin/env bash
# Print detected Mac type and suggested MINIFLEET_NODE_NAME
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
python3 -c "
from minifleet.device import detect_device_type, device_label, suggest_node_name
t = detect_device_type()
print(f'Device:     {device_label(t)} ({t})')
print(f'Suggested:  MINIFLEET_NODE_NAME={suggest_node_name(t)}')
"
