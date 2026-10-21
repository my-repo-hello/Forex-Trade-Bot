"""Pick the MT5 backend for this OS.

MT5_MODE=auto  (default) Windows -> official MetaTrader5 package
                          Linux   -> same package inside Wine, via rpyc bridge
MT5_MODE=paper            in-memory simulator (no broker, for testing / dry runs)
"""
import os
import sys


def _strip(v):
    return (v or "").split("#")[0].strip()


def _port_open(host, port):
    import socket
    with socket.socket() as sk:
        sk.settimeout(1)
        return sk.connect_ex((host, port)) == 0


def _open_linux():
    """Connect to the bridge; if MT5 is closed, open it (mt5-stack service) first."""
    import subprocess
    import time
    import rpyc
    host = _strip(os.environ.get("MT5LINUX_HOST")) or "127.0.0.1"
    port = int(_strip(os.environ.get("MT5LINUX_PORT")) or 18812)
    if not _port_open(host, port):
        print("opening MT5 (mt5-stack) ...", flush=True)
        subprocess.run(["systemctl", "--user", "start", "mt5-stack"], check=False)
    for attempt in range(36):  # MT5 + bridge can take a while: wait up to 3 min
        try:
            conn = rpyc.classic.connect(host, port)
            break
        except OSError as e:
            if attempt == 35:
                raise RuntimeError(f"MT5 bridge not reachable on {host}:{port} — "
                                   "check: systemctl --user status mt5-stack") from e
            time.sleep(5)
    conn._config["sync_request_timeout"] = 60
    return _Bridge(conn)


class LazyMT5:
    """MT5 that is only opened when something actually needs it (a signal, a button,
    open trades to manage), and can be closed again when idle."""
    is_paper = False

    def __init__(self):
        self._mt5 = None

    @property
    def connected(self):
        return self._mt5 is not None

    def _get(self):
        if self._mt5 is None:
            if sys.platform == "win32":
                import MetaTrader5
                self._mt5 = MetaTrader5   # initialize() launches the terminal
            else:
                self._mt5 = _open_linux()
        return self._mt5

    def __getattr__(self, name):
        return getattr(self._get(), name)

    def is_open(self):
        """MT5 running — connected by the bot, or (Linux) a stack started by anything else."""
        if self._mt5 is not None:
            return True
        if sys.platform == "win32":
            return False
        return _port_open(_strip(os.environ.get("MT5LINUX_HOST")) or "127.0.0.1",
                          int(_strip(os.environ.get("MT5LINUX_PORT")) or 18812))

    def reset(self):
        """Forget a dead connection; the next use reconnects (and reopens MT5)."""
        self._mt5 = None

    def close_terminal(self):
        """Close MT5 completely (used when there is nothing to trade or manage)."""
        import subprocess
        m, self._mt5 = self._mt5, None
        if sys.platform == "win32":
            if m is not None:
                m.shutdown()
            subprocess.run(["taskkill", "/IM", "terminal64.exe"], check=False, capture_output=True)
        else:
            subprocess.run(["systemctl", "--user", "stop", "mt5-stack"], check=False)


def load():
    mode = _strip(os.environ.get("MT5_MODE")).lower() or "auto"
    if mode == "paper":
        from .paper_mt5 import PaperMT5
        return PaperMT5()
    return LazyMT5()


class _Bridge:
    """Makes the remote MetaTrader5 module look local."""
    def __init__(self, conn):
        self._conn, self._m = conn, conn.modules.MetaTrader5
        # MT5's C functions reject calls forwarded by rpyc ("Unnamed arguments not
        # allowed"), so order_send/order_check are invoked by a Windows-side helper
        conn.execute("import MetaTrader5 as _m5\n"
                     "def _call(fn, req): return getattr(_m5, fn)(dict(req))")
        self._call = conn.namespace["_call"]

    def __getattr__(self, name):
        return getattr(self._m, name)

    def order_send(self, request):
        return self._call("order_send", request)

    def order_check(self, request):
        return self._call("order_check", request)

mt5 = load()
