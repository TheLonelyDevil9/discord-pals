"""Deterministic PR activity rendering and version-specific Discord receipts."""

from __future__ import annotations

import re

import discord

from project_automation import WorkflowError, mirror_key
from project_automation_activity import activity_receipt_key, marker_prefix
from project_automation_store import run_store


NO_MENTIONS = discord.AllowedMentions.none()
COLOUR = 0x539BEB
_VERSION = re.compile(r"[A-Za-z0-9:_-]{1,128}\Z")
_LABELS = {
    "comment": "PR comment", "review_comment": "Code review comment",
    "review": "Review", "thread": "Review thread", "commits": "Code update",
}


def receipt_key(config, event):
    """The source object belongs to this exact helper/server/forum/repository."""
    if event.get("kind") == "pr_activity":
        return activity_receipt_key(config, event)
    # Any other event is the parent pull request, so the key is the key of the PR's post.
    return mirror_key(config, "pull", event["key"])


def activity_version(event):
    version = event.get("source_version")
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise WorkflowError("The PR activity is missing a valid source version.")
    return version


def activity_marker(config, event):
    return marker_prefix(receipt_key(config, event)) + activity_version(event)


def _safe(value):
    text = value if isinstance(value, str) else str(value or "")
    return re.sub(r"@(?!\u200b)", "@\u200b", text).replace("<#", "<\u200b#")


def _field(embed, name, value):
    if value:
        embed.add_field(name=name, value=_safe(value)[:1024], inline=False)


def activity_embed(event, marker):
    """Keep source prose intact within an explicitly labelled linked excerpt."""
    kind = event["activity_type"]
    label = _LABELS[kind]
    deleted = event.get("action") == "deleted" or event.get("state") == "deleted"
    edited = event.get("action") == "edited" or (
        kind in {"comment", "review_comment"} and event.get("created_at")
        and event.get("updated_at") and event["created_at"] != event["updated_at"]
    )
    state = str(event.get("state") or "").replace("_", " ").capitalize()
    status = "Deleted" if deleted else state if kind in {"review", "thread"} else "Edited" if edited else ""
    title = f"{label} · PR #{event['number']}" + (f" · {status}" if status else "")
    if edited and not deleted and status != "Edited":
        title += " · Edited"
    url = event.get("compare_url") or event.get("html_url") or event.get("url")
    embed = discord.Embed(title=title, url=url, color=COLOUR)
    author = discord.utils.escape_markdown(_safe(event.get("author") or "Unknown author"))
    if event.get("author_type") == "Bot":
        author += " (bot)"
    _field(embed, "Author", author)
    times = []
    for key, name in (("created_at", "Created"), ("updated_at", "Updated"), ("context_updated_at", "Comment updated")):
        if event.get(key):
            times.append(f"{name}: {_safe(event[key])}")
    _field(embed, "Source time", "\n".join(times))

    path = event.get("path")
    if path:
        location = discord.utils.escape_markdown(_safe(path))
        line = event.get("line")
        start = event.get("start_line")
        if line is not None:
            location += f"\nLine {start}–{line}" if start is not None and start != line else f"\nLine {line}"
        _field(embed, "Code location", location)
    reply_url = event.get("reply_to_url")
    if not reply_url and event.get("reply_to_id"):
        reply_url = f"https://github.com/{event['repository']}/pull/{event['number']}#discussion_r{event['reply_to_id']}"
    if kind == "thread":
        reply_url = event.get("html_url") or event.get("url")
    if reply_url:
        discord_reply = event.get("reply_to_discord_url")
        _field(embed, "Discussion" if kind == "thread" else "Reply", (f"[Parent comment in Discord]({discord_reply}) · " if discord_reply else "")
               + f"[Original comment on GitHub]({reply_url})")
    if event.get("resolved_by"):
        _field(embed, "Resolved by", discord.utils.escape_markdown(_safe(event["resolved_by"])))

    body = _safe(event.get("body", ""))
    truncated = bool(event.get("body_truncated"))
    if deleted:
        body = "Deleted on GitHub."
        truncated = False
    elif kind == "thread":
        body = "Review discussion resolved." if event.get("state") == "resolved" else "Review discussion reopened."
        truncated = False
    elif kind == "commits":
        head = ":".join(str(event.get(key) or "") for key in ("head_repository", "head_ref")).strip(":")
        _field(embed, "Head branch", discord.utils.escape_markdown(_safe(head)))
        before, after = event.get("before", ""), event.get("after", "")
        parts = [f"`{before[:12]}` → `{after[:12]}`"] if before and after else ([f"Head: `{after[:12]}`"] if after else [])
        commits = event.get("commits") or []
        for commit in commits:
            sha = str(commit.get("sha") or commit.get("id") or "")[:12]
            commit_url = commit.get("url")
            identifier = f"[{sha}]({commit_url})" if commit_url else f"`{sha}`"
            parts.append(f"{identifier} {_safe(commit.get('message', ''))}")
        if body:
            parts.append(body)
        body = "\n".join(parts)
        count = event.get("commit_count")
        if isinstance(count, int) and not isinstance(count, bool):
            _field(embed, "Commit count", str(count))
            truncated = truncated or count > len(commits)

    # Leave room for metadata and the version receipt within Discord's 6000
    # aggregate embed limit, as well as its 4096-character description limit.
    metadata_size = len(embed) + len(marker)
    suffix = f"\n\n[View on GitHub]({url})" if url else ""
    body = body or "No body text was provided."
    full_limit = max(0, min(3600, 4096 - len(suffix), 6000 - metadata_size - len(suffix)))
    truncated = truncated or len(body) > full_limit
    if truncated:
        prefix = f"**Excerpt — [read the full activity on GitHub]({url})**\n\n"
        body_limit = max(0, min(3600, 4096 - len(prefix), 6000 - metadata_size - len(prefix)))
        embed.description = prefix + body[:body_limit]
    else:
        embed.description = body + suffix
    embed.set_footer(text=marker)
    return embed


