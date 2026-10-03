"""Isolated Windows UIA worker; one scoped request on stdin, JSON on stdout."""
import json
import sys
from pathlib import Path
from windows_ui import LiveUI


def main():
    try:
        request = json.load(sys.stdin)
        method = request["method"]
        if method not in ("snapshot", "menu", "click"):
            raise ValueError("unsupported accessibility operation")
        result = getattr(LiveUI(Path(request["exe"])), method)(*request.get("args", []), **request.get("kwargs", {}))
        if method == "snapshot" and result is not None:
            result = {"name": result.name, "identity": result.identity,
                      "dialogs": [{"text": d.text, "buttons": d.buttons} for d in result.dialogs]}
        print(json.dumps({"result": result}))
    except Exception as exc:
        print(json.dumps({"error": str(exc), "kind": type(exc).__name__}))


if __name__ == "__main__":
    main()
