"""Stdlib-only local transport, shared with clients without importing Live.

Windows: exclusive IPv4 loopback, ephemeral port, 256-bit capability on EVERY
request. Discovery and lock files have a protected, current-user-only DACL.
Unix: owner-only directory/socket. A lifetime OS lock prevents a second Live
instance from stealing discovery; process death releases it for stale recovery.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import stat

WINDOWS = os.name == "nt"
MAX_FRAME = 64 * 1024 * 1024
MAX_ENDPOINT = 4096


class AuthenticationError(ValueError):
    pass


def encode_message(message, token=None, direction="request"):
    """NDJSON; Windows wraps exact UTF-8 JSON text in a signed envelope.

    HMAC key = hex-decoded endpoint token, input = UTF8('aae-v1/' + direction
    + '\\n' + body). Direction separation prevents request reflection attacks.
    Both peers authenticate without disclosing the key to a stale-port squatter.
    """
    body = json.dumps(message, separators=(",", ":"), default=str)
    if token is not None:
        mac = hmac.new(bytes.fromhex(token), ("aae-v1/" + direction + "\n" + body).encode("utf-8"), hashlib.sha256).hexdigest()
        body = json.dumps({"body": body, "mac": mac}, separators=(",", ":"))
    return (body + "\n").encode("utf-8")


def decode_message(line, token=None, direction="request"):
    try:
        message = json.loads(line)
        if token is not None:
            if not isinstance(message, dict) or not isinstance(message.get("body"), str) or not isinstance(message.get("mac"), str):
                raise AuthenticationError("authentication required")
            mac = hmac.new(bytes.fromhex(token), ("aae-v1/" + direction + "\n" + message["body"]).encode("utf-8"), hashlib.sha256).hexdigest()
            if not re.fullmatch(r"[0-9a-f]{64}", message["mac"]) or not hmac.compare_digest(mac, message["mac"]):
                raise AuthenticationError("authentication required")
            message = json.loads(message["body"])
        if not isinstance(message, dict):
            raise ValueError("message must be an object")
        return message
    except (ValueError, UnicodeError, RecursionError) as e:
        if token is not None:
            raise AuthenticationError("authentication required") from e
        raise ValueError("invalid JSON message") from e


def default_directory():
    if WINDOWS:
        root = os.environ.get("LOCALAPPDATA")
        if not root or not os.path.isabs(root):
            raise RuntimeError("LOCALAPPDATA must be an absolute per-user path")
        return os.path.join(root, "ActualAssistantEngineer")
    return os.path.expanduser("~/Library/Application Support/ActualAssistantEngineer")


DIRECTORY = default_directory()
SOCK_PATH = os.path.join(DIRECTORY, "live.sock")
ENDPOINT_PATH = os.path.join(DIRECTORY, "live.endpoint.json")


if WINDOWS:
    import ctypes
    import msvcrt
    import struct
    from ctypes import wintypes as W

    _adv = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    P = ctypes.c_void_p

    class _SA(ctypes.Structure):
        _fields_ = [("length", W.DWORD), ("descriptor", P), ("inherit", W.BOOL)]

    def _api(dll, name, result, args):
        fn = getattr(dll, name)
        fn.restype, fn.argtypes = result, args
        return fn

    _open_token = _api(_adv, "OpenProcessToken", W.BOOL, [W.HANDLE, W.DWORD, ctypes.POINTER(W.HANDLE)])
    _token_info = _api(_adv, "GetTokenInformation", W.BOOL, [W.HANDLE, ctypes.c_int, P, W.DWORD, ctypes.POINTER(W.DWORD)])
    _sid_string = _api(_adv, "ConvertSidToStringSidW", W.BOOL, [P, ctypes.POINTER(P)])
    _from_sddl = _api(_adv, "ConvertStringSecurityDescriptorToSecurityDescriptorW", W.BOOL, [W.LPCWSTR, W.DWORD, ctypes.POINTER(P), P])
    _get_security = _api(_adv, "GetNamedSecurityInfoW", W.DWORD, [W.LPCWSTR, ctypes.c_int, W.DWORD, ctypes.POINTER(P), P, ctypes.POINTER(P), P, ctypes.POINTER(P)])
    _get_control = _api(_adv, "GetSecurityDescriptorControl", W.BOOL, [P, ctypes.POINTER(W.WORD), ctypes.POINTER(W.DWORD)])
    _get_ace = _api(_adv, "GetAce", W.BOOL, [P, W.DWORD, ctypes.POINTER(P)])
    _create_dir = _api(_kernel, "CreateDirectoryW", W.BOOL, [W.LPCWSTR, ctypes.POINTER(_SA)])
    _create_file = _api(_kernel, "CreateFileW", W.HANDLE, [W.LPCWSTR, W.DWORD, W.DWORD, ctypes.POINTER(_SA), W.DWORD, W.DWORD, W.HANDLE])
    _close = _api(_kernel, "CloseHandle", W.BOOL, [W.HANDLE])
    _free = _api(_kernel, "LocalFree", P, [P])
    _process = _api(_kernel, "GetCurrentProcess", W.HANDLE, [])

    def _check(ok):
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

    def _sid_text(sid):
        text = P()
        _check(_sid_string(sid, ctypes.byref(text)))
        try:
            return ctypes.wstring_at(text)
        finally:
            _free(text)

    def _current_sid():
        token = W.HANDLE()
        _check(_open_token(_process(), 8, ctypes.byref(token)))
        try:
            size = W.DWORD()
            _token_info(token, 1, None, 0, ctypes.byref(size))
            buf = ctypes.create_string_buffer(size.value)
            _check(_token_info(token, 1, buf, size, ctypes.byref(size)))
            return _sid_text(ctypes.cast(buf, ctypes.POINTER(P))[0])
        finally:
            _close(token)

    def _security_attributes():
        sid = _current_sid()
        descriptor = P()
        _check(_from_sddl("O:%sD:P(A;;FA;;;%s)" % (sid, sid), 1, ctypes.byref(descriptor), None))
        return _SA(ctypes.sizeof(_SA), descriptor, False)

    def _win_private(path):
        owner, dacl, descriptor = P(), P(), P()
        code = _get_security(str(path), 1, 5, ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(descriptor))
        if code:
            raise ctypes.WinError(code)
        try:
            control, revision = W.WORD(), W.DWORD()
            _check(_get_control(descriptor, ctypes.byref(control), ctypes.byref(revision)))
            if not dacl or not control.value & 0x1000 or _sid_text(owner) != _current_sid():
                raise PermissionError("transport requires a protected current-user DACL and owner")
            # ACL header: revision, reserved, size, ACE count, reserved.
            count = struct.unpack("<BBHHH", ctypes.string_at(dacl, 8))[3]
            if count != 1:
                raise PermissionError("transport ACL must grant only the current user")
            ace = P()
            _check(_get_ace(dacl, 0, ctypes.byref(ace)))
            kind, flags, _, mask = struct.unpack("<BBHI", ctypes.string_at(ace, 8))
            if kind != 0 or flags != 0 or mask != 0x1F01FF or _sid_text(ace.value + 8) != _current_sid():
                raise PermissionError("transport ACL must grant only the current user full control")
        finally:
            _free(descriptor)

    def _win_open(path, disposition, share=1):
        sa = _security_attributes()
        try:
            handle = _create_file(str(path), 0xC0000000, share, ctypes.byref(sa), disposition, 0x00200080, None)
            if handle == W.HANDLE(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                return msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
            except Exception:
                _close(handle)
                raise
        finally:
            _free(sa.descriptor)


def validate_private(path, directory=False):
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & 0x400:
        raise PermissionError("transport path must not be a symlink or reparse point")
    if directory and not stat.S_ISDIR(st.st_mode):
        raise PermissionError("transport directory is not a directory")
    if WINDOWS:
        _win_private(path)
    elif st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise PermissionError("transport path must be owned by the current user and owner-only")
    return st


def _private_directory(directory):
    if WINDOWS:
        sa = _security_attributes()
        try:
            if not _create_dir(str(directory), ctypes.byref(sa)) and ctypes.get_last_error() != 183:
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            _free(sa.descriptor)
    else:
        os.makedirs(directory, mode=0o700, exist_ok=True)
    validate_private(directory, directory=True)


def read_endpoint(directory=None):
    directory = directory or DIRECTORY
    validate_private(directory, directory=True)
    if not WINDOWS:
        path = os.path.join(directory, "live.sock")
        if not stat.S_ISSOCK(validate_private(path).st_mode):
            raise PermissionError("Live endpoint is not a Unix socket")
        return {"path": path}
    path = os.path.join(directory, "live.endpoint.json")
    if not stat.S_ISREG(validate_private(path).st_mode):
        raise PermissionError("Live endpoint must be a regular file")
    with open(path, "rb") as f:
        raw = f.read(MAX_ENDPOINT + 1)
    if len(raw) > MAX_ENDPOINT:
        raise ValueError("Live endpoint file is too large")
    endpoint = json.loads(raw)
    if (not isinstance(endpoint, dict) or type(endpoint.get("protocol")) is not int or endpoint["protocol"] != 1
            or endpoint.get("transport") != "tcp" or endpoint.get("auth") != "hmac-sha256" or endpoint.get("host") != "127.0.0.1"
            or type(endpoint.get("port")) is not int or not 0 < endpoint["port"] < 65536
            or not isinstance(endpoint.get("token"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", endpoint["token"])):
        raise ValueError("invalid local Live endpoint")
    return endpoint


class Listener:
    def __init__(self, directory=None):
        self.directory = directory or DIRECTORY
        self.socket = None
        self.token = None
        self._lock = None
        self._identity = None
        self.path = os.path.join(self.directory, "live.endpoint.json" if WINDOWS else "live.sock")
        _private_directory(self.directory)
        lock_path = os.path.join(self.directory, "live.lock")
        if os.path.lexists(lock_path):
            validate_private(lock_path)
        try:
            if WINDOWS:
                self._lock = _win_open(lock_path, 4)  # OPEN_ALWAYS; deny other writers/deletion
            else:
                import fcntl
                self._lock = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
                fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            validate_private(lock_path)
            self.socket = socket.socket(socket.AF_INET if WINDOWS else socket.AF_UNIX, socket.SOCK_STREAM)
            if WINDOWS:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                self.socket.bind(("127.0.0.1", 0))
                self.token = secrets.token_hex(32)
            else:
                if os.path.lexists(self.path):
                    import errno
                    read_endpoint(self.directory)
                    # Also respect pre-lock-file bridge versions and other Unix
                    # listeners. Only remove a positively stale socket, without
                    # waiting on Live's main thread.
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                        probe.setblocking(False)
                        code = probe.connect_ex(self.path)
                    if code not in (errno.ECONNREFUSED, errno.ENOENT):
                        raise OSError("Live bridge already listening at the Unix endpoint")
                    os.unlink(self.path)
                self.socket.bind(self.path)
                os.chmod(self.path, 0o600)
            self.socket.listen(8)
            self.socket.setblocking(False)
            if WINDOWS:
                endpoint = {"protocol": 1, "transport": "tcp", "auth": "hmac-sha256", "host": "127.0.0.1",
                            "port": self.socket.getsockname()[1], "token": self.token}
                temp = self.path + "." + secrets.token_hex(8)
                try:
                    with os.fdopen(_win_open(temp, 1, 0), "wb") as f:  # CREATE_NEW
                        f.write(json.dumps(endpoint).encode("ascii"))
                    os.replace(temp, self.path)
                finally:
                    if os.path.exists(temp):
                        os.unlink(temp)
            self._identity = os.stat(self.path).st_ino
        except Exception:
            self.close()
            raise

    def close(self):
        if self.socket is not None:
            self.socket.close()
            self.socket = None
        try:
            if self._identity is not None and os.path.exists(self.path) and os.lstat(self.path).st_ino == self._identity:
                os.unlink(self.path)
        finally:
            self._identity = None
            if self._lock is not None:
                os.close(self._lock)
                self._lock = None
        # Never unlink the lock file: unlinking an OS-locked inode lets a third
        # process lock a replacement while another process still holds the old one.
