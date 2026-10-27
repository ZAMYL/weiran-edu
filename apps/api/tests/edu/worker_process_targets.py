"""Spawn targets must not import HTTP fixtures before testing process failure handling."""

import os


def crashed_child(*args):
    os._exit(7)
