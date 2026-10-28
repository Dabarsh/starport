"""Starport menu bar app: status item + popover, Settings, Log and first-run windows.

The windows are WKWebViews showing ui/*.html; pages post JSON messages here and
Python pushes state back with window.render(state). All AppKit calls run on the main thread.
"""

from __future__ import annotations

import csv
import fcntl
import json
import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

import objc
from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSApp,
    NSAppearance,
    NSAppearanceNameDarkAqua,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSColor,
    NSMakeRect,
    NSMenu,
    NSMenuItem,
    NSMinYEdge,
    NSModalResponseOK,
    NSOpenPanel,
    NSPasteboard,
    NSPasteboardTypeString,
    NSPopover,
    NSPopoverBehaviorTransient,
    NSSavePanel,
    NSStatusBar,
    NSVariableStatusItemLength,
    NSViewController,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
    NSWorkspace,
)
from Foundation import NSURL, NSBundle, NSObject, NSTimer
from PyObjCTools import AppHelper
from WebKit import WKWebView, WKWebViewConfiguration

import icons
import lrc
import settings as settings_mod
from engine import Engine

HERE = Path(__file__).resolve().parent
UI_DIR = Path(os.environ.get("RESOURCEPATH", HERE)) / "ui"
if not UI_DIR.is_dir():
    UI_DIR = HERE / "ui"

PRIVACY_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
AGENT_LABEL = "com.starport.app"

WKScriptMessageHandler = objc.protocolNamed("WKScriptMessageHandler")


def _srgb(hex_color: str) -> NSColor:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0)


class Bridge(NSObject, protocols=[WKScriptMessageHandler]):
    def userContentController_didReceiveScriptMessage_(self, controller, message):
        try:
            msg = json.loads(str(message.body()))
        except ValueError:
            return
        self.handler(msg)


class WebPane:
    def __init__(self, page: str, width: float, height: float, handler):
        config = WKWebViewConfiguration.alloc().init()
        self.bridge = Bridge.alloc().init()
        self.bridge.handler = handler
        config.userContentController().addScriptMessageHandler_name_(self.bridge, "starport")
        self.view = WKWebView.alloc().initWithFrame_configuration_(NSMakeRect(0, 0, width, height), config)
        self.view.setValue_forKey_(False, "drawsBackground")
        self.ready = False
        url = NSURL.fileURLWithPath_(str(UI_DIR / page))
        self.view.loadFileURL_allowingReadAccessToURL_(url, NSURL.fileURLWithPath_(str(UI_DIR)))

    def render(self, data: dict) -> None:
        if not self.ready:
            return
        js = "window.render && window.render(" + json.dumps(data) + ")"
        self.view.evaluateJavaScript_completionHandler_(js, None)


class Window:
    def __init__(self, title: str, page: str, width: float, height: float, handler, resizable=False):
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable
        if resizable:
            style |= NSWindowStyleMaskResizable
        self.win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, width, height), style, NSBackingStoreBuffered, False)
        self.win.setTitle_(title)
        self.win.setReleasedWhenClosed_(False)
        self.win.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua))
        self.win.setBackgroundColor_(_srgb("#1E1E1E"))
        if resizable:
            self.win.setContentMinSize_((640, 360))
        self.pane = WebPane(page, width, height, handler)
        self.win.setContentView_(self.pane.view)
        self.win.center()

    @property
    def visible(self) -> bool:
        return bool(self.win.isVisible())

    def show(self) -> None:
        NSApp.activateIgnoringOtherApps_(True)
        self.win.makeKeyAndOrderFront_(None)


# launch at login

def in_app_bundle() -> bool:
    return NSBundle.mainBundle().bundlePath().endswith(".app") and bool(getattr(sys, "frozen", False))


def set_launch_at_login(enabled: bool) -> str | None:
    """Signed .app: SMAppService (macOS 13+). From source: a LaunchAgent. Returns an error message or None."""
    if in_app_bundle():
        try:
            from ServiceManagement import SMAppService

            svc = SMAppService.mainAppService()
            ok, err = (svc.registerAndReturnError_ if enabled else svc.unregisterAndReturnError_)(None)
            return None if ok else str(err.localizedDescription()) if err else "could not change login item"
        except Exception as e:
            return str(e)
    agent = Path.home() / "Library" / "LaunchAgents" / f"{AGENT_LABEL}.plist"
    try:
        if enabled:
            agent.parent.mkdir(parents=True, exist_ok=True)
            with open(agent, "wb") as f:
                plistlib.dump({
                    "Label": AGENT_LABEL,
                    "ProgramArguments": [sys.executable, str(HERE / "app.py")],
                    "RunAtLoad": True,
                    "ProcessType": "Interactive",
                }, f)
        else:
            agent.unlink(missing_ok=True)
    except OSError as e:
        return str(e)
    return None


