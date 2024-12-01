# TODO: write tests
import pytest
from unittest.mock import Mock, AsyncMock
import sched_bot


@pytest.mark.asyncio
async def test_bot():
    update = Mock()
    query = Mock()
    query.data = "change-event 15"
    query.from_user = Mock()
    query.from_user.username = 'mariialytkina'
    query.answer = AsyncMock()
    query.edit_message_media = AsyncMock()
    context = Mock()
    update.callback_query = query
    await sched_bot.button(update, context)
    assert query.edit_message_media.called

