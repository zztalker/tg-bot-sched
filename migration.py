from tinydb import TinyDB, Query
import logging
logger = logging.getLogger(__name__)


db = TinyDB("db.json")
events = db.table("events")
channels = db.table("channels")
migrations = db.table("migrations")

def users_as_names():
    for event in events.all():
        event["registered_users"] = [
            user for user in event["registered_users"]
            if type(user) == str
        ]
        events.update(event, doc_ids=[event.doc_id])

def int_event_id():
    i = 1
    for event in events.all():
        event["id"] = i
        events.update(event, doc_ids=[event.doc_id])
        i += 1

migrations_to_apply = [
    {"name": "users_as_names", "callback": users_as_names},
    {"name": "int_event_id", "callback": int_event_id},
]

def apply():
    logger.info("Starting migrations...")
    for migration in migrations_to_apply:
        if migrations.contains(Query().name == migration["name"]):
            continue
        migration["callback"]()
        migrations.insert({"name": migration["name"]})
        logger.info("Migration applied: %s", migration["name"])
    logger.info("Migrations completed.")
