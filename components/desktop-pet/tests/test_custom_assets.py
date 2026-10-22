"""Public runtime tests use only synthetic pixels and temporary directories."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pytest
from PIL import Image
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from codex_pet.animation import AnimationPlayer
from codex_pet.controller import PetController
from codex_pet.custom_assets import AssetPackError, import_single_image, load_asset_pack, select_asset_pack
from codex_pet.renderer import FrameRenderer
from codex_pet.settings import Settings
from codex_pet.window import PetWindow


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def source(tmp_path):
    path = tmp_path / "my-image.png"
    picture = Image.new("RGBA", (64, 128), (0, 0, 0, 0))
    picture.paste((0, 180, 30, 255), (0, 0, 32, 128))
    picture.save(path)
    return path


def edit_pack(manifest, update):
    data = json.loads(manifest.read_text(encoding="utf-8"))
    update(data)
    manifest.write_text(json.dumps(data), encoding="utf-8")


def test_single_image_import_keeps_source_and_enables_every_required_state(source, tmp_path):
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = import_single_image(source, tmp_path / "my-pack")
    pack = load_asset_pack(manifest)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert (manifest.parent / "idle.png").read_bytes() == source.read_bytes()
    assert pack.size == (64, 128)
    assert len(pack.catalog.animations) == 9
    assert len(pack.catalog.look_poses) == 16
    assert not pack.catalog.animations["waving"].loop
    assert not pack.catalog.animations["jumping"].loop
    assert len(set(pack.frames.values())) == 1
    with pytest.raises(AssetPackError, match="已有"):
        import_single_image(source, manifest.parent)


def test_invalid_image_is_rejected_before_creating_output(tmp_path):
    source = tmp_path / "bad.png"
    source.write_bytes(b"not an image")
    with pytest.raises(AssetPackError):
        import_single_image(source, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_explicit_pack_never_silently_falls_back_to_private_baseline(tmp_path):
    private = tmp_path / "assets/codex_pet/source-manifest.json"
    private.parent.mkdir(parents=True)
    private.write_text("{}", encoding="utf-8")
    assert select_asset_pack(tmp_path, tmp_path) is None
    with pytest.raises(AssetPackError, match="缺少自备"):
        select_asset_pack(tmp_path, tmp_path, tmp_path / "missing.json")
    with pytest.raises(AssetPackError, match="asset_pack.py init"):
        select_asset_pack(tmp_path / "public", tmp_path / "public")


@pytest.mark.parametrize("bad_name", ["../my-image.png", "sub/../../my-image.png", "C:/outside.png"])
def test_rejects_paths_outside_pack(source, tmp_path, bad_name):
    manifest = import_single_image(source, tmp_path / "pack")
    edit_pack(manifest, lambda data: data["animations"]["idle"].update(frames=[bad_name]))
    with pytest.raises(AssetPackError):
        load_asset_pack(manifest)


@pytest.mark.parametrize("durations", [[0], [-1], [float("nan")], [float("inf")], [True], [16, 20], "150"])
def test_rejects_bad_timing(source, tmp_path, durations):
    manifest = import_single_image(source, tmp_path / "pack")
    edit_pack(manifest, lambda data: data["animations"]["idle"].update(durations_ms=durations))
    with pytest.raises(AssetPackError, match="durations_ms"):
        load_asset_pack(manifest)


def test_rejects_mixed_dimensions_and_invalid_look_count(source, tmp_path):
    manifest = import_single_image(source, tmp_path / "pack")
    Image.new("RGBA", (32, 32)).save(manifest.parent / "other.png")
    edit_pack(manifest, lambda data: data["animations"].update(waving={"frames": ["other.png"]}))
    with pytest.raises(AssetPackError, match="相同画布"):
        load_asset_pack(manifest)
    edit_pack(manifest, lambda data: (data["animations"].pop("waving"), data.update(look=["idle.png"])))
    with pytest.raises(AssetPackError, match="16"):
        load_asset_pack(manifest)


def test_arbitrary_idle_timing_and_frames_survive_natural_behavior(source, tmp_path):
    manifest = import_single_image(source, tmp_path / "pack")
    edit_pack(manifest, lambda data: data["animations"]["idle"].update(
        frames=["idle.png"] * 2, durations_ms=[100, 200]))
    pack = load_asset_pack(manifest)
    clock = [0.0]
    player = AnimationPlayer(pack.catalog, clock_ms=lambda: clock[0])
    controller = PetController(pack.catalog, player)
    assert player.current_definition.durations_ms == (100.0, 200.0)
    for t in (100, 301, 402, 603, 704):
        clock[0] = t
        controller.tick()
        assert player.current_frame() in ((0, 0), (0, 1))


def test_all_look_directions_and_optional_actions_render(source, tmp_path, qt_app):
    manifest = import_single_image(source, tmp_path / "pack")
    edit_pack(manifest, lambda data: (data.update(look=["idle.png"] * 16),
        data["animations"].update(thinking={"frames": ["idle.png"]})))
    pack = load_asset_pack(manifest)
    renderer = FrameRenderer(manifest.parent, extra_frames=pack.frames)
    assert renderer.baseline_available_groups is None
    for index in range(16):
        assert renderer.full_frame(*pack.catalog.pose_cell(index)).width() == 64
    assert renderer.full_frame(13, 0).height() == 128


def test_custom_window_preserves_aspect_ratio_and_alpha_hit(source, tmp_path, qt_app):
    manifest = import_single_image(source, tmp_path / "pack")
    pack = load_asset_pack(manifest)
    player = AnimationPlayer(pack.catalog)
    controller = PetController(pack.catalog, player)
    window = PetWindow(pack.catalog, player, controller,
                       FrameRenderer(manifest.parent, extra_frames=pack.frames), Settings())
    try:
        assert (window.width(), window.height()) == (160, 320)
        assert window._alpha_hit(QPoint(20, 100))
        assert not window._alpha_hit(QPoint(140, 100))
        window.set_canvas_height(640, save=False)
        assert (window.width(), window.height()) == (320, 640)
    finally:
        window.close()
        window.deleteLater()


def test_full_app_constructs_without_private_assets(source, tmp_path, qt_app, monkeypatch):
    from codex_pet import app as application
    manifest = import_single_image(source, tmp_path / "public/user-assets/pet")
    monkeypatch.setenv("CODEXTOOLS_PET_DATA_DIR", str(tmp_path / "isolated-data"))
    monkeypatch.setattr(application.paths, "app_base_dir", lambda: tmp_path / "public")
    monkeypatch.setattr(application.paths, "resource_base_dir", lambda: tmp_path / "public")
    monkeypatch.setattr(application.PlanUsageProvider, "start", lambda self: None)
    monkeypatch.setattr(application.GlobalHotkey, "configure", lambda *args: None)
    monkeypatch.setattr(application.TrayController, "show", lambda self: None)
    monkeypatch.setattr(application.PetVisibility, "start", lambda *args: None)
    def private_cache_forbidden():
        pytest.fail("custom packs must never read the private baseline or frame cache")
    monkeypatch.setattr(application, "bootstrap_frame_cache", private_cache_forbidden)
    instance = application.CodexToolsApp(qt_app, argparse.Namespace(asset_pack=None, reset_position=False))
    try:
        assert instance.catalog.custom_asset_pack
        assert instance.renderer.full_frame(0, 0).width() == 64
        assert instance._bridge.__class__.__name__ == "DisabledBridge"
    finally:
        instance.quit()
        qt_app.aboutToQuit.disconnect(instance.visibility.stop)
        qt_app.aboutToQuit.disconnect(instance.hotkey.close)
        instance.window.close()
        instance.window.deleteLater()
