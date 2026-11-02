import unittest
from estimate_remaining import fit_prediction,forecast_tokens
from estimate_remaining import simulate
import test_translation as fixtures
import test_branch_scheduler as branch_fixtures
from branch_scheduler import BranchScheduler


class EstimateTests(unittest.TestCase):
    setUp=fixtures.TranslationTests.setUp
    tearDown=fixtures.TranslationTests.tearDown
    workspace=fixtures.TranslationTests.workspace
    def test_fixed_prompt_overhead_and_source_tokens_are_not_confused(self):
        self.assertEqual(fit_prediction([(100,1200),(200,1400),(300,1600)],10,1000),12000)
        self.assertEqual(fit_prediction([(100,100),(200,200)],10,1000),1000)

    def test_reasoning_is_subset_of_completion_not_extra_charge(self):
        values={1:{name:dict(requests=1,prompt=100,completion=50,cached=20,reasoning=30,cost=.01) for name in ('flash_initial','flash_corrections','pro')}}
        sample=dict(characters={1:100},values=values)
        prices={name:dict(prompt=.0001,completion=.0002,cached=.00001) for name in ('deepseek-v4.1-flash','deepseek-v4-pro-0813')}
        forecast=forecast_tokens(sample,10,1000,prices)
        self.assertEqual(forecast['total_tokens'],4500)
        self.assertAlmostEqual(forecast['estimated_cost_usd'],.3)

    def test_all_flash_forecast_uses_flash_price_factor_not_historical_pro_fee(self):
        values={1:{name:dict(requests=1,prompt=100,completion=50,cached=20,reasoning=0,cost=.01) for name in ('flash_initial','flash_corrections','pro')}}
        sample=dict(characters={1:100},values=values,model_totals={'deepseek-v4.1-flash':dict(prompt=100,completion=50,cached=20,cost=.01)},category_models={'pro':['deepseek-v4-pro-0813']})
        prices={'deepseek-v4.1-flash':dict(prompt=.0001,completion=.0002,cached=.00001),'deepseek-v4-pro-0813':dict(prompt=.01,completion=.02,cached=.001)}
        result=forecast_tokens(sample,10,1000,prices)
        self.assertEqual(result['pro']['model'],'deepseek-v4.1-flash');self.assertTrue(result['pro']['review_model_switch_caveat'])
        self.assertAlmostEqual(result['pro']['predicted']['estimated_cost_usd'],.1)

    def test_graph_dry_run_counts_branch_boundaries_not_total_chars_divided_by_budget(self):
        service=self.workspace(['a'*6000,'b'*6000,'c'*6000],[(0,1),(0,2)])
        run=service.new_run('same')['id'];scheduler=BranchScheduler(service);scheduler.start(run,1,1,'synthetic-only',True,branch_fixtures.BranchTests.echo,False)
        report,sample,groups=simulate(service,self.root)
        self.assertEqual(report['remaining_batches'],2);self.assertEqual(report['remaining_characters'],12000)
        self.assertTrue(report['independent_inventory_matches']);self.assertEqual(report['remaining_units'],2)

    def test_already_allocated_inflight_batch_is_counted_once_without_request(self):
        service=self.workspace(['a'*6000,'b'*6000,'c'*6000],[(0,1),(0,2)])
        run=service.new_run('same')['id'];scheduler=BranchScheduler(service)
        with service.db() as db:
            scheduler._initialize(db,run);db.execute("INSERT INTO branch_sessions(id,run_id,requested,concurrency,status)VALUES('pending-copy',?,3,1,'running')",(run,));job=db.execute('SELECT * FROM branch_jobs').fetchone();scheduler._plan(db,job,'pending-copy')
        report,sample,groups=simulate(service,self.root)
        self.assertEqual(report['remaining_batches'],3);self.assertEqual(report['remaining_units'],3)
        self.assertTrue(report['independent_inventory_matches'])


if __name__=='__main__':unittest.main()
