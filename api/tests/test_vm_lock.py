from pyxie_core.maintenance import vm_lock_reason


def test_unlocked_vm_is_fine():
    assert vm_lock_reason(100, {"memory": 1024}) is None
    assert vm_lock_reason(100, None) is None
    assert vm_lock_reason(100, {"lock": ""}) is None


def test_stale_snapshot_lock_explains_the_fix():
    r = vm_lock_reason(209, {"lock": "snapshot-delete"})
    assert "snapshot-delete" in r and "qm unlock 209" in r and "refuses to migrate" in r


def test_backup_lock_says_wait_not_unlock():
    r = vm_lock_reason(7, {"lock": "backup"})
    assert "wait for it to finish" in r and "qm unlock" not in r


def test_other_locks_get_a_generic_message_with_the_vmid():
    r = vm_lock_reason(55, {"lock": "clone"})
    assert "(clone)" in r and "qm unlock 55" in r
