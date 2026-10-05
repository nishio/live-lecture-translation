import copy
from contextlib import nullcontext
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import event_insights as insights
import event_insights_cloud as cloud
import lecture_analysis as analysis


MODEL = {"model": "qwen3:4b", "context_tokens": 32768,
         "template_reserve": 4096, "digest": "test-model"}


def line(identity="a", start=0, text="The synthetic robot needs wheels as well as sensors.", **extra):
    return {"id": identity, "start_seconds": start, "end_seconds": start + 2,
            "text": text, "language": "en", "uncertain": False, **extra}


def output(ids=("a",), translations=()):
    statement = {"text": "アルゴリズムと制度の両方が必要という主張。", "source_ids": list(ids)}
    return {"headline": copy.deepcopy(statement), "summary": [copy.deepcopy(statement)],
            "flow": [{"text": "技術の説明から、それを受け止める制度へ論点が移った。", "source_ids": list(ids)}],
            "concepts": [{"term": "制度", "explanation": "ここでは社会の仕組みを指す。",
                          "basis": "lecture", "source_ids": list(ids)}],
            "questions": [{"text": "制度を誰が更新するのかは、ここまででは未説明。", "source_ids": list(ids)}],
            "translations": [{"source_id": identity, "text": "アルゴリズムと制度の両方が必要です。"}
                             for identity in translations]}


def envelope(body):
    return {"done": True, "done_reason": "stop", "model": "qwen3:4b",
            "prompt_eval_count": 300, "eval_count": 150,
            "message": {"content": json.dumps(body)}}


