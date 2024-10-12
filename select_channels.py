from tinydb import TinyDB
from pprint import pprint
import sys

db = TinyDB("db.json")
events = db.table("events")
channels = db.table("channels")

if sys.argv[1] == "all":
    pprint(channels.all())
    pprint(events.all())
elif sys.argv[1] == "channels":
    pprint(channels.all())
elif sys.argv[1] == "events":
    pprint(events.all())
