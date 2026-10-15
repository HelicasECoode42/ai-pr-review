# 模块清单（由 `scripts/module_map.py` 从 AST 生成，请勿手改）

| 模块 | 公开函数（行号） | 调用到的 src 模块 | 触及的账目 | 接缝参数 |
|---|---|---|---|---|
| `src.__init__` | — | — | — | — |
| `src.agent.__init__` | — | — | — | — |
| `src.agent.policy` | `choose_agent_strategy`:16 | — | — | — |
| `src.agent.registry` | `register`:40<br>`get`:45<br>`names`:54 | — | — | — |
| `src.agent.runner` | `run`:197 | src.agent.policy, src.agent.trace, src.analyzer.cross_file, src.analyzer.risk_rules, src.output.json_report, src.output.markdown, src.reviewer.engine, src.utils.config | report | — |
| `src.agent.state` | — | — | report | — |
| `src.agent.trace` | `summarize_input`:119<br>`summarize_observation`:167<br>`redact_secrets`:232 | — | report | — |
| `src.analyzer.__init__` | — | — | — | — |
| `src.analyzer.ast_rules` | `visit`:93<br>`scan_ast_risks`:379 | src.analyzer.diff_parser, src.utils.__init__, src.utils.degradation | — | — |
| `src.analyzer.context_builder` | `count_tokens`:40<br>`tokenizer_name`:48<br>`truncate_to_token_budget`:55<br>`partial_inclusion_counts`:402<br>`partial_inclusion_warning`:415<br>`build_review_context`:424<br>`safe_attr`:459 | src.analyzer.diff_parser, src.utils.config | budget, coverage | — |
| `src.analyzer.cross_file` | `visit_FunctionDef`:62<br>`visit_Call`:82<br>`analyze_cross_file_impact`:225 | src.analyzer.diff_parser, src.utils.degradation | — | — |
| `src.analyzer.diff_parser` | `parse_file_hunks`:17<br>`changed_line_map`:132 | src.utils.degradation | — | — |
| `src.analyzer.git_loader` | `load_frozen_plan_inputs`:38 | — | snapshot | — |
| `src.analyzer.plan_baseline` | `summarize_plan`:22<br>`summarize_context`:59<br>`summarize_unit_coverage`:101<br>`compare_planner_and_context`:140 | — | coverage, report | — |
| `src.analyzer.repository_context` | `safe_source_path`:24<br>`read`:35<br>`search`:36<br>`audit`:37<br>`read`:54<br>`search`:67<br>`audit`:103<br>`read`:121<br>`search`:137<br>`audit`:168<br>`function_window`:175<br>`budget_report`:233<br>`add`:267<br>`follow`:303<br>`execute`:332<br>`render`:364<br>`audit`:368 | src.analyzer.context_builder, src.analyzer.diff_parser | budget, evidence, snapshot | — |
| `src.analyzer.review_plan` | `parse`:314<br>`flush`:397<br>`build_review_plan`:492 | src.analyzer.context_builder, src.analyzer.diff_parser, src.analyzer.repository_context | budget, coverage, report, snapshot, units | snapshot |
| `src.analyzer.risk_rules` | `reload_rules`:69<br>`scan_signals`:80<br>`scan_risks`:132<br>`collect_signals`:140 | src.analyzer.ast_rules, src.analyzer.cross_file, src.analyzer.diff_parser, src.utils.__init__, src.utils.degradation, src.utils.rule_loader | — | — |
| `src.analyzer.signal_selection` | `select_verifiable_signals`:15 | — | — | — |
| `src.application.__init__` | — | — | — | — |
| `src.application.protocol` | `from_entries`:124 | — | budget, coverage, report | — |
| `src.application.review` | `review`:84 | src.analyzer.repository_context, src.knowledge.playbook, src.reviewer.usage | criteria, playbook, report, snapshot, units, usage | — |
| `src.cli.__init__` | — | — | — | — |
| `src.cli.main` | `analyze`:98 | src.analyzer.risk_rules, src.output.json_report, src.output.markdown, src.reviewer.engine, src.reviewer.fix_tracker, src.utils.actions, src.utils.config | — | report_confidence, report_format |
| `src.cli.plan` | `plan_review`:16 | src.analyzer.git_loader, src.analyzer.review_plan | coverage, report | — |
| `src.evaluation.__init__` | — | — | — | — |
| `src.evaluation.adjudicate` | `summarize`:13<br>`main`:75 | src.evaluation.scoring | — | — |
| `src.evaluation.corpus` | `git`:22<br>`case_id`:28<br>`write_json`:34<br>`prepare`:41<br>`evaluate`:137<br>`main`:198 | src.evaluation.adjudicate, src.evaluation.reviewbench, src.evaluation.runtime, src.utils.config | — | provider |
| `src.evaluation.critic_replay` | `replay`:19<br>`retained_findings`:92<br>`main`:101 | src.evaluation.adjudicate, src.evaluation.corpus, src.evaluation.reviewbench, src.evaluation.runtime, src.reviewer.critic, src.utils.config | usage | provider |
| `src.evaluation.experiment` | `run`:24<br>`main`:82 | src.agent.runner, src.evaluation.adjudicate, src.evaluation.reviewbench, src.evaluation.runtime, src.evaluation.scoring, src.utils.config | report | provider |
| `src.evaluation.improvement` | `improve`:24<br>`digest`:47 | src.evaluation.corpus, src.evaluation.scoring | report | provider |
| `src.evaluation.paired` | `begin_attempt`:40<br>`stop_after_fatal_error`:65<br>`snapshot`:71<br>`usage_records`:86<br>`complete_json`:88<br>`paired`:126<br>`attempt`:194<br>`checkpoint`:275<br>`main`:293 | src.evaluation.adjudicate, src.evaluation.corpus, src.evaluation.reviewbench, src.evaluation.runtime, src.utils.config | snapshot, usage | — |
| `src.evaluation.parallel_retry` | `run`:20<br>`attempt`:40<br>`checkpoint`:83<br>`main`:144<br>`factory`:156 | src.agent.runner, src.evaluation.adjudicate, src.evaluation.corpus, src.evaluation.paired, src.evaluation.reviewbench, src.evaluation.runtime, src.utils.config | — | provider_factory |
| `src.evaluation.reviewbench` | `parse_diff`:22<br>`decode`:32<br>`run_review`:99<br>`main`:245<br>`flag`:266 | src.analyzer.context_builder, src.analyzer.diff_parser, src.analyzer.risk_rules, src.evaluation.adjudicate, src.reviewer.engine, src.utils.config | report, snapshot, usage | provider, repository_snapshot |
| `src.evaluation.runtime` | `record`:25<br>`complete_json`:44<br>`source_identity`:60 | — | — | — |
| `src.evaluation.scoring` | `validate_labels`:36<br>`validate_splits`:45<br>`score`:57<br>`augment`:67<br>`accept_candidate`:95 | — | — | — |
| `src.github.__init__` | — | — | — | — |
| `src.github.client` | `close`:69<br>`get_pull_request`:92<br>`list_pull_requests`:109<br>`get_compare`:139<br>`get_changed_files`:162<br>`get_review_comments`:185<br>`get_file_contents`:210<br>`get_issue_comments`:240 | — | — | — |
| `src.knowledge.__init__` | — | — | playbook | — |
| `src.knowledge.playbook` | `language_of`:41<br>`as_dict`:66<br>`parse_entry`:78<br>`load_text`:120<br>`load_playbook`:134<br>`recall`:158<br>`expired_by_symbols`:169<br>`contract_domains`:182<br>`violates_domain`:191<br>`render`:204 | — | criteria, playbook | — |
| `src.models` | `over_limit`:52<br>`scopes`:74<br>`lines`:78<br>`omissions`:82<br>`total_used`:96<br>`over_cap`:112<br>`over_limit`:116 | — | budget, report | — |
| `src.output.__init__` | — | — | — | — |
| `src.output.json_report` | `render_json`:6 | — | report | report |
| `src.output.markdown` | `render_markdown`:305<br>`t`:604 | — | report | report |
| `src.reviewer.__init__` | — | — | — | — |
| `src.reviewer.budget` | `run_budget_report`:18<br>`budget_warnings`:26 | — | budget | report, reports |
| `src.reviewer.criteria` | `as_dict`:51<br>`quote_was_shown`:55<br>`valid_citation`:66<br>`witness_for_evidence`:104<br>`domain_violation`:130<br>`confirmation_verdicts`:144<br>`blocked_by_witness`:246 | src.knowledge.playbook, src.reviewer.witness | criteria, playbook, verdicts | snapshot |
| `src.reviewer.critic` | `select_critic_candidates`:37<br>`critique_batch`:48<br>`apply_critic`:89 | src.analyzer.diff_parser | report | provider |
| `src.reviewer.deep_review` | `run_deep_review`:73 | — | — | provider |
| `src.reviewer.engine` | `build_diagnostic_report`:151<br>`validate_report`:206<br>`build_rule_only_report`:275<br>`review_with_ai`:380<br>`run_one_unit`:424<br>`grounded`:442<br>`read_frozen_head`:558 | src.analyzer.context_builder, src.analyzer.signal_selection, src.reviewer.budget, src.reviewer.critic, src.reviewer.evidence_validator, src.reviewer.grounded_review, src.reviewer.model_payload, src.reviewer.prompt, src.reviewer.review_merger, src.reviewer.signal_verifier, src.reviewer.suggestion_filter, src.reviewer.two_stage, src.reviewer.unit_review, src.utils.config | budget, playbook, report, snapshot, units | critic_provider, provider, report, report_confidence, repository_snapshot, snapshot, unit_provider_factory |
| `src.reviewer.evidence_validator` | `validate_ai_evidence`:18 | src.analyzer.diff_parser | — | — |
| `src.reviewer.fix_tracker` | `build_fix_tracking`:95 | — | — | — |
| `src.reviewer.grounded_review` | `review_grounded`:213<br>`build_grounded_report`:431 | src.analyzer.context_builder, src.analyzer.plan_baseline, src.analyzer.review_plan, src.reviewer.budget, src.reviewer.criteria, src.reviewer.model_payload, src.reviewer.prompt, src.reviewer.suggestion_filter | budget, criteria, evidence, playbook, report, snapshot, units, verdicts | provider, report_confidence, snapshot |
| `src.reviewer.model_payload` | `parse_model_payload`:32<br>`repair_truncated_json`:82 | — | — | — |
| `src.reviewer.prompt` | `build_user_prompt`:48 | — | — | — |
| `src.reviewer.provider` | `complete_json`:27<br>`complete_json`:57<br>`close`:228 | — | usage | — |
| `src.reviewer.review_merger` | `merge_review_results`:15 | — | — | — |
| `src.reviewer.signal_contracts` | `from_finding`:35<br>`unsure`:82<br>`verify_batch`:90<br>`merge`:99 | — | — | — |
| `src.reviewer.signal_verifier` | `build_signal_envelopes`:17<br>`verify_batch`:35 | — | report | — |
| `src.reviewer.suggestion_filter` | `locate_symbol`:37<br>`filter_suggestions`:65 | src.analyzer.diff_parser | — | — |
| `src.reviewer.triage` | `run_triage`:77 | — | — | provider |
| `src.reviewer.two_stage` | `two_stage_review`:26 | src.analyzer.context_builder, src.reviewer.deep_review, src.reviewer.model_payload, src.reviewer.prompt, src.reviewer.suggestion_filter, src.reviewer.triage | — | provider |
| `src.reviewer.unit_review` | `build_unit_context`:40<br>`unit_files`:100<br>`reviewed`:121<br>`failed`:125<br>`units_planned`:129<br>`counts`:132<br>`review_by_units`:141<br>`run`:176<br>`merge_grounded_results`:226<br>`run_units_for_review`:284 | src.agent.runner, src.analyzer.context_builder, src.analyzer.review_plan | budget, coverage, units | snapshot |
| `src.reviewer.usage` | `summarize_usage`:40 | — | usage | — |
| `src.reviewer.witness` | `blocks_confirmation`:80<br>`parse_expression`:99<br>`free_names`:114<br>`spend`:147<br>`evaluate_equivalence`:270<br>`expression_from_quote`:304 | — | criteria | — |
| `src.service.__init__` | — | — | — | — |
| `src.service.app` | `analyze_stream`:64<br>`emit`:77<br>`run_review`:80<br>`event_stream`:103<br>`health`:125<br>`list_rules`:130<br>`get_config`:145<br>`get_demo_report`:157<br>`analyze`:177<br>`list_history`:309<br>`get_history_entry`:350<br>`repo_trend`:370 | src.analyzer.risk_rules, src.output.markdown, src.reviewer.engine, src.utils.config, src.utils.rule_loader | report | — |
| `src.utils.__init__` | `detect_programming_language`:1 | — | — | — |
| `src.utils.actions` | `write_step_summary`:9 | — | — | — |
| `src.utils.config` | `get_settings`:55<br>`detect_output_language`:66 | — | playbook, units | — |
| `src.utils.degradation` | `record_degradation`:13 | — | — | — |
| `src.utils.rule_loader` | `load_rules`:107<br>`rules_to_dict`:160 | — | — | — |

共 71 个模块。图与表都来自代码，手画容易与实现脱节。