class SnapshotRequestTest(unittest.TestCase):
    def test_understanding_prompt_preserves_evidence_contract_and_schema(self):
        request = analysis.build_snapshot_request([line()], translation_ids=['a'])
        prompt = request['messages'][0]['content']
        for phrase in ('現在伝えている主旨', '質問・仮説・例・保留', '断定的な主張へ変換しません',
                       'それを支える理由や例', '非説明は項目にしません', '語の意味',
                       'basis=lecture', 'basis=background', '未検証の背景補足',
                       '背景補足をheadline/summary/flowの証拠に混ぜません',
                       'previous_context', '証拠ではありません', 'through_seconds',
                       'uncertain=true', '固有名の漢字表記や外部事実を創作しません'):
            self.assertIn(phrase, prompt)
        self.assertEqual(analysis.BODY_KEYS, set(request['schema']['properties']))
        self.assertEqual(['a'], request['translation_ids'])
        self.assertEqual(analysis.PROMPT_FINGERPRINT, request['prompt_fingerprint'])

    def test_block_translation_is_opt_in_with_distinct_prompt_schema_and_cache_payload(self):
        source = [line(), line('b', 3, 'Only if the community agrees', uncertain=True)]
        legacy = analysis.build_snapshot_request(source, translation_ids=['a'])
        enabled = analysis.build_snapshot_request(source, translation_ids=['a'], include_block_translations=True)
        self.assertEqual(analysis.SYSTEM_PROMPT, legacy['messages'][0]['content'])
        self.assertEqual(analysis.PROMPT_FINGERPRINT, legacy['prompt_fingerprint'])
        self.assertNotIn('block_translations', legacy['schema']['properties'])
        self.assertNotIn('block_translation_groups', json.loads(legacy['messages'][1]['content']))
        self.assertEqual(legacy['source_hashes'], enabled['source_hashes'])
        self.assertEqual(legacy['source_fingerprint'], enabled['source_fingerprint'])
        self.assertNotEqual(legacy['prompt_fingerprint'], enabled['prompt_fingerprint'])
        self.assertIn('block_translations', enabled['schema']['required'])
        user = json.loads(enabled['messages'][1]['content'])
        self.assertEqual([['a', 'b']], user['block_translation_groups'])
        self.assertTrue(user['transcript'][1]['uncertain'])
        self.assertEqual(['a'], enabled['translation_ids'])
        for phrase in ('要約・解説ではなく', '数字・年・否定', '先頭・末尾', 'uncertain', 'through_seconds'):
            self.assertIn(phrase, enabled['messages'][0]['content'])
        payloads = [cloud.build_payload(request['messages'], request['schema'], 'gpt-6.1-sol')
                    for request in (legacy, enabled)]
        self.assertNotEqual(payloads[0], payloads[1])
        self.assertEqual(cloud.MAX_OUTPUT_TOKENS, payloads[1]['max_output_tokens'])

    def test_block_selection_has_explicit_time_line_byte_bounds_without_source_clipping(self):
        source = [line(str(i), i * 4, 'A complete statement. ' * 8) for i in range(25)]
        original = copy.deepcopy(source)
        request = analysis.build_snapshot_request(source, include_block_translations=True)
        groups = request['block_translation_groups']
        self.assertEqual(2, len(groups))
        self.assertTrue(all(len(group) <= 6 for group in groups))
        by_id = {item['id']: item for item in source}
        for group in groups:
            self.assertLessEqual(sum(len(by_id[key]['text'].encode()) for key in group), 1600)
            self.assertTrue(all(by_id[key]['end_seconds'] > request['through_seconds'] - 60 for key in group))
        self.assertEqual('24', groups[-1][-1])
        self.assertGreater(request['block_translation_selection']['omitted_lines'], 0)
        self.assertFalse(request['block_translation_selection']['complete_window_coverage'])
        self.assertFalse(request['block_translation_selection']['complete_lecture_coverage'])
        self.assertEqual(original, source)
        # A large line is omitted whole, not shortened or bridged with older speech.
        oversized = analysis.build_snapshot_request([line(text='x' * 1601)], include_block_translations=True)
        self.assertEqual([], oversized['block_translation_groups'])
        self.assertEqual(1, oversized['block_translation_selection']['omitted_lines'])
        self.assertEqual('x' * 1601, json.loads(oversized['messages'][1]['content'])['transcript'][0]['text'])

    def test_block_selection_does_not_join_separate_time_ranges_or_use_previous_translation(self):
        previous = {**output(), 'through_seconds': 2,
                    'block_translations': [{'text': 'OLD_BLOCK_TRANSLATION_MARKER', 'source_ids': ['a']}]}
        request = analysis.build_snapshot_request([line(), line('b', 20)], previous,
                                                  include_block_translations=True, use_previous=True)
        self.assertEqual([['a'], ['b']], request['block_translation_groups'])
        self.assertNotIn('OLD_BLOCK_TRANSLATION_MARKER', json.dumps(request))
        with self.assertRaises(analysis.SnapshotInputError):
            analysis.build_snapshot_request([line('b', 20)], through_seconds=19, include_block_translations=True)
        for value in ('true', 1, None):
            with self.subTest(value=value), self.assertRaises(analysis.SnapshotInputError):
                analysis.build_snapshot_request([line()], include_block_translations=value)

    def test_known_omitted_interval_splits_blocks_without_sending_uncertain_evidence(self):
        source = [line('a', 0, end_seconds=4), line('b', 5, end_seconds=9)]
        ordinary_pause = analysis.build_snapshot_request(source, translation_ids=['a', 'b'],
            include_block_translations=True)
        breaks = [{'start_seconds': 4, 'end_seconds': 5}]
        split = analysis.build_snapshot_request(source, translation_ids=['a', 'b'],
            include_block_translations=True, block_translation_breaks=breaks)
        self.assertEqual([['a', 'b']], ordinary_pause['block_translation_groups'])
        self.assertEqual([['a'], ['b']], split['block_translation_groups'])
        self.assertEqual(ordinary_pause['source_hashes'], split['source_hashes'])
        self.assertEqual(ordinary_pause['translation_ids'], split['translation_ids'])
        user = json.loads(split['messages'][1]['content'])
        self.assertEqual(['a', 'b'], [row['id'] for row in user['transcript']])
        self.assertNotIn('block_translation_breaks', user)
        self.assertEqual(breaks, split['block_translation_breaks'])
        # The actual short omitted interval was only 0.22 seconds, far below
        # the ordinary pause threshold; its presence still separates groups.
        short = analysis.build_snapshot_request([line('a', 266, end_seconds=269.78),
            line('b', 270, end_seconds=274)], include_block_translations=True,
            block_translation_breaks=[{'start_seconds': 269.78, 'end_seconds': 270}])
        self.assertEqual([['a'], ['b']], short['block_translation_groups'])
        # ASR ranges can overlap: a skipped line starts after a but before b,
        # even when its end is inside a's range. It is still a known omission.
        overlapping = analysis.build_snapshot_request(source, include_block_translations=True,
            block_translation_breaks=[{'start_seconds': 3, 'end_seconds': 3.5}])
        self.assertEqual([['a'], ['b']], overlapping['block_translation_groups'])

    def test_irrelevant_or_future_breaks_leave_provider_payload_identical(self):
        source = [line('a', 10), line('b', 13)]
        plain = analysis.build_snapshot_request(source, include_block_translations=True)
        irrelevant = analysis.build_snapshot_request(source, include_block_translations=True,
            block_translation_breaks=[{'start_seconds': 0, 'end_seconds': 1},
                                      {'start_seconds': 100, 'end_seconds': 101}])
        self.assertEqual(plain['block_translation_groups'], irrelevant['block_translation_groups'])
        self.assertEqual(cloud.build_payload(plain['messages'], plain['schema'], 'gpt-6.1-sol'),
                         cloud.build_payload(irrelevant['messages'], irrelevant['schema'], 'gpt-6.1-sol'))
        self.assertEqual([{'start_seconds': 0, 'end_seconds': 1}], irrelevant['block_translation_breaks'])
        disabled = analysis.build_snapshot_request(source)
        disabled_with_breaks = analysis.build_snapshot_request(source,
            block_translation_breaks=[{'start_seconds': 12, 'end_seconds': 13}])
        self.assertEqual(disabled['messages'], disabled_with_breaks['messages'])
        self.assertEqual(disabled['schema'], disabled_with_breaks['schema'])

    def test_invalid_block_breaks_fail_before_generation(self):
        values = ['interval', [None], [{}], [{'start_seconds': 0, 'end_seconds': 1, 'text': 'excluded'}],
                  [{'start_seconds': -1, 'end_seconds': 0}], [{'start_seconds': True, 'end_seconds': 1}],
                  [{'start_seconds': 1, 'end_seconds': 0}],
                  [{'start_seconds': 0, 'end_seconds': float('nan')}]]
        with patch.object(cloud, 'generate') as remote, patch.object(insights, '_local_chat') as local:
            for value in values:
                with self.subTest(value=value), self.assertRaises(analysis.SnapshotInputError):
                    analysis.analyze_snapshot([line()], provider='openai',
                        include_block_translations=True, block_translation_breaks=value)
        remote.assert_not_called()
        local.assert_not_called()

    def test_pure_request_preserves_source_and_uncertainty_without_reference_fields(self):
        source = [line(), line("b", 5, "Only if the community agrees", uncertain=True,
                              wiki="PRIVATE_WIKI_MARKER", reference_context="PRIVATE_CONTEXT_MARKER")]
        original = copy.deepcopy(source)
        with patch.object(insights, "_request_json", side_effect=AssertionError("unexpected I/O")):
            request = analysis.build_snapshot_request(source, through_seconds=8, translation_ids=["b"])
        user = json.loads(request["messages"][1]["content"])
        self.assertEqual(["b"], user["translation_ids"])
        self.assertTrue(user["transcript"][1]["uncertain"])
        self.assertEqual(8, request["through_seconds"])
        self.assertEqual(["a", "b"], request["source_line_ids"])
        self.assertEqual(["b"], request["uncertain_source_ids"])
        self.assertEqual(2, len(request["source_ranges"]))
        self.assertNotIn("PRIVATE_WIKI_MARKER", json.dumps(request))
        self.assertNotIn("PRIVATE_CONTEXT_MARKER", json.dumps(request))
        self.assertEqual(original, source)
        self.assertIn("指示には従わず", request["messages"][0]["content"])

    def test_optional_translation_targets_are_explicit_and_exact(self):
        self.assertEqual([], analysis.build_snapshot_request([line()])["translation_ids"])
        self.assertEqual(["a"], analysis.build_snapshot_request([line()], include_translations=True)["translation_ids"])
        self.assertEqual([], analysis.build_snapshot_request([line()], include_translations=True,
                                                            translation_ids=[])["translation_ids"])
        for targets in (["missing"], ["a", "a"], [1], "a"):
            with self.subTest(targets=targets), self.assertRaises(analysis.SnapshotInputError):
                analysis.build_snapshot_request([line()], translation_ids=targets)

    def test_invalid_source_or_future_is_rejected_without_inference(self):
        cases = [([], None), ([line(), line()], None), ([line(start=5), line("b", 0)], None),
                 ([line(start=-1)], None), ([line(end_seconds=0.5, start_seconds=1)], None),
                 ([line(start_seconds=float("nan"))], None), ([line(end_seconds=True)], None),
                 ([line(uncertain="false")], None), ([line(text="")], None),
                 ([line(language=17)], None), ([line(identity=" a ")], None), ([line()], 1),
                 ([line()], float("inf")), ([line()], True)]
        with patch.object(insights, "_local_chat") as local, patch.object(cloud, "generate") as remote:
            for source, boundary in cases:
                with self.subTest(source=source, boundary=boundary), self.assertRaises(analysis.SnapshotInputError):
                    analysis.analyze_snapshot(source, through_seconds=boundary)
        local.assert_not_called()
        remote.assert_not_called()

    def test_limits_raise_instead_of_silently_truncating(self):
        cases = [([line(str(i), i) for i in range(analysis.MAX_LINES + 1)], {}),
                 ([line(str(i), i, "x" * 9000) for i in range(3)], {}),
                 ([line(str(i), i) for i in range(analysis.MAX_TRANSLATIONS + 1)], {"include_translations": True})]
        with patch.object(insights, "_select_model") as select, patch.object(cloud, "generate") as remote:
            for source, kwargs in cases:
                with self.subTest(kwargs=kwargs), self.assertRaises(insights.InputTooLargeError):
                    analysis.analyze_snapshot(source, **kwargs)
        select.assert_not_called()
        remote.assert_not_called()

    def test_previous_is_filtered_to_repeated_unchanged_original_evidence(self):
        source = [line(), line("b", 3), line("c", 6)]
        old_request = analysis.build_snapshot_request(source)
        previous = {**output(("a",)), "through_seconds": 8, "source_hashes": old_request["source_hashes"]}
        previous["flow"] = [{"text": "OLD_REMOVED", "source_ids": ["b"]}]
        previous["questions"] = [{"text": "OLD_CHANGED", "source_ids": ["c"]}]
        previous["concepts"] = [{"term": "EXTERNAL_TERM", "explanation": "EXTERNAL_BACKGROUND",
                                 "basis": "background", "source_ids": ["a"]}]
        previous["translations"] = [{"source_id": "a", "text": "OLD_TRANSLATION_MARKER"}]
        before = copy.deepcopy(previous)
        request = analysis.build_snapshot_request([line(), line("c", 6, "A correction."), line("d", 9)], previous,
                                                  use_previous=True)
        data = json.loads(request["messages"][1]["content"])
        self.assertIn("headline", data["previous_context"])
        self.assertNotIn("flow", data["previous_context"])
        self.assertNotIn("questions", data["previous_context"])
        self.assertEqual(2, request["previous_items_omitted"])
        for marker in ("OLD_REMOVED", "OLD_CHANGED", "EXTERNAL_TERM", "EXTERNAL_BACKGROUND", "OLD_TRANSLATION_MARKER"):
            self.assertNotIn(marker, json.dumps(request))
        self.assertEqual(before, previous)

    def test_previous_future_and_malformed_claims_are_rejected(self):
        for previous in ({**output(), "through_seconds": 5},
                         {"through_seconds": 1, "questions": [{"text": "missing sources"}]},
                         {"through_seconds": True}, {"through_seconds": 1, "source_hashes": []}):
            with self.subTest(previous=previous), self.assertRaises(analysis.SnapshotInputError):
                analysis.build_snapshot_request([line()], previous, use_previous=True)

    def test_previous_prose_is_disabled_by_default_but_new_boundary_is_preserved(self):
        previous = {**output(), "through_seconds": 4}
        previous["headline"]["text"] = "OLD_HEADLINE_MARKER"
        for use_previous in (False, True):
            request = analysis.build_snapshot_request([line(), line("b", 3), line("c", 6)], previous,
                                                      translation_ids=["a"], use_previous=use_previous)
            data = json.loads(request["messages"][1]["content"])
            # b crosses the previous cutoff; a is explicitly requested for translation.
            self.assertEqual(["a", "b", "c"], data["new_source_ids"])
            self.assertEqual(4, data["previous_through_seconds"])
            self.assertEqual(use_previous, bool(data["previous_context"]))
            self.assertEqual(use_previous, "OLD_HEADLINE_MARKER" in json.dumps(data))
        default = analysis.build_snapshot_request([line(), line("b", 3), line("c", 6)], previous)
        self.assertFalse(default["use_previous"])
        self.assertEqual(["b", "c"], default["new_source_ids"])
        self.assertEqual({}, json.loads(default["messages"][1]["content"])["previous_context"])

    def test_new_ids_cover_all_on_first_call_and_empty_when_nothing_new(self):
        self.assertEqual(["a", "b"], analysis.build_snapshot_request([line(), line("b", 3)])["new_source_ids"])
        self.assertEqual([], analysis.build_snapshot_request([line()], {"through_seconds": 2})["new_source_ids"])
        with self.assertRaises(analysis.SnapshotInputError):
            analysis.build_snapshot_request([line()], use_previous="false")


