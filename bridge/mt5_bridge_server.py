"""Runs INSIDE Wine's Windows Python. Exposes the official MetaTrader5 package
to the Linux bot over rpyc on 127.0.0.1 only (never expose this port publicly)."""
from rpyc.core import SlaveService
from rpyc.utils.server import ThreadedServer

if __name__ == "__main__":
    print("MT5 bridge listening on 127.0.0.1:18812", flush=True)
    ThreadedServer(SlaveService, hostname="127.0.0.1", port=18812,
                   protocol_config={"allow_public_attrs": True, "allow_pickle": True}).start()
