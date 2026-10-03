import pytest

from app.models import Action


@pytest.mark.parametrize("text,action,obj,contact", [
    # Command examples from the project brief
    ("Where is my phone?", Action.FIND_OBJECT, "phone", None),
    ("Where is my spoon?", Action.FIND_OBJECT, "spoon", None),
    ("Where is my medicine?", Action.FIND_OBJECT, "medicine", None),
    ("I need medicine.", Action.GET_OBJECT, "medicine", None),
    ("I need water.", Action.GET_OBJECT, "water", None),
    ("Bring my phone closer.", Action.GET_OBJECT, "phone", None),
    ("Call my son.", Action.CALL_CONTACT, None, "son"),
    ("Tell my daughter I need help.", Action.EMERGENCY, None, "daughter"),
    ("Send my caregiver a message.", Action.MESSAGE_CONTACT, None, "caregiver"),
    ("Help me.", Action.EMERGENCY, None, None),
    ("Stop.", Action.STOP, None, None),
    ("What is my status?", Action.STATUS, None, None),
    # Variations
    ("where's my mobile", Action.FIND_OBJECT, "phone", None),
    ("Can you please bring me my tablets", Action.GET_OBJECT, "medicine", None),
    ("I need my medicine please", Action.GET_OBJECT, "medicine", None),
    ("tell my son I am hungry", Action.MESSAGE_CONTACT, None, "son"),
    ("go home", Action.HOME, None, None),
    ("what's the weather", Action.UNKNOWN, None, None),
])
def test_examples(parser, text, action, obj, contact):
    intent = parser.parse(text)
    assert intent.action is action, intent
    assert intent.object == obj
    assert intent.contact == contact
    assert intent.parser == "rules"


@pytest.mark.parametrize("text", ["help", "HELP!", "Help me", "emergency", "I need help", "call caregiver",
                                  "please call my caregiver", "call the nurse", "SOS", "I fell", "somebody help"])
def test_emergency_phrases_are_local(parser, text):
    assert parser.parse(text).action is Action.EMERGENCY


@pytest.mark.parametrize("text", ["help me find my phone", "can you help me with my spoon", "this is helpful"])
def test_help_inside_normal_request_is_not_emergency(parser, text):
    assert parser.parse(text).action is not Action.EMERGENCY


def test_calling_the_caregiver_escalates_to_emergency(parser):
    intent = parser.parse("call my carer")
    assert intent.action is Action.EMERGENCY and intent.contact == "caregiver"


def test_emergency_keeps_message_for_named_contact(parser):
    intent = parser.parse("Tell my daughter I need help")
    assert intent.action is Action.EMERGENCY
    assert intent.contact == "daughter" and intent.message == "I need help"


def test_stop_wins_anywhere_in_sentence(parser):
    assert parser.parse("no no stop it now").action is Action.STOP


def test_emergency_beats_stop(parser):
    assert parser.parse("stop help me I need help").action is Action.EMERGENCY


def test_message_without_text_uses_default(parser, intents):
    intent = parser.parse("send my son a message")
    assert intent.action is Action.MESSAGE_CONTACT and intent.message == intents.default_message


def test_unknown_object_is_not_guessed(parser):
    intent = parser.parse("where is my unicorn")
    assert intent.action is Action.FIND_OBJECT and intent.object is None


def test_unknown_contact_is_not_guessed(parser):
    intent = parser.parse("call my neighbour")
    assert intent.action is Action.CALL_CONTACT and intent.contact is None


def test_empty_text_is_unknown(parser):
    assert parser.parse("  ?!  ").action is Action.UNKNOWN
