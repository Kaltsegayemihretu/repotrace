# Loaded automatically by the test interpreter (via PYTHONPATH). Refuses outgoing network connections: a precaution, not a sandbox.
import socket
def _refuse(*a, **k): raise OSError('Network access is blocked for this evaluation run.')
_orig = socket.socket.connect
def _connect(self, address):
    if self.family == getattr(socket, 'AF_UNIX', None): return _orig(self, address)
    _refuse()
socket.socket.connect = _connect; socket.socket.connect_ex = lambda self, address: _refuse()
socket.create_connection = _refuse
