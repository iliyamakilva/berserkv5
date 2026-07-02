import time

_flood = {}

def check_fraud(uid):
    now = time.time()
    last = _flood.get(uid, 0)

    if now - last < 10:
        return False

    _flood[uid] = now
    return True
