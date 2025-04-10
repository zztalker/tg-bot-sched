import asyncio
import os
import pickle
import sentry_sdk
from pathlib import Path
from tinydb import TinyDB, Query
from uuid import uuid4
from html import escape
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Update,
    InlineQueryResultsButton,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    InlineQueryHandler,
    CommandHandler,
    CallbackContext,
    ContextTypes,
    MessageHandler,
    filters,
)
from datetime import datetime, timedelta
import logging
import migration

SUPER_ADMINS = ["zztalker"]

# Initialize logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)
sentry_sdk.init(
    dsn="https://02ae47787c57ba6fe2a02cf8b213525b@o314947.ingest.us.sentry.io/4508109744177152",
    # Set traces_sample_rate to 1.0 to capture 100%
    # of transactions for tracing.
    traces_sample_rate=1.0,
    # Set profiles_sample_rate to 1.0 to profile 100%
    # of sampled transactions.
    # We recommend adjusting this value in production.
    profiles_sample_rate=1.0,
)

db = TinyDB("db.json")
db_lock = asyncio.Lock()
events = db.table("events")
channels = db.table("channels")
notification = db.table("notification")
settings = db.table("settings")

channels_obj = {}
wait_for_message = {}


def get_next_id(table):
    return table.all()[-1]["id"] + 1 if table.all() else 1


