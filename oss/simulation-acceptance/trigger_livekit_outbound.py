from __future__ import annotations

import asyncio
import json
import os
import uuid

from livekit import api
from livekit.protocol.sip import ListSIPDispatchRuleRequest


async def main() -> None:
    client = api.LiveKitAPI(
        url=_api_url(os.environ["ACCEPTANCE_LIVEKIT_URL"]),
        api_key=os.environ["LIVEKIT_API_KEY"],
        api_secret=os.environ["LIVEKIT_API_SECRET"],
    )
    origin_room = f"acceptance-origin-{uuid.uuid4().hex[:12]}"
    origin_room_created = False
    try:
        target_room = await _wait_for_target_room(client)
        await client.room.create_room(api.CreateRoomRequest(name=origin_room))
        origin_room_created = True
        await client.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=os.environ["LIVEKIT_TARGET_AGENT_NAME"],
                room=origin_room,
                metadata=json.dumps(
                    {
                        "target_instructions": os.environ[
                            "LIVEKIT_TARGET_SYSTEM_PROMPT"
                        ],
                        "outbound_sip_trunk_id": os.environ[
                            "LIVEKIT_OUTBOUND_TRUNK_ID"
                        ],
                        "outbound_sip_number": os.environ["PSTN_CALLER_NUMBER"],
                        "outbound_sip_call_to": os.environ["LIVEKIT_INBOUND_DID"],
                        "outbound_sip_participant_identity": (
                            "livekit-originating-target"
                        ),
                    },
                    sort_keys=True,
                ),
            )
        )
        await _wait_for_target_cleanup(client, target_room)
    finally:
        try:
            if origin_room_created:
                await client.room.delete_room(api.DeleteRoomRequest(room=origin_room))
        finally:
            await client.aclose()


async def _wait_for_target_room(client: api.LiveKitAPI) -> str:
    for _ in range(240):
        response = await client.sip.list_dispatch_rule(
            ListSIPDispatchRuleRequest()
        )
        for item in response.items:
            direct = getattr(item.rule, "dispatch_rule_direct", None)
            room_name = getattr(direct, "room_name", "") if direct else ""
            if item.name.startswith("sim-inbound-") and room_name.startswith(
                "acceptance-1-2-1-"
            ):
                return room_name
        await asyncio.sleep(0.5)
    raise TimeoutError("livekit_outbound_target_room_not_ready")


async def _wait_for_target_cleanup(
    client: api.LiveKitAPI,
    target_room: str,
) -> None:
    for _ in range(400):
        response = await client.sip.list_dispatch_rule(
            ListSIPDispatchRuleRequest()
        )
        if not any(
            getattr(
                getattr(item.rule, "dispatch_rule_direct", None),
                "room_name",
                "",
            )
            == target_room
            for item in response.items
        ):
            return
        await asyncio.sleep(0.5)


def _api_url(url: str) -> str:
    if url.startswith("wss://"):
        return f"https://{url.removeprefix('wss://')}"
    if url.startswith("ws://"):
        return f"http://{url.removeprefix('ws://')}"
    return url


if __name__ == "__main__":
    asyncio.run(main())