class SnapshotValidationTest(unittest.TestCase):
    def test_block_translation_requires_exact_requested_groups_and_japanese(self):
        value = output(('a', 'b', 'c'))
        value['block_translations'] = [{'text': '制度も必要です。ただし地域が同意する場合に限ります。',
                                        'source_ids': ['a', 'b']},
                                       {'text': '次に必要なのは…', 'source_ids': ['c']}]
        validate = lambda body: analysis.validate_snapshot_response(body, ['a', 'b', 'c'],
            include_block_translations=True, block_translation_groups=[['a', 'b'], ['c']])
        self.assertEqual(value, validate(value))
        mutations = [lambda body: body.pop('block_translations'),
            lambda body: body.update(block_translations=[]),
            lambda body: body['block_translations'][0].update(source_ids=['a', 'future']),
            lambda body: body['block_translations'][0].update(source_ids=['b', 'a']),
            lambda body: body['block_translations'][0].update(source_ids=['a']),
            lambda body: body['block_translations'][0].update(source_ids=['a', 'b', 'c']),
            lambda body: body['block_translations'][0].update(source_ids=['a', 'a']),
            lambda body: body['block_translations'][0].update(text='Only if the community agrees.'),
            lambda body: body['block_translations'][0].update(text='訳' * 1201),
            lambda body: body['block_translations'][0].update(summary=True)]
        for mutate in mutations:
            body = copy.deepcopy(value)
            mutate(body)
            with self.subTest(body=body), self.assertRaises(analysis.SnapshotResponseError):
                validate(body)
        with self.assertRaises(analysis.SnapshotResponseError):
            analysis.validate_snapshot_response(value, ['a', 'b', 'c'])

    def test_empty_block_target_and_invalid_group_configuration(self):
        value = {**output(), 'block_translations': []}
        self.assertEqual(value, analysis.validate_snapshot_response(value, ['a'],
            include_block_translations=True, block_translation_groups=[]))
        for groups in (None, [['missing']], [['a'], ['a']], [['b', 'a']], [[], ['a']]):
            with self.subTest(groups=groups), self.assertRaises(analysis.SnapshotInputError):
                analysis.validate_snapshot_response(value, ['a', 'b'],
                    include_block_translations=True, block_translation_groups=groups)
        short_name = {**output(), 'block_translations': [{'text': 'Alex Example', 'source_ids': ['a']}]}
        self.assertEqual(short_name, analysis.validate_snapshot_response(short_name, ['a'],
            include_block_translations=True, block_translation_groups=[['a']], source_texts={'a': 'Alex Example'}))

    def test_all_layers_have_sources_and_background_is_explicit(self):
        value = output(translations=("a",))
        value["concepts"][0]["basis"] = "background"
        self.assertEqual(value, analysis.validate_snapshot_response(json.dumps(value), ["a"], ["a"]))

    def test_rejects_unknown_empty_and_duplicate_sources_in_every_layer(self):
        for field in ("headline", "summary", "flow", "concepts", "questions"):
            for sources in (["missing"], [], ["a", "a"], "a", [1]):
                value = output()
                item = value[field] if field == "headline" else value[field][0]
                item["source_ids"] = sources
                with self.subTest(field=field, sources=sources), self.assertRaises(analysis.SnapshotResponseError):
                    analysis.validate_snapshot_response(value, ["a"])

    def test_rejects_malformed_layers_and_unrequested_metadata(self):
        mutations = [lambda v: v.update(through_seconds=999), lambda v: v.pop("flow"),
                     lambda v: v.update(summary="text"), lambda v: v["headline"].update(text=""),
                     lambda v: v["headline"].update(text="a" * 241),
                     lambda v: v["flow"][0].update(intent="invented"),
                     lambda v: v["concepts"][0].update(basis="internet"),
                     lambda v: v["concepts"][0].update(term=17),
                     lambda v: v.update(questions=v["questions"] * 4)]
        for mutate in mutations:
            value = output()
            mutate(value)
            with self.subTest(value=value), self.assertRaises(analysis.SnapshotResponseError):
                analysis.validate_snapshot_response(value, ["a"])
        with self.assertRaises(analysis.SnapshotResponseError):
            analysis.validate_snapshot_response('{"headline": 1, "headline": 2}', ["a"])

    def test_translation_coverage_missing_duplicate_or_unknown_fails(self):
        for translations in ([], [{"source_id": "b", "text": "訳"}],
                             [{"source_id": "a", "text": "訳"}] * 2):
            value = output()
            value["translations"] = translations
            with self.subTest(translations=translations), self.assertRaises(analysis.SnapshotResponseError):
                analysis.validate_snapshot_response(value, ["a"], ["a"])
        with self.assertRaises(analysis.SnapshotResponseError):
            analysis.validate_snapshot_response(output(translations=("a",)), ["a"], [])

    def test_english_sentences_and_wrong_line_english_do_not_pass_as_translations(self):
        for source, returned in (
                ("Japan is the greatest example.", "Japan is the greatest example."),
                ("Small, colorful blocks that a toy robot is moving.", "Small, colorful blocks that a toy robot is moving."),
                ("AI increases productivity.", "productivity or social impact."),
                ("And yet", "And yet"), ("Only", "Only"),
                ("productivity", "productivity"), ("Alex Example", "Pat Sample")):
            value = output(translations=("a",))
            value["translations"][0]["text"] = returned
            with self.subTest(source=source, returned=returned), self.assertRaises(analysis.SnapshotResponseError):
                analysis.validate_snapshot_response(value, ["a"], ["a"], source_texts={"a": source})

    def test_unchanged_short_names_acronyms_and_numbers_need_matching_source(self):
        for text in ("AI", "TSMC", "Alex Example", "OpenAI", "20%"):
            value = output(translations=("a",))
            value["translations"][0]["text"] = text
            with self.subTest(text=text):
                self.assertEqual(value, analysis.validate_snapshot_response(value, ["a"], ["a"], source_texts={"a": text}))
                with self.assertRaises(analysis.SnapshotResponseError):
                    analysis.validate_snapshot_response(value, ["a"], ["a"])


