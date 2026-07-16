import os
import sys
import json
import random
import logging
import asyncio

if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("gc_joiner")


class JoinProgress:
    def __init__(self):
        self.reset()

    def reset(self):
        self.total = 0
        self.joined = 0
        self.failed = 0
        self.already_in = 0
        self.current = ""
        self.current_index = 0
        self.running = False
        self.done = False
        self.errors = []

    def to_dict(self):
        return {
            "total": self.total,
            "joined": self.joined,
            "failed": self.failed,
            "already_in": self.already_in,
            "current": self.current,
            "current_index": self.current_index,
            "running": self.running,
            "done": self.done,
            "errors": self.errors[-10:]
        }


progress = JoinProgress()


def extract_invite_hash(link):
    if not link:
        return None
    link = link.strip().rstrip('/')
    parts = link.split('/')
    if not parts:
        return None
    last_part = parts[-1]
    if last_part.startswith('+'):
        last_part = last_part[1:]
    return last_part if last_part else None


def parse_group_action(group):
    username = group.get("username")
    link = group.get("link")
    if link and ('+' in link or 'joinchat' in link):
        hash_val = extract_invite_hash(link)
        if hash_val:
            return "invite_hash", hash_val
    if username and username.strip():
        return "username", username.strip()
    if link and ('t.me/' in link or 'telegram.me/' in link):
        hash_val = extract_invite_hash(link)
        if hash_val and not hash_val.startswith('+'):
            return "username", hash_val
    return None, None


def load_groups(filepath="groups.json"):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to read groups file '{filepath}': {e}")
        return []


def prepare_join_list(groups):
    to_join = []
    for idx, g in enumerate(groups, 1):
        action, identifier = parse_group_action(g)
        if action:
            to_join.append({
                "index": idx,
                "group_id": g.get("group_id"),
                "title": g.get("title"),
                "action": action,
                "identifier": identifier
            })
    return to_join


async def run_joiner(client, groups, delay_min=60, delay_max=180, max_joins=100, status_callback=None):
    from telethon.tl.functions.channels import JoinChannelRequest
    from telethon.tl.functions.messages import ImportChatInviteRequest
    from telethon.errors import (
        FloodWaitError, UserAlreadyParticipantError,
        InviteHashExpiredError, InviteHashInvalidError, ChannelsTooMuchError
    )

    progress.reset()
    to_join = prepare_join_list(groups)
    progress.total = len(to_join)
    progress.running = True

    joined_count = 0

    if status_callback:
        await status_callback()

    for item in to_join:
        if not progress.running:
            logger.info("Joining stopped by user.")
            progress.errors.append("Stopped by user.")
            break

        if joined_count >= max_joins:
            logger.info(f"Reached limit of {max_joins} groups.")
            break

        progress.current = item['title'] or f"Group #{item['index']}"
        progress.current_index = item['index']

        try:
            if item["action"] == "username":
                await client(JoinChannelRequest(item["identifier"]))
            elif item["action"] == "invite_hash":
                await client(ImportChatInviteRequest(item["identifier"]))

            progress.joined += 1
            joined_count += 1
            logger.info(f"[{progress.joined}/{progress.total}] Joined: {item['title']}")

            if status_callback:
                await status_callback()

            if joined_count < max_joins:
                sleep_time = random.randint(delay_min, delay_max)
                await asyncio.sleep(sleep_time)

        except UserAlreadyParticipantError:
            progress.already_in += 1
            logger.info(f"Already in: {item['title']}")
        except InviteHashExpiredError:
            progress.failed += 1
            progress.errors.append(f"Invite expired: {item['title']}")
        except InviteHashInvalidError:
            progress.failed += 1
            progress.errors.append(f"Invalid invite: {item['title']}")
        except ChannelsTooMuchError:
            progress.errors.append("Joined too many channels/groups. Stopping.")
            logger.error("Too many channels joined.")
            break
        except FloodWaitError as e:
            wait = e.seconds + 5
            logger.warning(f"Flood wait {wait}s")
            await asyncio.sleep(wait)
        except Exception as e:
            progress.failed += 1
            progress.errors.append(f"Error: {item['title']} -> {e}")
            logger.error(f"Error joining {item['title']}: {e}")

        if status_callback:
            await status_callback()

    progress.running = False
    progress.done = True

    if status_callback:
        await status_callback()

    logger.info(f"Finished. Joined: {progress.joined}, Already in: {progress.already_in}, Failed: {progress.failed}")