def _context(transport, event):
    config = dict(transport.service.settings())
    if (event.get("kind") != "pr_activity" or event.get("activity_type") not in _LABELS
            or event.get("repository", "").lower() != config["repository"].lower()):
        raise WorkflowError("This PR activity belongs to another project or is invalid.")
    binding = event.get("_binding")
    if binding is not None and any(binding.get(key) != config.get(key) for key in (
        "repository", "bot_name", "guild_id", "reviews_channel_id",
    )):
        raise WorkflowError("The PR activity belongs to an earlier Discord configuration.")
    pull = event.get("pull")
    if (not isinstance(pull, dict) or pull.get("number") != event.get("number")
            or pull.get("state") not in {"open", "closed"} or pull.get("kind", "pull") != "pull"):
        raise WorkflowError("The PR activity needs the current pull request before delivery.")
    activity_version(event)
    parent = {**pull, "kind": "pull", "key": f"pull:{event['number']}",
              "repository": config["repository"], "closed": pull["state"] == "closed" or bool(pull.get("merged"))}
    return config, parent


def _message_version(message, prefix):
    for embed in message.embeds:
        footer = embed.footer.text or ""
        if footer.startswith(prefix) and _VERSION.fullmatch(footer[len(prefix):]):
            return footer[len(prefix):]
    return None


def _saved_footer(message, receipt, prefix):
    """Return (prefix, version) from the footer of a saved activity message, or None when it is not ours.

    A message keeps the footer prefix it was written with. After the rename-helper command moves
    its receipt to a new key, that prefix differs from the computed one, so the receipt stores it
    as "marker_prefix". An edit whose acknowledgement was lost can leave the message on the
    computed prefix while the receipt still names the earlier one, so either is accepted.
    """
    stored = (receipt or {}).get("marker_prefix")
    for candidate in ([stored] if isinstance(stored, str) and stored else []) + [prefix]:
        version = _message_version(message, candidate)
        if version:
            return candidate, version
    return None


def _record(transport, config, event, thread_id, message_id, version, *, current, prefix):
    """`prefix` must be the footer prefix the message shows, because the next check of the saved
    message accepts only that prefix or the computed one."""
    key = receipt_key(config, event)
    receipt = {"channel_id": str(thread_id), "message_id": str(message_id),
               "source_version": version, "source_key": event["key"],
               "number": event["number"], "activity_type": event["activity_type"],
               "forum_id": config["reviews_channel_id"], "repository": config["repository"],
               "guild_id": config["guild_id"], "bot_name": config["bot_name"], "marker_prefix": prefix,
               "jump_url": f"https://discord.com/channels/{config['guild_id']}/{thread_id}/{message_id}"}
    transport.service.store.put_link("pr_activity_delivery", f"{key}:{version}", receipt)
    previous = transport.service.store.get_link("pr_activity", key)
    # Recovery of an older job must not replace the latest object receipt.
    if current or not previous or previous.get("source_version") == version:
        transport.service.store.put_link("pr_activity", key, receipt)
    attempt = transport.service.store.get_link("pr_activity_attempt", key)
    if not attempt or attempt.get("source_version") == version:
        transport.service.store.put_link("pr_activity_attempt", key, {"state": "delivered", "source_version": version})


async def _with_parent_reply(transport, config, event, thread):
    kind = event.get("activity_type")
    parent_id = event.get("comment_id") if kind == "thread" else event.get("reply_to_id")
    if kind not in {"review_comment", "thread"} or not parent_id:
        return event
    parent = {**event, "key": f"pr:{event['number']}:review_comment:{parent_id}"}
    key = receipt_key(config, parent)
    receipt = await run_store(transport.service.store.get_link, "pr_activity", key)
    if not receipt or receipt.get("channel_id") != str(thread.id):
        return event
    try:
        message = await thread.fetch_message(int(receipt["message_id"]))
    except discord.NotFound:
        return event
    if (message.author.id != transport._bot().client.user.id
            or not _saved_footer(message, receipt, marker_prefix(key))):
        return event
    return {**event, "reply_to_discord_url": f"https://discord.com/channels/{config['guild_id']}/{thread.id}/{message.id}"}


