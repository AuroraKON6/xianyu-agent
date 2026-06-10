import unittest
from pathlib import Path


class ConsoleUIContractTests(unittest.TestCase):
    def test_console_summarizes_agent_discussions_and_collaboration(self):
        app_js = Path("web/app.js").read_text(encoding="utf-8")

        self.assertIn('data.type === "agent_discussion"', app_js)
        self.assertIn("collaboration", app_js)
        self.assertIn("圆桌讨论", app_js)


    def test_console_can_render_collaboration_history_feed(self):
        console_html = Path("web/console.html").read_text(encoding="utf-8")
        app_js = Path("web/app.js").read_text(encoding="utf-8")

        self.assertIn('id="collaboration-feed"', console_html)
        self.assertIn('id="refresh-collaboration"', console_html)
        self.assertIn("/agents/messages", app_js)
        self.assertIn("/agents/discussions", app_js)
        self.assertIn("renderCollaborationFeed", app_js)

    def test_console_declares_empty_favicon_to_keep_browser_console_clean(self):
        console_html = Path("web/console.html").read_text(encoding="utf-8")

        self.assertIn('rel="icon"', console_html)
        self.assertIn('href="data:,"', console_html)

    def test_console_can_render_work_order_feed(self):
        console_html = Path("web/console.html").read_text(encoding="utf-8")
        app_js = Path("web/app.js").read_text(encoding="utf-8")

        self.assertIn('id="work-order-feed"', console_html)
        self.assertIn('id="refresh-work-orders"', console_html)
        self.assertIn("/work-orders", app_js)
        self.assertIn("renderWorkOrderFeed", app_js)

    def test_console_has_operations_dashboard_filters_and_counts(self):
        console_html = Path("web/console.html").read_text(encoding="utf-8")
        app_js = Path("web/app.js").read_text(encoding="utf-8")

        self.assertIn('id="work-order-filter-status"', console_html)
        self.assertIn('id="work-order-filter-kind"', console_html)
        self.assertIn('id="work-order-filter-owner"', console_html)
        self.assertIn('id="work-order-filter-risk"', console_html)
        self.assertIn('id="approval-count-badges"', console_html)

        self.assertIn("applyWorkOrderFilters", app_js)
        self.assertIn("renderApprovalCounts", app_js)
        self.assertIn("renderWorkOrderTimeline", app_js)
        self.assertIn("work-order-timeline", app_js)

    def test_console_has_execution_job_controls_with_filters_and_confirmations(self):
        console_html = Path("web/console.html").read_text(encoding="utf-8")
        app_js = Path("web/app.js").read_text(encoding="utf-8")

        self.assertIn('id="execution-job-feed"', console_html)
        self.assertIn('id="execution-job-filter-status"', console_html)
        self.assertIn('id="run-next-jobs"', console_html)
        self.assertIn('id="run-pending-jobs"', console_html)

        self.assertIn("refreshExecutionJobs", app_js)
        self.assertIn("renderExecutionJobFeed", app_js)
        self.assertIn("applyExecutionJobFilters", app_js)
        self.assertIn("runPendingJobs", app_js)
        self.assertIn('confirm("确认执行下一批已排队任务？")', app_js)
        self.assertIn('confirm("确认执行所有当前可执行任务？")', app_js)


if __name__ == "__main__":
    unittest.main()
