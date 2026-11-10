"""Minimal import targets that do not import the full pytest module under Windows spawn."""

import os
import time


def sleeping_child(*args):
    time.sleep(30)


def crashed_child(*args):
    os._exit(7)
