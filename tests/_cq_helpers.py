"""Shared test fixtures for the contact-quality suites.

Imported with an absolute import (``from _cq_helpers import _dn_batch``) by
both test_contact_quality_monitor.py and test_contact_quality_workflow.py.
``tests/`` is not a package and pytest runs in prepend mode, so a relative
import would fail collection.
"""

import numpy as np

from omotion.pipeline.batch import FrameBatch


def _dn_batch(n_frames, dn_value, frame_types=None):
    """A real FrameBatch with uniform DN across all cams.

    The live pipeline delivers one (side, cam) per row, so each logical frame
    expands to 16 rows (2 sides x 8 cams) sharing the frame_type. Both
    subtracted_mean and mean_dc_rt carry the same value so the test stays
    valid whichever the consumer reads for a given frame_type.
    """
    if frame_types is None:
        frame_types = ["light"] * n_frames
    rows = n_frames * 16
    cam_ids = np.tile(np.arange(8, dtype=np.int8), n_frames * 2)
    side_ids = np.tile(np.repeat(np.array([0, 1], dtype=np.int8), 8), n_frames)
    arr = np.full((rows, 2, 8), dn_value, dtype=np.float32)
    return FrameBatch(
        cam_ids=cam_ids,
        frame_ids=np.tile(np.arange(n_frames, dtype=np.uint8).repeat(16), 1),
        side_ids=side_ids,
        raw_histograms=None,
        temperature_c=None,
        timestamp_s=np.zeros(rows, dtype=np.float64),
        pdc=None, tcm=None, tcl=None,
        frame_type=np.repeat(np.array(frame_types, dtype="<U8"), 16),
        subtracted_mean=arr,
        mean_dc_rt=arr.copy(),
        std_raw=np.full((rows, 2, 8), 2.5, dtype=np.float32),
    )
