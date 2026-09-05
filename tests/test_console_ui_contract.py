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


    def test_console_has_account_profile_panel(self):
        console_html = Path("web/console.html").read_text(encoding="utf-8")
        app_js = Path("web/app.js").read_text(encoding="utf-8")
        style_css = Path("web/style.css").read_text(encoding="utf-8")

        self.assertIn('id="account-profile-panel"', console_html)
        self.assertIn('id="refresh-account-profiles"', console_html)
        self.assertIn('id="account-profile-list"', console_html)
        self.assertIn("/accounts/profiles", app_js)
        self.assertIn("loadAccountProfiles", app_js)
        self.assertIn("renderAccountProfile", app_js)
        self.assertIn("account-profile-card", app_js)
        self.assertIn("refreshAccountProfiles", app_js)
        self.assertIn(".account-profile-list", style_css)

    def test_console_has_first_account_bootstrap_form(self):
        console_html = Path("web/console.html").read_text(encoding="utf-8")
        app_js = Path("web/app.js").read_text(encoding="utf-8")
        style_css = Path("web/style.css").read_text(encoding="utf-8")

        self.assertIn('id="account-bootstrap-form"', console_html)
        self.assertIn('name="cookie_id"', console_html)
        self.assertIn('name="cookie_value"', console_html)
        self.assertIn("submitAccountBootstrapForm", app_js)
        self.assertIn('api("/accounts"', app_js)
        self.assertIn('data.type === "account_bootstrap"', app_js)
        self.assertIn("refreshOPCDashboard", app_js)
        self.assertIn(".account-bootstrap-form", style_css)

    def test_console_account_profile_has_prompt_shortcuts(self):
        app_js = Path("web/app.js").read_text(encoding="utf-8")
        style_css = Path("web/style.css").read_text(encoding="utf-8")

        self.assertIn("checkAccountPromptProfile", app_js)
        self.assertIn("bootstrapAccountPromptProfile", app_js)
        self.assertIn("/prompt-profile", app_js)
        self.assertIn("/prompt-profile/bootstrap", app_js)
        self.assertIn('data.type === "account_prompt_profile"', app_js)
        self.assertIn('data.type === "account_prompt_bootstrap"', app_js)
        self.assertIn("missing_required_keys", app_js)
        self.assertIn("account-profile-tools", app_js)
        self.assertIn(".account-profile-tools", style_css)

    def test_console_has_two_stage_browser_account_import(self):
        console_html = Path("web/console.html").read_text(encoding="utf-8")
        app_js = Path("web/app.js").read_text(encoding="utf-8")
        style_css = Path("web/style.css").read_text(encoding="utf-8")

        self.assertIn('id="open-xianyu-login-browser"', console_html)
        self.assertIn('id="preview-browser-account"', console_html)
        self.assertIn('id="browser-account-import-preview"', console_html)
        self.assertIn('id="manual-account-bootstrap"', console_html)
        self.assertIn("/accounts/browser-import/preview", app_js)
        self.assertIn("/accounts/browser-import/commit", app_js)
        self.assertIn("/accounts/browser-import/open", app_js)
        self.assertIn("previewBrowserAccountImport", app_js)
        self.assertIn("commitBrowserAccountImport", app_js)
        self.assertIn("browserAccountImportPreview", app_js)
        self.assertIn("browserAccountImportErrorMessage", app_js)
        self.assertIn("请先点击“打开登录浏览器”", app_js)
        self.assertIn(".browser-account-preview-grid", style_css)

    def test_console_account_profile_has_conversation_summary_shortcut(self):
        app_js = Path("web/app.js").read_text(encoding="utf-8")

        self.assertIn("checkAccountConversationSummary", app_js)
        self.assertIn("/conversation-summary", app_js)
        self.assertIn('data.type === "account_conversation_summary"', app_js)
        self.assertIn("next_operator_action", app_js)
        self.assertIn("recent_messages", app_js)

    def test_console_renders_opc_next_actions_and_readiness(self):
        app_js = Path("web/app.js").read_text(encoding="utf-8")
        style_css = Path("web/style.css").read_text(encoding="utf-8")

        self.assertIn("renderOPCNextActions", app_js)
        self.assertIn("renderOPCReadiness", app_js)
        self.assertIn("renderOPCMessageImportScheduler", app_js)
        self.assertIn("renderOPCOrderRuntime", app_js)
        self.assertIn("renderOPCExecutionEvidence", app_js)
        self.assertIn("renderFirstRunReadiness", app_js)
        self.assertIn("/dashboard/first-run-readiness", app_js)
        self.assertIn("first-run-gates", app_js)
        self.assertIn("next_actions", app_js)
        self.assertIn("account_readiness", app_js)
        self.assertIn("adapter_health", app_js)
        self.assertIn("message_import_scheduler", app_js)
        self.assertIn("order_runtime", app_js)
        self.assertIn("execution_evidence", app_js)
        self.assertIn("evidence_manifest", app_js)
        self.assertIn("after_screenshot", app_js)
        self.assertIn("scope_envelope", app_js)
        self.assertIn("approved_action_digest", app_js)
        self.assertIn("已绑定批准内容", app_js)
        self.assertIn("sync_capabilities", app_js)
        self.assertIn(".opc-action-list", style_css)
        self.assertIn(".opc-readiness-grid", style_css)
        self.assertIn(".opc-runtime-grid", style_css)
        self.assertIn(".opc-evidence-list", style_css)
        self.assertIn(".execution-job-evidence", style_css)
        self.assertIn(".approval-scope", style_css)
        self.assertIn(".first-run-gates", style_css)

    def test_console_first_run_gates_have_operator_actions(self):
        app_js = Path("web/app.js").read_text(encoding="utf-8")
        style_css = Path("web/style.css").read_text(encoding="utf-8")

        self.assertIn("renderFirstRunGateAction", app_js)
        self.assertIn("handleFirstRunGateAction", app_js)
        self.assertIn("focusAccountBootstrapForm", app_js)
        self.assertIn("getPrimaryAccountId", app_js)
        self.assertIn("data-gate-id", app_js)
        self.assertIn("account_configured", app_js)
        self.assertIn("prompt_ready", app_js)
        self.assertIn("sync_current", app_js)
        self.assertIn("no_unassigned_work", app_js)
        self.assertIn("runDailyReview", app_js)
        self.assertIn(".first-run-gate-action", style_css)

    def test_console_renders_account_queue_drilldown(self):
        app_js = Path("web/app.js").read_text(encoding="utf-8")
        style_css = Path("web/style.css").read_text(encoding="utf-8")

        self.assertIn("/dashboard/account-queues", app_js)
        self.assertIn("renderAccountQueueDrilldown", app_js)
        self.assertIn("account_queue", app_js)
        self.assertIn(".account-queue-grid", style_css)
        self.assertIn(".account-queue-card", style_css)

    def test_console_has_account_attribution_repair_controls(self):
        app_js = Path("web/app.js").read_text(encoding="utf-8")
        style_css = Path("web/style.css").read_text(encoding="utf-8")

        self.assertIn("/dashboard/account-queues/repair-attribution", app_js)
        self.assertIn("/dashboard/account-queues/unassigned-work", app_js)
        self.assertIn("repairAccountAttribution", app_js)
        self.assertIn("resolveAccountAttribution", app_js)
        self.assertIn("preview_token", app_js)
        self.assertIn("work_order_ids", app_js)
        self.assertIn("account-attribution-tools", app_js)
        self.assertIn("account-attribution-review", app_js)
        self.assertIn(".account-attribution-tools", style_css)
        self.assertIn(".account-attribution-review", style_css)

    def test_console_has_account_queue_sync_controls_and_freshness(self):
        app_js = Path("web/app.js").read_text(encoding="utf-8")
        style_css = Path("web/style.css").read_text(encoding="utf-8")

        self.assertIn("/adapter/sync-persist", app_js)
        self.assertIn("syncAccountQueue", app_js)
        self.assertIn("sync_freshness", app_js)
        self.assertIn("account-sync-freshness", app_js)
        self.assertIn("account-sync-actions", app_js)
        self.assertIn(".account-sync-freshness", style_css)
        self.assertIn(".account-sync-actions", style_css)

    def test_console_has_account_runtime_badges(self):
        app_js = Path("web/app.js").read_text(encoding="utf-8")
        style_css = Path("web/style.css").read_text(encoding="utf-8")

        self.assertIn("renderAccountRuntimeBadge", app_js)
        self.assertIn("account_runtime", app_js)
        self.assertIn("connection_status", app_js)
        self.assertIn("manual_takeover_count", app_js)
        self.assertIn("account-runtime-badges", app_js)
        self.assertIn(".account-runtime-badges", style_css)
        self.assertIn(".account-runtime-good", style_css)
        self.assertIn(".account-runtime-warn", style_css)

    def test_first_run_actions_target_the_selected_ready_account(self):
        app_js = Path("web/app.js").read_text(encoding="utf-8")

        self.assertIn("readiness.selected_account", app_js)
        self.assertIn("data-cookie-id", app_js)
        self.assertIn('gateId === "account_enabled"', app_js)
        self.assertIn("selectedCookieId || getPrimaryAccountId()", app_js)

    def test_listing_draft_requires_explicit_account_selection_for_multi_account_operation(self):
        app_js = Path("web/app.js").read_text(encoding="utf-8")

        self.assertIn("chooseListingAccountId", app_js)
        self.assertIn("?cookie_id=${encodeURIComponent(cookieId)}", app_js)

    def test_console_has_publish_preflight_action(self):
        app_js = Path("web/app.js").read_text(encoding="utf-8")
        style_css = Path("web/style.css").read_text(encoding="utf-8")

        self.assertIn("checkPublishPreflight", app_js)
        self.assertIn("/publish-preflight", app_js)
        self.assertIn("发布前检查", app_js)
        self.assertIn("data.type === \"resale_preflight\"", app_js)
        self.assertIn("summarizePreflight", app_js)
        self.assertIn("renderPreflightGateList", app_js)
        self.assertIn(".preflight-gate", style_css)
        self.assertIn(".preflight-gate--fail", style_css)

    def test_console_listing_draft_displays_409_preflight_detail(self):
        app_js = Path("web/app.js").read_text(encoding="utf-8")

        self.assertIn("resale_preflight_blocked", app_js)
        self.assertIn("error.detail", app_js)
        self.assertIn("发布被阻止", app_js)
        self.assertIn("error.status", app_js)

    def test_console_evidence_modal_has_required_controls_and_payload_fields(self):
        console_html = Path("web/console.html").read_text(encoding="utf-8")
        app_js = Path("web/app.js").read_text(encoding="utf-8")
        style_css = Path("web/style.css").read_text(encoding="utf-8")

        for field in [
            "title",
            "category",
            "source_platform",
            "source_url",
            "supplier_name",
            "supplier_sku",
            "specifications",
            "supplier_price",
            "supplier_shipping",
            "target_sale_price",
            "supplier_verified",
            "supplier_verification_note",
            "supplier_quote_checked_at",
            "supplier_quote_evidence_ref",
            "minimum_order_quantity",
            "stock_status",
            "stock_quantity",
            "stock_checked_at",
            "stock_evidence_ref",
            "fulfillment_verified",
            "fulfillment_verification_note",
            "fulfillment_evidence_ref",
            "neutral_shipping_confirmed",
            "dispatch_time_hours",
            "supplier_return_policy",
            "damage_liability_note",
            "images",
            "image_rights_type",
            "image_rights_evidence",
            "image_rights_evidence_ref",
            "competitor_links",
            "competitor_price_min",
            "competitor_price_max",
            "competitor_checked_at",
            "competitor_evidence_ref",
            "estimated_demand_signal",
        ]:
            self.assertIn(f'name="{field}"', console_html)

        self.assertIn('id="evidence-modal"', console_html)
        self.assertIn('id="evidence-form"', console_html)
        self.assertIn("saveEvidenceModal", app_js)
        self.assertIn("closeEvidenceModal", app_js)
        self.assertIn("populateEvidenceModal", app_js)
        self.assertIn("evidence-form-error", console_html)
        self.assertIn("evidenceCandidateId", app_js)
        self.assertIn("candidateActivationPacket", app_js)
        self.assertIn("renderCandidateActivationPacket", app_js)
        self.assertIn("/activation-packet", app_js)
        self.assertIn('method: "PATCH"', app_js)
        self.assertIn("manual_decision_invalidated", app_js)
        self.assertIn("旧人工决策已失效", app_js)
        self.assertIn("activation_summary", app_js)
        self.assertIn("splitListField", app_js)
        self.assertIn("toServerTimestamp", app_js)
        self.assertIn(".modal-overlay", style_css)
        self.assertIn(".modal-card", style_css)
        self.assertIn(".evidence-grid", style_css)
        self.assertIn(".resale-evidence-summary", style_css)
        self.assertIn(".candidate-activation-gate", style_css)
        self.assertIn(".candidate-activation-modal-card", style_css)

        self.assertIn('id="candidate-evidence-kind"', console_html)
        self.assertIn('id="candidate-evidence-observed-at"', console_html)
        self.assertIn('id="candidate-evidence-file"', console_html)
        self.assertIn('id="upload-candidate-evidence"', console_html)
        self.assertIn('id="candidate-evidence-artifact-list"', console_html)
        self.assertIn("uploadCandidateEvidenceArtifact", app_js)
        self.assertIn("renderCandidateEvidenceArtifacts", app_js)
        self.assertIn("/evidence-artifacts", app_js)
        self.assertIn('body: file', app_js)
        self.assertIn("SHA-256", app_js)
        self.assertIn("integrity", app_js)
        self.assertIn(".candidate-evidence-upload-grid", style_css)
        self.assertIn(".candidate-evidence-artifact", style_css)
        for ref_field in [
            "supplier_quote_evidence_ref",
            "stock_evidence_ref",
            "fulfillment_evidence_ref",
            "image_rights_evidence_ref",
            "competitor_evidence_ref",
        ]:
            self.assertRegex(
                console_html,
                rf'<input name="{ref_field}"[^>]*\breadonly\b',
            )

    def test_console_candidate_creation_form_has_images_field(self):
        console_html = Path("web/console.html").read_text(encoding="utf-8")
        app_js = Path("web/app.js").read_text(encoding="utf-8")

        self.assertIn('name="images"', console_html)
        self.assertIn("images: splitListField(fd.get(\"images\"))", app_js)

    def test_evidence_modal_is_hidden_on_load(self):
        console_html = Path("web/console.html").read_text(encoding="utf-8")
        style_css = Path("web/style.css").read_text(encoding="utf-8")

        # The modal starts closed: the markup must carry the hidden attribute and
        # the overlay's display:flex must NOT override it.
        self.assertRegex(console_html, r'id="evidence-modal"[^>]*\bhidden\b')
        self.assertIn(".modal-overlay[hidden] {", style_css)
        self.assertIn("display: none;", style_css.split(".modal-overlay[hidden] {")[1].split("}")[0])
        # When opened (hidden removed) the overlay must still flex-center its card.
        overlay_rule = style_css.split(".modal-overlay {")[1].split("}")[0]
        self.assertIn("display: flex", overlay_rule)

    def test_console_has_discovery_inbox_controls(self):
        console_html = Path("web/console.html").read_text(encoding="utf-8")
        app_js = Path("web/app.js").read_text(encoding="utf-8")
        style_css = Path("web/style.css").read_text(encoding="utf-8")

        self.assertIn('id="discovery-inbox-feed"', console_html)
        self.assertIn("refreshDiscoveryInbox", app_js)
        self.assertIn("renderDiscoveryInbox", app_js)
        self.assertIn("promoteDiscoveryEvent", app_js)
        self.assertIn("markDiscoveryEventRead", app_js)
        self.assertIn("/discovery/inbox", app_js)
        self.assertIn(".discovery-inbox-feed", style_css)

    def test_console_has_daily_review_snapshot_control(self):
        console_html = Path("web/console.html").read_text(encoding="utf-8")
        app_js = Path("web/app.js").read_text(encoding="utf-8")

        self.assertIn('id="run-daily-review"', console_html)
        self.assertIn("/dashboard/daily-review", app_js)
        self.assertIn("runDailyReview", app_js)

    def test_console_has_browser_message_import_control(self):
        console_html = Path("web/console.html").read_text(encoding="utf-8")
        app_js = Path("web/app.js").read_text(encoding="utf-8")

        self.assertIn('id="import-browser-messages"', console_html)
        self.assertIn("importBrowserMessages", app_js)
        self.assertIn("/message-intake/import-browser", app_js)
        self.assertIn("browser_message_import", app_js)

    def test_console_has_browser_conversation_import_control(self):
        console_html = Path("web/console.html").read_text(encoding="utf-8")
        app_js = Path("web/app.js").read_text(encoding="utf-8")

        self.assertIn('id="import-browser-conversations"', console_html)
        self.assertIn("importBrowserConversations", app_js)
        self.assertIn("/message-intake/import-browser-conversations", app_js)
        self.assertIn("browser_conversation_import", app_js)

    def test_console_has_message_import_scheduler_controls(self):
        console_html = Path("web/console.html").read_text(encoding="utf-8")
        app_js = Path("web/app.js").read_text(encoding="utf-8")

        self.assertIn('id="message-import-scheduler"', console_html)
        self.assertIn('id="configure-message-import-scheduler"', console_html)
        self.assertIn('id="run-message-import-scheduler"', console_html)
        self.assertIn("renderMessageImportScheduler", app_js)
        self.assertIn("configureMessageImportScheduler", app_js)
        self.assertIn("runMessageImportSchedulerNow", app_js)
        self.assertIn("consecutive_failures", app_js)
        self.assertIn("backoff_seconds", app_js)
        self.assertIn("due_profiles", app_js)
        self.assertIn("profiles", app_js)
        self.assertIn("/message-intake/import-scheduler", app_js)
        self.assertIn("/message-intake/import-scheduler/run-now", app_js)
        self.assertIn("message_import_scheduler_run", app_js)

    def test_console_has_message_import_scheduler_profile_controls(self):
        console_html = Path("web/console.html").read_text(encoding="utf-8")
        app_js = Path("web/app.js").read_text(encoding="utf-8")

        self.assertIn('id="message-import-scheduler-profile-form"', console_html)
        self.assertIn('id="message-import-scheduler-profiles"', console_html)
        self.assertIn('name="scheduler_cookie_id"', console_html)
        self.assertIn("renderMessageImportSchedulerProfiles", app_js)
        self.assertIn("submitMessageImportSchedulerProfileForm", app_js)
        self.assertIn("runMessageImportSchedulerProfile", app_js)
        self.assertIn("toggleMessageImportSchedulerProfile", app_js)
        self.assertIn("data-scheduler-cookie-id", app_js)
        self.assertIn("scheduler-profile-row", app_js)
        self.assertIn("/message-intake/import-scheduler/run-now?cookie_id=", app_js)

    def test_console_has_message_import_scheduler_presets(self):
        console_html = Path("web/console.html").read_text(encoding="utf-8")
        app_js = Path("web/app.js").read_text(encoding="utf-8")

        self.assertIn('name="scheduler_preset"', console_html)
        self.assertIn('id="apply-message-import-scheduler-preset"', console_html)
        self.assertIn('value="high_touch"', console_html)
        self.assertIn('value="observe"', console_html)
        self.assertIn('value="paused"', console_html)
        self.assertIn("SCHEDULER_PRESETS", app_js)
        self.assertIn("applyMessageImportSchedulerPreset", app_js)
        self.assertIn("scheduler-preset-control", app_js)
        self.assertIn("high_touch", app_js)
        self.assertIn("observe", app_js)
        self.assertIn("paused", app_js)

    def test_console_can_bootstrap_scheduler_profiles_from_accounts(self):
        console_html = Path("web/console.html").read_text(encoding="utf-8")
        app_js = Path("web/app.js").read_text(encoding="utf-8")
        style_css = Path("web/style.css").read_text(encoding="utf-8")

        self.assertIn('id="bootstrap-message-import-scheduler-profiles"', console_html)
        self.assertIn('name="scheduler_overwrite"', console_html)
        self.assertIn("bootstrapMessageImportSchedulerProfiles", app_js)
        self.assertIn("/message-intake/import-scheduler/bootstrap-accounts", app_js)
        self.assertIn('data.type === "message_import_scheduler_bootstrap"', app_js)
        self.assertIn("scheduler-bootstrap-control", style_css)

    def test_opc_dashboard_renders_scheduler_health_actions(self):
        app_js = Path("web/app.js").read_text(encoding="utf-8")
        style_css = Path("web/style.css").read_text(encoding="utf-8")

        self.assertIn("const health = scheduler.health || {}", app_js)
        self.assertIn("health.missing_profiles", app_js)
        self.assertIn("opc-scheduler-health-list", app_js)
        self.assertIn("suggested_action", app_js)
        self.assertIn(".opc-scheduler-health-list", style_css)
        self.assertIn('[data-priority="high"]', style_css)

    def test_console_has_commercial_alpha_trial_operator_section(self):
        console_html = Path("web/console.html").read_text(encoding="utf-8")
        app_js = Path("web/app.js").read_text(encoding="utf-8")
        style_css = Path("web/style.css").read_text(encoding="utf-8")

        # Operator panel, batch creation form, and refresh control.
        self.assertIn('id="commercial-trial-panel"', console_html)
        self.assertIn('id="commercial-trial-create-form"', console_html)
        self.assertIn('id="refresh-commercial-trials"', console_html)
        self.assertIn('id="commercial-trial-summary"', console_html)
        self.assertIn('name="ct_cookie_id"', console_html)
        self.assertIn('id="ct-account-select"', console_html)
        self.assertIn('name="ct_candidate_id"', console_html)
        self.assertIn('id="ct-candidate-select"', console_html)
        self.assertIn('name="ct_category"', console_html)
        self.assertIn('name="ct_max_realized_loss"', console_html)
        self.assertIn('name="ct_max_after_sales_rate"', console_html)
        self.assertIn('name="ct_target_inquiries"', console_html)
        self.assertIn('name="ct_target_paid_orders"', console_html)
        self.assertIn('id="commercial-launch-preview"', console_html)
        self.assertIn('id="record-commercial-terms"', console_html)
        self.assertIn('id="commercial-terms-modal"', console_html)
        self.assertIn('id="commercial-terms-form"', console_html)
        self.assertIn('name="seller_type"', console_html)
        self.assertIn('name="platform_fixed_fee"', console_html)
        self.assertIn('name="guarantee_required"', console_html)

        # API wiring and render helpers.
        self.assertIn("/commercial-trials/active", app_js)
        self.assertIn('api("/accounts")', app_js)
        self.assertIn("activeResp.trials", app_js)
        self.assertIn("populateCommercialTrialInputs", app_js)
        self.assertIn("refreshCommercialTrials", app_js)
        self.assertIn("renderCommercialTrial", app_js)
        self.assertIn("submitCommercialTrialCreate", app_js)
        self.assertIn("renderCommercialLaunchPreview", app_js)
        self.assertIn("previewCommercialLaunch", app_js)
        self.assertIn("startCommercialLaunch", app_js)
        self.assertIn("/commercial-launch/preview", app_js)
        self.assertIn("/commercial-launch/start", app_js)
        self.assertIn("preview_token", app_js)
        self.assertIn('String(candidate.sales_platform || "xianyu").toLowerCase() === "xianyu"', app_js)
        self.assertIn('String(candidate.market_scope || "domestic").toLowerCase() === "domestic"', app_js)
        self.assertIn("START 1", app_js)
        self.assertIn("openCommercialTermsDialog", app_js)
        self.assertIn("saveCommercialTermsModal", app_js)
        self.assertIn("/commercial-terms", app_js)
        self.assertIn("activation_plan", app_js)
        self.assertIn("complete_commercial_terms", app_js)
        self.assertIn("attachTrialCandidate", app_js)
        self.assertIn("createTrialListingDraft", app_js)
        self.assertIn("decideCommercialTrial", app_js)
        self.assertIn("reportCommercialTrial", app_js)
        self.assertIn("renderCommercialTrialOrders", app_js)
        self.assertIn("submitCommercialFulfillmentHandoff", app_js)
        self.assertIn("submitCommercialExperimentActivation", app_js)
        self.assertIn("cancelCommercialExperiment", app_js)
        self.assertIn("确认变更并开始计时", app_js)
        self.assertIn("提交变更审批", app_js)
        self.assertIn("financial_evidence_note", app_js)
        self.assertIn("履约证据", app_js)
        self.assertIn("利润证据", app_js)
        self.assertIn("releaseCommercialFulfillmentHandoff", app_js)
        self.assertIn("/fulfillment-handoff", app_js)
        self.assertIn("/fulfillment-release", app_js)
        self.assertIn("trial-stage-list", app_js)
        self.assertIn("batch_listing_draft_blocked", app_js)
        self.assertIn("trial-fulfillment-form", style_css)
        self.assertIn("trial-confirmation-grid", style_css)
        self.assertIn("synthetic_orders", app_js)
        self.assertIn("publish_commitment_count", app_js)
        self.assertIn("committed_cash_requirement", app_js)

        # Visual language matches the existing console.
        self.assertIn(".commercial-trial-card", style_css)
        self.assertIn(".commercial-launch-preview", style_css)
        self.assertIn(".commercial-launch-gates", style_css)
        self.assertIn(".commercial-launch-gate.is-blocked", style_css)
        self.assertIn(".commercial-launch-actions", style_css)
        self.assertIn(".commercial-terms-grid", style_css)
        self.assertIn(".commercial-terms-gates", style_css)
        self.assertIn(".commercial-activation-plan", style_css)
        self.assertIn(".trial-experiment-pending", style_css)
        self.assertIn(".trial-experiment-activation", style_css)
        self.assertIn(".trial-order-audit", style_css)
        self.assertIn(".trial-stage-list", style_css)
        self.assertIn(".trial-stop-alert", style_css)
        self.assertIn(".trial-controls-grid", style_css)
        self.assertIn(".trial-decision-actions", style_css)


if __name__ == "__main__":
    unittest.main()
