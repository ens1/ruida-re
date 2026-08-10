"""Drive LightBurn's Save Machine File action on macOS."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


LIGHTBURN_APP = Path("/Applications/LightBurn.app")


def _quote_applescript(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _wait_for_process(timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["pgrep", "-x", "LightBurn"],
            check=False,
            capture_output=True,
        )
        if result.returncode == 0:
            return
        time.sleep(0.25)
    raise TimeoutError("LightBurn did not start")


def _automation_script(project: Path, output: Path) -> str:
    project_name = _quote_applescript(project.stem)
    directory = _quote_applescript(str(output.parent))
    filename = _quote_applescript(output.name)
    return f'''
tell application "LightBurn" to activate
tell application "System Events"
    tell process "LightBurn"
        set frontmost to true
        set projectWindow to missing value
        repeat 120 times
            repeat with candidateWindow in windows
                if name of candidateWindow contains "{project_name}" then
                    set projectWindow to candidateWindow
                    exit repeat
                end if
            end repeat
            if projectWindow is not missing value then exit repeat
            delay 0.25
        end repeat
        if projectWindow is missing value then
            error "Project window did not appear"
        end if
        perform action "AXRaise" of projectWindow
        click menu item "Save RD file" of menu "File" of menu bar 1
        repeat 120 times
            if exists window "Save RD file" then exit repeat
            delay 0.25
        end repeat
        if not (exists window "Save RD file") then
            error "Save dialog did not appear"
        end if
        perform action "AXRaise" of window "Save RD file"
        keystroke "g" using {{command down, shift down}}
        delay 0.5
        keystroke "{directory}"
        key code 36
        delay 0.75
        keystroke "a" using {{command down}}
        keystroke "{filename}"
        key code 36
    end tell
end tell
'''


def export(project: Path, output: Path) -> None:
    """Open a project and save its Ruida machine file through LightBurn."""
    project = project.resolve()
    output = output.resolve()
    if not LIGHTBURN_APP.is_dir():
        raise FileNotFoundError(LIGHTBURN_APP)
    if not project.is_file() or project.suffix.lower() != ".lbrn2":
        raise ValueError(f"Not a LightBurn project: {project}")
    if output.suffix.lower() != ".rd":
        raise ValueError(f"Output must end in .rd: {output}")
    if output.exists():
        raise FileExistsError(output)

    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["open", "-a", str(LIGHTBURN_APP), str(project)],
        check=True,
    )
    _wait_for_process()
    time.sleep(2.0)
    subprocess.run(
        ["osascript", "-e", _automation_script(project, output)],
        check=True,
    )

    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if output.is_file() and output.stat().st_size:
            print(output)
            return
        time.sleep(0.25)
    raise TimeoutError(f"LightBurn did not create {output}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        export(args.project, args.output)
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
