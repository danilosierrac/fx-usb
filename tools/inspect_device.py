"""Query the connected EP-2350 without writing files or resetting it."""
import argparse
import json
import os
from pathlib import Path
import select
import termios
import time
import tty


def exchange(fd, command, timeout=5):
    token = "FXMIC_QUERY_DONE"
    statement = command + "; print('" + token + "')"
    result = bytearray()
    request = ("\r" + statement + "\r").encode()
    for offset in range(0, len(request), 48):
        block = memoryview(request)[offset:offset+48]
        while block:
            sent = os.write(fd, block)
            block = block[sent:]
        time.sleep(0.003)
        while select.select([fd], [], [], 0)[0]:
            result.extend(os.read(fd, 65536))
    deadline = time.monotonic() + timeout
    marker = ("\r\n" + token + "\r\n").encode()
    while time.monotonic() < deadline:
        if select.select([fd], [], [], 0.1)[0]:
            block = os.read(fd, 65536)
            if block:
                result.extend(block)
            if marker in result and result.endswith(b">>> "):
                return result.decode("utf-8", "replace")
    raise TimeoutError(result.decode("utf-8", "replace"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("port")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--command", action="append", required=True)
    args = parser.parse_args()
    fd = os.open(args.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    old = termios.tcgetattr(fd)
    transcript = []
    try:
        tty.setraw(fd)
        for command in args.command:
            answer = exchange(fd, command)
            transcript.append({"command": command, "response": answer})
            print(answer)
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, old)
        os.close(fd)
        if args.output:
            args.output.write_text(json.dumps(transcript, indent=2) + "\n")


if __name__ == "__main__":
    main()
