"""CPU-only FIFO and adapter tests; no credentials, microphone, or inference."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lecture_translation as translation
import event_insights_cloud as cloud


def line(index, text=None, **changes):
    row = {"id": f"s{index}", "start_seconds": index * 3.0, "end_seconds": index * 3.0 + 2.5,
           "text": text if text is not None else f"This is the source sentence number {index}.",
           "language": "en", "uncertain": False}
    return {**row, **changes}


def response(plan, text="これは原文に対応する日本語訳です。"):
    return {"blocks": [{"text": text, "source_ids": list(group)} for group in plan["groups"]]}


def resign(plan):
    """Model a malformed externally supplied plan, not just a stale checksum."""
    plan["source_hashes"] = {row["id"]: translation._hash(row) for row in plan["source_lines"]}
    plan["source_fingerprint"] = translation._hash(plan["source_lines"])
    plan["plan_fingerprint"] = translation._hash(translation._core(plan))
    return plan


class PlannerTests(unittest.TestCase):
    def test_fifo_backlog_eventually_covers_every_eligible_line_once(self):
        rows = [line(index) for index in range(103)]
        rows[18]["uncertain"] = True
        rows[34]["language"] = "ja"
        rows[51]["text"] = " "
        expected = [row["id"] for row in rows if translation._eligible({**row, "text": row["text"].strip()})]
        covered, actual = set(), []
        while True:
            plan = translation.plan_translation(rows, covered, flush=True)
            if plan is None:
                break
            self.assertTrue(set(plan["target_source_ids"]).isdisjoint(covered))
            self.assertEqual(plan["target_source_ids"], expected[len(actual):len(actual) + len(plan["target_source_ids"])])
            self.assertLessEqual(len(plan["groups"]), 3)
            self.assertLessEqual(plan["selection"]["selected_target_bytes"], 6000)
            actual.extend(plan["target_source_ids"])
            covered.update(plan["target_source_ids"])
        self.assertEqual(actual, expected)
        self.assertEqual(actual[0], "s0")  # Old speech outside any recent window remains pending.

    def test_filler_japanese_empty_and_covered_rows_split_groups(self):
        for change, covered in [({"text": "um", "uncertain": True}, set()), ({"language": "ja"}, set()),
                                ({"text": ""}, set()), ({}, {"s1"})]:
            with self.subTest(change=change, covered=covered):
                rows = [line(0), line(1, **change), line(2)]
                plan = translation.plan_translation(rows, covered, flush=True)
                self.assertEqual(plan["groups"], [["s0"], ["s2"]])

    def test_overlap_filler_line_is_a_boundary_even_without_silence(self):
        rows = [line(0, end_seconds=4), line(1, "uh", start_seconds=2, end_seconds=3, uncertain=True),
                line(2, start_seconds=3, end_seconds=5)]
        self.assertEqual(translation.plan_translation(rows, set(), flush=True)["groups"], [["s0"], ["s2"]])

    def test_large_gap_splits_and_45_second_group_limit_holds(self):
        rows = [line(0, start_seconds=0, end_seconds=30), line(1, start_seconds=30, end_seconds=50),
                line(2, start_seconds=56, end_seconds=57)]
        plan = translation.plan_translation(rows, set(), flush=True)
        self.assertEqual(plan["groups"], [["s0"], ["s1"], ["s2"]])

    def test_partial_group_waits_without_flush_but_barrier_closes_it(self):
        self.assertIsNone(translation.plan_translation([line(0)], set()))
        self.assertEqual(translation.plan_translation([line(0)], set(), flush=True)["groups"], [["s0"]])
        rows = [line(0), line(1, "erm", uncertain=True), line(2)]
        self.assertEqual(translation.plan_translation(rows, set())["groups"], [["s0"]])
        self.assertEqual(translation.plan_translation([line(i) for i in range(8)], set())["target_source_ids"],
                         [f"s{i}" for i in range(8)])

    def test_oversized_oldest_is_explicit_and_never_skipped(self):
        for row in [line(0, "x" * 2001), line(0, end_seconds=45.01)]:
            with self.subTest(row=row["end_seconds"]):
                with self.assertRaises(translation.OversizedSourceError) as failure:
                    translation.plan_translation([row, line(1, start_seconds=50, end_seconds=52)], set(), flush=True)
                self.assertEqual(failure.exception.source_id, "s0")
        rows = [line(0), line(1, "x" * 2001), line(2)]
        first = translation.plan_translation(rows, set(), flush=True)
        self.assertEqual(first["target_source_ids"], ["s0"])
        self.assertEqual(first["selection"]["blocked_next_source_id"], "s1")
        with self.assertRaises(translation.OversizedSourceError):
            translation.plan_translation(rows, {"s0"}, flush=True)

    def test_utf8_byte_boundaries_are_not_character_limits(self):
        rows = [line(0, "é" * 500), line(1, "é" * 500), line(2, "é" * 500)]
        plan = translation.plan_translation(rows, set(), flush=True)
        self.assertEqual(plan["groups"], [["s0", "s1"], ["s2"]])
        self.assertEqual(plan["selection"]["selected_target_bytes"], 3000)
        exact = translation.plan_translation([line(0, "x" * 2000, end_seconds=45)], set())
        self.assertEqual(exact["groups"], [["s0"]])

    def test_context_is_original_bounded_and_not_coverage(self):
        rows = [line(i, translation_ja="DO NOT COPY", private_note="SECRET") for i in range(60)]
        plan = translation.plan_translation(rows, {row["id"] for row in rows if row["id"] != "s20"}, flush=True)
        self.assertEqual(plan["target_source_ids"], ["s20"])
        self.assertEqual(plan["through_seconds"], rows[-1]["end_seconds"])
        self.assertEqual(plan["target_through_seconds"], rows[20]["end_seconds"])
        for row in plan["source_lines"]:
            self.assertGreaterEqual(row["start_seconds"], rows[20]["start_seconds"] - 45)
            self.assertLessEqual(row["end_seconds"], rows[20]["end_seconds"] + 45)
        request = translation.build_translation_request(plan)
        payload = json.dumps(request["messages"])
        self.assertNotIn("DO NOT COPY", payload)
        self.assertNotIn("SECRET", payload)
        self.assertNotIn("s59", payload)
        self.assertEqual(plan["source_hashes"], {row["id"]: translation._hash(row) for row in plan["source_lines"]})

    def test_dense_context_omission_is_explicit_targets_intact(self):
        rows = [line(i, "A" * 900, start_seconds=i * 0.1, end_seconds=i * 0.1 + 0.1) for i in range(200)]
        covered = {row["id"] for row in rows[1:]}
        plan = translation.plan_translation(rows, covered, flush=True)
        self.assertEqual(plan["target_source_ids"], ["s0"])
        self.assertGreater(len(plan["selection"]["context_omitted_source_ids"]), 0)
        self.assertEqual(plan["selection"]["context_available_lines"],
                         plan["selection"]["context_selected_lines"] + len(plan["selection"]["context_omitted_source_ids"]))
        self.assertLessEqual(translation.build_translation_request(plan)["input_bytes"], translation.MAX_INPUT_BYTES)

    def test_empty_and_already_covered_have_no_plan(self):
        self.assertIsNone(translation.plan_translation([], set(), flush=True))
        self.assertIsNone(translation.plan_translation([line(0)], {"s0"}, flush=True))
        self.assertIsNone(translation.plan_translation([line(0, "um", uncertain=True), line(1, language="ja")], set(), flush=True))

    def test_bad_input_types_order_ids_times_and_limits_fail_explicitly(self):
        bad_inputs = [[line(0), line(0)], [line(1), line(0)], [line(0, start_seconds=True)],
                      [line(0, end_seconds=float("nan"))], [line(0, end_seconds=-1)],
                      [line(0, uncertain="false")], [line(0, text=None)], [line(0, language=42)],
                      [line(0, id=" s0")], ["line"]]
        # line(None) means default text in this helper; set it explicitly.
        bad_inputs[6][0]["text"] = None
        for rows in bad_inputs:
            with self.subTest(rows=rows), self.assertRaises(translation.TranslationInputError):
                translation.plan_translation(rows, set(), flush=True)
        for coverage in ["s0", {"missing"}, {12}]:
            with self.subTest(coverage=coverage), self.assertRaises(translation.TranslationInputError):
                translation.plan_translation([line(0)], coverage, flush=True)
        with self.assertRaises(translation.TranslationInputError):
            translation.plan_translation([line(0)], set(), flush=1)
        with mock.patch.object(translation, "MAX_TRANSCRIPT_LINES", 1), self.assertRaises(translation.TranslationInputError):
            translation.plan_translation([line(0), line(1)], set(), flush=True)
        with mock.patch.object(translation, "MAX_TRANSCRIPT_BYTES", 1), self.assertRaises(translation.TranslationInputError):
            translation.plan_translation([line(0)], set(), flush=True)

    def test_plan_changes_and_resigned_invalid_groups_are_rejected(self):
        original = translation.plan_translation([line(0), line(1), line(2, "uh", uncertain=True), line(3)], set(), flush=True)
        mutated = copy.deepcopy(original)
        mutated["source_lines"][0]["text"] += " changed"
        with self.assertRaises(translation.TranslationInputError):
            translation.build_translation_request(mutated)
        for changes in [{"groups": [["s0", "s3"]], "target_source_ids": ["s0", "s3"]},
                        {"groups": [["s1", "s0"], ["s3"]], "target_source_ids": ["s1", "s0", "s3"]},
                        {"groups": [["s0", "s2"]], "target_source_ids": ["s0", "s2"]},
                        {"through_seconds": 1}, {"target_through_seconds": 0}]:
            with self.subTest(changes=changes), self.assertRaises(translation.TranslationInputError):
                translation.build_translation_request(resign({**copy.deepcopy(original), **changes}))

    def test_planning_and_building_are_pure(self):
        with mock.patch.object(cloud, "_api_key", side_effect=AssertionError("key read")), \
                mock.patch.object(cloud, "generate", side_effect=AssertionError("inference")), \
                mock.patch.object(cloud, "_atomic_json", side_effect=AssertionError("write")):
            plan = translation.plan_translation([line(0)], set(), flush=True)
            request = translation.build_translation_request(plan)
            cloud.build_payload(request["messages"], request["schema"], "gpt-6-luna")


class SourcePolicyTests(unittest.TestCase):
    def plans(self, rows, covered=()):
        yield translation.plan_translation(rows, covered, flush=True)
        yield translation.plan_sentence_translation(rows, covered,
            through_seconds=max(row['end_seconds'] for row in rows), end_of_input=True)['plan']

    def test_meaningful_uncertain_text_remains_a_translation_target(self):
        examples = [('No, the condition does not hold.', ['no_speech']),
                    ('We should preserve this complete sentence.', ['timestamp_outside_audio']),
                    ('so', ['repetition']), ('42', ['unknown']),
                    ('This clause still matters. ' * 55, ['repetition'])]
        for text, reasons in examples:
            rows = [line(0, text, uncertain=True, doubt_reasons=reasons)]
            original = copy.deepcopy(rows)
            for plan in self.plans(rows):
                with self.subTest(text=text[:40], mode=plan['selection'].get('policy')):
                    self.assertEqual(['s0'], plan['target_source_ids'])
                    self.assertEqual(text.strip(), plan['source_lines'][0]['text'])
                    self.assertTrue(plan['source_lines'][0]['uncertain'])
                    self.assertEqual(reasons, plan['source_lines'][0]['doubt_reasons'])
                    self.assertEqual(2, plan['plan_version'])
                    self.assertEqual(1, plan['selection']['source_policy_version'])
                    self.assertEqual([], plan['selection']['excluded_sources'])
                    translation.build_translation_request(plan)
            self.assertEqual(original, rows)

    def test_excluded_filler_and_empty_text_cannot_reenter_as_context(self):
        rows = [line(0, 'No, that is not the requirement.', uncertain=True, doubt_reasons=['no_speech']),
                line(1, 'um, uh... erm', uncertain=True), line(2), line(3, ' ')]
        for plan in self.plans(rows, {'s0'}):
            self.assertEqual(['s2'], plan['target_source_ids'])
            self.assertEqual(['s0', 's2'], [row['id'] for row in plan['source_lines']])
            request = translation.build_translation_request(plan)
            data = json.loads(request['messages'][1]['content'])
            self.assertEqual(['s0'], data['context_source_ids'])
            self.assertEqual(['no_speech'], data['transcript'][0]['doubt_reasons'])
            self.assertEqual([], data['transcript'][1]['doubt_reasons'])
            self.assertEqual([{'source_id': 's1', 'reason': 'filler_only', 'duplicate_of': None},
                              {'source_id': 's3', 'reason': 'empty', 'duplicate_of': None}],
                             plan['selection']['excluded_sources'])
            self.assertNotIn('exclusion_reason', json.dumps(data))
            self.assertNotIn('duplicate_of', json.dumps(data))

    def test_zero_duration_same_chunk_duplicate_is_audited_without_losing_first_source(self):
        text = 'No, the condition does not hold.'
        first = line(0, text, id='c000004-l0001', start_seconds=3, end_seconds=3,
                     uncertain=True, doubt_reasons=['timestamp_outside_audio'])
        duplicate = {**first, 'id': 'c000004-l0002',
                     'doubt_reasons': ['repetition', 'timestamp_outside_audio']}
        rows = [first, duplicate, line(2)]
        original = copy.deepcopy(rows)
        for plan in self.plans(rows):
            self.assertEqual(['c000004-l0001', 's2'], plan['target_source_ids'])
            self.assertEqual([['c000004-l0001'], ['s2']], plan['groups'])
            self.assertEqual(['c000004-l0001', 's2'], [row['id'] for row in plan['source_lines']])
            self.assertEqual([{'source_id': 'c000004-l0002', 'reason': 'duplicate_invalid_timing',
                              'duplicate_of': 'c000004-l0001'}], plan['selection']['excluded_sources'])
            translation.build_translation_request(plan)
        self.assertEqual(original, rows)

    def test_repetition_at_a_different_time_or_without_chunk_provenance_is_retained(self):
        for identities, times in [(('s0', 's1'), ((3, 3), (3, 3))),
                                  (('c000004-l0001', 'c000004-l0002'), ((0, 1), (3, 4))),
                                  (('c000004-l0001', 'c000005-l0001'), ((3, 3), (3, 3)))]:
            rows = [line(index, 'No.', id=identity, start_seconds=times[index][0],
                         end_seconds=times[index][1], uncertain=True,
                         doubt_reasons=['repetition', 'timestamp_outside_audio'])
                    for index, identity in enumerate(identities)]
            for plan in self.plans(rows):
                self.assertEqual(list(identities), plan['target_source_ids'])
                self.assertEqual([], plan['selection']['excluded_sources'])

    def test_explicit_chunk_duplicate_audit_is_frozen_and_saved_with_result(self):
        rows = [line(0, 'No.', start_seconds=3, end_seconds=3, segment=2, chunk=4,
                     uncertain=True, doubt_reasons=['timestamp_outside_audio']),
                line(1, 'No.', start_seconds=3, end_seconds=3, segment=2, chunk=4,
                     uncertain=True, doubt_reasons=['repetition', 'timestamp_outside_audio'])]
        expected = [{'source_id': 's1', 'reason': 'duplicate_invalid_timing', 'duplicate_of': 's0'}]
        for plan in self.plans(rows):
            self.assertEqual(['s0'], plan['target_source_ids'])
            self.assertEqual(expected, plan['selection']['excluded_sources'])
            self.assertNotIn('segment', plan['source_lines'][0])
            altered = copy.deepcopy(plan)
            altered['selection']['excluded_sources'] = []
            with self.assertRaises(translation.TranslationInputError):
                translation.build_translation_request(altered)
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(translation, 'PRIVATE_ROOTS', (Path(directory),)), \
                mock.patch.object(cloud, 'generate', return_value={'result': response(plan)}):
            result = translation.translate_batch(plan, provider='openai', model='gpt-6-luna', out_dir=directory)
            saved = json.loads((Path(result['artifact_dir']) / 'result.json').read_text())
            self.assertEqual(expected, saved['selection']['excluded_sources'])
            self.assertEqual(['s0'], saved['target_source_ids'])
            self.assertEqual(plan['source_hashes'], saved['source_hashes'])

    def test_canonical_reason_metadata_matches_analysis_and_changes_fingerprints(self):
        import lecture_analysis as analysis
        source = line(0, uncertain=True, doubt_reasons=['repetition', 'timestamp_outside_audio',
            'repetition', 'PRIVATE_REASON'], annotations='PRIVATE_ANNOTATION',
            exclusion_reason='PRIVATE_EXCLUSION', duplicate_of='PRIVATE_DUPLICATE')
        first = translation.plan_translation([source], (), flush=True)
        reordered = translation.plan_translation([line(0, uncertain=True,
            doubt_reasons=['unknown', 'timestamp_outside_audio', 'repetition'])], (), flush=True)
        self.assertEqual(first['source_hashes'], reordered['source_hashes'])
        self.assertEqual(first['source_fingerprint'], reordered['source_fingerprint'])
        self.assertEqual(first['plan_fingerprint'], reordered['plan_fingerprint'])
        changed = translation.plan_translation([line(0, uncertain=True,
            doubt_reasons=['timestamp_outside_audio'])], (), flush=True)
        self.assertNotEqual(first['source_hashes'], changed['source_hashes'])
        self.assertNotEqual(first['plan_fingerprint'], changed['plan_fingerprint'])
        snapshot = analysis.build_snapshot_request([source], translation_ids=['s0'])
        self.assertEqual(first['source_hashes'], snapshot['source_hashes'])
        self.assertEqual(first['source_fingerprint'], snapshot['source_fingerprint'])
        self.assertEqual(['repetition', 'timestamp_outside_audio', 'unknown'],
                         first['source_lines'][0]['doubt_reasons'])
        self.assertNotIn('PRIVATE_', json.dumps(first))
        self.assertEqual({'id', 'start_seconds', 'end_seconds', 'text', 'language',
                          'uncertain', 'doubt_reasons'}, set(first['source_lines'][0]))


class SentencePlannerTests(unittest.TestCase):
    def plan(self, rows, covered=(), **options):
        return translation.plan_sentence_translation(rows, covered, **{
            "through_seconds": max((row["end_seconds"] for row in rows), default=0), **options})

    def reasons(self, result):
        return [item["reason"] for item in result["plan"]["selection"]["group_boundaries"]]

    def test_continuation_is_held_then_selected_with_its_whole_source(self):
        initial = [line(0, "We can solve this."), line(1, "But only if")]
        first = self.plan(initial)
        self.assertEqual(first["ready_source_ids"], ["s0"])
        self.assertEqual(first["waiting_source_ids"], ["s1"])
        self.assertEqual(first["next_through_seconds"], 35.5)
        self.assertEqual(self.reasons(first), ["sentence"])
        rows = initial + [line(2, "we work together.")]
        edge = self.plan(rows, {"s0"})
        self.assertIsNone(edge["plan"])
        self.assertEqual(edge["waiting_source_ids"], ["s1", "s2"])
        self.assertEqual(edge["next_through_seconds"], 10.5)
        ready = self.plan(rows, {"s0"}, through_seconds=10.5)
        self.assertEqual(ready["plan"]["groups"], [["s1", "s2"]])
        self.assertEqual(ready["waiting_source_ids"], [])

    def test_whole_mixed_row_waits_even_if_it_contains_a_complete_sentence(self):
        rows = [line(0, "We can solve this. But only if")]
        result = self.plan(rows, through_seconds=10)
        self.assertIsNone(result["plan"])
        self.assertEqual(result["waiting_source_ids"], ["s0"])
        self.assertEqual(result["next_through_seconds"], 32.5)

    def test_lookahead_and_timeout_use_only_processed_audio(self):
        rows = [line(0)]
        with mock.patch.object(translation.time, "time", return_value=10**12), \
                mock.patch.object(translation.time, "monotonic", return_value=10**12):
            waiting = self.plan(rows)
            self.assertIsNone(waiting["plan"])
            self.assertEqual(waiting["next_through_seconds"], 4.5)
            ready = self.plan(rows, through_seconds=4.5)
            self.assertEqual(self.reasons(ready), ["sentence"])
            incomplete = [line(0, "We can only")]
            self.assertIsNone(self.plan(incomplete, through_seconds=32.49)["plan"])
            forced = self.plan(incomplete, through_seconds=32.5)
            self.assertEqual(self.reasons(forced), ["timeout"])
            self.assertEqual(forced["plan"]["through_seconds"], 32.5)

    def test_abbreviations_initials_ellipses_and_dangling_words_wait(self):
        for text in ["Please ask Dr.", "The initials are A.", "In the U.S.",
                     "For example e.g.", "A Ph.D.", "On Dec.", "Smith et al.", "The label is (A.)",
                     "The amount is 3.14", "We should...", "We should…",
                     "We should…!", "But only if.", "This is because.", "Such as."]:
            with self.subTest(text=text):
                self.assertIsNone(self.plan([line(0, text)], through_seconds=10)["plan"])
        for text in ["We can solve this.", "Can we solve this?", "We did it!", 'She said, "Yes."']:
            with self.subTest(text=text):
                self.assertEqual(self.reasons(self.plan([line(0, text)], through_seconds=10)), ["sentence"])

    def test_each_capped_group_returns_to_its_last_sentence_boundary(self):
        rows = [line(i, "continuing the thought") for i in range(18)]
        rows[3]["text"] = "the first sentence ends here."
        rows[10]["text"] = "the second sentence ends here."
        rows[16]["text"] = "the third sentence ends here."
        result = self.plan(rows, through_seconds=60)
        self.assertEqual(result["plan"]["groups"],
                         [[f"s{i}" for i in range(4)], [f"s{i}" for i in range(4, 11)],
                          [f"s{i}" for i in range(11, 17)]])
        self.assertEqual(self.reasons(result), ["sentence"] * 3)
        self.assertEqual(result["waiting_source_ids"], ["s17"])

    def test_capacity_forces_fragment_but_retains_the_next_tail(self):
        rows = [line(i, "continuing") for i in range(9)]
        result = self.plan(rows)
        self.assertEqual(result["plan"]["groups"], [[f"s{i}" for i in range(8)]])
        self.assertEqual(self.reasons(result), ["limit"])
        self.assertEqual(result["waiting_source_ids"], ["s8"])
        self.assertEqual(result["next_through_seconds"], 56.5)
        for limit_rows in [
                [line(0, "é" * 500), line(1, "é" * 501)],
                [line(0, "continuing", end_seconds=30), line(1, "continuing", start_seconds=30, end_seconds=50)]]:
            with self.subTest(rows=limit_rows):
                capped = self.plan(limit_rows)
                self.assertEqual(capped["ready_source_ids"], ["s0"])
                self.assertEqual(self.reasons(capped), ["limit"])
                self.assertEqual(capped["waiting_source_ids"], ["s1"])

    def test_final_drain_bypasses_lookahead_but_labels_unfinished_tail(self):
        result = self.plan([line(0), line(1, "But only if")], end_of_input=True)
        self.assertEqual(result["plan"]["groups"], [["s0"], ["s1"]])
        self.assertEqual(self.reasons(result), ["sentence", "end_of_input"])
        self.assertEqual(result["waiting_source_ids"], [])
        self.assertIsNone(result["next_through_seconds"])

    def test_excluded_covered_and_overlapping_rows_are_source_barriers(self):
        for change, covered in [({"text": "um", "uncertain": True}, set()), ({"language": "ja"}, set()),
                                ({"text": ""}, set()), ({}, {"s1"})]:
            with self.subTest(change=change, covered=covered):
                rows = [line(0, "An unfinished clause", end_seconds=4),
                        line(1, start_seconds=2, end_seconds=3, **change),
                        line(2, "Another unfinished clause", start_seconds=3, end_seconds=5)]
                result = self.plan(rows, covered)
                self.assertEqual(result["plan"]["groups"], [["s0"]])
                self.assertEqual(self.reasons(result), ["source_gap"])
                self.assertEqual(result["waiting_source_ids"], ["s2"])

    def test_known_failed_audio_interval_splits_even_a_short_gap(self):
        rows = [line(0, "Before a missing interval"), line(1, "After that interval")]
        self.assertIsNone(self.plan(rows)["plan"])
        failed = [{"start_seconds": 2.5, "end_seconds": 3.0}]
        result = self.plan(rows, source_breaks=failed)
        self.assertEqual(result["plan"]["groups"], [["s0"]])
        self.assertEqual(self.reasons(result), ["source_gap"])
        self.assertEqual(result["waiting_source_ids"], ["s1"])
        self.assertEqual(result["plan"]["selection"]["source_breaks"], failed)
        long_gap = [rows[0], line(1, "After a long gap", start_seconds=8, end_seconds=10)]
        self.assertEqual(self.reasons(self.plan(long_gap)), ["source_gap"])

    def test_failed_final_interval_closes_tail_without_inventing_completion(self):
        rows = [line(0, "But only if")]
        failed = [{"start_seconds": 2.5, "end_seconds": 3.0}]
        for end_of_input in [False, True]:
            with self.subTest(end_of_input=end_of_input):
                result = self.plan(rows, source_breaks=failed, end_of_input=end_of_input)
                self.assertEqual(result["ready_source_ids"], ["s0"])
                self.assertEqual(self.reasons(result), ["source_gap"])
                self.assertEqual(result["plan"]["selection"]["source_breaks"], failed)
                self.assertIsNone(result["next_through_seconds"])

    def test_oversized_source_blocks_fifo_instead_of_disappearing(self):
        rows = [line(0, "complete."), line(1, "x" * 2001), line(2)]
        first = self.plan(rows, through_seconds=12)
        self.assertEqual(first["ready_source_ids"], ["s0"])
        self.assertEqual(first["waiting_source_ids"], ["s1", "s2"])
        self.assertEqual(first["plan"]["selection"]["blocked_next_source_id"], "s1")
        with self.assertRaises(translation.OversizedSourceError):
            self.plan(rows, {"s0"}, through_seconds=12)
        with self.assertRaises(translation.OversizedSourceError):
            self.plan([line(0, end_seconds=45.01)])

    def test_successive_plans_conserve_all_eligible_ids_and_strict_order(self):
        rows = [line(i, "a complete sentence." if i % 5 == 4 else "a continuing phrase") for i in range(103)]
        rows[20]["uncertain"] = True
        rows[40]["language"] = "ja"
        rows[60]["text"] = ""
        expected = [row["id"] for row in rows if translation._eligible(row)]
        covered, actual = set(), []
        while len(covered) < len(expected):
            result = self.plan(rows, covered, end_of_input=True)
            self.assertIsNotNone(result["plan"])
            self.assertEqual(result["ready_source_ids"], expected[len(actual):len(actual) + len(result["ready_source_ids"])])
            self.assertEqual(result["ready_source_ids"] + result["waiting_source_ids"], expected[len(actual):])
            request = translation.build_translation_request(result["plan"])
            self.assertEqual(request["target_source_ids"], result["ready_source_ids"])
            actual.extend(result["ready_source_ids"])
            covered.update(result["ready_source_ids"])
        self.assertEqual(actual, expected)
        finished = self.plan(rows, covered, end_of_input=True)
        self.assertEqual(finished, {"plan": None, "ready_source_ids": [], "waiting_source_ids": [],
                                    "next_through_seconds": None})

    def test_group_reason_is_frozen_with_plan_and_saved_with_result(self):
        result = self.plan([line(0, "But only if")], end_of_input=True)
        plan = result["plan"]
        altered = copy.deepcopy(plan)
        altered["selection"]["group_boundaries"][0]["reason"] = "sentence"
        with self.assertRaises(translation.TranslationInputError):
            translation.build_translation_request(altered)
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(translation, "PRIVATE_ROOTS", (Path(directory),)), \
                mock.patch.object(cloud, "generate", return_value={"result": response(plan)}):
            generated = translation.translate_batch(plan, provider="openai", model="gpt-6-luna", out_dir=directory)
            saved = json.loads((Path(generated["artifact_dir"]) / "result.json").read_text())
            self.assertEqual(saved["selection"]["group_boundaries"],
                             [{"source_ids": ["s0"], "reason": "end_of_input"}])

    def test_invalid_frontier_timing_and_breaks_fail_without_inference(self):
        for options in [{"through_seconds": 2}, {"through_seconds": float("nan")},
                        {"through_seconds": True}, {"max_wait_seconds": -1},
                        {"lookahead_seconds": True}, {"end_of_input": 1},
                        {"source_breaks": "bad"}, {"source_breaks": [{}]},
                        {"source_breaks": [{"start_seconds": 4, "end_seconds": 3}]}]:
            with self.subTest(options=options), self.assertRaises(translation.TranslationInputError):
                self.plan([line(0)], **options)
        with mock.patch.object(cloud, "generate", side_effect=AssertionError("inference")), \
                mock.patch.object(cloud, "_atomic_json", side_effect=AssertionError("write")):
            result = self.plan([line(0)], through_seconds=5)
            translation.build_translation_request(result["plan"])


class ResponseTests(unittest.TestCase):
    def setUp(self):
        self.plan = translation.plan_translation([line(0), line(1), line(2, "um", uncertain=True), line(3)], set(), flush=True)

    def test_exact_ordered_groups_validate_from_object_and_json(self):
        value = response(self.plan)
        self.assertEqual(translation.validate_translation_response(value, self.plan), value)
        self.assertEqual(translation.validate_translation_response(json.dumps(value), self.plan), value)

    def test_missing_extra_reordered_unknown_duplicate_and_english_fail(self):
        good = response(self.plan)
        bad = [{"blocks": []}, {"blocks": good["blocks"][:1]}, {"blocks": list(reversed(good["blocks"]))},
               {"blocks": good["blocks"] + good["blocks"]}, {**good, "summary": []},
               {"blocks": [{"text": "訳", "source_ids": ["missing"]}, good["blocks"][1]]},
               {"blocks": [{"text": "訳", "source_ids": ["s1", "s0"]}, good["blocks"][1]]},
               {"blocks": [{"text": "訳", "source_ids": ["s0", "s0"]}, good["blocks"][1]]},
               response(self.plan, "This is an unchanged English sentence."), response(self.plan, " "),
               response(self.plan, "訳" * (translation.MAX_BLOCK_TEXT + 1)),
               '{"blocks":[],"blocks":[]}']
        for value in bad:
            with self.subTest(value=str(value)[:100]), self.assertRaises(translation.TranslationResponseError):
                translation.validate_translation_response(value, self.plan)

    def test_unchanged_short_name_allowed_but_english_clause_not_allowed(self):
        plan = translation.plan_translation([line(0, "OpenAI")], set(), flush=True)
        self.assertEqual(translation.validate_translation_response(response(plan, "OpenAI"), plan)["blocks"][0]["text"], "OpenAI")
        with self.assertRaises(translation.TranslationResponseError):
            translation.validate_translation_response(response(plan, "Microsoft"), plan)
        plan = translation.plan_translation([line(0, "We Are Here")], set(), flush=True)
        with self.assertRaises(translation.TranslationResponseError):
            translation.validate_translation_response(response(plan, "We Are Here"), plan)


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.plan = translation.plan_translation([line(0), line(1)], set(), flush=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        roots = mock.patch.object(translation, "PRIVATE_ROOTS", (Path(self.tmp.name),))
        roots.start()
        self.addCleanup(roots.stop)
        no_key = mock.patch.object(cloud, "_api_key", side_effect=AssertionError("test must not read keys"))
        no_key.start()
        self.addCleanup(no_key.stop)

    def call(self, **overrides):
        return translation.translate_batch(self.plan, **{"provider": "openai", "model": "gpt-6-luna",
            "out_dir": self.tmp.name, **overrides})

    def fake_generate(self, messages, schema, **options):
        options["observe_response"]({"id": "fake-response", "output": []})
        return {"result": options["validate"](response(self.plan)), "usage": {"input_tokens": 100, "output_tokens": 50},
                "cost_usd": 0.002, "usage_confirmed": True, "cache_hit": False, "spent_usd": 0.2}

    def test_single_call_forwards_timeout_manual_retry_and_saves_evidence(self):
        with mock.patch.object(cloud, "generate", side_effect=self.fake_generate) as generate:
            result = self.call(timeout=37, retry_failed=True)
        generate.assert_called_once()
        self.assertEqual(generate.call_args.kwargs["timeout"], 37)
        self.assertIs(generate.call_args.kwargs["retry_failed"], True)
        self.assertEqual(result["target_source_ids"], ["s0", "s1"])
        self.assertEqual(result["source_hashes"], self.plan["source_hashes"])
        self.assertFalse(result["semantic_quality_verified"])
        directory = Path(result["artifact_dir"])
        self.assertEqual({path.name for path in directory.iterdir()},
                         {"request.json", "payload.json", "response.json", "generated.json", "result.json"})
        saved = json.loads((directory / "request.json").read_text())
        self.assertEqual(saved["plan_fingerprint"], self.plan["plan_fingerprint"])
        self.assertEqual(json.loads((directory / "payload.json").read_text())["store"], False)

    def test_default_no_retry_timeout_is_not_retried_and_retains_error_request(self):
        with mock.patch.object(cloud, "generate", side_effect=TimeoutError("fake timeout")) as generate:
            with self.assertRaises(TimeoutError):
                self.call()
        generate.assert_called_once()
        self.assertIs(generate.call_args.kwargs["retry_failed"], False)
        directory = next(Path(self.tmp.name).iterdir())
        self.assertTrue((directory / "payload.json").is_file())
        self.assertTrue((directory / "error.json").is_file())
        self.assertFalse((directory / "result.json").exists())

    def test_invalid_response_preserves_raw_and_never_returns_partial_coverage(self):
        def bad_generate(messages, schema, **options):
            options["observe_response"]({"raw": "invalid-output"})
            return {"result": {"blocks": []}}
        with mock.patch.object(cloud, "generate", side_effect=bad_generate):
            with self.assertRaises(translation.TranslationResponseError):
                self.call()
        directory = next(Path(self.tmp.name).iterdir())
        self.assertEqual(json.loads((directory / "response.json").read_text()), {"raw": "invalid-output"})
        self.assertFalse((directory / "result.json").exists())

    def test_cache_hit_charges_zero_and_each_attempt_keeps_its_directory(self):
        generated = {"result": response(self.plan), "cost_usd": 0.02, "cache_hit": True, "usage_confirmed": True}
        with mock.patch.object(cloud, "generate", return_value=generated):
            first, second = self.call(), self.call()
        self.assertEqual(first["cost_usd"], 0)
        self.assertEqual(first["cached_request_cost_usd"], 0.02)
        self.assertNotEqual(first["artifact_dir"], second["artifact_dir"])

    def test_caller_plan_mutation_during_request_cannot_change_saved_coverage(self):
        before = copy.deepcopy(self.plan)
        def mutate_generate(messages, schema, **options):
            self.plan["source_lines"][0]["text"] = "mutated during request"
            self.plan["groups"].clear()
            return {"result": options["validate"](response(before)), "cost_usd": 0.001}
        with mock.patch.object(cloud, "generate", side_effect=mutate_generate):
            result = self.call()
        self.assertEqual(result["source_hashes"], before["source_hashes"])
        self.assertEqual(result["groups"], before["groups"])

    def test_bad_config_fails_before_generation_or_output_creation(self):
        for kwargs in [{"provider": "local"}, {"timeout": 0}, {"timeout": True}, {"retry_failed": 1},
                       {"model": "not-priced"}, {"model": " gpt-6-luna"}]:
            with self.subTest(kwargs=kwargs), mock.patch.object(cloud, "generate") as generate:
                with self.assertRaises(translation.TranslationInputError):
                    self.call(**kwargs)
                generate.assert_not_called()
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_storage_failure_after_generation_propagates_without_success(self):
        original_save = translation._save
        def failing_save(directory, name, value):
            if name == "result.json":
                raise OSError("fake disk full")
            return original_save(directory, name, value)
        with mock.patch.object(cloud, "generate", side_effect=self.fake_generate) as generate, \
                mock.patch.object(translation, "_save", side_effect=failing_save):
            with self.assertRaises(OSError):
                self.call()
        generate.assert_called_once()
        directory = next(Path(self.tmp.name).iterdir())
        self.assertTrue((directory / "generated.json").exists())
        self.assertTrue((directory / "error.json").exists())

    def test_real_adapter_keeps_failed_reservation_and_requires_manual_new_attempt(self):
        # Exercise the actual persistent ledger/cache with only its HTTP/key
        # boundary replaced. This test never reaches the real shared ledger.
        state_dir = Path(self.tmp.name) / "isolated-ledger"
        fresh_response = {"model": "gpt-6-luna", "service_tier": "default", "status": "completed",
            "output": [{"type": "message", "status": "completed", "content": [{"type": "output_text",
                "text": json.dumps(response(self.plan))}]}],
            "usage": {"input_tokens": 100, "output_tokens": 50,
                      "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0}}}
        with mock.patch.object(cloud, "STATE_DIR", state_dir), \
                mock.patch.object(cloud, "_api_key", return_value="fake-key-never-sent"), \
                mock.patch.object(cloud, "_day", return_value="2030-06-04"), \
                mock.patch.object(cloud, "_budget", return_value=cloud.NANODOLLARS), \
                mock.patch.object(cloud, "_post", side_effect=[TimeoutError("ambiguous"), fresh_response]) as post:
            with self.assertRaises(TimeoutError):
                self.call()
            first = cloud._load_ledger()
            entry = next(iter(first["requests"].values()))
            held = entry["charged_nanodollars"]
            self.assertEqual(entry["state"], "failed")
            self.assertEqual(held, entry["reserved_nanodollars"])
            with self.assertRaises(cloud.CloudError):
                self.call()
            self.assertEqual(post.call_count, 1)
            result = self.call(retry_failed=True)
            self.assertEqual(post.call_count, 2)
            ledger = cloud._load_ledger()
            self.assertEqual(len(ledger["requests"]), 2)
            self.assertEqual(sum(item["state"] == "failed" for item in ledger["requests"].values()), 1)
            self.assertGreater(result["spent_usd"], held / cloud.NANODOLLARS)
            self.assertEqual(result["reserved_usd"], held / cloud.NANODOLLARS)
            cached = self.call()
            self.assertEqual(post.call_count, 2)
            self.assertEqual(cached["cost_usd"], 0)
            self.assertTrue(cached["cache_hit"])


if __name__ == "__main__":
    unittest.main()