class Channel:
    def __init__(self, id, name):
        self.id = id
        self.name = name

    def __str__(self) -> str:
        return f"Channel(id={self.id}, name={self.name})"

    async def all_events(self, cmd=None, full=False, username=None):
        event_list = events.search(Query().channel_id == self.id)
        logger.info("Events %r", event_list)
        keyboard = []
        for event in sorted(event_list, key=lambda x: x["date"]):
            if not full:
                if (
                    datetime.strptime(event["date"], "%Y-%m-%d").date()
                    < datetime.now().date()
                ):
                    continue
                if event.get("hidden", False):
                    continue
            name = event["name"]
            date = datetime.strptime(event["date"], "%Y-%m-%d").strftime("%a %d.%b")
            time = event["time"]
            free_places = event["capacity"] - len(event["registered_users"])
            if event["capacity"] != 0 and free_places == 0:
                mark = "🚫"
            else:
                mark = "🆓"
            if cmd == "register" and username in event["registered_users"]:
                keyboard.append(
                    [
                        InlineKeyboardButton(
                            f"✅[{free_places}] {name} {date} {time}",
                            callback_data=f"unregister {event['id']}",
                        ),
                    ]
                )
            else:
                keyboard.append(
                    [
                        InlineKeyboardButton(
                            text=f"{mark}[{free_places}] {name} {date} {time}",
                            callback_data=f"{cmd} {event['id']}",
                        ),
                    ]
                )
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = f"Список предстоящих мероприятий {self.name}:"
        logger.info("List of events %r", reply_markup)
        return text, reply_markup

    async def register_as_user(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        user = update.effective_user.username
        logger.info("register as user by %r in channel %r", user, self)
        if not user:
            text = (
                "Для регистрации необходимо установить username в настройках телеграм"
            )
        else:
            async with db_lock:
                r = channels.search(Query().id == self.id)[0].get(
                    "registered_users", []
                )
                if user in r:
                    await start(update, context)
                    return
                else:
                    r.append(user)
                    channels.update({"registered_users": r}, Query().id == self.id)
                    ch = channels_obj[self.id]
                    if msg_id := ch.welcome_message():
                        try:
                            msg_data = pickle.load(open(f"data/{msg_id}.pkl", "rb"))
                        except Exception as e:
                            logger.error(e, exc_info=True)
                        else:
                            logger.info("Send welcome message %r", msg_data)

                    if msg_data:
                        keyboard = [
                            [
                                InlineKeyboardButton(
                                    text="Записаться",
                                    callback_data=f"events {self.id}",
                                ),
                            ]
                        ]
                        reply = InlineKeyboardMarkup(keyboard)

                        logger.info("DBG %r", escape(msg_data["msg"]))
                        await update.message.reply_photo(
                            photo=msg_data["photo"],
                            caption=escape(msg_data["msg"]),
                            parse_mode=ParseMode.MARKDOWN_V2,
                            reply_markup=reply,
                        )

                        return
                    else:
                        await update.message.reply_text(text=text, reply_markup=reply, parse_mode=ParseMode.HTML)


                    # text = f"Вы успешно зарегистрировались на канал {self.name} - для продолженния напишите /start"

        await update.message.reply_text(text=text, parse_mode=ParseMode.HTML)

    async def register_as_admin(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        user = update.effective_user.username
        logger.info("register as admin by %r in channel %r", user, self)
        if not user:
            text = (
                "Для регистрации необходимо установить username в настройках телеграм"
            )
        else:
            async with db_lock:
                r = channels.search(Query().id == self.id)[0].get("admins", [])
                if user in r:
                    text = "Вы уже зарегистрированы как админ на канал"
                else:
                    r.append(user)
                    channels.update({"admins": r}, Query().id == self.id)
                    text = f"Вы успешно зарегистрировались как админ на канал {self.name} - для продолженния напишите /start"
        await update.message.reply_text(text=text, parse_mode=ParseMode.HTML)

    async def admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [
                InlineKeyboardButton(
                    "Добавить события", callback_data=f"add-event {self.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "Список событий", callback_data=f"list-event {self.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "Добавить или изменить welcome message",
                    callback_data=f"add-message {self.id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "Удалить welcome message", callback_data=f"del-message {self.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "Добавить или изменить event-list message",
                    callback_data=f"add-emessage {self.id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "Удалить event-list message", callback_data=f"del-emessage {self.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "Удалить прошедшие события", callback_data=f"delete-old {self.id}"
                )
            ],
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)
        return f"Упправление событиями в {self.name}:", reply_markup

    def welcome_message(self):
        return channels.get(Query().id == self.id).get("welcome_message")

    def event_list_message(self):
        return channels.get(Query().id == self.id).get("event_list_message")

    def __repr__(self):
        return self.__str__()


async def start(update: Update, context: CallbackContext):
    if update.message.chat_id in wait_for_message:
        del wait_for_message[update.message.chat_id]

    user = update.effective_user.username
    logger.info("Start command by %r", user)
    if not user:
        await update.message.reply_text(
            "Для регистрации необходимо установить username в настройках телеграм"
        )
        return

    keyboard = []
    was_admin = False
    only_channel = None
    for channel in channels.all():
        if user in channel.get("registered_users", []):
            keyboard.append(
                [
                    InlineKeyboardButton(
                        channel["name"], callback_data=f"events {channel["id"]}"
                    )
                ]
            )
            if only_channel is None:
                only_channel = channel["id"]
            else:
                only_channel = False
        if user in channel.get("admins", []):
            was_admin = True
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"адм. {channel["name"]}",
                        callback_data=f"admin {channel["id"]}",
                    )
                ]
            )
    if user in SUPER_ADMINS:
        was_admin = True
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"настройки бота",
                    callback_data="settings",
                )
            ],
        )
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"Добавить канал",
                    callback_data="add-channel",
                )
            ],
        )
    if not keyboard:
        await update.message.reply_text("Вы не зарегистрированы ни на одном канале")
        return

    reply_markup = InlineKeyboardMarkup(keyboard)
    if was_admin:
        admin_text = "управления и"
    else:
        admin_text = ""

    text = "Выберите канал для {admin} просмотра событий:".format(admin=admin_text)

    if only_channel:
        ch = channels_obj[only_channel]
        if msg_id := ch.event_list_message():
            try:
                msg_data = pickle.load(open(f"data/{msg_id}.pkl", "rb"))
            except Exception as e:
                logger.error(e, exc_info=True)
            else:
                logger.info("Send welcome message %r", msg_data)
        text, reply = await ch.all_events(cmd="register", username=user)
        if msg_data:
            await update.message.reply_photo(
                photo=msg_data["photo"],
                caption=escape(text),
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=reply,
            )
        else:
            await update.message.reply_text(text=text, reply_markup=reply_markup)

    elif photo := settings.get(Query().name == "base_image"):
        if photo_id := photo.get("value"):
            photo_data = pickle.load(open(f"data/{photo_id}.pkl", "rb"))
            await update.message.reply_photo(
                photo=photo_data,
                caption=text,
                parse_mode="markdown",
                reply_markup=reply_markup,
            )
    else:
        await update.message.reply_text(text=text, reply_markup=reply_markup)


