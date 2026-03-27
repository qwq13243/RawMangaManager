import os
import sys

def bootstrap_runtime() -> None:
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    try:
        import google.protobuf  # noqa: F401
    except Exception:
        pass
