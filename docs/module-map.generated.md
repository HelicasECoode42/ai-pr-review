# 模块清单（由 `scripts/module_map.py` 从 AST 生成，请勿手改）

| 模块 | 公开函数（行号） | 调用到的 src 模块 | 触及的账目 | 接缝参数 |
|---|---|---|---|---|
| `src.__init__` | — | — | — | — |
| `src.agent.__init__` | — | — | — | — |
| `src.agent.policy` | `choose_agent_strategy`:16 | — | — | — |
| `src.agent.registry` | `register`:40<br>`get`:45<br>`names`:54 | — | — | — |
| `src.agent.runner` | `run`:204 | src.agent.policy, src.agent.trace, src.analyzer.cross_file, src.analyzer.risk_rules, src.output.json_report, src.output.markdown, src.reviewer.engine, src.utils.config | report, snapshot | — |
| `src.agent.state` | — | — | report | — |
| `src.agent.trace` | `summarize_input`:119<br>`summarize_observation`:167<br>`redact_secrets`:233 | — | report | — |
| `src.analyzer.__init__` | — | — | — | — |
| `src.analyzer.ast_rules` | `visit`:93<br>`scan_ast_risks`:379 | src.analyzer.diff_parser, src.utils.__init__, src.utils.degradation | — | — |
| `src.analyzer.context_builder` | `count_tokens`:40<br>`tokenizer_name`:48<br>`truncate_to_token_budget`:55<br>`decode_prefix`:67<br>`partial_inclusion_counts`:450<br>`partial_inclusion_warning`:463<br>`build_review_context`:472<br>`safe_attr`:507 | src.analyzer.diff_parser, src.analyzer.tiered_scan, src.utils.config | budget, coverage | — |
| `src.analyzer.cross_file` | `visit_FunctionDef`:62<br>`visit_Call`:82<br>`analyze_cross_file_impact`:225 | src.analyzer.diff_parser, src.utils.degradation | — | — |
| `src.analyzer.deterministic_tools` | `summary`:52<br>`run_linters`:191 | src.analyzer.diff_parser, src.analyzer.repository_context | snapshot | snapshot |
| `src.analyzer.diff_parser` | `parse_file_hunks`:17<br>`changed_line_map`:129 | src.utils.degradation | — | — |
| `src.analyzer.git_loader` | `load_frozen_plan_inputs`:53 | — | snapshot | — |
| `src.analyzer.impact_closure` | `nodes`:32<br>`summary`:39<br>`build_impact_closure`:48<br>`impact_coverage`:73 | src.reviewer.suggestion_filter | — | snapshot |
| `src.analyzer.plan_baseline` | `summarize_plan`:22<br>`summarize_context`:59<br>`summarize_unit_coverage`:101<br>`compare_planner_and_context`:140 | — | coverage, report | — |
| `src.analyzer.repository_context` | `safe_source_path`:94<br>`read`:105<br>`search`:106<br>`audit`:107<br>`read`:133<br>`search`:168<br>`audit`:242<br>`read`:275<br>`search`:300<br>`audit`:348<br>`function_window`:355<br>`budget_report`:414<br>`add`:448<br>`follow`:498<br>`execute`:527<br>`render`:662<br>`audit`:666 | src.analyzer.context_builder, src.analyzer.diff_parser | budget, evidence, snapshot | — |
| `src.analyzer.review_plan` | `parse`:314<br>`flush`:397<br>`build_review_plan`:492 | src.analyzer.context_builder, src.analyzer.diff_parser, src.analyzer.repository_context | budget, coverage, report, snapshot, units | snapshot |
| `src.analyzer.risk_rules` | `reload_rules`:69<br>`scan_signals`:80<br>`scan_risks`:154<br>`collect_signals`:163 | src.analyzer.ast_rules, src.analyzer.cross_file, src.analyzer.deterministic_tools, src.analyzer.diff_parser, src.utils.__init__, src.utils.degradation, src.utils.rule_loader | — | snapshot |
| `src.analyzer.selection` | `value`:35<br>`token_coverage`:55<br>`value_coverage`:59<br>`summary`:62<br>`select_hunks`:82<br>`select_hunks_optimal`:123 | — | — | — |
| `src.analyzer.semantic_diff` | `tokens`:21<br>`changed_lines`:25<br>`classify`:75<br>`add`:94<br>`mechanical_tokens`:99<br>`semantic_tokens`:103<br>`summary`:106<br>`partition`:117 | — | — | — |
| `src.analyzer.signal_selection` | `select_verifiable_signals`:15 | — | — | — |
| `src.analyzer.tiered_scan` | `is_suspicion_finding`:54<br>`score`:71<br>`summary`:87<br>`text`:105<br>`triage`:111 | — | — | — |
| `src.application.__init__` | — | — | — | — |
| `src.application.protocol` | `from_entries`:127 | — | budget, coverage, report, snapshot | — |
| `src.application.review` | `review`:259 | src.analyzer.diff_parser, src.analyzer.repository_context, src.application.strategy_policy, src.knowledge.playbook, src.reviewer.decision_ledger, src.reviewer.publish, src.reviewer.usage | criteria, playbook, report, snapshot, units, usage | — |
| `src.application.strategy_policy` | `validate_prompt_template`:60<br>`reject_unreplayed_retrieval_change`:71<br>`estimate_prompt_tokens`:97<br>`current_criteria_fingerprint`:102<br>`load_active_policy`:118 | — | criteria, playbook, report | — |
| `src.cli.__init__` | — | — | — | — |
| `src.cli.main` | `analyze`:98 | src.analyzer.risk_rules, src.output.json_report, src.output.markdown, src.reviewer.engine, src.reviewer.fix_tracker, src.utils.actions, src.utils.config | snapshot | report_confidence, report_format |
| `src.cli.plan` | `plan_review`:16 | src.analyzer.git_loader, src.analyzer.review_plan | coverage, report | — |
| `src.evaluation.__init__` | — | — | — | — |
| `src.evaluation.adjudicate` | `summarize`:13<br>`main`:75 | src.analyzer.tiered_scan | — | — |
| `src.evaluation.corpus` | `git`:22<br>`case_id`:28<br>`write_json`:34<br>`trace_input_identity`:41<br>`persist_review_trace`:67<br>`prepare`:97<br>`evaluate`:193<br>`main`:264 | src.evaluation.adjudicate, src.evaluation.reviewbench, src.evaluation.runtime, src.utils.config | — | provider |
| `src.evaluation.critic_replay` | `replay`:19<br>`retained_findings`:92<br>`main`:101 | src.evaluation.adjudicate, src.evaluation.corpus, src.evaluation.reviewbench, src.evaluation.runtime, src.reviewer.critic, src.utils.config | usage | provider |
| `src.evaluation.experiment` | `run`:24<br>`main`:82 | src.agent.runner, src.evaluation.adjudicate, src.evaluation.reviewbench, src.evaluation.runtime, src.evaluation.scoring, src.utils.config | report | provider |
| `src.evaluation.improvement` | `canonical_bytes`:74<br>`protocol_fingerprint`:80<br>`budget_fingerprint`:86<br>`current_criteria_fingerprint`:98<br>`sha256_file`:108<br>`build_input_manifest`:193<br>`derive_trace_diagnoses`:394<br>`generate_candidate`:485<br>`replay_candidate`:687<br>`evaluate_gate`:886<br>`bound_to_replay`:920<br>`promote_candidate`:1287<br>`rollback_policy`:1341 | src.application.strategy_policy, src.evaluation.reviewbench, src.evaluation.runtime | criteria, playbook, report | provider_factory, provider_mode, report |
| `src.evaluation.paired` | `begin_attempt`:39<br>`stop_after_fatal_error`:64<br>`snapshot`:70<br>`usage_records`:85<br>`complete_json`:87<br>`paired`:125<br>`attempt`:197<br>`checkpoint`:282<br>`main`:300 | src.evaluation.adjudicate, src.evaluation.corpus, src.evaluation.reviewbench, src.evaluation.runtime, src.utils.config | snapshot, usage | — |
| `src.evaluation.parallel_retry` | `run`:20<br>`attempt`:40<br>`checkpoint`:83<br>`main`:144<br>`factory`:156 | src.agent.runner, src.evaluation.adjudicate, src.evaluation.corpus, src.evaluation.paired, src.evaluation.reviewbench, src.evaluation.runtime, src.utils.config | — | provider_factory |
| `src.evaluation.reviewbench` | `parse_diff`:22<br>`decode`:32<br>`run_review`:99<br>`main`:313<br>`flag`:334 | src.analyzer.context_builder, src.analyzer.diff_parser, src.analyzer.risk_rules, src.evaluation.adjudicate, src.reviewer.engine, src.utils.config | report, snapshot, usage | provider, repository_snapshot |
| `src.evaluation.runtime` | `record`:25<br>`complete_json`:44<br>`source_identity`:60 | — | — | — |
| `src.evaluation.scoring` | `validate_labels`:36<br>`validate_splits`:45<br>`score`:57<br>`augment`:67<br>`accept_candidate`:95 | — | — | — |
| `src.github.__init__` | — | — | — | — |
| `src.github.client` | `close`:87<br>`get_json_limited`:176<br>`get_pull_request`:185<br>`list_pull_requests`:202<br>`get_compare`:232<br>`get_changed_files`:255<br>`get_review_comments`:278<br>`get_file_contents`:303<br>`get_issue_comments`:353 | — | snapshot | — |
| `src.knowledge.__init__` | — | — | playbook | — |
| `src.knowledge.playbook` | `language_of`:42<br>`as_dict`:67<br>`parse_entry`:79<br>`load_text`:121<br>`load_playbook`:135<br>`recall`:182<br>`expired_by_symbols`:196<br>`contract_domains`:209<br>`violates_domain`:233<br>`render`:246 | — | criteria, playbook | — |
| `src.models` | `over_limit`:53<br>`scopes`:75<br>`lines`:79<br>`omissions`:83<br>`total_used`:97<br>`over_cap`:113<br>`over_limit`:117 | — | budget, report | — |
| `src.output.__init__` | — | — | — | — |
| `src.output.json_report` | `render_json`:6 | — | report | report |
| `src.output.markdown` | `render_markdown`:359<br>`t`:659 | — | report | report |
| `src.reviewer.__init__` | — | — | — | — |
| `src.reviewer.budget` | `run_budget_report`:18<br>`budget_warnings`:26 | — | budget | report, reports |
| `src.reviewer.claim_check` | `contradicted`:77<br>`supported`:81<br>`status`:92<br>`extract_claims`:110<br>`added_text`:129<br>`check_claims`:140<br>`unimplemented`:153 | — | — | — |
| `src.reviewer.criteria` | `as_dict`:52<br>`quote_was_shown`:56<br>`valid_citation`:67<br>`witness_for_evidence`:105<br>`domain_violation`:131<br>`confirmation_verdicts`:145<br>`blocked_by_witness`:247 | src.knowledge.playbook, src.reviewer.witness | criteria, playbook, verdicts | snapshot |
| `src.reviewer.critic` | `select_critic_candidates`:37<br>`critique_batch`:48<br>`apply_critic`:89 | src.analyzer.diff_parser | report | provider |
| `src.reviewer.decision_ledger` | `as_dict`:123<br>`decision_records`:151<br>`build_candidate_lifecycle`:241<br>`append_candidate_ledger`:464<br>`append_ledger`:518<br>`load_ledger`:532<br>`filter_failure_counts`:560 | — | criteria, report, verdicts | report |
| `src.reviewer.deep_review` | `run_deep_review`:73 | — | — | provider |
| `src.reviewer.engine` | `build_diagnostic_report`:273<br>`validate_report`:328<br>`build_rule_only_report`:397<br>`review_with_ai`:506<br>`run_one_unit`:575<br>`grounded`:603<br>`read_frozen_head`:825 | src.analyzer.context_builder, src.analyzer.signal_selection, src.reviewer.budget, src.reviewer.critic, src.reviewer.evidence_validator, src.reviewer.grounded_review, src.reviewer.model_payload, src.reviewer.prompt, src.reviewer.review_merger, src.reviewer.signal_verifier, src.reviewer.suggestion_filter, src.reviewer.two_stage, src.reviewer.unit_review, src.utils.config | budget, criteria, playbook, report, snapshot, units | critic_provider, provider, report, report_confidence, repository_snapshot, snapshot, unit_provider_factory |
| `src.reviewer.evidence_validator` | `validate_ai_evidence`:18 | src.analyzer.diff_parser | — | — |
| `src.reviewer.fix_tracker` | `build_fix_tracking`:95 | — | — | — |
| `src.reviewer.grounded_review` | `review_grounded`:592<br>`build_grounded_report`:1255 | src.analyzer.context_builder, src.analyzer.diff_parser, src.analyzer.impact_closure, src.analyzer.plan_baseline, src.analyzer.repository_context, src.analyzer.review_plan, src.reviewer.budget, src.reviewer.criteria, src.reviewer.model_payload, src.reviewer.prompt, src.reviewer.suggestion_filter | budget, coverage, criteria, evidence, playbook, report, snapshot, units, verdicts | provider, report_confidence, snapshot |
| `src.reviewer.model_payload` | `parse_model_payload`:32<br>`repair_truncated_json`:82 | — | — | — |
| `src.reviewer.prompt` | `build_user_prompt`:48 | — | — | — |
| `src.reviewer.provider` | `complete_json`:29<br>`reserve`:56<br>`check_deadline`:84<br>`snapshot`:90<br>`complete_json`:141<br>`close`:364 | — | usage | — |
| `src.reviewer.publish` | `normalize_comment`:23<br>`comment_fingerprint`:30<br>`review_comment_body`:35<br>`review_publication_fingerprint`:47<br>`duplicates`:62<br>`is_dismissed`:88<br>`dismiss`:91<br>`dismissed`:102 | — | — | — |
| `src.reviewer.review_merger` | `merge_review_results`:15 | — | — | — |
| `src.reviewer.signal_contracts` | `from_finding`:35<br>`unsure`:82<br>`verify_batch`:90<br>`merge`:99 | — | — | — |
| `src.reviewer.signal_verifier` | `build_signal_envelopes`:17<br>`verify_batch`:35 | — | report | — |
| `src.reviewer.suggestion_filter` | `locate_symbol`:37<br>`filter_suggestions`:65 | src.analyzer.diff_parser | — | — |
| `src.reviewer.triage` | `run_triage`:77 | — | — | provider |
| `src.reviewer.two_stage` | `two_stage_review`:26 | src.analyzer.context_builder, src.reviewer.deep_review, src.reviewer.model_payload, src.reviewer.prompt, src.reviewer.suggestion_filter, src.reviewer.triage | — | provider |
| `src.reviewer.unit_review` | `build_unit_context`:58<br>`unit_files`:118<br>`reviewed`:142<br>`failed`:151<br>`units_planned`:160<br>`counts`:163<br>`load_checkpoint`:290<br>`append_checkpoint`:348<br>`release`:516<br>`review_by_units`:564<br>`run`:681<br>`merge_grounded_results`:794<br>`run_units_for_review`:853 | src.agent.runner, src.analyzer.context_builder, src.analyzer.review_plan | budget, coverage, report, units | snapshot |
| `src.reviewer.usage` | `summarize_usage`:40 | — | usage | — |
| `src.reviewer.witness` | `blocks_confirmation`:90<br>`parse_expression`:131<br>`free_names`:148<br>`spend`:185<br>`evaluate_equivalence`:393<br>`expression_from_quote`:439 | — | criteria | — |
| `src.service.__init__` | — | — | — | — |
| `src.service.app` | `analyze_stream`:64<br>`emit`:77<br>`run_review`:80<br>`event_stream`:113<br>`health`:135<br>`list_rules`:140<br>`get_config`:155<br>`get_demo_report`:167<br>`analyze`:187<br>`list_history`:333<br>`get_history_entry`:374<br>`repo_trend`:394 | src.analyzer.risk_rules, src.cli.main, src.output.markdown, src.reviewer.engine, src.utils.config, src.utils.rule_loader | report, snapshot | — |
| `src.utils.__init__` | `detect_programming_language`:1 | — | — | — |
| `src.utils.actions` | `write_step_summary`:9 | — | — | — |
| `src.utils.config` | `get_settings`:78<br>`detect_output_language`:89 | — | playbook, units | — |
| `src.utils.degradation` | `record_degradation`:13 | — | — | — |
| `src.utils.rule_loader` | `load_rules`:107<br>`rules_to_dict`:160 | — | — | — |

共 80 个模块。图与表都来自代码，手画容易与实现脱节。
