from __future__ import annotations

from voicepipe.last_output import clear_last_output, load_last_output, save_last_output


def test_save_and_load_last_output(isolated_home) -> None:
    clear_last_output()
    assert load_last_output() is None

    saved = save_last_output("hello world\n", payload={"source": "test"})
    assert saved.text == "hello world"

    loaded = load_last_output()
    assert loaded is not None
    assert loaded.text == "hello world"
    assert loaded.payload == {"source": "test"}

    clear_last_output()
    assert load_last_output() is None

