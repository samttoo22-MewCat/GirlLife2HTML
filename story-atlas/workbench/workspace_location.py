"""Resolve the single authoritative workspace; credentials remain in root data."""
import json
from pathlib import Path
import re


def workspace_directory(data):
    data=Path(data).resolve();config=data/'translation/active-workspace.json'
    if not config.exists():return data
    value=json.loads(config.read_text(encoding='utf-8'));name=value.get('directory')
    if value.get('promoted') is not True or not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',name):
        raise ValueError('正式工作區設定不合法')
    root=(data/'test-rounds').resolve();folder=(root/name).resolve()
    if folder==root or not folder.is_relative_to(root) or not (folder/'translation/workspace.sqlite3').is_file():
        raise ValueError('正式工作區資料不存在或超出允許範圍')
    return folder
