from copy import deepcopy
import threading
import unittest

from credit_gate import CreditGate,AdmissionPaused,spacing
from bazaarlink import ProviderError


class CreditGateTests(unittest.TestCase):
    def info(self,balance=.694919,low=True,rpm=60000):
        return dict(balance=balance,low_balance=low,rpm=rpm,prices={'deepseek-v4.1-flash':dict(prompt=.00000021225,completion=.00000084825)})

    def request(self):
        return dict(model='deepseek/deepseek-v4.1-flash',max_tokens=384000,messages=[dict(role='user',content='source')])

    def test_low_credit_serializes_five_workers_without_changing_output_contract(self):
        release=threading.Event();entered=threading.Event();lock=threading.Lock();active=0;peak=0;calls=[];errors=[]
        def post(url,request,key):
            nonlocal active,peak
            with lock:active+=1;peak=max(peak,active);calls.append(deepcopy(request));entered.set()
            try:
                self.assertTrue(release.wait(5));return dict(usage=dict(cost=.001))
            finally:
                with lock:active-=1
        gate=CreditGate('synthetic',5,status_loader=lambda *args:self.info(),transport=post)
        request=self.request();original=deepcopy(request)
        def work():
            try:gate('fixture',request,'synthetic')
            except Exception as error:errors.append(error)
        threads=[threading.Thread(target=work) for _ in range(5)]
        for thread in threads:thread.start()
        self.assertTrue(entered.wait(5));self.assertEqual(gate.snapshot()['effective_concurrency'],1);release.set()
        for thread in threads:thread.join(6);self.assertFalse(thread.is_alive())
        self.assertEqual(errors,[]);self.assertEqual(peak,1);self.assertEqual(len(calls),5);self.assertEqual(request,original)
        self.assertTrue(all(call['max_tokens']==384000 for call in calls))

    def test_sufficient_credit_retains_parallel_admission(self):
        barrier=threading.Barrier(2,timeout=5);errors=[]
        def post(*args):barrier.wait();return dict(usage=dict(cost=.001))
        gate=CreditGate('synthetic',5,status_loader=lambda *args:self.info(balance=10,low=False),transport=post)
        def work():
            try:gate('fixture',self.request(),'synthetic')
            except Exception as error:errors.append(error)
        threads=[threading.Thread(target=work) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join(6);self.assertFalse(t.is_alive())
        self.assertEqual(errors,[]);self.assertEqual(gate.snapshot()['effective_concurrency'],5)

    def test_insufficient_reservation_stops_before_post_and_does_not_claim_zero_balance(self):
        calls=[];gate=CreditGate('synthetic',5,status_loader=lambda *args:self.info(balance=.1),transport=lambda *args:calls.append(1))
        with self.assertRaisesRegex(AdmissionPaused,'不代表帳戶餘額歸零'):gate('fixture',self.request(),'synthetic')
        with self.assertRaises(AdmissionPaused):gate('fixture',self.request(),'synthetic')
        self.assertEqual(calls,[]);self.assertTrue(gate.snapshot()['paused'])

    def test_402_latches_queue_closed_with_exactly_one_post_not_five_retries(self):
        entered=threading.Event();release=threading.Event();calls=[];errors=[]
        def post(*args):
            calls.append(1);entered.set();release.wait(5);raise ProviderError('BazaarLink HTTP 402 insufficient credits',dict(error=dict(code='402')))
        gate=CreditGate('synthetic',5,status_loader=lambda *args:self.info(),transport=post)
        def work():
            try:gate('fixture',self.request(),'synthetic')
            except ValueError as error:errors.append(str(error))
        threads=[threading.Thread(target=work) for _ in range(5)]
        for t in threads:t.start()
        self.assertTrue(entered.wait(5));release.set()
        for t in threads:t.join(6);self.assertFalse(t.is_alive())
        self.assertEqual(len(calls),1);self.assertEqual(len(errors),5);self.assertTrue(gate.snapshot()['paused'])

    def test_metadata_failure_never_posts_or_leaks_credentials(self):
        def fail(*args):raise ValueError('secret synthetic-credential')
        gate=CreditGate('synthetic-credential',5,status_loader=fail,transport=lambda *args:self.fail('No POST'))
        with self.assertRaises(AdmissionPaused) as error:gate('fixture',self.request(),'synthetic-credential')
        self.assertNotIn('synthetic-credential',str(error.exception))

    def test_unknown_review_cap_is_not_fabricated_and_twenty_rpm_gets_spacing(self):
        gate=CreditGate('synthetic',5,status_loader=lambda *args:self.info(),transport=lambda *args:dict(usage=dict(cost=.001)))
        request=self.request();request.pop('max_tokens');gate('fixture',request,'synthetic')
        self.assertIsNone(gate.estimate_reservation(request));self.assertGreaterEqual(spacing(20),3.75)
        self.assertEqual(gate.snapshot()['effective_concurrency'],1)


if __name__=='__main__':unittest.main()
