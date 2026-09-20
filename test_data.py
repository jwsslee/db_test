import unittest
from unittest.mock import patch
import math
from data import KIS, APIError, number, krx_market

class DataTests(unittest.TestCase):
    def test_missing_numbers_remain_missing(self):
        self.assertTrue(math.isnan(number('')))
        self.assertEqual(number('1,200'),1200)
    def test_balance_pagination(self):
        api=KIS('test','test')
        calls=[]
        def get(endpoint,tr,params,continuation=''):
            calls.append((dict(params),continuation))
            row={'pdno':str(len(calls)),'hldg_qty':'2','evlu_amt':'200'}
            return ({'output1':[row],'output2':[{'tot_evlu_amt':'400'}],
                     'ctx_area_fk100':'a','ctx_area_nk100':'b'}, {'tr_cont':'F' if len(calls)==1 else 'D'})
        api.get=get
        frame,summary,_=api.balance('12345678','01')
        self.assertEqual(len(frame),2)
        self.assertEqual(calls[1][1],'N')
        self.assertEqual(calls[1][0]['CTX_AREA_NK100'],'b')
    def test_bad_cursor_does_not_show_partial_balance(self):
        api=KIS('test','test')
        api.get=lambda *args:({'output1':[],'output2':[]}, {'tr_cont':'F'})
        with self.assertRaises(APIError): api.balance('12345678','01')
    def test_krx_empty_date(self):
        with patch('data.http',return_value=({'OutBlock_1':[]},{})):
            self.assertTrue(krx_market('test','KOSPI','20260101').empty)
    def test_history_paginates_dates(self):
        api=KIS('test','test'); calls=[]
        def get(endpoint,tr,params):
            calls.append(dict(params))
            if len(calls)>1:return {'output2':[]},{}
            return {'output2':[{'stck_bsop_date':'20260901','stck_oprc':'100','stck_hgpr':'110',
                'stck_lwpr':'90','stck_clpr':'105','acml_vol':'1000'}]},{}
        api.get=get
        frame=api.history('005930')
        self.assertEqual(len(frame),1)
        self.assertEqual(calls[1]['FID_INPUT_DATE_2'],'20260831')

if __name__=='__main__':unittest.main()
