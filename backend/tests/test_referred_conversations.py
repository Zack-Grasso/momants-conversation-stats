from app.models.conversation import Conversation, Message
from app.utils.referred_conversations import conversation_is_referred


def test_conversation_is_referred_when_agent_shares_email():
    conversation = Conversation(
        id=1,
        agent_id="agent-1",
        external_id="ext-1",
        messages=[
            Message(id=1, conversation_id=1, from_agent=False, content="Ik wil mijn ticket annuleren"),
            Message(
                id=2,
                conversation_id=1,
                from_agent=True,
                content="Neem contact op via support@festival.nl",
            ),
        ],
    )
    assert conversation_is_referred(conversation) is True


def test_conversation_is_not_referred_without_agent_email():
    conversation = Conversation(
        id=2,
        agent_id="agent-1",
        external_id="ext-2",
        messages=[
            Message(id=3, conversation_id=2, from_agent=False, content="Waar is mijn ticket?"),
            Message(id=4, conversation_id=2, from_agent=True, content="Je ticket staat in de app."),
        ],
    )
    assert conversation_is_referred(conversation) is False