class AppDelegate(NSObject):
    def init(self):
        self = objc.super(AppDelegate, self).init()
        if self is None:
            return None
        self.engine = Engine()
        self.pending_refresh = False
        self.last_log_push = 0.0
        self.anim_timer = None
        self.anim_phase = 0.0
        self.icon_state = None
        self.copied_path = False
        self.windows = {}
        return self

    # lifecycle

    def applicationDidFinishLaunching_(self, note):
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        self.build_main_menu()

        self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        button = self.status_item.button()
        button.setImage_(icons.status_image("idle"))
        button.setTarget_(self)
        button.setAction_("togglePopover:")

        self.popover_pane = WebPane("popover.html", 340, 620, self.on_popover)
        vc = NSViewController.alloc().init()
        vc.setView_(self.popover_pane.view)
        self.popover = NSPopover.alloc().init()
        self.popover.setContentViewController_(vc)
        self.popover.setContentSize_((340, 620))
        self.popover.setBehavior_(NSPopoverBehaviorTransient)
        self.popover.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua))

        self.engine.add_listener(self.schedule_refresh)
        self.engine.start()
        self.refresh()
        if not self.engine.settings.onboarded:
            self.show_window("onboarding")
        elif os.environ.get("STARPORT_OPEN"):
            # Dev convenience: STARPORT_OPEN=settings,log opens those windows at launch.
            for name in os.environ["STARPORT_OPEN"].split(","):
                if name == "popover":
                    AppHelper.callLater(0.5, self.togglePopover_, None)
                elif name in ("settings", "log", "onboarding"):
                    self.show_window(name)

    def applicationWillTerminate_(self, note):
        self.engine.shutdown()

    def applicationShouldHandleReopen_hasVisibleWindows_(self, app, visible):
        self.togglePopover_(None)
        return True

    @objc.python_method
    def build_main_menu(self):
        """Hidden in an accessory app, but gives text fields copy/paste and windows ⌘W."""
        bar = NSMenu.alloc().init()
        app_item = NSMenuItem.alloc().init()
        app_menu = NSMenu.alloc().init()
        app_menu.addItemWithTitle_action_keyEquivalent_("Settings…", "openSettings:", ",").setTarget_(self)
        app_menu.addItemWithTitle_action_keyEquivalent_("Quit Starport", "terminate:", "q")
        app_item.setSubmenu_(app_menu)
        bar.addItem_(app_item)
        edit_item = NSMenuItem.alloc().init()
        edit = NSMenu.alloc().initWithTitle_("Edit")
        for title, action, key in (("Undo", "undo:", "z"), ("Redo", "redo:", "Z"), ("Cut", "cut:", "x"),
                                   ("Copy", "copy:", "c"), ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")):
            edit.addItemWithTitle_action_keyEquivalent_(title, action, key)
        edit_item.setSubmenu_(edit)
        bar.addItem_(edit_item)
        win_item = NSMenuItem.alloc().init()
        win = NSMenu.alloc().initWithTitle_("Window")
        win.addItemWithTitle_action_keyEquivalent_("Close", "performClose:", "w")
        win.addItemWithTitle_action_keyEquivalent_("Log", "openLog:", "l").setTarget_(self)
        win_item.setSubmenu_(win)
        bar.addItem_(win_item)
        NSApp.setMainMenu_(bar)

    # state -> UI

    @objc.python_method
    def schedule_refresh(self):
        """Called from engine threads; coalesce and hop to the main thread."""
        if self.pending_refresh:
            return
        self.pending_refresh = True
        AppHelper.callAfter(self.refresh)

    @objc.python_method
    def refresh(self):
        self.pending_refresh = False
        st = self.engine.state()
        st["problems"] = self.engine.settings.problems() if self.engine.settings.source_folder else []
        self.set_icon(st["status"], st["label"])
        self.popover_pane.render(st)
        w = self.windows.get("settings")
        if w and w.visible:
            w.pane.render(st)
        w = self.windows.get("onboarding")
        if w and w.visible:
            w.pane.render(self.onboarding_state(st))
        w = self.windows.get("log")
        if w and w.visible and time.monotonic() - self.last_log_push > 0.5:
            self.last_log_push = time.monotonic()
            w.pane.render({"events": self.engine.events(200)})

    @objc.python_method
    def onboarding_state(self, st: dict) -> dict:
        s = self.engine.settings
        problem = s.problems()[0] if s.source_folder and s.problems() else ""
        readable = False
        if s.source_folder and not problem:
            try:
                os.listdir(s.source)
                readable = True
            except PermissionError:
                readable = False
            except OSError:
                problem = "Folder not found. Connect the drive or choose another folder"
        st["onboarding"] = {"readable": readable, "problem": problem, "copied": self.copied_path}
        return st

    @objc.python_method
    def set_icon(self, state: str, label: str):
        if state == "working":
            if self.anim_timer is None:
                self.anim_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    0.12, self, "animate:", None, True)
        elif self.anim_timer is not None:
            self.anim_timer.invalidate()
            self.anim_timer = None
        if state != self.icon_state:
            self.icon_state = state
            self.status_item.button().setImage_(icons.status_image(state, self.anim_phase))
        self.status_item.button().setToolTip_(f"Starport · {label}")

    def animate_(self, timer):
        self.anim_phase = (self.anim_phase - 0.5) % 10
        self.status_item.button().setImage_(icons.status_image("working", self.anim_phase))

    # actions

    def togglePopover_(self, sender):
        if self.popover.isShown():
            self.popover.performClose_(None)
            return
        button = self.status_item.button()
        NSApp.activateIgnoringOtherApps_(True)
        self.popover.showRelativeToRect_ofView_preferredEdge_(button.bounds(), button, NSMinYEdge)
        self.popover.contentViewController().view().window().makeKeyWindow()
        self.refresh()

    @objc.python_method
    def close_popover(self):
        if self.popover.isShown():
            self.popover.performClose_(None)

    def openSettings_(self, sender):
        self.show_window("settings")

    def openLog_(self, sender):
        self.show_window("log")

    @objc.python_method
    def show_window(self, name: str):
        self.close_popover()
        w = self.windows.get(name)
        if w is None:
            if name == "settings":
                w = Window("Starport Settings", "settings.html", 840, 700, self.on_settings)
            elif name == "log":
                w = Window("Log", "log.html", 860, 520, self.on_log, resizable=True)
            else:
                w = Window("", "onboarding.html", 680, 460, self.on_onboarding)
            self.windows[name] = w
        w.show()
        self.refresh()

    @objc.python_method
    def choose_folder(self, key: str, parent):
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseDirectories_(True)
        panel.setCanChooseFiles_(False)
        panel.setCanCreateDirectories_(True)
        panel.setAllowsMultipleSelection_(False)
        panel.setPrompt_("Choose")
        current = getattr(self.engine.settings, "source" if key == "source_folder" else "staging")
        if current and os.path.isdir(current):
            panel.setDirectoryURL_(NSURL.fileURLWithPath_(current))
        elif key == "source_folder" and os.path.isdir("/Volumes"):
            panel.setDirectoryURL_(NSURL.fileURLWithPath_("/Volumes"))
        NSApp.activateIgnoringOtherApps_(True)
        if panel.runModal() == NSModalResponseOK and panel.URLs():
            path = str(panel.URLs()[0].path())
            home = str(Path.home())
            if path.startswith(home + os.sep):
                path = "~" + path[len(home):]
            self.engine.update_settings(**{key: path})
        if parent is not None:
            parent.win.makeKeyAndOrderFront_(None)

    @objc.python_method
    def open_staging(self):
        path = self.engine.settings.staging
        os.makedirs(path, exist_ok=True)
        NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(path))

    @objc.python_method
    def open_lightroom(self):
        try:
            lrc.open_lightroom()
        except lrc.LightroomNotFound as e:
            self.alert("Lightroom Classic not found", f"{e}. Starport still copies keepers to the staging folder.")

    @objc.python_method
    def alert(self, title: str, text: str, confirm: str | None = None) -> bool:
        a = NSAlert.alloc().init()
        a.setMessageText_(title)
        a.setInformativeText_(text)
        if confirm:
            a.addButtonWithTitle_(confirm)
            a.addButtonWithTitle_("Cancel")
        NSApp.activateIgnoringOtherApps_(True)
        return a.runModal() == NSAlertFirstButtonReturn

    @objc.python_method
    def set_setting(self, key: str, value):
        known = set(settings_mod.Settings.__dataclass_fields__) - {"paused", "onboarded"}
        if key not in known:
            return
        before = getattr(self.engine.settings, key)
        self.engine.update_settings(**{key: value})
        if key == "launch_at_login" and before != value:
            err = set_launch_at_login(bool(value))
            if err:
                self.alert("Couldn't change Launch at login", err)

    # page messages

    @objc.python_method
    def on_popover(self, msg):
        cmd = msg.get("cmd")
        if cmd == "ready":
            self.popover_pane.ready = True
            self.refresh()
        elif cmd == "togglePause":
            (self.engine.resume if self.engine.settings.paused else self.engine.pause)()
        elif cmd == "ingestNow":
            self.engine.ingest_now()
        elif cmd == "openStaging":
            self.close_popover()
            self.open_staging()
        elif cmd == "openLightroom":
            self.close_popover()
            self.open_lightroom()
        elif cmd == "openLog":
            self.show_window("log")
        elif cmd == "openSettings":
            self.show_window("settings")
        elif cmd == "quit":
            NSApp.terminate_(None)

    @objc.python_method
    def on_settings(self, msg):
        cmd = msg.get("cmd")
        w = self.windows["settings"]
        if cmd == "ready":
            w.pane.ready = True
            self.refresh()
        elif cmd == "set":
            self.set_setting(msg.get("key"), msg.get("value"))
        elif cmd == "chooseFolder":
            self.choose_folder(msg.get("key"), w)
        elif cmd == "revealData":
            NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(str(settings_mod.support_dir())))
        elif cmd == "clearHistory":
            if self.alert("Clear ingest history?",
                          "Starport forgets which files it has copied or skipped. Files already in staging stay put, "
                          "and the next scan may copy keepers again.", "Clear History"):
                self.engine.clear_history()

    @objc.python_method
    def on_log(self, msg):
        cmd = msg.get("cmd")
        w = self.windows["log"]
        if cmd == "ready":
            w.pane.ready = True
            self.last_log_push = 0
            self.refresh()
        elif cmd == "exportLog":
            self.export_log(w)
        elif cmd == "revealDb":
            NSWorkspace.sharedWorkspace().activateFileViewerSelectingURLs_([NSURL.fileURLWithPath_(self.engine.db.path)])

    @objc.python_method
    def export_log(self, w):
        panel = NSSavePanel.savePanel()
        panel.setNameFieldStringValue_(f"Starport log {time.strftime('%Y-%m-%d')}.csv")
        if panel.runModal() != NSModalResponseOK:
            return
        path = str(panel.URL().path())
        with open(path, "w", newline="") as f:
            out = csv.writer(f)
            out.writerow(["time", "event", "file", "rating", "detail"])
            for e in self.engine.events(200):
                out.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e["ts"])), e["kind"], e["file"],
                              "" if e["rating"] is None else e["rating"], e["detail"]])

    @objc.python_method
    def on_onboarding(self, msg):
        cmd = msg.get("cmd")
        w = self.windows["onboarding"]
        if cmd == "ready":
            w.pane.ready = True
            self.refresh()
        elif cmd == "chooseFolder":
            self.choose_folder("source_folder", w)
        elif cmd == "openPrivacy":
            NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(PRIVACY_URL))
        elif cmd == "copyStaging":
            pb = NSPasteboard.generalPasteboard()
            pb.clearContents()
            pb.setString_forType_(self.engine.settings.staging, NSPasteboardTypeString)
            self.copied_path = True
            self.refresh()
        elif cmd == "recheckAccess":
            self.refresh()
        elif cmd == "openSettings":
            self.show_window("settings")
        elif cmd == "finishSetup":
            os.makedirs(self.engine.settings.staging, exist_ok=True)
            self.engine.update_settings(onboarded=True, paused=False)
            if self.engine.settings.launch_at_login:
                set_launch_at_login(True)
            w.win.close()
            AppHelper.callLater(0.3, self.togglePopover_, None)


def single_instance_lock():
    """Two copies watching the same folder would race; keep one."""
    f = open(settings_mod.support_dir() / "starport.lock", "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("Starport is already running", file=sys.stderr)
        sys.exit(0)
    return f


def main():
    lock = single_instance_lock()  # noqa: F841 (held for the process lifetime)
    app = NSApplication.sharedApplication()
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
