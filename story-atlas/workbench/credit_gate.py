"""Shared per-session admission control, not an HTTP retry layer.

Keep the requested model/output contract. Below $5, serialize model POSTs and
space them within the key's current RPM. A rejected/unknown request latches the
gate closed; already queued workers cannot replay it or multiply the failure.
"""
import json
import math
import re
import threading
import time
from urllib.request import Request,build_opener

from bazaarlink import NoRedirect,request_json


class AdmissionPaused(ValueError):
    pass


def spacing(rpm):
    # Leave room for read-only key/catalog checks and other same-key traffic.
    return 60/max(1,rpm-4)


def live_status(key,prices=None):
    def get(path):
        request=Request('https://api.bazaarlink.ai/v1/'+path,headers={'Authorization':'Bearer '+key,'X-Title':'Story Atlas'})
        with build_opener(NoRedirect).open(request,timeout=25) as response:
            raw=response.read(4*1024*1024+1)
        if len(raw)>4*1024*1024:raise ValueError('Admission status response too large')
        result=json.loads(raw)
        if not isinstance(result,dict) or result.get('error'):raise ValueError('Admission status unavailable')
        return result
    data=get('key')['data'];balance=data.get('limit_remaining');rpm=(data.get('rate_limit') or {}).get('requests')
    if type(balance) not in (int,float) or not math.isfinite(balance) or balance<0 or type(rpm) is not int or rpm<1:
        raise ValueError('Admission balance/rate fields unavailable')
    if prices is None:
        prices={}
        for model in get('models').get('data',[]):
            price=model.get('pricing') or {}
            try:prompt=float(price['prompt']);completion=float(price['completion'])
            except (ValueError,TypeError,KeyError):continue
            if not all(math.isfinite(x) and x>=0 for x in (prompt,completion)):continue
            entry=dict(prompt=prompt,completion=completion)
            for name in [model.get('id')]+(model.get('aliases') or []):
                if isinstance(name,str):prices[name]=entry
    return dict(balance=float(balance),rpm=rpm,low_balance=bool(data.get('is_free_tier')) or balance<5,prices=prices)


class CreditGate:
    def __init__(self,key,max_concurrency,status_loader=live_status,transport=request_json,clock=time.monotonic):
        self.key=key;self.maximum=max_concurrency;self.loader=status_loader;self.transport=transport;self.clock=clock
        self.condition=threading.Condition();self.active=0;self.waiting=0;self.reserved=0.;self.next_start=0.;self.checked=None;self.info=None;self.halted=None

    def _refresh(self):
        now=self.clock()
        if self.info is not None and self.checked is not None and now-self.checked<30:return
        try:self.info=self.loader(self.key,self.info.get('prices') if self.info else None)
        except Exception:
            # No credentials or raw third-party body in the error/audit.
            self.halted='帳務預檢暫停：無法確認目前金鑰餘額／速率，未送出模型請求。'
            raise AdmissionPaused(self.halted) from None
        self.checked=now

    def estimate_reservation(self,request):
        cap=request.get('max_tokens')
        price=self.info['prices'].get(request.get('model')) or self.info['prices'].get(str(request.get('model','')).split('/')[-1])
        if type(cap) is not int or cap<1 or price is None:return None
        # UTF-8 byte count is a conservative input proxy, NOT exact tokenizer or
        # the vendor's private reservation formula. No cache discount assumed.
        source_bytes=len(json.dumps(request.get('messages',[]),ensure_ascii=False).encode('utf-8'))
        return source_bytes*price['prompt']+cap*price['completion']

    def __call__(self,url,request,key):
        if key!=self.key:raise AdmissionPaused('帳務預檢暫停：金鑰來源不一致，未送出。')
        reserve=0.;response=None
        with self.condition:
            self.waiting+=1
            try:
                while True:
                    if self.halted:raise AdmissionPaused(self.halted)
                    self._refresh();limit=1 if self.info['low_balance'] else self.maximum
                    if self.active>=limit:self.condition.wait();continue
                    estimate=self.estimate_reservation(request)
                    available=self.info['balance']-self.reserved
                    if available<=0 or estimate is not None and estimate>available:
                        if self.active:self.condition.wait();continue
                        self.halted=f'帳務預檢暫停：目前可用餘額US${self.info["balance"]:.6f}不足以保留本次請求估算US${estimate or 0:.6f}；未送出。輸出上限未降低，這不代表帳戶餘額歸零。'
                        raise AdmissionPaused(self.halted)
                    delay=self.next_start-self.clock()
                    if delay>0:self.condition.wait(timeout=delay);continue
                    reserve=estimate or 0.;self.reserved+=reserve;self.active+=1;self.next_start=self.clock()+spacing(self.info['rpm']);break
            finally:self.waiting-=1
        try:
            response=self.transport(url,request,key)
            return response
        except Exception as error:
            text=str(error)
            if re.search(r'(?:HTTP |回應內錯誤狀態 )(?:401|402|403|429|5\d\d)',text) or any(word in text for word in ('連線未完成','upstream_error','upstream_timeout')):
                with self.condition:
                    self.halted='帳務預檢暫停：上一個請求被拒絕或結果未確認，後續排隊請求不再送出；請檢查原始失敗紀錄。'
            raise
        finally:
            with self.condition:
                if isinstance(response,dict):
                    usage=response.get('usage') or {};cost=usage.get('cost',usage.get('bz_cost'))
                    if type(cost) in (int,float) and math.isfinite(cost) and cost>=0:self.info['balance']=max(0.,self.info['balance']-cost)
                    else:self.checked=None
                self.reserved=max(0.,self.reserved-reserve);self.active-=1;self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            return dict(active_requests=self.active,queued_requests=self.waiting,effective_concurrency=1 if self.info and self.info['low_balance'] else self.maximum,rpm=self.info.get('rpm') if self.info else None,balance=self.info.get('balance') if self.info else None,paused=bool(self.halted),estimate_only=True)
