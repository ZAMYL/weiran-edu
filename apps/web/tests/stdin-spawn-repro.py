"""Minimal Windows host-control reproduction; no application, SQLite or model imports."""

import json
import multiprocessing
import sys
import threading
import time


def report(stage: str, **fields) -> None:
    print(json.dumps({"stage": stage, "time": time.perf_counter(), **fields}), flush=True)


def child(payload: bytes, sender) -> None:
    report("child_enter", payload_bytes=len(payload))
    sender.send("done")
    sender.close()


def main() -> None:
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    finished = threading.Event()

    def launch() -> None:
        process = context.Process(target=child, args=(b"x" * 262144, sender), daemon=True)
        report("process_start_begin")
        process.start()
        report("process_start_end")
        sender.close()
        assert receiver.recv() == "done"
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join(2)
        receiver.close()
        process.close()
        finished.set()
        report("finished")

    thread = threading.Thread(target=launch)
    thread.start()
    if sys.argv[1] == "stdin":
        sys.stdin.readline()
        report("stdin_released")
    else:
        finished.wait(10)
    thread.join(10)


if __name__ == "__main__":
    main()
