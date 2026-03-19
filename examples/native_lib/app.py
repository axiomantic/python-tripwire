"""Compute Euclidean distance using libm via ctypes."""

import ctypes


def compute_distance(x1, y1, x2, y2):
    """Calculate the distance between two points using native sqrt."""
    libm = ctypes.CDLL("libm")
    dx = x2 - x1
    dy = y2 - y1
    return libm.sqrt(ctypes.c_double(dx * dx + dy * dy))
