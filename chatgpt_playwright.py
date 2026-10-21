"""Send a prompt to ChatGPT using Hermex and print the response."""

import argparse
import os
import re

from hermex import ChatGPT


def get_chrome_version() -> int | None:
    if os.name != "nt":
        return None

    import winreg

    registry_keys = (
        (winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Google\Chrome\BLBeacon"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"Software\WOW6432Node\Google\Chrome\BLBeacon",
        ),
    )
    for root, key_path in registry_keys:
        try:
            with winreg.OpenKey(root, key_path) as key:
                version, _ = winreg.QueryValueEx(key, "version")
        except FileNotFoundError:
            continue

        match = re.match(r"(\d+)\.", version)
        if match:
            return int(match.group(1))
        raise RuntimeError(f"Could not parse Chrome version from: {version!r}")

    raise RuntimeError(
        "Could not find Chrome's version in the Windows registry. "
        "Pass it explicitly with --chrome-version."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send a prompt to ChatGPT and print its response."
    )
    parser.add_argument("prompt", nargs="+", help="The prompt to send to ChatGPT.")
    parser.add_argument(
        "--chrome-version",
        type=int,
        help="Chrome major version (detected from the Windows registry by default).",
    )
    args = parser.parse_args()

    if args.chrome_version is not None and args.chrome_version < 1:
        parser.error("--chrome-version must be a positive integer.")

    prompt = " ".join(args.prompt).strip()
    chrome_version = (
        args.chrome_version
        if args.chrome_version is not None
        else get_chrome_version()
    )
    print(f"Using Chrome version {chrome_version or 'auto-detect'}.", flush=True)

    chatgpt = ChatGPT(chrome_version=chrome_version)
    try:
        print("Opening ChatGPT...", flush=True)
        chatgpt.open_url().short_wait()
        print("Sending prompt and waiting for the response...", flush=True)
        response = chatgpt.query(prompt, paste=True)
    finally:
        chatgpt.close()

    if response.text is None:
        raise RuntimeError("ChatGPT returned no text response.")

    print(response.text)


if __name__ == "__main__":
    main()