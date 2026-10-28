import threading
import time

from watcher import Watcher


def test_slow_copy_waits_for_settle(tmp_path):
    ready = []
    got = threading.Event()

    def on_ready(images, sidecar_images):
        ready.extend(images)
        got.set()

    w = Watcher(str(tmp_path), settle=1.5, on_ready=on_ready, poll=0.2)
    w.start()
    try:
        time.sleep(0.8)
        target = tmp_path / "IMG_1.CR3"
        with open(target, "wb") as f:  # a card copy trickling in
            for _ in range(6):
                f.write(b"x" * 1024)
                f.flush()
                time.sleep(0.4)
        written = time.monotonic()
        assert not ready, "handed on before the copy finished"
        assert got.wait(10)
        assert time.monotonic() - written >= 1.2
        assert ready == [str(target)]
        assert target.stat().st_size == 6 * 1024
    finally:
        w.stop()


def test_ignores_hidden_and_other_files(tmp_path):
    ready = []
    w = Watcher(str(tmp_path), settle=0.5, on_ready=lambda i, s: ready.extend(i), poll=0.2)
    w.start()
    try:
        time.sleep(0.8)
        (tmp_path / "._IMG_1.CR3").write_bytes(b"x")
        (tmp_path / "notes.txt").write_text("x")
        time.sleep(2)
        assert ready == []
    finally:
        w.stop()


def test_sidecar_change_maps_to_image(tmp_path):
    (tmp_path / "IMG_2.ARW").write_bytes(b"x")
    side = []
    w = Watcher(str(tmp_path), settle=0.5, on_ready=lambda i, s: side.extend(s), poll=0.2)
    w.start()
    try:
        time.sleep(0.8)
        (tmp_path / "IMG_2.xmp").write_text("<xmp:Rating>5</xmp:Rating>")
        deadline = time.time() + 6
        while not side and time.time() < deadline:
            time.sleep(0.2)
        assert side == [str(tmp_path / "IMG_2.ARW")]
    finally:
        w.stop()


def test_mount_callbacks(tmp_path):
    root = tmp_path / "card"
    events = []
    w = Watcher(str(root), settle=0.5, on_ready=lambda i, s: None, on_mount=events.append, poll=0.2)
    w.start()
    try:
        time.sleep(0.6)
        root.mkdir()
        deadline = time.time() + 5
        while events != [True] and time.time() < deadline:
            time.sleep(0.2)
        assert events == [True]
    finally:
        w.stop()
