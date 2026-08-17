"""Runnable operator procedures, shipped inside the ``omotion`` wheel.

Each module here is a self-contained ``python -m omotion.scripts.<name>``
entry point: it prompts on stdin, reports on stdout, and exits 0 on pass /
nonzero otherwise, so it runs identically from a bare terminal and from the
test-app's Procedures pane (which drives it as a subprocess). Living inside
the package - rather than the repo's ``scripts/`` directory - means any
environment that can import ``omotion`` can run them; no SDK checkout needed.

Current procedures are the WI-00015 calibration runners; operator-facing
usage lives in ``docs/WI15Procedures.md``.

``framed_prompts`` is not a procedure but a host-facing runner: it executes a
procedure module with each interactive prompt announced as one sentinel-tagged
JSON line on stdout, so a hosting program (the Procedures pane) can recognize
prompts exactly instead of sniffing for them. Terminal use never needs it.
"""
