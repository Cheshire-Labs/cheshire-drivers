"""Swapping the deck must not make the driver forget tips still on a nozzle.

``attach_deck`` replaces the deck. It does not recompose the heads, so tips a head
picked up are physically still on it afterwards. Forgetting where they came from
lets a rebuilt rack count a carried tip as still sitting in its spot, and the next
pick aims at a spot nothing can be taken from.

Found on the bench 2026-08-26, where a reconnecting agent re-runs configure_deck on a
live session with tips on the head.
"""

import pytest
from cheshire_drivers.liquid_handler_models import DeckLayoutConfig
from cheshire_drivers.plr.opentrons_flex import (
    FLEX_DECK_TYPE,
    _mount_heads,
    _MountedTip,
)

from tests.test_opentrons_flex_pipetting import (
    _EIGHT_CHANNEL,
    _SINGLE_CHANNEL,
    _bench,
)

pytestmark = pytest.mark.asyncio


async def test_reconfiguring_a_deck_keeps_the_tips_a_head_is_carrying() -> None:
    driver, _ = await _bench(_EIGHT_CHANNEL, _SINGLE_CHANNEL, gripper=True)
    flex = driver._require_flex("test")
    head = next(head for _, head in _mount_heads(flex))
    driver._mounted_tips = [_MountedTip(head=head, channel=0, rack="rack-a", position="A1")]

    await driver.configure_deck(DeckLayoutConfig(deck_type=FLEX_DECK_TYPE, resources=[]))

    assert driver._spots_a_head_carries() == {"rack-a": {"A1"}}, (
        "a deck swap forgot tips the head is still holding"
    )
