from datetime import date, datetime, timezone
import unittest
from her_agent.public_dates import bind_local_date
from her_agent.web_lookup import PublicQuery
from test_lookup import CLOCK


class PublicDateTests(unittest.TestCase):
    def bind(self, when, text, **kwargs):
        return bind_local_date(when,text,now=CLOCK.now(),**kwargs)

    def test_explicit_current_turn_dates_bind_only_locally(self):
        for when,text in [('2026-11-03','11月3日下午能去吗？'),('2026-10-31','周六下午能去Fisher吗？'),
                          ('明天','我想明天去看看。'),('2026-11-03','2026年11月3号有什么展？')]:
            day,reason=self.bind(when,text)
            self.assertEqual(reason,'current_user')
            with self.assertRaisesRegex(ValueError,'local only'):
                PublicQuery('usc_exposition','architecture',day=day)

    def test_persona_date_old_turn_mismatch_or_ambiguity_does_not_bind(self):
        for when,text in [('1990-02-03','找个小展'),('2026-11-03','2026-11-04去看看'),
                          ('2026-11-03','11月3日还是11月4日呢？'),('2026-11-03','不要用历史里的时间'),
                          ('2026-11-03','编号12026-11-0312')]:
            self.assertEqual(self.bind(when,text),(None,'unbound_or_ambiguous'))
        for when,text in [('2026-02-30','2026-02-30去'),('1990-02-03','2026年99月99号')]:
            self.assertEqual(self.bind(when,text),(None,'unparsed'))

    def test_week_correction_needs_active_previously_bound_query(self):
        text='我是说下周，不是这周。'
        self.assertEqual(self.bind('2026-11-07',text,previous_date=date(2026,10,31),revising=True),
                         (date(2026,11,7),'current_user_week_correction'))
        for kwargs in ({},{'previous_date':date(2026,10,31)},{'revising':True}):
            self.assertEqual(self.bind('2026-11-07',text,**kwargs),(None,'unbound_or_ambiguous'))

    def test_missing_date_does_not_infer_from_private_material(self):
        self.assertEqual(self.bind(None,'附近小展'),(None,'not_requested'))

    def test_year_without_number_uses_los_angeles_at_utc_new_year(self):
        self.assertEqual(bind_local_date('2026-12-31','12月31日去看看',
            now=datetime(2027,1,1,1,tzinfo=timezone.utc)),(date(2026,12,31),'current_user'))
