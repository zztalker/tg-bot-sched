import asyncio
import os
import sentry_sdk
from tinydb import TinyDB, Query
from uuid import uuid4
from html import escape
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    InlineQueryResultsButton,
    Bot,
    InlineQueryResultArticle,
    InputTextMessageContent,
    helpers,
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

channels_obj = {}
wait_for_message = {}


class Channel:
    def __init__(self, id, name):
        self.id = id
        self.name = name

    def __str__(self) -> str:
        return f"Channel(id={self.id}, name={self.name})"

    async def all_events(self, cmd=None, full=False):
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
            if not full:
                if event["capacity"] != 0 and free_places == 0:
                    continue
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text=f"🆓[{free_places}] {name} {date} {time}",
                        callback_data=f"{cmd} {event['id']}",
                    ),
                ]
            )
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = f"Список предстоящих мероприятий {self.name}:"
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
                    text = f"Вы успешно зарегистрировались на канал {self.name} - для продолженния напишите /start"
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
                    "Удалить прошедшие события", callback_data=f"delete-old {self.id}"
                )
            ],
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)
        return f"Упправление событиями в {self.name}:", reply_markup

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
    for channel in channels.all():
        if user in channel.get("registered_users", []):
            keyboard.append(
                [
                    InlineKeyboardButton(
                        channel["name"], callback_data=f"events {channel["id"]}"
                    )
                ]
            )
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

    if not keyboard:
        await update.message.reply_text("Вы не зарегистрированы ни на одном канале")
        return

    reply_markup = InlineKeyboardMarkup(keyboard)
    if was_admin:
        admin_text = "управления и"
    else:
        admin_text = ""

    await update.message.reply_text(
        "Вы можете выбрать канал для {admin} записи на события:".format(
            admin=admin_text
        ),
        reply_markup=reply_markup,
    )


async def event_show_change(event):
    keyboard = []
    keyboard.append(
        [
            InlineKeyboardButton(
                "Изменить название", callback_data=f"change-event {event['id']} name"
            )
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                "Изменить дату", callback_data=f"change-event {event['id']} date"
            )
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                "Изменить время", callback_data=f"change-event {event['id']} time"
            )
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                "Изменить количество мест",
                callback_data=f"change-event {event['id']} capacity",
            )
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                "Скрыть событие", callback_data=f"change-event {event['id']} hidden"
            )
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                "Удалить событие", callback_data=f"change-event {event['id']} delete"
            )
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                "🔙 К списку", callback_data=f"list-event {event["channel_id"]}"
            )
        ]
    )
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = f"""Изменение события {event['name']}\n
    дата:\t<b>{event["date"]} </b>
    время:\t<b>{event["time"]} </b>
    мест:\t<b>{event["capacity"]}</b>
    занято:\t<b>{len(event["registered_users"])}</b>
    кто записан:\t{', '.join([f'@{name}' for name in event["registered_users"]])}
    событие <b>{'скрыто' if event.get("hidden", False) else 'открыто'}</b>
"""
    return text, reply_markup


def event_return_back(event_id, channel_id):
    keyboard = []
    keyboard.append(
        [InlineKeyboardButton("🔙 К события", callback_data=f"change-event {event_id}")]
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


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Parses the CallbackQuery and updates the message text."""
    query = update.callback_query
    reply = None
    data = query.data.split(" ")
    user_name = query.from_user.username
    logger.info("Button pressed by %r data %r", user_name, data)
    if data[0] == "add-event":
        text = (
            "Для добавления события отправьте сообщение в формате:\n"
            "<b>Название события</b>@<b>дата в формате 2024-09-30</b>@"
            "<b>время</b>@<b>количество свободных мест, числом</b>\n\n"
            "Пример:\n"
            "<b>Событие 1</b>@<b>2024-09-30</b>@<b>12:00</b>@<b>10</b>"
        )
        wait_for_message[query.message.chat_id] = {
            "type": "add-event",
            "channel_id": data[1],
        }
    elif data[0] == "events":
        ch = channels_obj[int(data[1])]
        text, reply = await ch.all_events(cmd="register")
    elif data[0] == "admin":
        ch = channels_obj[int(data[1])]
        text, reply = await ch.admin(update, context)
    elif data[0] == "list-event":
        ch = channels_obj[int(data[1])]
        text, reply = await ch.all_events(cmd="change-event", full=True)
    elif data[0] == "change-event":
        event = events.get(Query().id == data[1])
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
            elif data[2] == "hidden":
                async with db_lock:
                    event = events.get(Query().id == data[1])
                    event["hidden"] = not event.get("hidden", False)
                    events.update(event, Query().id == data[1])
                text = "Событие скрыто" if event["hidden"] else "Событие открыто"
            elif data[2] == "delete":
                async with db_lock:
                    events.remove(Query().id == data[1])
                text = "Событие удалено"
                reply.remove(0)
        else:
            text, reply = await event_show_change(event)
    elif data[0] == "register":
        async with db_lock:
            event = events.get(Query().id == data[1])
            if event["capacity"] == 0 or (
                len(event["registered_users"]) < event["capacity"]
            ):
                event["registered_users"].append(query.from_user.username)
                events.update(event, Query().id == data[1])
                text = "Вы успешно записались на событие"
            else:
                text = "Все места на событие заняты"
    else:
        text = f"Какая-то ошибка в обработке кнопки {data}"

    # CallbackQueries need to be answered, even if no notification to the user is needed
    # Some clients may have trouble otherwise. See https://core.telegram.org/bots/api#callbackquery
    await query.answer()
    await query.edit_message_text(
        text=text, reply_markup=reply, parse_mode=ParseMode.HTML
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
                event = {
                    "id": str(uuid4()),
                    "name": msg_data[0],
                    "date": msg_data[1],
                    "time": msg_data[2],
                    "capacity": int(msg_data[3]),
                    "registered_users": [],
                    "channel_id": int(data["channel_id"]),
                }
                async with db_lock:
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
                event = events.get(Query().id == data["event_id"])
                if data["type"] == "event-name":
                    event["name"] = update.message.text
                elif data["type"] == "event-date":
                    event["date"] = update.message.text
                elif data["type"] == "event-time":
                    event["time"] = update.message.text
                elif data["type"] == "event-capacity":
                    event["capacity"] = int(update.message.text)

                events.update(event, Query().id == data["event_id"])
            text, reply = await event_show_change(event)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=text,
                reply_markup=reply,
                parse_mode=ParseMode.HTML,
            )
            return

    text = f"Для начала работы отправьте /start"
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text)


# Main function to set up the bot
def main():
    application = (
        Application.builder().token(os.environ.get("TELEGRAM_BOT_TOKEN")).build()
    )

    # Command handlers
    echo_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), msg_process)

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
    application.add_handler(echo_handler)
    # Start the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    migration.apply()
    main()
