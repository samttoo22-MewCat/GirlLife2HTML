import sqlite3
import unittest

import test_translation as fixtures
from branch_scheduler import BranchScheduler
import test_branch_scheduler as branch_fixtures
from handover_guard import install,remove,MESSAGE


class HandoverTests(unittest.TestCase):
    setUp=fixtures.TranslationTests.setUp
    tearDown=fixtures.TranslationTests.tearDown
    workspace=fixtures.TranslationTests.workspace

    def test_inflight_finishes_next_prepare_blocked_then_same_progress_branches(self):
        service=self.workspace(['r'*6000,'l'*6000,'t'*6000],[(0,1),(0,2)])
        run=service.new_run('same')['id'];bid=service.prepare(run)['id']
        with service.db() as db:install(db,run,1)
        batch=service.batch(bid)
        service.generate(bid,batch['revision'],service.settings()['revision'],'synthetic-only',True,branch_fixtures.BranchTests.echo)
        batch=service.batch(bid);service.approve(bid,batch['revision'],False,automatic=True)
        with self.assertRaisesRegex(sqlite3.IntegrityError,MESSAGE):service.prepare(run)
        with service.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM batches').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT status FROM batches').fetchone()[0],'approved')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM model_attempts').fetchone()[0],1)
            remove(db)
        scheduler=BranchScheduler(service);scheduler.start(run,2,30,'synthetic-only',True,branch_fixtures.BranchTests.echo,False)
        self.assertEqual(scheduler.status(run)['completed'],2)
        with service.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM translations').fetchone()[0],3)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM model_attempts').fetchone()[0],3)

    def test_guard_refuses_duplicate_and_invalid_boundary(self):
        service=self.workspace(['root'],[]);run=service.new_run('same')['id']
        with service.db() as db:
            with self.assertRaises(ValueError):install(db,"unsafe'",1)
            install(db,run,1)
            with self.assertRaises(ValueError):install(db,run,1)
            remove(db)


if __name__=='__main__':unittest.main()