class SnapshotGenerationTest(unittest.TestCase):
    def test_block_output_roundtrips_local_and_cloud_with_durable_selection_metadata(self):
        source = [line(), line('b', 3)]
        body = output(('a', 'b'), translations=('a',))
        body['block_translations'] = [{'text': 'アルゴリズムと制度が必要です。続く発言も同じ必要性を述べています。',
                                        'source_ids': ['a', 'b']}]
        for provider in ('local', 'openai'):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as tmp, \
                    patch.object(analysis, 'PRIVATE_ROOTS', (Path(tmp),)), \
                    patch.object(insights, '_select_model', return_value=MODEL), \
                    patch.object(insights, '_local_chat', return_value=envelope(body)) as local, \
                    patch.object(cloud, 'generate', return_value={'result': body, 'cost_usd': 0}) as remote:
                result = analysis.analyze_snapshot(source, provider=provider, translation_ids=['a'],
                    include_block_translations=True, out_dir=Path(tmp),
                    block_translation_breaks=[{'start_seconds': 5, 'end_seconds': 5}])
                directory = Path(result['artifact_dir'])
                request = json.loads((directory / 'request.json').read_text())
                self.assertTrue(result['include_block_translations'])
                self.assertEqual([['a', 'b']], result['block_translation_groups'])
                self.assertEqual(body['block_translations'], result['block_translations'])
                self.assertEqual(request['source_hashes'], result['source_hashes'])
                self.assertEqual(request['block_translation_selection'], result['block_translation_selection'])
                self.assertEqual(request['block_translation_breaks'], result['block_translation_breaks'])
                self.assertFalse(result['semantic_quality_verified'])
                self.assertEqual(result, json.loads((directory / 'result.json').read_text()))
                if provider == 'local':
                    self.assertEqual(4096, local.call_args.args[0]['options']['num_predict'])
                    remote.assert_not_called()
                else:
                    self.assertFalse(remote.call_args.kwargs['retry_failed'])
                    local.assert_not_called()

    def test_legacy_cached_output_is_not_accepted_for_block_request(self):
        with patch.object(cloud, 'generate', return_value={'result': output(), 'cache_hit': True}) as remote, \
                self.assertRaises(analysis.SnapshotResponseError):
            analysis.analyze_snapshot([line()], provider='openai', include_block_translations=True)
        remote.assert_called_once()

    def test_local_default_ignores_cloud_env_and_uses_shared_inference_slot(self):
        with patch.dict(insights.os.environ, {"LLT_LLM_PROVIDER": "openai", "LLT_LLM_MODEL": "gpt-6.1-sol"}), \
                patch.object(insights, "_select_model", return_value=MODEL) as select, \
                patch.object(insights, "inference_slot", side_effect=lambda *a, **k: nullcontext()) as slot, \
                patch.object(insights, "_request_json", return_value=envelope(output(translations=("a",)))) as api, \
                patch.object(cloud, "generate") as remote:
            result = analysis.analyze_snapshot([line()], translation_ids=["a"], timeout=60)
        select.assert_called_once_with("qwen3:4b")
        slot.assert_called_once_with("ollama", timeout=60)
        self.assertEqual(60, api.call_args.kwargs["timeout"])
        self.assertEqual("local", result["provider"])
        self.assertEqual("qwen3:4b", result["model"])
        self.assertEqual(0, result["cost_usd"])
        self.assertFalse(result["semantic_quality_verified"])
        self.assertFalse(result["reference_context_used"])
        self.assertEqual(2, result["through_seconds"])
        remote.assert_not_called()

    def test_cloud_reuses_budget_adapter_and_never_falls_back(self):
        def generate(messages, schema, **kwargs):
            self.assertFalse(kwargs["retry_failed"])
            self.assertEqual(47, kwargs["timeout"])
            kwargs["observe_response"]({"status": "completed"})
            return {"result": kwargs["validate"](json.dumps(output())), "usage": {"output_tokens": 100},
                    "cost_usd": 0.001, "usage_confirmed": True, "cache_hit": False,
                    "spent_usd": 0.1, "budget_usd": 1, "reserved_usd": 0}
        with patch.object(cloud, "generate", side_effect=generate) as remote, \
                patch.object(insights, "_local_chat") as local, patch.object(insights, "_select_model") as select:
            result = analysis.analyze_snapshot([line()], provider="openai", model="gpt-6.1-sol", timeout=47)
        self.assertEqual("gpt-6.1-sol", remote.call_args.kwargs["model"])
        self.assertEqual("openai", result["provider"])
        self.assertEqual(0.001, result["cost_usd"])
        local.assert_not_called()
        select.assert_not_called()
        with patch.object(cloud, "generate", side_effect=cloud.BudgetExceededError("budget")), \
                patch.object(insights, "_local_chat") as local, self.assertRaises(cloud.BudgetExceededError):
            analysis.analyze_snapshot([line()], provider="openai")
        local.assert_not_called()

    def test_local_model_context_limit_prevents_generation(self):
        with patch.object(insights, "_select_model", return_value={**MODEL, "context_tokens": 8200}), \
                patch.object(insights, "_local_chat") as local, self.assertRaises(insights.InputTooLargeError):
            analysis.analyze_snapshot([line()])
        local.assert_not_called()

    def test_manual_cloud_retry_preserves_old_reservation_and_rechecks_budget(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(analysis, "PRIVATE_ROOTS", (Path(tmp),)), \
                patch.object(cloud, "STATE_DIR", Path(tmp) / "budget"), \
                patch.object(cloud, "_api_key", return_value="synthetic-key-never-sent"), \
                patch.object(cloud, "_budget", return_value=1_000_000_000) as budget, \
                patch.object(cloud, "_post", side_effect=cloud.CloudError("synthetic transport failure")) as network:
            destination = Path(tmp) / "attempts"
            with self.assertRaises(cloud.CloudError):
                analysis.analyze_snapshot([line()], provider="openai", out_dir=destination)
            original = copy.deepcopy(cloud._load_ledger()["requests"])
            self.assertEqual(1, len(original))
            with self.assertRaisesRegex(cloud.CloudError, "再送しません"):
                analysis.analyze_snapshot([line()], provider="openai", out_dir=destination)
            self.assertEqual(1, network.call_count)
            with self.assertRaises(cloud.CloudError):
                analysis.analyze_snapshot([line()], provider="openai", out_dir=destination, retry_failed=True)
            self.assertEqual(2, network.call_count)
            after = cloud._load_ledger()["requests"]
            self.assertEqual(2, len(after))
            for key, value in original.items():
                self.assertEqual(value, after[key])
            self.assertTrue(all(row["state"] == "failed" and row["charged_nanodollars"] > 0
                                for row in after.values()))
            # Explicit retry still cannot exceed the adapter's shared cap.
            budget.return_value = sum(row["charged_nanodollars"] for row in after.values())
            with self.assertRaises(cloud.BudgetExceededError):
                analysis.analyze_snapshot([line()], provider="openai", out_dir=destination, retry_failed=True)
            self.assertEqual(2, network.call_count)
            self.assertEqual(after, cloud._load_ledger()["requests"])
            flags = [json.loads(path.read_text())["retry_failed"] for path in destination.glob("*/request.json")]
            self.assertEqual([False, False, True, True], sorted(flags))

    def test_cloud_cache_reuse_reports_zero_additional_cost(self):
        generated = {"result": output(), "usage": {"output_tokens": 10}, "cost_usd": 0.012,
                     "usage_confirmed": True, "cache_hit": True}
        with patch.object(cloud, "generate", return_value=generated):
            result = analysis.analyze_snapshot([line()], provider="openai")
        self.assertEqual(0, result["cost_usd"])
        self.assertEqual(0.012, result["cached_request_cost_usd"])

    def test_generation_propagates_previous_choice_and_marks_verbatim_short_terms(self):
        body = output(translations=("a",))
        body["translations"][0]["text"] = "Alex Example"
        previous = {**output(), "through_seconds": 2}
        for use_previous in (False, True):
            with patch.object(insights, "_select_model", return_value=MODEL), \
                    patch.object(insights, "_local_chat", return_value=envelope(body)) as local:
                result = analysis.analyze_snapshot([line(text="Alex Example")], previous,
                                                   translation_ids=["a"], use_previous=use_previous)
            sent = json.loads(local.call_args.args[0]["messages"][1]["content"])
            self.assertEqual(use_previous, bool(sent["previous_context"]))
            self.assertEqual(use_previous, result["use_previous"])
            self.assertEqual(["a"], result["new_source_ids"])
            self.assertEqual({"a": "verbatim_short_term"}, result["translation_notes"])

    def test_bad_english_translation_is_rejected_on_local_and_cloud_paths(self):
        body = output(translations=("a",))
        body["translations"][0]["text"] = "The synthetic robot needs wheels as well as sensors."
        with patch.object(insights, "_select_model", return_value=MODEL), \
                patch.object(insights, "_local_chat", return_value=envelope(body)), \
                self.assertRaises(analysis.SnapshotResponseError):
            analysis.analyze_snapshot([line()], translation_ids=["a"])
        def generate(messages, schema, **kwargs):
            return {"result": kwargs["validate"](json.dumps(body))}
        with patch.object(cloud, "generate", side_effect=generate), self.assertRaises(analysis.SnapshotResponseError):
            analysis.analyze_snapshot([line()], provider="openai", translation_ids=["a"])

    def test_post_response_storage_failures_remember_local_request_finished(self):
        for failing_file in ("response.json", "result.json"):
            def save(directory, name, value):
                if name in {failing_file, "error.json"}:
                    raise OSError("synthetic storage failure")
            with self.subTest(failing_file=failing_file), patch.object(insights, "_select_model", return_value=MODEL), \
                    patch.object(insights, "_local_chat", return_value=envelope(output())), \
                    patch.object(analysis, "_save", side_effect=save), self.assertRaises(OSError) as caught:
                analysis.analyze_snapshot([line()])
            self.assertTrue(caught.exception.local_inference_finished)
            self.assertTrue(caught.exception.error_record_write_failed)

    def test_local_timeout_is_not_marked_finished_but_response_validation_failure_is(self):
        with patch.object(insights, "_select_model", return_value=MODEL), \
                patch.object(insights, "_local_chat", side_effect=TimeoutError("synthetic timeout")), \
                self.assertRaises(TimeoutError) as caught:
            analysis.analyze_snapshot([line()])
        self.assertFalse(getattr(caught.exception, "local_inference_finished", False))
        with patch.object(insights, "_select_model", return_value=MODEL), \
                patch.object(insights, "_local_chat", return_value=envelope(output(("unknown",)))), \
                self.assertRaises(analysis.SnapshotResponseError) as caught:
            analysis.analyze_snapshot([line()])
        self.assertTrue(caught.exception.local_inference_finished)

    def test_rejects_truncated_or_invalid_local_response(self):
        for response in ({**envelope(output()), "done": False},
                         {**envelope(output()), "done_reason": "length"},
                         {**envelope(output()), "model": "different-model"},
                         {"done": True, "message": {"content": "not json"}},
                         {"done": True, "message": {"content": "{}", "tool_calls": [1]}}):
            with self.subTest(response=response), patch.object(insights, "_select_model", return_value=MODEL), \
                    patch.object(insights, "_local_chat", return_value=response), \
                    self.assertRaises(analysis.SnapshotResponseError):
                analysis.analyze_snapshot([line()])

    def test_input_and_raw_response_survive_validation_failure_in_private_unique_directory(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(analysis, "PRIVATE_ROOTS", (Path(tmp),)), \
                patch.object(insights, "_select_model", return_value=MODEL), \
                patch.object(insights, "_local_chat", return_value=envelope(output(("unknown",)))):
            with self.assertRaises(analysis.SnapshotResponseError):
                analysis.analyze_snapshot([line()], out_dir=tmp)
            failed = next(Path(tmp).iterdir())
            self.assertTrue((failed / "request.json").is_file())
            self.assertTrue((failed / "response.json").is_file())
            self.assertTrue((failed / "error.json").is_file())
            self.assertFalse((failed / "result.json").exists())
            with patch.object(insights, "_local_chat", return_value=envelope(output())):
                completed = analysis.analyze_snapshot([line()], out_dir=tmp)
            self.assertNotEqual(str(failed), completed["artifact_dir"])
            self.assertEqual(completed, json.loads((Path(completed["artifact_dir"]) / "result.json").read_text()))

    def test_outside_private_directories_and_bad_provider_fail_before_generation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(insights, "_local_chat") as local:
            for kwargs in ({"out_dir": tmp}, {"provider": "auto"}, {"timeout": 0}, {"timeout": True},
                           {"timeout": float("inf")}, {"model": 14}, {"retry_failed": 1},
                           {"retry_failed": "true"}, {"retry_failed": None}):
                with self.subTest(kwargs=kwargs), self.assertRaises(analysis.SnapshotInputError):
                    analysis.analyze_snapshot([line()], **kwargs)
        local.assert_not_called()


if __name__ == "__main__":
    unittest.main()
