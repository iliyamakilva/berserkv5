import os
import tempfile
from datetime import datetime, date

import qrcode


_PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


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


def to_persian_digits(value) -> str:
    return str(value).translate(_PERSIAN_DIGITS)


def gregorian_to_jalali(gy: int, gm: int, gd: int):
    """Convert Gregorian date to Jalali date without extra dependencies."""
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    if gy > 1600:
        jy = 979
        gy -= 1600
    else:
        jy = 0
        gy -= 621
    gy2 = gy + 1 if gm > 2 else gy
    days = (
        365 * gy
        + (gy2 + 3) // 4
        - (gy2 + 99) // 100
        + (gy2 + 399) // 400
        - 80
        + gd
        + g_d_m[gm - 1]
    )
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + days // 31
        jd = 1 + days % 31
    else:
        jm = 7 + (days - 186) // 30
        jd = 1 + (days - 186) % 30
    return jy, jm, jd


def _parse_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    raw = str(value).strip()
    if not raw or raw == "-":
        return None
    # SQLite datetime('now') is usually YYYY-MM-DD HH:MM:SS.
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw[:19] if "%S" in fmt else raw[:10], fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def jalali_date(value, persian_digits: bool = True) -> str:
    dt = _parse_datetime(value)
    if not dt:
        return "-"
    jy, jm, jd = gregorian_to_jalali(dt.year, dt.month, dt.day)
    out = f"{jy:04d}/{jm:02d}/{jd:02d}"
    return to_persian_digits(out) if persian_digits else out


def format_dual_datetime(value, show_time: bool = True) -> str:
    """Return Gregorian + Jalali date for admin/user messages."""
    dt = _parse_datetime(value)
    if not dt:
        return "-"
    greg = dt.strftime("%Y-%m-%d %H:%M") if show_time else dt.strftime("%Y-%m-%d")
    return f"{greg} | شمسی: {jalali_date(dt)}"