def escape(text):
    if text is None:
        return
    else:
        return (
            text.replace("_", "\\_")
            .replace(".", "\\.")
            .replace(":", "\\:")
            .replace("-", "\\-")
            .replace("!", "\\!")
            .replace("(", "\\(")
            .replace(")", "\\)")
        )


async def event_show_change(event):
    keyboard = [
        [
            InlineKeyboardButton(
                "Изменить название", callback_data=f"change-event {event['id']} name"
            )
        ],
        [
            InlineKeyboardButton(
                "Изменить дату", callback_data=f"change-event {event['id']} date"
            )
        ],
        [
            InlineKeyboardButton(
                "Изменить время", callback_data=f"change-event {event['id']} time"
            )
        ],
        [
            InlineKeyboardButton(
                "Изменить количество мест",
                callback_data=f"change-event {event['id']} capacity",
            )
        ],
        [
            InlineKeyboardButton(
                "Изменить/добавить notifycation message",
                callback_data=f"change-event {event['id']} message",
            )
        ],
        [
            InlineKeyboardButton(
                "Добавить участника", callback_data=f"change-event {event['id']} add"
            )
        ],
        [
            InlineKeyboardButton(
                "Удалить участника", callback_data=f"change-event {event['id']} remove"
            )
        ],
        [
            InlineKeyboardButton(
                "Скрыть событие", callback_data=f"change-event {event['id']} hidden"
            )
        ],
        [
            InlineKeyboardButton(
                "Удалить событие", callback_data=f"change-event {event['id']} delete"
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 К списку", callback_data=f"list-event {event["channel_id"]}"
            )
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = f"""Изменение события {event['name']}\n
    дата:\t*{event["date"]}*
    время:\t*{event["time"]}*
    мест:\t*{event["capacity"]}*
    занято:\t*{len(event["registered_users"])}*
    кто записан:\t{', '.join([f'@{name}' for name in event["registered_users"]])}
    событие *{'скрыто' if event.get("hidden", False) else 'открыто'}*
"""
    return text, reply_markup


async def send_notification(context):
    logger.info("Send notification %r", context)
    now = datetime.now()
    logger.info("Now %r", now)
    # 15:00 UTC
    if now.hour < 15:
        return
    for n in notification.search(Query().date == now.date().isoformat()):
        logger.info("Notification %r", n)
        event = events.get(Query().id == n["event_id"])
        logger.info("Event %r", event)
        text = f"Напоминание о событии {event['name']} {event['date']} {event['time']}"
        await context.bot.send_message(chat_id=n["chat_id"], text=text)
        if msg_id := event.get("welcome_message"):
            try:
                msg_data = pickle.load(open(f"data/{msg_id}.pkl", "rb"))
            except Exception as e:
                logger.error(e, exc_info=True)
            else:
                try:
                    await context.bot.send_photo(
                        chat_id=n["chat_id"],
                        photo=msg_data["photo"],
                        caption=msg_data["msg"],
                    )
                except Exception as e:
                    logger.error(e, exc_info=True)
        async with db_lock:
            notification.remove(doc_ids=[n.doc_id])


def event_return_back(event_id, channel_id):
    keyboard = []
    if event_id:
        keyboard.append(
            [
                InlineKeyboardButton(
                    "🔙 К события", callback_data=f"change-event {event_id}"
                )
            ]
        )
    keyboard.append(
        [InlineKeyboardButton("🔙 К списку", callback_data=f"list-event {channel_id}")]
    )
    reply_markup = InlineKeyboardMarkup(keyboard)
    return reply_markup


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays info on how to use the bot."""
    logger.info("Help command by %r %r", update.effective_user, update.message)
    await update.message.reply_text("Use /start to test this bot.")


async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the inline query. This is run when you type: @botusername <query>"""
    query = update.inline_query.query
    user_name = update.inline_query.from_user.username
    logger.info("Inline query by %r data %r ", user_name, query)
    results = InlineQueryResultsButton(
        text="Записаться на событие",
        start_parameter="CMD_event_id_register",
    )

    await update.inline_query.answer([], button=results)


def get_list_of_users(event):
    keyboard = []
    for user in event["registered_users"]:
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"Убрать {user}",
                    callback_data=f"change-event {event['id']} remove-user {user}",
                )
            ]
        )
    keyboard.append(
        [
            InlineKeyboardButton(
                "🔙 К событию", callback_data=f"change-event {event['id']}"
            )
        ],
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                "🔙 К списку", callback_data=f"list-event {event['channel_id']}"
            )
        ]
    )
    reply_markup = InlineKeyboardMarkup(keyboard)
    logger.info("List of users %r", reply_markup)
    return reply_markup


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Parses the CallbackQuery and updates the message text."""
    query = update.callback_query
    reply = None
    data = query.data.split(" ")
    user_name = query.from_user.username
    msg_data = None
    logger.info("Button pressed by %r data %r", user_name, data)
    if data[0] == "add-event":
        text = (
            "Для добавления события отправьте сообщение в формате:\n"
            "*Название события*@*дата в формате 2024-09-30*@"
            "*время*@*количество свободных мест, числом*\n\n"
            "Пример:\n"
            "*Событие 1*@*2024-09-30*@*12:00*@*10*"
        )
        wait_for_message[query.message.chat_id] = {
            "type": "add-event",
            "channel_id": data[1],
        }
    elif data[0] == "events":
        ch = channels_obj[int(data[1])]
        if msg_id := ch.event_list_message():
            try:
                msg_data = pickle.load(open(f"data/{msg_id}.pkl", "rb"))
            except Exception as e:
                logger.error(e, exc_info=True)
            else:
                logger.info("Send welcome message %r", msg_data)
        text, reply = await ch.all_events(cmd="register", username=user_name)
    elif data[0] == "admin":
        ch = channels_obj[int(data[1])]
        text, reply = await ch.admin(update, context)
    elif data[0] == "list-event":
        ch = channels_obj[int(data[1])]
        text, reply = await ch.all_events(cmd="change-event", full=True)
    elif data[0] == "change-event":
        event = events.get(Query().id == int(data[1]))
        if len(data) > 2:
            reply = event_return_back(data[1], event["channel_id"])
            if data[2] == "name":
                text = "Введите новое название события"
                wait_for_message[query.message.chat_id] = {
                    "type": "event-name",
                    "event_id": data[1],
                }
            elif data[2] == "date":
                text = "Введите новую дату события в формате 2024-09-30"
                wait_for_message[query.message.chat_id] = {
                    "type": "event-date",
                    "event_id": data[1],
                }
            elif data[2] == "time":
                text = "Введите новое время события"
                wait_for_message[query.message.chat_id] = {
                    "type": "event-time",
                    "event_id": data[1],
                }
            elif data[2] == "capacity":
                text = "Введите новое количество мест"
                wait_for_message[query.message.chat_id] = {
                    "type": "event-capacity",
                    "event_id": data[1],
                }
            elif data[2] == "message":
                text = "Для добавления event-message отправьте сообщение"
                wait_for_message[query.message.chat_id] = {
                    "type": "event-message",
                    "event_id": data[1],
                }
            elif data[2] == "hidden":
                async with db_lock:
                    event = events.get(Query().id == int(data[1]))
                    event["hidden"] = not event.get("hidden", False)
                    events.update(event, Query().id == int(data[1]))
                text = "Событие скрыто" if event["hidden"] else "Событие открыто"
            elif data[2] == "delete":
                channel_id = event["channel_id"]
                async with db_lock:
                    events.remove(Query().id == int(data[1]))
                text = "Событие удалено"
                reply = event_return_back(None, channel_id)
            elif data[2] == "add":
                text = "Введите username участника"
                wait_for_message[query.message.chat_id] = {
                    "type": "event-add",
                    "event_id": data[1],
                }
            elif data[2] == "remove":
                text = "Кого убрать?"
                reply = get_list_of_users(event)
            elif data[2] == "remove-user":
                async with db_lock:
                    event = events.get(Query().id == int(data[1]))
                    event["registered_users"].remove(data[3])
                    events.update(event, Query().id == int(data[1]))
                text = f"Пользователь @{data[3]} удален"
                reply = get_list_of_users(event)
        else:
            text, reply = await event_show_change(event)
    elif data[0] == "register":
        async with db_lock:
            event = events.get(Query().id == int(data[1]))
            if event["capacity"] == 0 or (
                len(event["registered_users"]) < event["capacity"]
            ):
                user = query.from_user.username
                if user not in event["registered_users"]:
                    event["registered_users"].append(user)
                    events.update(event, Query().id == int(data[1]))
                    date = datetime.strptime(event["date"], "%Y-%m-%d").strftime("%a %d.%b")
                    text = f"Вы успешно записались на событие {date} {event["time"]}"
                    notify_date = datetime.fromisoformat(event["date"]) - timedelta(
                        days=1
                    )
                    notification.insert_multiple(
                        [
                            {
                                "event_id": event["id"],
                                "chat_id": query.message.chat_id,
                                "date": notify_date.date().isoformat(),
                            },
                        ]
                    )
                else:
                    text = "Вы уже записаны на событие"
            else:
                text = "Все места на событие заняты"
    elif data[0] == "unregister":
        async with db_lock:
            event = events.get(Query().id == int(data[1]))
            user = query.from_user.username
            if user in event["registered_users"]:
                event["registered_users"].remove(user)
                events.update(event, Query().id == int(data[1]))
                text = "Вы успешно отменили регистрацию на событие /start"
                notification.remove(
                    Query().fragment(
                        {"event_id": event["id"], "chat_id": query.message.chat_id}
                    )
                )
            else:
                text = "Вы небыли записаны на событие /start"
    elif data[0] == "add-message":
        text = "Отправьте фото и текст для welcome message"
        wait_for_message[query.message.chat_id] = {
            "type": "add-message",
            "channel_id": data[1],
        }
    elif data[0] == "del-message":
        async with db_lock:
            channel = channels.get(Query().id == int(data["channel_id"]))
            Path(f"data/{channel["welcome_message"]}.pkl").unlink(missing_ok=True)
            channel["welcome_message"] = None
            channels.update(channel, Query().id == int(data["channel_id"]))
            text = "Сообщение удалено"
    elif data[0] == "add-emessage":
        text = "Отправьте фото и текст для welcome message"
        wait_for_message[query.message.chat_id] = {
            "type": "add-emessage",
            "channel_id": data[1],
        }
    elif data[0] == "del-emessage":
        async with db_lock:
            channel = channels.get(Query().id == int(data["channel_id"]))
            if channel.get("event_list_message"):
                Path(f"data/{channel["event_list_message"]}.pkl").unlink(missing_ok=True)
                channel["event_list_message"] = None
                channels.update(channel, Query().id == int(data["channel_id"]))
                text = "Сообщение удалено"
            else:
                text = "Сообщение не было установлено"
    elif data[0] == "settings":
        text = "Отправьте сообщение с картинкой для установки базового изображения"
        wait_for_message[query.message.chat_id] = {
            "type": "set-base-image",
        }
    elif data[0] == "add-channel":
        text = "Отправьте сообщение с названием канала"
        wait_for_message[query.message.chat_id] = {
            "type": "add-channel",
        }
    else:
        logger.error("Unknown button %r", data)
        text = f"Какая-то ошибка в обработке кнопки - начните с начала /start"

    # CallbackQueries need to be answered, even if no notification to the user is needed
    # Some clients may have trouble otherwise. See https://core.telegram.org/bots/api#callbackquery
    await query.answer()
    if msg_data:
        await query.edit_message_media(
            media=InputMediaPhoto(
                media=msg_data["photo"].file_id,
                caption=escape(msg_data["msg"]),
                parse_mode=ParseMode.MARKDOWN_V2,
            ),
            reply_markup=reply,
        )
        return
    elif query.message.photo:
        await query.edit_message_media(
            media=InputMediaPhoto(
                media=query.message.photo[-1].file_id,
                caption=escape(text),
                parse_mode=ParseMode.MARKDOWN_V2,
            ),
            reply_markup=reply,
        )
        return
    await query.edit_message_text(
        text=escape(text), reply_markup=reply, parse_mode=ParseMode.MARKDOWN_V2
    )


async def photo_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(
        "Message from %r: %r", update.effective_user.username, update.message.text
    )
    photo = update.message.photo[-1]
    msg = update.message.caption
    if update.message.chat_id in wait_for_message:
        data = wait_for_message[update.message.chat_id]
        logger.info("Wait for message %r", data)
        uuid = f"{uuid4()}"
        if data["type"] == "add-message":
            pickle.dump({"photo": photo, "msg": msg}, open(f"data/{uuid}.pkl", "wb"))
            async with db_lock:
                channel = channels.get(Query().id == int(data["channel_id"]))
                channel["welcome_message"] = uuid
                channels.update(channel, Query().id == int(data["channel_id"]))
            await context.bot.send_message(
                chat_id=update.effective_chat.id, text="Сообщение сохранено"
            )
            return
        elif data["type"] == "add-emessage":
            pickle.dump({"photo": photo, "msg": msg}, open(f"data/{uuid}.pkl", "wb"))
            async with db_lock:
                channel = channels.get(Query().id == int(data["channel_id"]))
                channel["event_list_message"] = uuid
                channels.update(channel, Query().id == int(data["channel_id"]))
            await context.bot.send_message(
                chat_id=update.effective_chat.id, text="Сообщение сохранено"
            )
            return
        elif data["type"] == "set-base-image":
            pickle.dump(photo, open(f"data/{uuid}.pkl", "wb"))
            async with db_lock:
                settings.upsert(
                    {"name": "base_image", "value": uuid}, Query().name == "base_image"
                )
            await context.bot.send_message(
                chat_id=update.effective_chat.id, text="Изображение сохранено"
            )
            return
        elif data["type"] == "event-message":
            event_id = data["event_id"]
            pickle.dump({"photo": photo, "msg": msg}, open(f"data/{uuid}.pkl", "wb"))
            async with db_lock:
                event = events.get(Query().id == int(event_id))
                event["welcome_message"] = uuid
                events.update(event, Query().id == int(event_id))
            await context.bot.send_message(
                chat_id=update.effective_chat.id, text="Сообщение сохранено"
            )
            return
        else:
            logger.error("Unknown wait for photo-message %r", data)
            await context.bot.send_message(
                chat_id=update.effective_chat.id, text="Неизвестная команда"
            )
            return
    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=photo,
        caption=msg,
        parse_mode="markdown",
    )


async def msg_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(
        "Message from %r: %r", update.effective_user.username, update.message.text
    )
    if update.message.chat_id in wait_for_message:
        data = wait_for_message[update.message.chat_id]
        logger.info("Wait for message %r", data)
        if data["type"] == "add-event":
            msg_data = update.message.text.split("@")
            try:
                async with db_lock:
                    event = {
                        "id": get_next_id(events),
                        "name": msg_data[0],
                        "date": msg_data[1],
                        "time": msg_data[2],
                        "capacity": int(msg_data[3]),
                        "registered_users": [],
                        "channel_id": int(data["channel_id"]),
                    }

                    events.insert(event)
                    text = "Событие успешно добавлено! Вы можете отправить следующее событие или нажать /start для возврата в главное меню"
            except Exception as e:
                logger.error(e)
                text = f"Ошибка при добавлении события {e!r}"
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=text,
            )
            return
        elif data["type"].startswith("event-"):
            async with db_lock:
                event = events.get(Query().id == int(data["event_id"]))
                if data["type"] == "event-name":
                    event["name"] = update.message.text
                elif data["type"] == "event-date":
                    event["date"] = update.message.text
                elif data["type"] == "event-time":
                    event["time"] = update.message.text
                elif data["type"] == "event-capacity":
                    event["capacity"] = int(update.message.text)
                elif data["type"] == "event-add":
                    user = update.message.text.replace("@", "")
                    if user not in event["registered_users"]:
                        event["registered_users"].append(user)
                elif data["type"] == "event-message":
                    uuid = f"{uuid4()}"
                    pickle.dump(
                        {"photo": None, "msg": update.message.text},
                        open(f"data/{uuid}.pkl", "wb"),
                    )
                    event["welcome_message"] = uuid
                events.update(event, Query().id == int(data["event_id"]))
            text, reply = await event_show_change(event)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=text,
                reply_markup=reply,
                parse_mode=ParseMode.HTML,
            )
            return
        elif data["type"] == "add-channel":
            async with db_lock:
                token = f"{uuid4()}"
                admin_token = f"{uuid4()}"
                channel = {
                    "id": get_next_id(channels),
                    "name": update.message.text,
                    "registered_users": [],
                    "admins": [update.effective_user.username],
                    "token": token,
                    "admin_token": admin_token,
                }
                channels.insert(channel)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Канал успешно добавлен. \n Токен для регистрации: {token} \n Токен для администрирования: {admin_token}".format(
                    token=token, admin_token=admin_token
                ),
            )
            return
        else:
            logger.error("Unknown wait for message %r", data)

    text = f"Для начала работы отправьте /start"
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text)


# Main function to set up the bot
def main():
    application = (
        Application.builder().token(os.environ.get("TELEGRAM_BOT_TOKEN")).build()
    )

    for channel in channels.all():
        ch = Channel(channel["id"], channel["name"])
        logger.info("%r register hooks", ch)
        channels_obj[channel["id"]] = ch
        application.add_handler(
            CommandHandler(
                "start", ch.register_as_user, filters.Regex(channel["token"])
            )
        )
        application.add_handler(
            CommandHandler(
                "start", ch.register_as_admin, filters.Regex(channel["admin_token"])
            )
        )

    application.add_handler(CommandHandler("start", start))

    application.add_handler(CallbackQueryHandler(button))
    application.add_handler(InlineQueryHandler(inline_query))
    application.add_handler(CommandHandler("help", help_command))

    # Command handlers
    msg_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), msg_process)
    photo_handler = MessageHandler(filters.PHOTO, photo_process)

    application.add_handler(msg_handler)
    application.add_handler(photo_handler)

    # Start the bot
    application.job_queue.run_repeating(send_notification, interval=60, first=0)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    migration.apply()
    main()
