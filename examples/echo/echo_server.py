#!/usr/bin/env python3
"""Example protocol, server side: answers every UDP request with the same bytes.

Not part of the testbed; see echo_client.py. Runs until SIGTERM (the testbed
stops receivers after the client ends), then writes how many requests it
answered to its log in the output directory.
"""

import argparse
import os
import signal
import socket
import sys

running = True


def stop(_signal, _frame):
    global running
    running = False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--log", required=True)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    answered = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((args.bind, args.port))
        sock.settimeout(0.2)
        while running:
            try:
                request, client = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                if not running:
                    break
                raise
            sock.sendto(request, client)
            answered += 1

    with open(args.log, "w", encoding="utf-8") as log:
        log.write(f"node={os.environ.get('TESTBED_NODE_ID')} role={os.environ.get('TESTBED_ROLE')} "
                  f"bind={args.bind}:{args.port} cwd={os.getcwd()}\nanswered {answered}\n")
    print(f"echo server: answered {answered}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
