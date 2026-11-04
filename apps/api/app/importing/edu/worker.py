"""Bounded subprocess execution for authenticated uploads and confirmation parsing."""

import multiprocessing
import re
import threading
from multiprocessing.connection import Connection

from .parser import parse_excel
from .questions import QuestionWorkbook, parse_questions
from .types import ImportFileError, ParsedWorkbook, ParseLimits

SLOTS = threading.BoundedSemaphore(2)


def _parse_child(data: bytes, sender: Connection, questions: bool = False) -> None:
    try:
        try:
            sender.send(("ok", parse_questions(data) if questions else parse_excel(data)))
        except ImportFileError as exc:
            sender.send(("error", exc.code))
        except Exception:
            sender.send(("error", "INVALID_WORKBOOK"))
    finally:
        sender.close()


def parse_isolated(
    data: bytes, *, timeout: float = 15, questions: bool = False
) -> ParsedWorkbook | QuestionWorkbook:
    if len(data) > ParseLimits().compressed_bytes:
        raise ImportFileError("FILE_SIZE_LIMIT")
    if not SLOTS.acquire(blocking=False):
        raise ImportFileError("PARSER_BUSY")
    receiver = sender = process = None
    try:
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(target=_parse_child, args=(data, sender, questions), daemon=True)
        process.start()
        sender.close()
        if not receiver.poll(timeout):
            raise ImportFileError("PARSE_TIME_LIMIT")
        # This pipe carries objects only from our child, never client-provided pickle bytes.
        kind, value = receiver.recv()
        if kind == "ok" and isinstance(value, QuestionWorkbook if questions else ParsedWorkbook):
            return value
        code = (
            value
            if isinstance(value, str) and re.fullmatch(r"[A-Z_]{1,60}", value)
            else "INVALID_WORKBOOK"
        )
        raise ImportFileError(code)
    except ImportFileError:
        raise
    except Exception:
        raise ImportFileError("PARSER_UNAVAILABLE") from None
    finally:
        if sender is not None:
            sender.close()
        if receiver is not None:
            receiver.close()
        if process is not None and process.pid is not None:
            process.join(timeout=0.2)
            if process.is_alive():
                process.terminate()
                process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)
            process.close()
        SLOTS.release()
