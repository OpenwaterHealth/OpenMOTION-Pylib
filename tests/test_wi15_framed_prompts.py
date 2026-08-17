"""Unit tests for ``omotion.scripts.framed_prompts`` (issue #241).

The wrapper runs a procedure module's ``main(argv, *, input_func=...)`` with
an ``input_func`` that frames each prompt as one sentinel-tagged JSON line on
stdout before blocking on stdin. Pure software: fake procedure modules are
injected into ``sys.modules``; the real wi15 scripts are imported only to pin
their conformance to the injection seam the wrapper drives.
"""

import io
import json
import sys
import types

import pytest

from omotion.scripts import framed_prompts


def _fake_module(monkeypatch, name, main_func):
    module = types.ModuleType(name)
    module.main = main_func
    monkeypatch.setitem(sys.modules, name, module)
    return name


def _frames(stdout_text):
    """The decoded payload of every frame line in captured stdout."""
    return [
        json.loads(line[len(framed_prompts.PROMPT_SENTINEL):])
        for line in stdout_text.splitlines()
        if line.startswith(framed_prompts.PROMPT_SENTINEL)
    ]


def test_prompt_becomes_one_frame_and_the_reply_comes_from_stdin(
        monkeypatch, capsys):
    prompt = "Which sensor is installed? (left/right): "

    def fake_main(argv, *, input_func=None, output_func=None):
        return 0 if input_func(prompt) == "left" else 5

    name = _fake_module(monkeypatch, "fake_proc_roundtrip", fake_main)
    monkeypatch.setattr(sys, "stdin", io.StringIO("left\n"))

    assert framed_prompts.main([name]) == 0
    assert _frames(capsys.readouterr().out) == [{"prompt": prompt}]


def test_awkward_prompt_text_still_frames_as_one_lossless_line(
        monkeypatch, capsys):
    """Quotes, backslashes, non-ASCII and newlines must survive the frame."""
    prompt = 'Confirm "läser" path C:\\x?\n(yes/no): '
    seen = {}

    def fake_main(argv, *, input_func=None, output_func=None):
        seen["answer"] = input_func(prompt)
        return 0

    name = _fake_module(monkeypatch, "fake_proc_awkward", fake_main)
    monkeypatch.setattr(sys, "stdin", io.StringIO("yes\n"))

    assert framed_prompts.main([name]) == 0
    out = capsys.readouterr().out
    frame_lines = [l for l in out.splitlines()
                   if l.startswith(framed_prompts.PROMPT_SENTINEL)]
    assert len(frame_lines) == 1
    assert _frames(out) == [{"prompt": prompt}]
    assert seen["answer"] == "yes"


def test_ordinary_output_is_not_framed(monkeypatch, capsys):
    """Only prompts are framed — even output that looks prompt-shaped."""

    def fake_main(argv, *, input_func=None, output_func=None):
        output = print if output_func is None else output_func
        output("Measuring channel 0: ")  # prompt-shaped, but not a prompt
        output("done")
        return 0

    name = _fake_module(monkeypatch, "fake_proc_output", fake_main)

    assert framed_prompts.main([name]) == 0
    out = capsys.readouterr().out
    assert _frames(out) == []
    assert "Measuring channel 0: " in out
    assert "done" in out


def test_eof_on_stdin_reaches_the_procedure_as_its_cancel_path(
        monkeypatch, capsys):
    def fake_main(argv, *, input_func=None, output_func=None):
        try:
            input_func("Operator: ")
        except EOFError:
            return 7
        return 0

    name = _fake_module(monkeypatch, "fake_proc_eof", fake_main)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    assert framed_prompts.main([name]) == 7
    # The frame was still announced before stdin ran dry.
    assert _frames(capsys.readouterr().out) == [{"prompt": "Operator: "}]


def test_procedure_args_pass_through_verbatim(monkeypatch):
    seen = {}

    def fake_main(argv, *, input_func=None, output_func=None):
        seen["argv"] = list(argv)
        return 0

    name = _fake_module(monkeypatch, "fake_proc_args", fake_main)

    assert framed_prompts.main(
        [name, "--operator", "op", "--output-dir", "out"]) == 0
    assert seen["argv"] == ["--operator", "op", "--output-dir", "out"]


@pytest.mark.parametrize("returned, expected", [(3, 3), (0, 0), (None, 0)])
def test_the_procedures_exit_code_is_the_wrappers(
        monkeypatch, returned, expected):
    def fake_main(argv, *, input_func=None, output_func=None):
        return returned

    name = _fake_module(monkeypatch, f"fake_proc_exit_{returned}", fake_main)

    assert framed_prompts.main([name]) == expected


def test_a_missing_module_is_exit_2_with_a_named_reason(capsys):
    assert framed_prompts.main(["omotion.scripts.no_such_procedure"]) == 2
    assert "omotion.scripts.no_such_procedure" in capsys.readouterr().err


def test_a_module_without_the_injection_seam_is_exit_2(monkeypatch, capsys):
    def unframable_main(argv):  # no input_func, no **kwargs
        return 0

    name = _fake_module(monkeypatch, "fake_proc_unframable", unframable_main)

    assert framed_prompts.main([name]) == 2
    assert "cannot be prompt-framed" in capsys.readouterr().err


def test_no_module_argument_is_exit_2_with_usage(capsys):
    assert framed_prompts.main([]) == 2
    assert "usage" in capsys.readouterr().err


@pytest.mark.parametrize("module_name", [
    "omotion.scripts.wi15_single_sensor_laser_calibration",
    "omotion.scripts.wi15_dual_sensor_laser_calibration",
    "omotion.scripts.wi15_safety_calibration",
    "omotion.scripts.wi15_measurement_calibration",
])
def test_real_wi15_scripts_conform_to_the_injection_seam(module_name):
    """The wrapper's whole premise: every wi15 main accepts input_func."""
    import importlib
    import inspect

    module = importlib.import_module(module_name)
    parameters = inspect.signature(module.main).parameters
    assert "input_func" in parameters
    assert parameters["input_func"].kind is inspect.Parameter.KEYWORD_ONLY
