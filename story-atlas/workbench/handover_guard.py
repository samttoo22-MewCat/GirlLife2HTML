"""One-off safe boundary for handing an existing sequential worker to branches.

The old worker may already have loaded code without a cooperative stop flag.
Block ONLY its next batch INSERT; allow its in-flight translation/review/approval
to finish unchanged. Remove the guard only after its locks are released.
"""
import re

NAME='story_branch_handover_guard'
MESSAGE='分支切換：目前批次已完成，停止舊單線排程，不啟動下一批。'


def install(db,run_id,last_ordinal):
    if not re.fullmatch(r'[0-9a-f]{32}',run_id) or type(last_ordinal) is not int or last_ordinal<1:
        raise ValueError('Invalid handover boundary')
    if db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(NAME,)).fetchone():
        raise ValueError('A handover is already installed; do not start another')
    db.execute(f"""CREATE TRIGGER {NAME} BEFORE INSERT ON batches
WHEN NEW.run_id='{run_id}' AND NEW.ordinal>{last_ordinal}
BEGIN SELECT RAISE(ABORT,'{MESSAGE}'); END""")


def remove(db):
    db.execute('DROP TRIGGER '+NAME)
