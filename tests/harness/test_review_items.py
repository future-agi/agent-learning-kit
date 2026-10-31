def test_a_writing_chat_tool_notes_the_items_it_moved_for_the_message_it_answered(tmp_path):
    import asyncio
    import json
    from types import SimpleNamespace

    from fi.alk.harness.hosted_chat_entrypoint import HostedChatRuntime
    from fi.alk.harness.review_items import PROVENANCE_FILE, item_digests

    (tmp_path / "contract.json").write_text(json.dumps({"tools": [{"name": "issue_refund", "description": "a"}]}))

    async def change_world(_args):
        (tmp_path / "contract.json").write_text(json.dumps({"tools": [{"name": "issue_refund", "description": "b"}]}))
        return {"content": [{"type": "text", "text": "ok"}]}

    async def refused(_args):
        (tmp_path / "actors.json").write_text(json.dumps({"actors": [{"id": "x"}]}))
        return {"content": [{"type": "text", "text": "no"}], "is_error": True}

    from dataclasses import make_dataclass

    tool = make_dataclass("Tool", ["name", "handler"])
    runtime = SimpleNamespace(workspace=tmp_path, _turn_message_id="msg-7")
    async def reads(_args):
        (tmp_path / "notes.md").write_text("scratch")
        return {"content": [{"type": "text", "text": "read"}]}

    for name, handler in (("change_world", change_world), ("fix_tool", refused), ("read_document", reads)):
        spec = HostedChatRuntime._cited(runtime, tool(name, handler))
        asyncio.run(spec.handler({}))
    lines = [json.loads(one) for one in (tmp_path / PROVENANCE_FILE).read_text().splitlines()]
    # Only the writing tool that succeeded is recorded; a refused call and a read-only tool add nothing.
    assert [(one["message_id"], one["tool"], one["items"]) for one in lines] == [
        ("msg-7", "change_world", ["tool:issue_refund"])]
    assert "tool:issue_refund" in item_digests(tmp_path)
