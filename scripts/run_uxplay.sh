#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
binary="${UXPLAY_BINARY:-.local/uxplay/build/uxplay}"
if [[ "$(uname -s)" == Darwin ]] && command -v brew >/dev/null; then
  # Use just the media plugins needed for this headless experiment. Some
  # Homebrew gst-python/GI combinations hang during global plugin discovery.
  prefix="$(brew --prefix)"
  plugins="$PWD/.local/gst-plugins"
  mkdir -p "$plugins"
  for name in coreelements app libav videoparsersbad videoconvertscale audioconvert audioresample audioparsers typefindfunctions playback applemedia osxaudio autodetect volume level jpeg multifile; do
    source_plugin="$prefix/lib/gstreamer-1.0/libgst${name}.dylib"
    if [[ -f "$source_plugin" ]]; then
      ln -sfn "$source_plugin" "$plugins/libgst${name}.dylib"
    fi
  done
  export GST_PLUGIN_SYSTEM_PATH="$plugins"
  export GST_PLUGIN_PATH="$plugins"
  export GST_REGISTRY="$PWD/.local/gst-registry.bin"
fi
# The patched macOS DNS-SD backend advertises locally, preventing the iPhone
# from merging the upstream and C3 services and bypassing the C3 over IPv6.
if [[ "$(uname -s)" == Darwin ]]; then
  export C3_AIRBRIDGE_LOCAL_DNSSD="${C3_AIRBRIDGE_LOCAL_DNSSD:-1}"
fi
exec "$binary" -n C3-Upstream -nh -key .local/uxplay.pem -p tcp 17100,17000,17001 -p udp 16000,16001,16002 \
  -s 480x1040 -fps 5 -vsync no -vs fakesink -as fakesink "$@"