async def mirror_activity(transport, event, *, recover_only=False):
    config, parent = _context(transport, event)
    key, version = receipt_key(config, event), activity_version(event)
    # Parent creation has its own durable attempt guard. A lost acknowledgement
    # cannot be mistaken for a delivered activity or authorize another parent.
    if not await transport.mirror(parent, recover_only=recover_only):
        return False
    parent_key = receipt_key(config, parent)
    parent_link = await run_store(transport.service.store.get_link, "mirror", parent_key)
    thread = await transport._channel(parent_link["channel_id"])
    if not isinstance(thread, discord.Thread) or str(thread.parent_id) != config["reviews_channel_id"]:
        raise WorkflowError("The saved PR mirror belongs to a different forum.")

    prefix = marker_prefix(key)
    link = await run_store(transport.service.store.get_link, "pr_activity", key)
    message, observed, shown = None, None, prefix
    if link:
        if str(link["channel_id"]) != str(thread.id):
            raise WorkflowError("The saved PR activity belongs to a different thread.")
        try:
            message = await thread.fetch_message(int(link["message_id"]))
            if message.author.id != transport._bot().client.user.id:
                raise WorkflowError("The saved PR activity belongs to a different Discord author.")
            footer = _saved_footer(message, link, prefix)
            if not footer:
                raise WorkflowError("The saved PR activity has a different delivery marker.")
            shown, observed = footer
        except discord.NotFound:
            message = None
    if message is None:
        async for candidate in thread.history(limit=200):
            if candidate.author.id == transport._bot().client.user.id:
                observed = _message_version(candidate, prefix)
                if observed:
                    message = candidate
                    break
    found = await run_store(_settle_saved, transport, config, event, key, version, thread.id,
                            None if message is None else message.id, observed, shown, recover_only)
    if found is not None:
        return found
    embed = activity_embed(await _with_parent_reply(transport, config, event, thread), prefix + version)
    original = bool(thread.archived), bool(thread.locked)
    opened = False
    try:
        if any(original) or parent["closed"]:
            # edit() returns a new Thread; track our change independently of the
            # cached object's archive flags when restoring after a failed send.
            opened = True
            await thread.edit(archived=False, locked=False)
        await run_store(transport.service.store.put_link, "pr_activity_attempt", key,
                        {"state": "pending", "source_version": version})
        if message:
            await message.edit(embed=embed, allowed_mentions=NO_MENTIONS)
        else:
            message = await thread.send(embed=embed, allowed_mentions=NO_MENTIONS)
        await run_store(_record, transport, config, event, thread.id, message.id, version, current=True, prefix=prefix)
    finally:
        if opened or parent["closed"]:
            await thread.edit(archived=parent["closed"], locked=parent["closed"])
    return True


def _settle_saved(transport, config, event, key, version, thread_id, message_id, observed, shown, recover_only):
    """Record what the found message shows, and return the result when no send is needed, else None.

    message_id is the ID of the activity message found for `key`, or None, and observed is the
    version it shows. Raise WorkflowError when no message was found but an earlier send may exist.
    """
    store = transport.service.store
    if message_id is not None:
        _record(transport, config, event, thread_id, message_id, observed, current=False, prefix=shown)
        if observed == version:
            if not recover_only:
                _record(transport, config, event, thread_id, message_id, version, current=True, prefix=shown)
            return True
        if recover_only:
            # A persisted receipt proves that exact version was applied before a
            # later edit. Finding only a previous version does not prove this one.
            receipt = store.get_link("pr_activity_delivery", f"{key}:{version}")
            return bool(receipt and receipt.get("message_id") == str(message_id)
                        and receipt.get("channel_id") == str(thread_id))
    elif recover_only:
        return False
    attempt = store.get_link("pr_activity_attempt", key)
    if message_id is None and attempt and attempt.get("state") == "pending":
        raise WorkflowError("An earlier PR activity send needs recovery before it can be repeated.")
    return None


def confirm_not_delivered(transport, event):
    """Release absent-send guards only after the operator's explicit confirmation."""
    config = transport.service.settings()
    if event.get("kind") == "pr_activity":
        config, parent = _context(transport, event)
        key = receipt_key(config, event)
        transport.service.store.put_link("pr_activity_attempt", key, {"state": "retry_authorized"})
        parent_key = receipt_key(config, parent)
        transport.service.store.put_link("mirror_attempt", parent_key, {"state": "retry_authorized"})
    else:
        transport.service.store.put_link("mirror_attempt", mirror_key(config, event["kind"], event["key"]),
                                         {"state": "retry_authorized"})
