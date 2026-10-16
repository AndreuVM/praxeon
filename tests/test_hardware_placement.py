"""Reparto de LAYA y KEV entre GPU y CPU según la VRAM del equipo."""

import pytest

from praxeon.providers import hardware
from praxeon.providers.hardware import GpuInfo, estimate_kev_vram_mb, plan_placement


def gpu(total_mb, free_mb=None):
    return GpuInfo(name="GPU de prueba", total_mb=total_mb, free_mb=total_mb if free_mb is None else free_mb)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("LAYA_DEVICE", "KEV_DEVICE", "KEV_RUN", "KEV_AUTOSTART"):
        monkeypatch.delenv(name, raising=False)


def test_kev_size_is_estimated_from_the_checkpoint_name():
    assert estimate_kev_vram_mb("jaredpalmer/kev-0.8b") < estimate_kev_vram_mb("jaredpalmer/kev-4b")
    assert estimate_kev_vram_mb("jaredpalmer/kev-4b") < estimate_kev_vram_mb("jaredpalmer/kev-9b")
    assert 7000 < estimate_kev_vram_mb("jaredpalmer/kev-4b") < 8200  # medido: ~7,9 GB
    assert estimate_kev_vram_mb(None) == estimate_kev_vram_mb("jaredpalmer/kev-4b")


def test_no_gpu_means_everything_on_cpu(monkeypatch):
    monkeypatch.setattr(hardware, "detect_gpu", lambda: None)
    plan = plan_placement(kev_wanted=True)
    assert (plan.laya_device, plan.kev_device, plan.kev_cuda_graphs) == ("cpu", "cpu", False)


def test_8gb_card_runs_kev_4b_on_gpu_and_laya_on_cpu():
    plan = plan_placement(gpu(8188), "jaredpalmer/kev-4b", kev_wanted=True)
    assert (plan.laya_device, plan.kev_device, plan.kev_cuda_graphs) == ("cpu", "cuda", False)


def test_big_card_runs_both_on_gpu_with_graphs():
    plan = plan_placement(gpu(24576), "jaredpalmer/kev-4b", kev_wanted=True)
    assert (plan.laya_device, plan.kev_device, plan.kev_cuda_graphs) == ("cuda", "cuda", True)


def test_a_card_that_barely_fits_both_skips_the_cuda_graphs():
    plan = plan_placement(gpu(12000), "jaredpalmer/kev-4b", kev_wanted=True)
    assert (plan.laya_device, plan.kev_device, plan.kev_cuda_graphs) == ("cuda", "cuda", False)


def test_small_card_keeps_laya_on_gpu_and_sends_kev_to_cpu():
    plan = plan_placement(gpu(6144), "jaredpalmer/kev-4b", kev_wanted=True)
    assert (plan.laya_device, plan.kev_device) == ("cuda", "cpu")


def test_tiny_card_uses_cpu_for_everything():
    assert plan_placement(gpu(1024), kev_wanted=True).laya_device == "cpu"


def test_a_small_kev_model_fits_next_to_laya_on_8gb():
    plan = plan_placement(gpu(8188), "jaredpalmer/kev-0.8b", kev_wanted=True)
    assert (plan.laya_device, plan.kev_device) == ("cuda", "cuda")


def test_without_kev_laya_takes_the_gpu():
    assert plan_placement(gpu(8188), kev_wanted=False).laya_device == "cuda"


def test_env_overrides_win_over_the_plan(monkeypatch):
    monkeypatch.setattr(hardware, "detect_gpu", lambda: gpu(8188))
    monkeypatch.setenv("KEV_AUTOSTART", "1")
    assert hardware.laya_device()[0] == "cpu"
    monkeypatch.setenv("LAYA_DEVICE", "cuda")
    assert hardware.laya_device() == ("cuda", "forzado por configuración")
    assert hardware.laya_device("cpu")[0] == "cpu"  # lo pedido explícitamente manda
    monkeypatch.setenv("KEV_DEVICE", "cpu")
    assert hardware.kev_device()[0] == "cpu"


def test_laya_falls_back_to_cpu_when_another_app_holds_the_vram(monkeypatch):
    monkeypatch.setattr(hardware, "detect_gpu", lambda: gpu(24576, free_mb=500))
    monkeypatch.setenv("KEV_AUTOSTART", "1")
    device, reason = hardware.laya_device()
    assert device == "cpu" and "500" in reason


def test_kev_server_is_started_on_the_planned_device(monkeypatch, tmp_path):
    from praxeon.providers.kev_server import KevServerManager

    monkeypatch.setattr(hardware, "detect_gpu", lambda: gpu(8188))
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv")
    argv = KevServerManager(str(tmp_path), run="jaredpalmer/kev-4b", port=8010)._argv()
    assert argv[argv.index("--device") + 1] == "cuda"
    mgr = KevServerManager(str(tmp_path), run="jaredpalmer/kev-4b", port=8010)
    mgr._argv()
    assert mgr._cuda_graphs is False
