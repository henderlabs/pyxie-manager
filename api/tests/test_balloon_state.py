from pyxie_core.balloon import balloon_label, balloon_state


def test_zero_means_off_even_when_stopped():
    assert balloon_state("vm", "running", 0, True) == "off"
    assert balloon_state("vm", "stopped", 0, None) == "off"


def test_configured_and_reporting_is_on():
    assert balloon_state("vm", "running", 8192, True) == "on"
    assert balloon_state("vm", "running", None, True) == "on"  # absent = device with min=max


def test_configured_but_no_guest_stats_is_pending_only_when_running():
    assert balloon_state("vm", "running", 8192, False) == "pending"
    assert balloon_state("vm", "stopped", 8192, False) == "on"


def test_containers_have_no_state():
    assert balloon_state("lxc", "running", 512, None) is None


def test_labels():
    assert balloon_label("off", 0, 16384) == "Off"
    assert balloon_label("on", 8192, 16384) == "On (min 8 GiB)"
    assert balloon_label("on", None, 16384) == "On (min 16 GiB)"
    assert balloon_label("pending", 8192, 16384).startswith("Configured (min 8 GiB)")
    assert balloon_label(None, None, None) is None
