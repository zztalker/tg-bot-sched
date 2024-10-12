from tinydb import TinyDB, Query
import sys

db = TinyDB("db.json")
channels = db.table("channels")

next_id = 0
for ch in channels.all():
    next_id = max(next_id, ch["id"])

next_id += 1

channels.insert(
    {
        "id": next_id,
        "name": sys.argv[1],
        "admins": [],
        "token": sys.argv[2],
        "admin_token": sys.argv[3],
    }
)
print("Channel added with id", next_id)
