"""Desktop-shortcut launcher: start the dashboard in the background (no console window) and open it.
If it's already running, just open the browser tab."""
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

URL = "http://127.0.0.1:5000"


def running():
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", 5000)) == 0


if running():
    webbrowser.open(URL)
    sys.exit()

here = Path(__file__).parent
log = (here / "dashboard.log").open("a", encoding="utf-8")
sys.stdout = sys.stderr = log  # pythonw has no console; errors land in dashboard.log
sys.path.insert(0, str(here))
import dashboard  # noqa: E402

threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(URL)), daemon=True).start()
dashboard.app.run(host="127.0.0.1", port=5000, debug=False)
