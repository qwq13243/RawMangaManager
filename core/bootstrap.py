import os
import sys


def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    if hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return os.path.abspath(".")


def bootstrap_runtime() -> None:
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    base_dir = _base_dir()
    candidates = [
        os.path.join(base_dir, "ms-playwright"),
        os.path.join(base_dir, "_internal", "ms-playwright"),
    ]

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.extend(
            [
                os.path.join(meipass, "ms-playwright"),
                os.path.join(meipass, "_internal", "ms-playwright"),
            ]
        )

    for browsers in candidates:
        if os.path.isdir(browsers):
            os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", browsers)
            os.environ.setdefault("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", "1")
            break

    try:
        import google.protobuf  # noqa: F401
    except Exception:
        pass
