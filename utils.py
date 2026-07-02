import os
import tempfile
import qrcode


def make_qr(link: str, user_id) -> str:
    fd, path = tempfile.mkstemp(prefix=f"sub_{user_id}_", suffix=".png")
    os.close(fd)
    img = qrcode.make(link)
    img.save(path)
    return path


def cleanup_qr(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
