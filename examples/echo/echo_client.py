#!/usr/bin/env python3
"""Example protocol, client side: question and answer over UDP.

Not part of the testbed; it shows a program integrated through the week 6
contract (D18). It reads its message from the per-node data directory,
sends --count requests to the server, waits for each answer, writes its own
log in the output directory and exits by itself (experiment.end: sender).
The testbed never reads what is sent or printed.
"""

import argparse
import os
import socket
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--interval", type=float, default=0.1)
    parser.add_argument("--timeout", type=float, default=0.5)
    parser.add_argument("--message-file", required=True)
    parser.add_argument("--log", required=True)
    args = parser.parse_args()

    with open(args.message_file, "rb") as handle:
        message = handle.read().strip()

    answered = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock, open(args.log, "w", encoding="utf-8") as log:
        sock.settimeout(args.timeout)
        log.write(f"node={os.environ.get('TESTBED_NODE_ID')} role={os.environ.get('TESTBED_ROLE')} "
                  f"server={args.server}:{args.port} cwd={os.getcwd()}\n")
        for number in range(1, args.count + 1):
            request = b"%d " % number + message
            started = time.monotonic()
            try:
                sock.sendto(request, (args.server, args.port))
                reply, _ = sock.recvfrom(2048)
            except OSError as exc:  # timeout or no route: count it and go on
                log.write(f"{number} no answer ({exc.__class__.__name__})\n")
            else:
                answered += 1
                log.write(f"{number} answer in {(time.monotonic() - started) * 1000:.3f} ms\n")
            time.sleep(args.interval)
        log.write(f"answered {answered}/{args.count}\n")

    print(f"echo client: {answered}/{args.count} answered", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
