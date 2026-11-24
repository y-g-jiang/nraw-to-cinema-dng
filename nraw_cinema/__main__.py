import argparse
import json
import sys
import threading


def main():
    parser = argparse.ArgumentParser(description="Lossless NRAW Bayer extraction to CinemaDNG sequences")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("gui", help="Open the desktop interface")
    for name in ("inspect", "convert"):
        command = subparsers.add_parser(name)
        command.add_argument("source")
        command.add_argument("--sdk", required=True)
        command.add_argument("--profile", required=True, help="Same-camera reference CFA DNG")
        command.add_argument("--start", type=int, default=0, help="Zero-based source frame")
        command.add_argument("--count", type=int)
        command.add_argument("--step", type=int, default=1)
        if name == "convert":
            command.add_argument("--output", required=True)
            command.add_argument("--resume", action="store_true")
            command.add_argument("--threads", type=int, default=2)
            command.add_argument("--cancel-stdin", action="store_true", help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if arguments.command in (None, "gui"):
        from .gui import launch
        launch()
        return 0
    from .convert import convert, inspect
    options = vars(arguments).copy()
    command = options.pop("command")
    options["profile_path"] = options.pop("profile")
    try:
        if command == "inspect":
            print(json.dumps(dict(event="inspection", **inspect(**options)), ensure_ascii=True), flush=True)
        else:
            cancel_stdin = options.pop("cancel_stdin")
            stop = threading.Event()
            if cancel_stdin:
                def listen():
                    for line in sys.stdin:
                        if line.strip() == "cancel":
                            stop.set()
                            return
                threading.Thread(target=listen, daemon=True).start()
            success = convert(**options, stop=stop, emit=lambda event: print(json.dumps(event, ensure_ascii=True), flush=True))
            return 0 if success else 2
    except Exception as error:
        print(json.dumps(dict(event="error", message=str(error)), ensure_ascii=True), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
