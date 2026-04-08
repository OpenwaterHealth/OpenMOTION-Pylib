#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
#  install.sh — Install OpenMotion udev rules on Linux
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RULES_FILE="99-openmotion.rules"
DEST="/etc/udev/rules.d/${RULES_FILE}"

if [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root (or with sudo)."
    echo "Usage:  sudo $0"
    exit 1
fi

echo "Installing OpenMotion udev rules..."
cp "${SCRIPT_DIR}/${RULES_FILE}" "${DEST}"
chmod 644 "${DEST}"
echo "  Copied ${RULES_FILE} -> ${DEST}"

# Ensure the plugdev group exists
if ! getent group plugdev >/dev/null 2>&1; then
    groupadd plugdev
    echo "  Created 'plugdev' group"
fi

# Add current (non-root) user to plugdev if running via sudo
if [ -n "${SUDO_USER:-}" ]; then
    if ! id -nG "${SUDO_USER}" | grep -qw plugdev; then
        usermod -aG plugdev "${SUDO_USER}"
        echo "  Added user '${SUDO_USER}' to 'plugdev' group"
        echo "  (You may need to log out and back in for this to take effect)"
    fi
fi

# Reload udev rules
udevadm control --reload-rules
udevadm trigger
echo "  udev rules reloaded"

echo ""
echo "Done! OpenMotion devices will now be accessible without root."
echo "If a device is currently plugged in, unplug and replug it."
