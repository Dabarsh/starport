"""Build Starport.app:  python setup.py py2app

Bundle ExifTool first (scripts/fetch_exiftool.sh) so users don't need to install it.
Sign afterwards:  codesign --deep --force --options runtime -s "Developer ID Application: ..." dist/Starport.app
"""

from setuptools import setup

APP = ["app.py"]
DATA_FILES = [
    ("ui", ["ui/popover.html", "ui/settings.html", "ui/log.html", "ui/onboarding.html", "ui/style.css", "ui/bridge.js"]),
]
OPTIONS = {
    "argv_emulation": False,
    "resources": ["resources/exiftool"],
    "includes": ["watchdog.observers.fsevents"],
    "packages": ["watchdog"],
    "plist": {
        "CFBundleName": "Starport",
        "CFBundleDisplayName": "Starport",
        "CFBundleIdentifier": "com.starport.app",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1",
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

setup(app=APP, name="Starport", data_files=DATA_FILES, options={"py2app": OPTIONS}, setup_requires=["py2app"])
