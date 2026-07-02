import json, time

def log_event(user_id, action):
    data = {
        "user_id": user_id,
        "action": action,
        "time": time.time()
    }
    with open("logs.txt", "a") as f:
        f.write(json.dumps(data) + "\n")
