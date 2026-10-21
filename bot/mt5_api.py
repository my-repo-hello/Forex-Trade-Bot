"""Pick the MT5 backend for this OS.

MT5_MODE=auto  (default) Windows -> official MetaTrader5 package
                          Linux   -> same package inside Wine, via rpyc bridge
MT5_MODE=paper            in-memory simulator (no broker, for testing / dry runs)
"""
import os
import sys


def _strip(v):
    return (v or "").split("#")[0].strip()


def load():
    mode = _strip(os.environ.get("MT5_MODE")).lower() or "auto"
    if mode == "paper":
        from .paper_mt5 import PaperMT5
        return PaperMT5()
    if sys.platform == "win32":
        import MetaTrader5 as mt5
        return mt5
    # Linux: official MetaTrader5 package running in Wine, reached via the bridge (bridge/mt5_stack.sh)
    import rpyc
    host = _strip(os.environ.get("MT5LINUX_HOST")) or "127.0.0.1"
    port = int(_strip(os.environ.get("MT5LINUX_PORT")) or 18812)
    import time
    for attempt in range(36):  # after boot MT5 + bridge can take a while: wait up to 3 min
        try:
            conn = rpyc.classic.connect(host, port)
            break
        except OSError as e:
            if attempt == 35:
                raise RuntimeError(f"MT5 bridge not reachable on {host}:{port} — "
                                   "check: systemctl --user status mt5-stack") from e
            print(f"waiting for MT5 bridge on {host}:{port} ...", flush=True)
            time.sleep(5)
    conn._config["sync_request_timeout"] = 60
    return _Bridge(conn)


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
