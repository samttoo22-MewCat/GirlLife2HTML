"""Read-only, credential-free projection of the active isolated paid test round."""
from datetime import datetime, timezone
import fcntl
import json
import math
from pathlib import Path
import re
from workspace_location import workspace_directory
from bazaarlink import review_label


def test_round_status(data):
    root=(Path(data)/'test-rounds').resolve();index=root/'active-round.json'
    if not index.exists():return dict(state='idle',completed=0,count=0,stage='尚無隔離實測')
    name=json.loads(index.read_text(encoding='utf-8')).get('directory')
    if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',name):raise ValueError('實測目錄索引不合法')
    folder=(root/name).resolve()
    if not folder.is_relative_to(root) or folder==root:raise ValueError('實測目錄超出允許範圍')
    statefile=folder/'round.json'
    if statefile.stat().st_size>2*1024*1024:raise ValueError('實測狀態檔超過安全大小')
    state=json.loads(statefile.read_text(encoding='utf-8'))
    count=state.get('count');completed=state.get('completed',[]);calls=state.get('calls',[])
    if type(count) is not int or count not in (10,50) or not isinstance(completed,list) or len(completed)>count or not isinstance(calls,list):raise ValueError('實測狀態格式不合法')
    running=False
    with (folder/'runner.lock').open('r') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_SH|fcntl.LOCK_NB)
        except BlockingIOError:running=True
    status=state.get('status','created')
    if status in ('created','running') and not running:status='unconfirmed'
    last=calls[-1] if calls else {}
    labels={'translation':'Flash 翻譯','translation_correction':'正文局部修正','glossary_proposal_correction':'詞彙提案修正','glossary_review':review_label(last.get('model'))}
    stage=labels.get(last.get('task'),'準備批次')
    if last.get('state')=='received':stage='驗證／保存結果'
    if status=='complete':stage='本輪已完成'
    def number(value):return value if type(value) in (int,float) and math.isfinite(value) and value>=0 else 0
    cost=sum(number((c.get('usage') or {}).get('cost')) for c in calls if isinstance(c,dict) and isinstance(c.get('usage',{}),dict))
    error=state.get('error')
    if error is not None:error=re.sub(r'(?i)\b(?:sk-[a-z0-9_-]+|blk-[a-z0-9_-]+|Bearer\s+\S+)','[REDACTED]',str(error))[:1800]
    return dict(state=status,running=running and status in ('created','running'),completed=len(completed),count=count,current_batch=state.get('current_batch'),current_ordinal=state.get('current_ordinal'),stage=stage,calls=len(calls),reported_cost=cost,error=error,updated=datetime.fromtimestamp(statefile.stat().st_mtime,timezone.utc).isoformat(),isolated=workspace_directory(data)!=folder)
