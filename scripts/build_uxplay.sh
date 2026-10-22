#!/usr/bin/env bash
set -euo pipefail
# Separate upstream checkout, never vendored into the MIT firmware.
cd "$(dirname "$0")/.."
checkout="${UXPLAY_CHECKOUT:-.local/uxplay}"
commit=0477d471cc5899ea13382b5871cf70dbfe050ebd
if [[ ! -d "$checkout/.git" ]]; then
  git clone https://github.com/FDH2/UxPlay.git "$checkout"
fi
git -C "$checkout" checkout --detach "$commit"
cmake -S "$checkout" -B "$checkout/build" -DCMAKE_BUILD_TYPE=Release
cmake --build "$checkout/build" --parallel 4
printf 'UxPlay executable: %s/build/uxplay\n' "$checkout"
