"""Build Starport.app:  python setup.py py2app

Use scripts/build.sh: it bundles ExifTool, makes watchdog universal, runs the tests, builds,
signs and packages dist/Starport-<version>.dmg and .zip.
"""

from setuptools import setup

from version import BUILD, VERSION

APP = ["app.py"]
DATA_FILES = [
    ("ui", ["ui/popover.html", "ui/settings.html", "ui/log.html", "ui/onboarding.html", "ui/style.css", "ui/bridge.js"]),
]
OPTIONS = {
    "argv_emulation": False,
    "iconfile": "resources/Starport.icns",
    "resources": ["resources/exiftool"],
    "includes": ["watchdog.observers.fsevents"],
    "packages": ["watchdog"],
    "plist": {
        "CFBundleName": "Starport",
        "CFBundleDisplayName": "Starport",
        "CFBundleIdentifier": "com.starport.app",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": BUILD,
        "LSUIElement": True,  # menu bar only, no Dock icon
        "LSMinimumSystemVersion": "13.0",
        "NSHumanReadableCopyright": "",
        "NSRemovableVolumesUsageDescription": "Starport reads star ratings from photos on cards and external drives.",
        "NSDesktopFolderUsageDescription": "Starport reads star ratings from photos in the folder you choose.",
        "NSDocumentsFolderUsageDescription": "Starport reads star ratings from photos in the folder you choose.",
        "NSDownloadsFolderUsageDescription": "Starport reads star ratings from photos in the folder you choose.",
        "NSAppleEventsUsageDescription": "Starport shows batch notifications.",
    },
}

setup(app=APP, name="Starport", version=VERSION, data_files=DATA_FILES, options={"py2app": OPTIONS})
