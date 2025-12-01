"""JSON-based storage for conversations.

This module stores conversations as individual JSON files under `DATA_DIR`.
It includes defensive handling for malformed or non-conversation files
that may be present in the directory (e.g. `config.json`).
"""

import json
import os
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path
from .config import DATA_DIR


def ensure_data_dir():
    """Ensure the data directory exists."""
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)


def get_conversation_path(conversation_id: str) -> str:
    """Get the file path for a conversation."""
    return os.path.join(DATA_DIR, f"{conversation_id}.json")


def create_conversation(conversation_id: str) -> Dict[str, Any]:
    """Create and persist a new conversation."""
    ensure_data_dir()
    conversation = {
        "id": conversation_id,
        "created_at": datetime.utcnow().isoformat(),
        "title": "New Conversation",
        "messages": []
    }
    path = get_conversation_path(conversation_id)
    with open(path, 'w') as f:
        json.dump(conversation, f, indent=2)
    return conversation


def get_conversation(conversation_id: str) -> Optional[Dict[str, Any]]:
    """Load a conversation, or return None if not found or invalid.

    Internal files such as `config.json` are ignored.
    """
    path = get_conversation_path(conversation_id)
    if os.path.basename(path) == 'config.json':
        return None
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r') as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            else:
                return None
    except Exception:
        return None


def save_conversation(conversation: Dict[str, Any]):
    """Persist a conversation dictionary to disk."""
    ensure_data_dir()
    path = get_conversation_path(conversation['id'])
    with open(path, 'w') as f:
        json.dump(conversation, f, indent=2)


def list_conversations() -> List[Dict[str, Any]]:
    """Return metadata for all stored conversations.

    Skips `config.json` and any non-JSON or malformed files. Attempts to
    recover simple list-formatted files by treating the list as messages.
    """
    ensure_data_dir()
    conversations: List[Dict[str, Any]] = []
    for filename in os.listdir(DATA_DIR):
        if not filename.endswith('.json'):
            continue
        if filename == 'config.json':
            continue
        path = os.path.join(DATA_DIR, filename)
        try:
            with open(path, 'r') as f:
                data = json.load(f)
        except Exception as e:
            print(f"storage.list_conversations: skipping invalid file {path}: {e}")
            continue

        # If root is a list, assume it's a list of messages and recover
        if isinstance(data, list):
            convo_id = os.path.splitext(filename)[0]
            # Count completed user messages + assistant messages with completed stage3 (exclude summaries)
            msg_count = sum(
                1 for m in data if isinstance(m, dict) and (
                    (m.get('role') == 'user' and m.get('status', 'complete') == 'complete') or
                    (m.get('role') == 'assistant' and isinstance(m.get('stage3'), dict) and
                     m.get('stage3', {}).get('response') and
                     not m.get('stage3', {}).get('metadata', {}).get('summarized_count'))
                )
            )
            conversations.append({
                'id': convo_id,
                'created_at': datetime.utcnow().isoformat(),
                'title': 'Recovered Conversation',
                'message_count': msg_count
            })
            continue

        if not isinstance(data, dict):
            print(f"storage.list_conversations: unexpected JSON root in {path}: {type(data)}")
            continue

        convo_id = data.get('id') or os.path.splitext(filename)[0]
        created_at = data.get('created_at') or datetime.utcnow().isoformat()
        title = data.get('title', 'New Conversation')
        messages = data.get('messages') if isinstance(data.get('messages'), list) else []
        # Count completed user messages + assistant messages with completed stage3 (exclude summaries)
        # A user message is "complete" if status == 'complete' OR if there's no status field (legacy)
        msg_count = sum(
            1 for m in messages if (
                (m.get('role') == 'user' and m.get('status', 'complete') == 'complete') or
                (m.get('role') == 'assistant' and isinstance(m.get('stage3'), dict) and
                 m.get('stage3', {}).get('response') and
                 not m.get('stage3', {}).get('metadata', {}).get('summarized_count'))
            )
        )

        conversations.append({
            'id': convo_id,
            'created_at': created_at,
            'title': title,
            'message_count': msg_count
        })

    conversations.sort(key=lambda x: x['created_at'], reverse=True)
    return conversations


def add_user_message(conversation_id: str, content: str):
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")
    conversation.setdefault('messages', []).append({
        'role': 'user',
        'content': content,
        'status': 'pending',
        'created_at': datetime.utcnow().isoformat()
    })
    save_conversation(conversation)


def mark_last_user_message_status(conversation_id: str, status: str):
    """Mark the most recent user message's status."""
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")
    msgs = conversation.setdefault('messages', [])
    for m in reversed(msgs):
        if m.get('role') == 'user':
            m['status'] = status
            m['status_updated_at'] = datetime.utcnow().isoformat()
            save_conversation(conversation)
            return True
    return False


def remove_pending_user_messages(conversation_id: str, keep_last: bool = True) -> int:
    """Remove user messages that are pending or failed. If `keep_last` is True,
    preserves the most recent pending/failed user message (so UI can offer retry).

    Returns the number of messages removed.
    """
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")
    msgs = conversation.setdefault('messages', [])
    # Find indices of pending or failed user messages
    removable_indices = [
        i for i, m in enumerate(msgs)
        if m.get('role') == 'user' and m.get('status') in ('pending', 'failed')
    ]
    if not removable_indices:
        return 0
    # If keep_last, drop the last index from removal
    if keep_last and len(removable_indices) > 0:
        removable_indices = removable_indices[:-1]
    # Remove by index (from end to start to avoid shifting)
    removed = 0
    for idx in reversed(removable_indices):
        try:
            msgs.pop(idx)
            removed += 1
        except Exception:
            continue
    if removed:
        save_conversation(conversation)
    return removed


def get_last_user_message(conversation_id: str) -> dict | None:
    conversation = get_conversation(conversation_id)
    if conversation is None:
        return None
    msgs = conversation.get('messages', [])
    for m in reversed(msgs):
        if m.get('role') == 'user':
            return m
    return None


def add_assistant_message(
    conversation_id: str,
    stage1: List[Dict[str, Any]],
    stage2: List[Dict[str, Any]],
    stage3: Dict[str, Any]
):
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")
    conversation.setdefault('messages', []).append({
        'role': 'assistant',
        'stage1': stage1,
        'stage2': stage2,
        'stage3': stage3
    })
    save_conversation(conversation)


def update_conversation_title(conversation_id: str, title: str):
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")
    conversation['title'] = title
    save_conversation(conversation)


def delete_conversation(conversation_id: str):
    """Delete a conversation file."""
    path = get_conversation_path(conversation_id)
    if os.path.exists(path):
        os.remove(path)
