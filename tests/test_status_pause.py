import findphotodates as fpd


def test_status_pause_last_for_configured_window():
    now = [100.0]
    controller = fpd._StatusOutputPauseController(
        enabled=False,
        pause_seconds=60.0,
        clock=lambda: now[0],
    )

    controller.pause_now()

    assert controller.is_paused(159.9)
    assert not controller.is_paused(160.0)


def test_status_pause_extends_existing_pause():
    now = [100.0]
    controller = fpd._StatusOutputPauseController(
        enabled=False,
        pause_seconds=60.0,
        clock=lambda: now[0],
    )

    controller.pause_now()
    now[0] = 130.0
    controller.pause_now()

    assert controller.is_paused(189.9)
    assert not controller.is_paused(190.0)


def test_status_pause_notice_is_consumed_once():
    controller = fpd._StatusOutputPauseController(enabled=False)

    assert not controller.consume_pause_notice()
    controller.pause_now()

    assert controller.consume_pause_notice()
    assert not controller.consume_pause_notice()


def test_status_pause_extension_does_not_repeat_notice():
    now = [100.0]
    controller = fpd._StatusOutputPauseController(
        enabled=False,
        pause_seconds=60.0,
        clock=lambda: now[0],
    )

    controller.pause_now()
    assert controller.consume_pause_notice()

    now[0] = 120.0
    controller.pause_now()

    assert controller.is_paused(179.9)
    assert not controller.consume_pause_notice()


def test_status_pause_notice_returns_after_pause_expires():
    now = [100.0]
    controller = fpd._StatusOutputPauseController(
        enabled=False,
        pause_seconds=60.0,
        clock=lambda: now[0],
    )

    controller.pause_now()
    assert controller.consume_pause_notice()

    now[0] = 160.0
    controller.pause_now()

    assert controller.consume_pause_notice()


def test_status_pause_stop_clears_restore_after_keyboard_interrupt():
    controller = fpd._StatusOutputPauseController(enabled=False)

    def restore():
        raise KeyboardInterrupt

    controller._restore_posix_terminal = restore

    controller.stop()

    assert controller._restore_posix_terminal is None
