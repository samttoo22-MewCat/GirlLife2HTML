#!/bin/sh
set -eu
cd "$(dirname "$0")"

supports_builder() {
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

find_python() {
    for candidate in python3 python3.15 python3.14 python3.13 python3.12 python3.11 python3.10 python; do
        if command -v "$candidate" >/dev/null 2>&1 && supports_builder "$candidate"; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

if python_command=$(find_python); then
    exec "$python_command" build_girllife_html.py
fi

printf '%s\n' "Python 3.10 or newer is required. No compatible Python was found."
printf '%s' "Download and install the official Python macOS installer from python.org? [y/N] "
IFS= read -r answer || answer=
case "$answer" in
    y|Y|yes|YES|Yes) ;;
    *) echo "Cancelled. Python was not downloaded or installed."; exit 1 ;;
esac

if ! command -v curl >/dev/null 2>&1 || ! command -v perl >/dev/null 2>&1; then
    echo "Cannot bootstrap Python: curl and Perl are required to locate the official installer."
    exit 1
fi

installer_url=$(curl --compressed -fsSL https://www.python.org/downloads/macos/ |
    perl -0777 -ne 'if (/href="(https:\/\/www\.python\.org\/ftp\/python\/([0-9]+\.[0-9]+\.[0-9]+)\/python-\2-macos11\.pkg)"/) { print "$1\n"; exit }')
if [ -z "$installer_url" ]; then
    echo "Could not find a stable macOS installer on the official Python downloads page."
    exit 1
fi

version=$(printf '%s\n' "$installer_url" | sed -E 's#.*python/([0-9]+\.[0-9]+)\.[0-9]+/.*#\1#')
installer=$(mktemp -t girllife-python-XXXXXX.pkg)
trap 'rm -f "$installer"' EXIT
trap 'exit 1' HUP INT TERM
echo "Downloading the official Python installer: $installer_url"
curl -fL --progress-bar "$installer_url" -o "$installer"

signature=$(pkgutil --check-signature "$installer") || {
    echo "The Python installer signature could not be verified; refusing to open it."
    exit 1
}
if ! printf '%s\n' "$signature" | grep -q 'Python Software Foundation'; then
    echo "The installer is not signed by the Python Software Foundation; refusing to open it."
    exit 1
fi

echo "Opening the signed installer. macOS will ask you to confirm and complete installation."
open -W "$installer"

installed_python="/Library/Frameworks/Python.framework/Versions/$version/bin/python3"
if ! [ -x "$installed_python" ] || ! supports_builder "$installed_python"; then
    if python_command=$(find_python); then
        exec "$python_command" build_girllife_html.py
    fi
    echo "Python installation was not detected. Reopen Terminal and run this script again."
    exit 1
fi

exec "$installed_python" build_girllife_html.py
